/**
 * Chat Command Handler Hook
 *
 * Processes UI commands from chat responses with intelligent incremental
 * filter handling. This is the bridge between raw chat commands and the
 * command executor, adding:
 *
 * - Additive filter merging (add_filter): merges new params without resetting existing
 * - Subtractive filter removal (remove_filter): removes specific filter keys
 * - Filter context tracking: maintains awareness of active filters for chat context
 * - Command deduplication and ordering
 * - Execution result reporting for chat feedback
 *
 * The handler ensures that conversational follow-up queries like
 * "also add skill=fiber" or "remove the region filter" work correctly
 * without losing existing filter state.
 */

import { useCallback, useMemo, useRef } from 'react'
import { useSearchParams, useLocation } from 'react-router-dom'
import { useCommandExecutor, type CommandExecutionResult, type FilterOperation } from './useCommandExecutor'
import type { UICommand } from '@/lib/commandManifest'

// ── Filter Context ───────────────────────────────────────────────────────────

export interface ActiveFilterContext {
  /** Current page path */
  path: string
  /** Active filter key-value pairs from URL */
  filters: Record<string, string>
  /** Number of active filters */
  count: number
  /** Human-readable summary of active filters */
  summary: string
}

export interface CommandHandlerResult {
  /** Results from each executed command */
  results: CommandExecutionResult[]
  /** Whether all commands succeeded */
  allSucceeded: boolean
  /** Filter operations that were performed */
  filterOps: FilterOperation[]
  /** Active filter state after execution */
  activeFilters: ActiveFilterContext
  /** Human-readable summary of what happened */
  summary: string
}

export interface UseChatCommandHandlerReturn {
  /** Process and execute a batch of commands from a chat response */
  handleChatCommands: (commands: UICommand[]) => CommandHandlerResult
  /** Get the current active filter context for inclusion in chat prompts */
  getFilterContext: () => ActiveFilterContext
  /** Check if a filter key is currently active */
  isFilterActive: (key: string) => boolean
  /** Get a formatted context string for the chat agent prompt */
  getContextForAgent: () => string
}

// ── Human-readable filter labels ─────────────────────────────────────────────

const FILTER_LABELS: Record<string, string> = {
  search: 'Search',
  career_stage: 'Career Stage',
  deployability_status: 'Status',
  region: 'Region',
  skill: 'Skill',
  available_before: 'Available Before',
  status: 'Project Status',
  partner: 'Partner',
  stage: 'Training Stage',
  tab: 'Tab',
  type: 'Type',
  agent: 'Agent',
  highlight: 'Highlight',
  scrollTo: 'Scroll Target',
}

const PAGE_NAMES: Record<string, string> = {
  '/ops/dashboard': 'Dashboard',
  '/ops/technicians': 'Technician Directory',
  '/ops/training': 'Training Pipeline',
  '/ops/projects': 'Project Staffing',
  '/ops/inbox': 'Agent Inbox',
  '/ops/headcount': 'Headcount Requests',
  '/tech/portal': 'Technician Portal',
  '/tech/timesheets': 'Timesheets',
  '/partner/portal': 'Partner Portal',
}

function getFilterLabel(key: string): string {
  return FILTER_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function buildFilterSummary(filters: Record<string, string>, path: string): string {
  const filterEntries = Object.entries(filters).filter(
    ([k]) => !['highlight', 'scrollTo'].includes(k)
  )

  if (filterEntries.length === 0) {
    const pageName = PAGE_NAMES[path] || path
    return `Viewing ${pageName} (no filters)`
  }

  const parts = filterEntries.map(
    ([k, v]) => `${getFilterLabel(k)}: ${v}`
  )
  const pageName = PAGE_NAMES[path] || path
  return `${pageName} filtered by ${parts.join(', ')}`
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useChatCommandHandler(): UseChatCommandHandlerReturn {
  const { executeCommand, executeCommands, getCurrentFilters, getCurrentPath } = useCommandExecutor()
  const [searchParams] = useSearchParams()
  const location = useLocation()

  // Track the last execution for dedup
  const lastExecutionRef = useRef<string>('')

  /**
   * Build the current filter context from URL state
   */
  const getFilterContext = useCallback((): ActiveFilterContext => {
    const filters = getCurrentFilters()
    const path = getCurrentPath()
    // Exclude non-filter params
    const filterOnly: Record<string, string> = {}
    Object.entries(filters).forEach(([k, v]) => {
      if (!['highlight', 'scrollTo'].includes(k)) {
        filterOnly[k] = v
      }
    })

    return {
      path,
      filters: filterOnly,
      count: Object.keys(filterOnly).length,
      summary: buildFilterSummary(filterOnly, path),
    }
  }, [getCurrentFilters, getCurrentPath])

  /**
   * Check if a specific filter key is currently active
   */
  const isFilterActive = useCallback(
    (key: string): boolean => {
      return searchParams.has(key)
    },
    [searchParams]
  )

  /**
   * Preprocess commands to optimize execution order and detect incremental intent
   */
  const preprocessCommands = useCallback(
    (commands: UICommand[]): UICommand[] => {
      if (!commands || commands.length === 0) return []

      const processed: UICommand[] = []
      const currentPath = location.pathname
      const currentFilters = getCurrentFilters()

      for (const cmd of commands) {
        // If we get a plain "filter" command while already on the same page,
        // and there are existing filters, auto-upgrade to add_filter
        // to preserve incremental behavior
        if (
          cmd.type === 'filter' &&
          cmd.target === currentPath &&
          Object.keys(currentFilters).length > 0 &&
          cmd.params
        ) {
          // Check if this looks like an additive intent:
          // If the command params are a SUBSET of different keys than current filters,
          // treat it as additive. If it covers ALL current filter keys, it's a replace.
          const cmdKeys = new Set(Object.keys(cmd.params).filter((k) => k !== 'id'))
          const existingKeys = new Set(Object.keys(currentFilters))

          // If command introduces only NEW keys not in existing filters, it's additive
          const allNew = [...cmdKeys].every((k) => !existingKeys.has(k))
          if (allNew && cmdKeys.size > 0) {
            processed.push({
              ...cmd,
              type: 'add_filter',
            })
            continue
          }
        }

        processed.push(cmd)
      }

      return processed
    },
    [location.pathname, getCurrentFilters]
  )

  /**
   * Process and execute a batch of commands from a chat response
   */
  const handleChatCommands = useCallback(
    (commands: UICommand[]): CommandHandlerResult => {
      // Dedup: prevent double-execution of the same command set
      const cmdKey = JSON.stringify(commands)
      if (cmdKey === lastExecutionRef.current) {
        const ctx = getFilterContext()
        return {
          results: [],
          allSucceeded: true,
          filterOps: [],
          activeFilters: ctx,
          summary: 'Commands already applied (skipped duplicate execution)',
        }
      }
      lastExecutionRef.current = cmdKey

      // Preprocess for incremental filter detection
      const processed = preprocessCommands(commands)

      // Execute through the command executor
      const results = executeCommands(processed)

      // Gather filter operations
      const filterOps = results
        .filter((r) => r.filterOp)
        .map((r) => r.filterOp!)

      // Get post-execution filter state
      const activeFilters = getFilterContext()

      // Build summary
      const succeeded = results.filter((r) => r.success)
      const failed = results.filter((r) => !r.success)

      let summary = ''
      if (succeeded.length > 0) {
        summary = succeeded.map((r) => r.description).join('; ')
      }
      if (failed.length > 0) {
        const failSummary = failed.map((r) => r.error || r.description).join('; ')
        summary += summary ? ` (Errors: ${failSummary})` : `Errors: ${failSummary}`
      }

      // Append filter context to summary
      if (filterOps.length > 0 && activeFilters.count > 0) {
        summary += ` → ${activeFilters.summary}`
      }

      return {
        results,
        allSucceeded: failed.length === 0,
        filterOps,
        activeFilters,
        summary,
      }
    },
    [preprocessCommands, executeCommands, getFilterContext]
  )

  /**
   * Get a formatted context string to include in chat agent prompts
   * so the agent knows what filters are currently active
   */
  const getContextForAgent = useCallback((): string => {
    const ctx = getFilterContext()
    const lines: string[] = [
      `Current page: ${PAGE_NAMES[ctx.path] || ctx.path}`,
    ]

    if (ctx.count > 0) {
      lines.push(`Active filters (${ctx.count}):`)
      Object.entries(ctx.filters).forEach(([k, v]) => {
        lines.push(`  - ${getFilterLabel(k)}: ${v}`)
      })
      lines.push('User can say "also filter by X" to add, "remove X filter" to subtract, or "clear filters" to reset.')
    } else {
      lines.push('No active filters. All results shown.')
    }

    return lines.join('\n')
  }, [getFilterContext])

  return {
    handleChatCommands,
    getFilterContext,
    isFilterActive,
    getContextForAgent,
  }
}
