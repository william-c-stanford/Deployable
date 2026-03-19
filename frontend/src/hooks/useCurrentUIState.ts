/**
 * useCurrentUIState Hook
 *
 * React hook that captures the current UI state reactively.
 * Re-computes when route, search params, or relevant stores change.
 * Used by the chat sidebar to provide context to the conversational agent.
 */

import { useMemo } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { useTechnicianStore } from '@/stores/technicianStore'
import { useProjectStore } from '@/stores/projectStore'
import { useTrainingStore } from '@/stores/trainingStore'
import { useAgentInboxStore } from '@/stores/agentInboxStore'
import { useAuthStore } from '@/stores/authStore'
import { useChatStore } from '@/stores/chatStore'
import type {
  UIStatePayload,
  ViewState,
  FilterState,
  PaginationState,
  SortOrder,
  SortDirection,
  ColumnVisibility,
} from '@/lib/currentUIState'
import { summarizeUIState } from '@/lib/currentUIState'

// ─── Route → Screen Name Mapping ────────────────────────────────────────────

const ROUTE_SCREEN_MAP: Record<string, string> = {
  '/ops/dashboard': 'Ops Dashboard',
  '/ops/technicians': 'Technician Directory',
  '/ops/training': 'Training Pipeline',
  '/ops/projects': 'Project Staffing',
  '/ops/inbox': 'Agent Inbox',
  '/ops/headcount': 'Headcount Approval Queue',
  '/tech/portal': 'Technician Portal',
  '/tech/timesheets': 'Timesheet Submission',
  '/partner/portal': 'Partner Portal',
}

const DEFAULT_COLUMNS: Record<string, string[]> = {
  '/ops/technicians': [
    'name', 'home_base_city', 'career_stage', 'deployability_status',
    'skills', 'certifications', 'available_from', 'approved_regions',
  ],
  '/ops/projects': [
    'name', 'status', 'location_region', 'partner_name',
    'start_date', 'end_date', 'roles', 'assignments',
  ],
  '/ops/inbox': [
    'type', 'target_name', 'overall_score', 'agent',
    'explanation', 'status', 'created_at',
  ],
  '/ops/training': [
    'name', 'career_stage', 'skills', 'training_hours', 'certifications',
  ],
  '/ops/headcount': [
    'partner_name', 'role_name', 'quantity', 'priority',
    'required_skills', 'status', 'created_at',
  ],
}

function getScreenName(pathname: string): string {
  if (ROUTE_SCREEN_MAP[pathname]) return ROUTE_SCREEN_MAP[pathname]
  if (pathname.match(/^\/ops\/technicians\/[\w-]+$/)) return 'Technician Profile'
  if (pathname.match(/^\/ops\/projects\/[\w-]+$/)) return 'Project Detail'
  return 'Unknown'
}

function extractEntityFromPath(pathname: string): { type?: string; id?: string } {
  const techMatch = pathname.match(/^\/ops\/technicians\/([\w-]+)$/)
  if (techMatch) return { type: 'technician', id: techMatch[1] }
  const projectMatch = pathname.match(/^\/ops\/projects\/([\w-]+)$/)
  if (projectMatch) return { type: 'project', id: projectMatch[1] }
  return {}
}

function cleanFilters(filters: Record<string, unknown>): FilterState {
  const cleaned: FilterState = {}
  for (const [key, value] of Object.entries(filters)) {
    if (value === '' || value === null || value === undefined) continue
    if (Array.isArray(value) && value.length === 0) continue
    cleaned[key] = value as string | string[] | boolean | number
  }
  return cleaned
}

// ─── Hook ───────────────────────────────────────────────────────────────────

export interface UseCurrentUIStateReturn {
  /** The full serializable UI state payload */
  state: UIStatePayload
  /** Human-readable summary for chat agent context */
  summary: string
  /** Whether any filters are active */
  hasActiveFilters: boolean
  /** Count of active filters */
  activeFilterCount: number
}

export function useCurrentUIState(): UseCurrentUIStateReturn {
  const location = useLocation()
  const [searchParams] = useSearchParams()

  // Subscribe to relevant store slices
  const techFilters = useTechnicianStore((s) => s.filters)
  const techPage = useTechnicianStore((s) => s.page)
  const techPageSize = useTechnicianStore((s) => s.pageSize)
  const techTotalCount = useTechnicianStore((s) => s.totalCount)
  const techViewMode = useTechnicianStore((s) => s.viewMode)
  const selectedTech = useTechnicianStore((s) => s.selectedTechnician)

  const projectFilters = useProjectStore((s) => s.filters)
  const selectedProjectId = useProjectStore((s) => s.selectedProjectId)

  const trainingFilters = useTrainingStore((s) => s.filters)
  const selectedTrainingTechId = useTrainingStore((s) => s.selectedTechnicianId)

  const inboxFilters = useAgentInboxStore((s) => s.filters)
  const inboxActiveTab = useAgentInboxStore((s) => s.activeTab)

  const authUser = useAuthStore((s) => s.user)
  const chatOpen = useChatStore((s) => s.isOpen)

  const uiState = useMemo<UIStatePayload>(() => {
    const pathname = location.pathname
    const entity = extractEntityFromPath(pathname)

    // URL params
    const urlParams: Record<string, string> = {}
    searchParams.forEach((value, key) => {
      urlParams[key] = value
    })

    // View state
    const view: ViewState = {
      route: pathname,
      screenName: getScreenName(pathname),
      ...(entity.type && { selectedEntityType: entity.type }),
      ...(entity.id && { selectedEntityId: entity.id }),
    }

    let filters: FilterState = {}
    let pagination: PaginationState | null = null
    let sort: SortOrder | null = null
    let columns: ColumnVisibility | null = null

    // Route-specific collection
    if (pathname.startsWith('/ops/technicians') && !entity.id) {
      filters = cleanFilters(techFilters as unknown as Record<string, unknown>)
      view.viewMode = techViewMode
      pagination = {
        page: techPage,
        pageSize: techPageSize,
        totalCount: techTotalCount,
        totalPages: Math.ceil(techTotalCount / techPageSize) || 1,
      }
      // Merge URL params
      if (urlParams.search) filters.search = urlParams.search
      if (urlParams.career_stage) filters.career_stage = urlParams.career_stage
      if (urlParams.deployability_status) filters.deployability_status = urlParams.deployability_status
      if (urlParams.region) filters.region = urlParams.region
      if (urlParams.skill) filters.skill = urlParams.skill
      if (urlParams.available_before) filters.available_before = urlParams.available_before
      columns = { visible: DEFAULT_COLUMNS['/ops/technicians'] || [], hidden: [] }
    } else if (pathname.startsWith('/ops/technicians/') && entity.id) {
      if (selectedTech) {
        view.selectedEntityId = selectedTech.id
      }
    } else if (pathname.startsWith('/ops/projects') && !entity.id) {
      filters = cleanFilters(projectFilters as unknown as Record<string, unknown>)
      if (urlParams.status) filters.status = urlParams.status
      if (urlParams.region) filters.region = urlParams.region
      if (urlParams.partner) filters.partner = urlParams.partner
      if (urlParams.search) filters.search = urlParams.search
      columns = { visible: DEFAULT_COLUMNS['/ops/projects'] || [], hidden: [] }
    } else if (pathname.startsWith('/ops/projects/') && entity.id) {
      if (selectedProjectId) view.selectedEntityId = selectedProjectId
    } else if (pathname === '/ops/training') {
      filters = cleanFilters(trainingFilters as unknown as Record<string, unknown>)
      view.viewMode = 'kanban'
      if (selectedTrainingTechId) {
        view.selectedEntityId = selectedTrainingTechId
        view.selectedEntityType = 'technician'
      }
      if (urlParams.stage) filters.stage = urlParams.stage
      columns = { visible: DEFAULT_COLUMNS['/ops/training'] || [], hidden: [] }
    } else if (pathname === '/ops/inbox') {
      filters = cleanFilters(inboxFilters as unknown as Record<string, unknown>)
      view.activeTab = urlParams.tab || inboxActiveTab
      if (urlParams.type) filters.typeFilter = urlParams.type
      if (urlParams.agent) filters.agentFilter = urlParams.agent
      columns = { visible: DEFAULT_COLUMNS['/ops/inbox'] || [], hidden: [] }
    } else if (pathname === '/ops/headcount') {
      if (urlParams.status) filters.status = urlParams.status
      if (urlParams.priority) filters.priority = urlParams.priority
      if (urlParams.partner) filters.partner = urlParams.partner
      columns = { visible: DEFAULT_COLUMNS['/ops/headcount'] || [], hidden: [] }
    }

    // Parse sort from URL
    if (urlParams.sort) {
      const [field, direction] = urlParams.sort.split(':')
      if (field) {
        sort = { field, direction: (direction === 'desc' ? 'desc' : 'asc') as SortDirection }
      }
    }

    // Final clean
    filters = cleanFilters(filters as Record<string, unknown>)

    return {
      captured_at: new Date().toISOString(),
      view,
      filters,
      url_params: urlParams,
      sort,
      pagination,
      columns,
      user_role: authUser?.role || null,
      user_id: authUser?.user_id || null,
      chat_open: chatOpen,
    }
  }, [
    location.pathname,
    searchParams,
    techFilters, techPage, techPageSize, techTotalCount, techViewMode, selectedTech,
    projectFilters, selectedProjectId,
    trainingFilters, selectedTrainingTechId,
    inboxFilters, inboxActiveTab,
    authUser,
    chatOpen,
  ])

  const summary = useMemo(() => summarizeUIState(uiState), [uiState])

  const activeFilterCount = useMemo(
    () => Object.keys(uiState.filters).length,
    [uiState.filters]
  )

  return {
    state: uiState,
    summary,
    hasActiveFilters: activeFilterCount > 0,
    activeFilterCount,
  }
}
