/**
 * Current UI State Capture Utility
 *
 * Collects active filters, selected views, sort orders, pagination,
 * and visible columns from all Zustand stores and URL search params
 * into a single serializable payload object.
 *
 * This payload is sent to the chat agent so it has full context of
 * what the user is currently viewing when answering questions.
 */

// ─── Types ──────────────────────────────────────────────────────────────────

export type SortDirection = 'asc' | 'desc'

export interface SortOrder {
  /** Column or field key being sorted */
  field: string
  /** Sort direction */
  direction: SortDirection
}

export interface PaginationState {
  /** Current page (1-indexed) */
  page: number
  /** Items per page */
  pageSize: number
  /** Total count of items matching current filters */
  totalCount: number
  /** Total number of pages */
  totalPages: number
}

export interface FilterState {
  /** Key-value pairs of active filters */
  [key: string]: string | string[] | boolean | number | null | undefined
}

export interface ViewState {
  /** Current route path (e.g., /ops/technicians) */
  route: string
  /** Name of the current screen for human-readable context */
  screenName: string
  /** Active tab within the current screen, if any */
  activeTab?: string
  /** View mode (e.g., table vs cards vs kanban) */
  viewMode?: string
  /** Selected entity ID, if a detail view is open */
  selectedEntityId?: string
  /** Selected entity type */
  selectedEntityType?: string
}

export interface ColumnVisibility {
  /** List of visible column keys */
  visible: string[]
  /** List of hidden column keys */
  hidden: string[]
}

export interface UIStatePayload {
  /** Timestamp of capture */
  captured_at: string
  /** Current view/screen information */
  view: ViewState
  /** Active filters for the current screen */
  filters: FilterState
  /** URL search params (raw) */
  url_params: Record<string, string>
  /** Current sort order */
  sort: SortOrder | null
  /** Pagination state */
  pagination: PaginationState | null
  /** Visible/hidden columns */
  columns: ColumnVisibility | null
  /** Role of the current user */
  user_role: string | null
  /** Current user ID */
  user_id: string | null
  /** Whether the chat sidebar is open */
  chat_open: boolean
}

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

function getScreenName(pathname: string): string {
  // Direct match
  if (ROUTE_SCREEN_MAP[pathname]) return ROUTE_SCREEN_MAP[pathname]

  // Pattern matching for parameterized routes
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

// ─── Default Column Definitions Per Screen ──────────────────────────────────

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
    'name', 'career_stage', 'skills', 'training_hours',
    'certifications',
  ],
  '/ops/headcount': [
    'partner_name', 'role_name', 'quantity', 'priority',
    'required_skills', 'status', 'created_at',
  ],
}

// ─── Store Accessors ────────────────────────────────────────────────────────
// We use lazy imports via a registry pattern to avoid circular dependencies.
// Stores register themselves at module load time via registerStoreAccessor().

type StoreAccessor = () => Record<string, unknown>

const storeRegistry: Record<string, StoreAccessor> = {}

/**
 * Register a store accessor for use by the UI state capture utility.
 * Call this from each store module to make its state available.
 */
export function registerStoreAccessor(name: string, accessor: StoreAccessor): void {
  storeRegistry[name] = accessor
}

function getStoreState(name: string): Record<string, unknown> | null {
  const accessor = storeRegistry[name]
  if (!accessor) return null
  try {
    return accessor()
  } catch {
    return null
  }
}

function getTechnicianStoreState() {
  return getStoreState('technician') as {
    filters: FilterState
    page: number
    pageSize: number
    totalCount: number
    viewMode: string
    selectedTechnician: { id: string } | null
  } | null
}

function getProjectStoreState() {
  return getStoreState('project') as {
    filters: FilterState
    selectedProjectId: string | null
  } | null
}

function getTrainingStoreState() {
  return getStoreState('training') as {
    filters: FilterState
    selectedTechnicianId: string | null
  } | null
}

function getAgentInboxStoreState() {
  return getStoreState('agentInbox') as {
    filters: FilterState
    activeTab: string
  } | null
}

function getAuthStoreState() {
  return getStoreState('auth') as {
    user: { user_id: string; role: string } | null
  } | null
}

function getChatStoreState() {
  return getStoreState('chat') as {
    isOpen: boolean
  } | null
}

// ─── Filter Cleaners ────────────────────────────────────────────────────────

/**
 * Strip empty/falsy filter values so the payload only contains active filters.
 */
function cleanFilters(filters: FilterState): FilterState {
  const cleaned: FilterState = {}
  for (const [key, value] of Object.entries(filters)) {
    if (value === '' || value === null || value === undefined) continue
    if (Array.isArray(value) && value.length === 0) continue
    cleaned[key] = value
  }
  return cleaned
}

// ─── Main Capture Function ──────────────────────────────────────────────────

/**
 * Capture the current UI state as a serializable payload.
 *
 * This reads the current URL, Zustand stores, and derives context
 * about what the user is viewing. The payload can be sent to the
 * chat agent or logged for analytics.
 *
 * @param pathname - Current route path (from react-router location.pathname)
 * @param searchParams - Current URL search params (from useSearchParams or window.location.search)
 * @param overrides - Optional overrides for specific fields
 */
export function captureUIState(
  pathname: string,
  searchParams?: URLSearchParams | string,
  overrides?: Partial<UIStatePayload>
): UIStatePayload {
  // Parse search params
  const params = typeof searchParams === 'string'
    ? new URLSearchParams(searchParams)
    : searchParams || new URLSearchParams(window.location.search)

  const urlParams: Record<string, string> = {}
  params.forEach((value, key) => {
    urlParams[key] = value
  })

  // Determine the active screen
  const screenName = getScreenName(pathname)
  const entity = extractEntityFromPath(pathname)

  // Build the view state
  const view: ViewState = {
    route: pathname,
    screenName,
    ...(entity.type && { selectedEntityType: entity.type }),
    ...(entity.id && { selectedEntityId: entity.id }),
  }

  // Collect filters, pagination, sort, columns based on current route
  let filters: FilterState = {}
  let pagination: PaginationState | null = null
  let sort: SortOrder | null = null
  let columns: ColumnVisibility | null = null

  // Get auth info
  const authState = getAuthStoreState()
  const userRole = authState?.user?.role || localStorage.getItem('role') || null
  const userId = authState?.user?.user_id || localStorage.getItem('userId') || null

  // Get chat state
  const chatState = getChatStoreState()
  const chatOpen = chatState?.isOpen || false

  // ── Route-specific state collection ─────────────────────────────────────

  if (pathname.startsWith('/ops/technicians') && !entity.id) {
    const techState = getTechnicianStoreState()
    if (techState) {
      filters = cleanFilters(techState.filters)
      view.viewMode = techState.viewMode
      pagination = {
        page: techState.page,
        pageSize: techState.pageSize,
        totalCount: techState.totalCount,
        totalPages: Math.ceil(techState.totalCount / techState.pageSize),
      }
    }
    // Also merge URL params as filters (they may be set by chat commands)
    if (urlParams.search) filters.search = urlParams.search
    if (urlParams.career_stage) filters.career_stage = urlParams.career_stage
    if (urlParams.deployability_status) filters.deployability_status = urlParams.deployability_status
    if (urlParams.region) filters.region = urlParams.region
    if (urlParams.skill) filters.skill = urlParams.skill
    if (urlParams.available_before) filters.available_before = urlParams.available_before

    columns = {
      visible: DEFAULT_COLUMNS['/ops/technicians'] || [],
      hidden: [],
    }
  } else if (pathname.startsWith('/ops/technicians/') && entity.id) {
    // Technician profile — include selected tech
    const techState = getTechnicianStoreState()
    if (techState?.selectedTechnician) {
      view.selectedEntityId = techState.selectedTechnician.id
    }
  } else if (pathname.startsWith('/ops/projects') && !entity.id) {
    const projectState = getProjectStoreState()
    if (projectState) {
      filters = cleanFilters(projectState.filters)
    }
    // URL params override
    if (urlParams.status) filters.status = urlParams.status
    if (urlParams.region) filters.region = urlParams.region
    if (urlParams.partner) filters.partner = urlParams.partner
    if (urlParams.search) filters.search = urlParams.search

    columns = {
      visible: DEFAULT_COLUMNS['/ops/projects'] || [],
      hidden: [],
    }
  } else if (pathname.startsWith('/ops/projects/') && entity.id) {
    const projectState = getProjectStoreState()
    if (projectState?.selectedProjectId) {
      view.selectedEntityId = projectState.selectedProjectId
    }
  } else if (pathname === '/ops/training') {
    const trainingState = getTrainingStoreState()
    if (trainingState) {
      filters = cleanFilters(trainingState.filters)
      if (trainingState.selectedTechnicianId) {
        view.selectedEntityId = trainingState.selectedTechnicianId
        view.selectedEntityType = 'technician'
      }
    }
    view.viewMode = 'kanban'
    if (urlParams.stage) filters.stage = urlParams.stage

    columns = {
      visible: DEFAULT_COLUMNS['/ops/training'] || [],
      hidden: [],
    }
  } else if (pathname === '/ops/inbox') {
    const inboxState = getAgentInboxStoreState()
    if (inboxState) {
      filters = cleanFilters(inboxState.filters)
      view.activeTab = inboxState.activeTab
    }
    // URL tab param override
    if (urlParams.tab) view.activeTab = urlParams.tab
    if (urlParams.type) filters.typeFilter = urlParams.type
    if (urlParams.agent) filters.agentFilter = urlParams.agent

    columns = {
      visible: DEFAULT_COLUMNS['/ops/inbox'] || [],
      hidden: [],
    }
  } else if (pathname === '/ops/headcount') {
    // Headcount has simpler state — pull from URL params
    if (urlParams.status) filters.status = urlParams.status
    if (urlParams.priority) filters.priority = urlParams.priority
    if (urlParams.partner) filters.partner = urlParams.partner

    columns = {
      visible: DEFAULT_COLUMNS['/ops/headcount'] || [],
      hidden: [],
    }
  }

  // Parse sort from URL params (convention: sort=field:direction)
  if (urlParams.sort) {
    const [field, direction] = urlParams.sort.split(':')
    if (field) {
      sort = {
        field,
        direction: (direction === 'desc' ? 'desc' : 'asc') as SortDirection,
      }
    }
  }

  // Re-clean filters (after URL param merging)
  filters = cleanFilters(filters)

  // Build final payload
  const payload: UIStatePayload = {
    captured_at: new Date().toISOString(),
    view,
    filters,
    url_params: urlParams,
    sort,
    pagination,
    columns,
    user_role: userRole,
    user_id: userId,
    chat_open: chatOpen,
    ...overrides,
  }

  return payload
}

// ─── Hook-friendly Capture ──────────────────────────────────────────────────

/**
 * Capture UI state using window.location (for non-React contexts
 * or when router hooks are unavailable).
 */
export function captureUIStateFromWindow(
  overrides?: Partial<UIStatePayload>
): UIStatePayload {
  const pathname = window.location.pathname
  const searchParams = new URLSearchParams(window.location.search)
  return captureUIState(pathname, searchParams, overrides)
}

// ─── Summary Generator ──────────────────────────────────────────────────────

/**
 * Generate a human-readable summary of the current UI state.
 * Useful for inclusion in chat agent context prompts.
 */
export function summarizeUIState(state: UIStatePayload): string {
  const lines: string[] = []

  // Screen context
  lines.push(`Screen: ${state.view.screenName}`)
  if (state.view.route) lines.push(`Route: ${state.view.route}`)
  if (state.view.activeTab) lines.push(`Active Tab: ${state.view.activeTab}`)
  if (state.view.viewMode) lines.push(`View Mode: ${state.view.viewMode}`)

  // Selected entity
  if (state.view.selectedEntityId) {
    lines.push(`Selected ${state.view.selectedEntityType || 'entity'}: ${state.view.selectedEntityId}`)
  }

  // Active filters
  const filterEntries = Object.entries(state.filters)
  if (filterEntries.length > 0) {
    lines.push('Active Filters:')
    for (const [key, value] of filterEntries) {
      lines.push(`  ${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`)
    }
  } else {
    lines.push('Filters: none')
  }

  // Pagination
  if (state.pagination) {
    lines.push(`Page: ${state.pagination.page}/${state.pagination.totalPages} (${state.pagination.totalCount} total items)`)
  }

  // Sort
  if (state.sort) {
    lines.push(`Sorted by: ${state.sort.field} (${state.sort.direction})`)
  }

  // User context
  if (state.user_role) lines.push(`User Role: ${state.user_role}`)

  return lines.join('\n')
}

// ─── Serialization Helpers ──────────────────────────────────────────────────

/**
 * Serialize UI state to a JSON string.
 */
export function serializeUIState(state: UIStatePayload): string {
  return JSON.stringify(state)
}

/**
 * Deserialize a JSON string back to UIStatePayload.
 * Returns null if parsing fails.
 */
export function deserializeUIState(json: string): UIStatePayload | null {
  try {
    const parsed = JSON.parse(json)
    // Basic shape validation
    if (parsed && typeof parsed === 'object' && parsed.view && parsed.captured_at) {
      return parsed as UIStatePayload
    }
    return null
  } catch {
    return null
  }
}

/**
 * Compare two UI state snapshots and return the differences.
 * Useful for detecting what changed between captures.
 */
export function diffUIState(
  prev: UIStatePayload,
  next: UIStatePayload
): Partial<UIStatePayload> {
  const diff: Record<string, unknown> = {}

  // Route change
  if (prev.view.route !== next.view.route) {
    diff.view = next.view
  }

  // Filter changes
  const prevFilterKeys = new Set(Object.keys(prev.filters))
  const nextFilterKeys = new Set(Object.keys(next.filters))
  const allFilterKeys = new Set([...prevFilterKeys, ...nextFilterKeys])
  const filterDiff: FilterState = {}
  let filtersChanged = false
  for (const key of allFilterKeys) {
    const prevVal = JSON.stringify(prev.filters[key] ?? null)
    const nextVal = JSON.stringify(next.filters[key] ?? null)
    if (prevVal !== nextVal) {
      filterDiff[key] = next.filters[key] ?? null
      filtersChanged = true
    }
  }
  if (filtersChanged) diff.filters = filterDiff

  // Pagination change
  if (JSON.stringify(prev.pagination) !== JSON.stringify(next.pagination)) {
    diff.pagination = next.pagination
  }

  // Sort change
  if (JSON.stringify(prev.sort) !== JSON.stringify(next.sort)) {
    diff.sort = next.sort
  }

  // Tab change
  if (prev.view.activeTab !== next.view.activeTab) {
    if (!diff.view) diff.view = { ...next.view }
  }

  return diff as Partial<UIStatePayload>
}
