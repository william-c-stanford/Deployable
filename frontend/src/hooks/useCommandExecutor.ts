/**
 * Command Executor Hook
 *
 * Executes UI commands from the chat agent by driving React Router
 * navigation and URL search parameter updates. This is the bridge
 * between parsed chat responses and browser state.
 */

import { useCallback } from 'react'
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import type { UICommand } from '@/lib/commandManifest'
import { validateCommand, ROUTES } from '@/lib/commandManifest'

export type FilterOperation = 'set' | 'add' | 'remove' | 'clear'

export interface CommandExecutionResult {
  success: boolean
  command: UICommand
  error?: string
  /** Description of what was executed for logging */
  description: string
  /** Filter operation that was performed, for tracking */
  filterOp?: FilterOperation
  /** The resulting URL search params after the command */
  resultingParams?: Record<string, string>
}

export interface UseCommandExecutorReturn {
  /** Execute a single UI command */
  executeCommand: (cmd: UICommand) => CommandExecutionResult
  /** Execute a batch of UI commands sequentially */
  executeCommands: (cmds: UICommand[]) => CommandExecutionResult[]
  /** Get current URL state as filter params */
  getCurrentFilters: () => Record<string, string>
  /** Get current pathname */
  getCurrentPath: () => string
}

export function useCommandExecutor(): UseCommandExecutorReturn {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()

  /**
   * Resolve route template params (e.g. /ops/technicians/:id → /ops/technicians/tech-5)
   */
  const resolveRoute = useCallback(
    (target: string, params?: Record<string, string | string[] | boolean | number>): string => {
      let resolved = target
      if (params) {
        // Replace :param tokens in the route
        Object.entries(params).forEach(([key, value]) => {
          const token = `:${key}`
          if (resolved.includes(token) && typeof value === 'string') {
            resolved = resolved.replace(token, encodeURIComponent(value))
          }
        })
      }
      return resolved
    },
    []
  )

  /**
   * Build search params string from filter params, merging with existing
   */
  const buildSearchParams = useCallback(
    (
      params: Record<string, string | string[] | boolean | number>,
      currentPath: string,
      targetPath: string
    ): URLSearchParams => {
      // If navigating to a different page, start fresh
      const isNewPage = currentPath !== targetPath
      const newParams = isNewPage ? new URLSearchParams() : new URLSearchParams(searchParams)

      Object.entries(params).forEach(([key, value]) => {
        // Skip route template params (like :id)
        if (key === 'id') return

        if (value === '' || value === null || value === undefined || value === false) {
          newParams.delete(key)
        } else if (Array.isArray(value)) {
          newParams.delete(key)
          value.forEach((v) => newParams.append(key, v))
        } else {
          newParams.set(key, String(value))
        }
      })

      return newParams
    },
    [searchParams]
  )

  /**
   * Execute a single UI command
   */
  const executeCommand = useCallback(
    (cmd: UICommand): CommandExecutionResult => {
      // Validate command
      const validation = validateCommand(cmd)
      if (!validation.valid) {
        return {
          success: false,
          command: cmd,
          error: validation.error,
          description: `Rejected: ${validation.error}`,
        }
      }

      try {
        switch (cmd.type) {
          case 'navigate': {
            const route = resolveRoute(cmd.target, cmd.params as Record<string, string>)
            navigate(route)
            return {
              success: true,
              command: cmd,
              description: `Navigated to ${route}`,
            }
          }

          case 'filter': {
            if (!cmd.params) {
              return {
                success: false,
                command: cmd,
                error: 'Filter command requires params',
                description: 'Filter failed: no params',
              }
            }

            const targetPath = resolveRoute(cmd.target, cmd.params as Record<string, string>)
            const filterParams = buildSearchParams(
              cmd.params as Record<string, string>,
              location.pathname,
              targetPath
            )

            // If we're on a different page, navigate with search params
            if (location.pathname !== targetPath) {
              navigate(`${targetPath}?${filterParams.toString()}`)
            } else {
              setSearchParams(filterParams, { replace: true })
            }

            const filterDesc = Object.entries(cmd.params)
              .filter(([k]) => k !== 'id')
              .map(([k, v]) => `${k}=${v}`)
              .join(', ')

            const resultParams: Record<string, string> = {}
            filterParams.forEach((v, k) => { resultParams[k] = v })

            return {
              success: true,
              command: cmd,
              description: `Applied filters: ${filterDesc}`,
              filterOp: 'set',
              resultingParams: resultParams,
            }
          }

          case 'add_filter': {
            // Additive: merge new params INTO existing URL params without clearing
            if (!cmd.params) {
              return {
                success: false,
                command: cmd,
                error: 'add_filter command requires params',
                description: 'Add filter failed: no params',
              }
            }

            const addTarget = resolveRoute(cmd.target, cmd.params as Record<string, string>)
            // Always preserve existing params (additive merge)
            const addParams = new URLSearchParams(
              location.pathname === addTarget ? searchParams : undefined
            )

            Object.entries(cmd.params).forEach(([key, value]) => {
              if (key === 'id') return
              if (value === '' || value === null || value === undefined || value === false) {
                // Empty value in add_filter means no-op (skip)
                return
              } else if (Array.isArray(value)) {
                // For arrays, append without clearing existing values
                value.forEach((v) => {
                  if (!addParams.getAll(key).includes(v)) {
                    addParams.append(key, v)
                  }
                })
              } else {
                addParams.set(key, String(value))
              }
            })

            if (location.pathname !== addTarget) {
              navigate(`${addTarget}?${addParams.toString()}`)
            } else {
              setSearchParams(addParams, { replace: true })
            }

            const addDesc = Object.entries(cmd.params)
              .filter(([k]) => k !== 'id')
              .map(([k, v]) => `${k}=${v}`)
              .join(', ')

            const addResultParams: Record<string, string> = {}
            addParams.forEach((v, k) => { addResultParams[k] = v })

            return {
              success: true,
              command: cmd,
              description: `Added filters: ${addDesc}`,
              filterOp: 'add',
              resultingParams: addResultParams,
            }
          }

          case 'remove_filter': {
            // Subtractive: remove specified param keys from URL
            if (!cmd.params) {
              return {
                success: false,
                command: cmd,
                error: 'remove_filter command requires params specifying keys to remove',
                description: 'Remove filter failed: no params',
              }
            }

            const removeTarget = resolveRoute(cmd.target, cmd.params as Record<string, string>)
            const removeParams = new URLSearchParams(searchParams)

            // The params object keys indicate which filters to remove
            // e.g., { region: "", skill: "" } removes region and skill filters
            // Or { keys: ["region", "skill"] } as alternative format
            const keysParam = cmd.params.keys
            if (Array.isArray(keysParam)) {
              keysParam.forEach((k) => removeParams.delete(String(k)))
            } else {
              Object.keys(cmd.params).forEach((key) => {
                if (key === 'id') return
                removeParams.delete(key)
              })
            }

            if (location.pathname !== removeTarget) {
              navigate(`${removeTarget}?${removeParams.toString()}`)
            } else {
              setSearchParams(removeParams, { replace: true })
            }

            const removedKeys = Array.isArray(keysParam)
              ? keysParam.join(', ')
              : Object.keys(cmd.params).filter((k) => k !== 'id').join(', ')

            const removeResultParams: Record<string, string> = {}
            removeParams.forEach((v, k) => { removeResultParams[k] = v })

            return {
              success: true,
              command: cmd,
              description: `Removed filters: ${removedKeys}`,
              filterOp: 'remove',
              resultingParams: removeResultParams,
            }
          }

          case 'clear_filters': {
            setSearchParams(new URLSearchParams(), { replace: true })
            return {
              success: true,
              command: cmd,
              description: 'Cleared all filters',
              filterOp: 'clear',
              resultingParams: {},
            }
          }

          case 'open_detail': {
            const detailRoute = resolveRoute(cmd.target, cmd.params as Record<string, string>)
            navigate(detailRoute)
            return {
              success: true,
              command: cmd,
              description: `Opened detail view: ${detailRoute}`,
            }
          }

          case 'set_tab': {
            if (!cmd.params?.tab) {
              return {
                success: false,
                command: cmd,
                error: 'set_tab requires a tab param',
                description: 'Tab switch failed: no tab specified',
              }
            }

            const tabRoute = resolveRoute(cmd.target, cmd.params as Record<string, string>)
            const tabParams = new URLSearchParams(searchParams)
            tabParams.set('tab', String(cmd.params.tab))

            if (location.pathname !== tabRoute) {
              navigate(`${tabRoute}?${tabParams.toString()}`)
            } else {
              setSearchParams(tabParams, { replace: true })
            }

            return {
              success: true,
              command: cmd,
              description: `Switched to tab: ${cmd.params.tab}`,
            }
          }

          case 'highlight': {
            // Set highlight param in URL for the page to pick up
            const highlightParams = new URLSearchParams(searchParams)
            highlightParams.set('highlight', String(cmd.params?.id || cmd.target))
            setSearchParams(highlightParams, { replace: true })

            return {
              success: true,
              command: cmd,
              description: `Highlighted: ${cmd.params?.id || cmd.target}`,
            }
          }

          case 'scroll_to': {
            // Set scroll target in URL
            const scrollParams = new URLSearchParams(searchParams)
            scrollParams.set('scrollTo', cmd.target)
            setSearchParams(scrollParams, { replace: true })

            // Also try direct DOM scroll
            setTimeout(() => {
              const el = document.getElementById(cmd.target)
              el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
            }, 100)

            return {
              success: true,
              command: cmd,
              description: `Scrolled to: ${cmd.target}`,
            }
          }

          case 'toast': {
            // Toast commands are handled by the chat UI layer
            return {
              success: true,
              command: cmd,
              description: `Toast: ${cmd.params?.message || cmd.target}`,
            }
          }

          default:
            return {
              success: false,
              command: cmd,
              error: `Unknown command type: ${cmd.type}`,
              description: `Unknown command: ${cmd.type}`,
            }
        }
      } catch (err) {
        return {
          success: false,
          command: cmd,
          error: err instanceof Error ? err.message : 'Unknown error',
          description: `Error executing ${cmd.type}: ${err}`,
        }
      }
    },
    [navigate, location.pathname, searchParams, setSearchParams, resolveRoute, buildSearchParams]
  )

  /**
   * Execute a batch of commands sequentially
   */
  const executeCommands = useCallback(
    (cmds: UICommand[]): CommandExecutionResult[] => {
      if (!cmds || !Array.isArray(cmds)) return []

      // Deduplicate: if we have navigate + filter to same target, skip the navigate
      const filterTypes = new Set(['filter', 'add_filter', 'remove_filter', 'set_tab'])
      const dedupedCmds = cmds.reduce<UICommand[]>((acc, cmd, i) => {
        if (
          cmd.type === 'navigate' &&
          i < cmds.length - 1 &&
          filterTypes.has(cmds[i + 1].type) &&
          cmds[i + 1].target === cmd.target
        ) {
          // Skip standalone navigate when followed by filter/tab to same target
          return acc
        }
        acc.push(cmd)
        return acc
      }, [])

      return dedupedCmds.map((cmd) => executeCommand(cmd))
    },
    [executeCommand]
  )

  /**
   * Get current URL search params as a flat filter object
   */
  const getCurrentFilters = useCallback((): Record<string, string> => {
    const filters: Record<string, string> = {}
    searchParams.forEach((value, key) => {
      filters[key] = value
    })
    return filters
  }, [searchParams])

  const getCurrentPath = useCallback(() => location.pathname, [location.pathname])

  return {
    executeCommand,
    executeCommands,
    getCurrentFilters,
    getCurrentPath,
  }
}
