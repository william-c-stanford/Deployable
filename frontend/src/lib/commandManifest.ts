/**
 * UI Command Manifest
 *
 * Fixed schema that maps chat intents to browser URL state updates.
 * The chat agent returns commands from this manifest, which are then
 * executed by the command executor to drive React Router navigation
 * and URL search param updates.
 */

// ─── Command Types ──────────────────────────────────────────────────────────

export type UICommandType =
  | 'navigate'
  | 'filter'
  | 'add_filter'
  | 'remove_filter'
  | 'clear_filters'
  | 'highlight'
  | 'open_detail'
  | 'set_tab'
  | 'scroll_to'
  | 'toast'

export interface UICommand {
  /** The type of command to execute */
  type: UICommandType
  /** Target route or element */
  target: string
  /** Optional parameters for the command */
  params?: Record<string, string | string[] | boolean | number>
  /** Human-readable label for what this command does */
  label?: string
}

// ─── Intent Definitions ─────────────────────────────────────────────────────

export interface IntentMapping {
  /** Unique identifier for the intent */
  intent: string
  /** Human-readable description */
  description: string
  /** Example natural language queries */
  examples: string[]
  /** Commands to execute when this intent is matched */
  commands: UICommand[]
  /** Required role(s) to execute */
  roles: ('ops' | 'technician' | 'partner')[]
}

// ─── Route Registry ─────────────────────────────────────────────────────────

export const ROUTES = {
  OPS_DASHBOARD: '/ops/dashboard',
  OPS_TECHNICIANS: '/ops/technicians',
  OPS_TECHNICIAN_PROFILE: '/ops/technicians/:id',
  OPS_TRAINING: '/ops/training',
  OPS_PROJECTS: '/ops/projects',
  OPS_PROJECT_DETAIL: '/ops/projects/:id',
  OPS_INBOX: '/ops/inbox',
  TECH_PORTAL: '/tech/portal',
} as const

// ─── Filter Parameter Registry ──────────────────────────────────────────────

export const FILTER_PARAMS = {
  // Technician directory filters
  TECHNICIAN_SEARCH: 'search',
  TECHNICIAN_CAREER_STAGE: 'career_stage',
  TECHNICIAN_DEPLOYABILITY: 'deployability_status',
  TECHNICIAN_REGION: 'region',
  TECHNICIAN_SKILL: 'skill',
  TECHNICIAN_AVAILABLE_BEFORE: 'available_before',

  // Project filters
  PROJECT_SEARCH: 'search',
  PROJECT_STATUS: 'status',
  PROJECT_REGION: 'region',
  PROJECT_PARTNER: 'partner',

  // Training filters
  TRAINING_STAGE: 'stage',

  // Inbox filters
  INBOX_TAB: 'tab',
  INBOX_TYPE: 'type',
  INBOX_AGENT: 'agent',
} as const

// ─── Valid Filter Values ────────────────────────────────────────────────────

export const VALID_VALUES = {
  career_stage: [
    'Sourced', 'Screened', 'In Training',
    'Training Completed', 'Awaiting Assignment', 'Deployed',
  ],
  deployability_status: [
    'Ready Now', 'In Training', 'Currently Assigned',
    'Missing Cert', 'Missing Docs', 'Rolling Off Soon', 'Inactive',
  ],
  project_status: [
    'Draft', 'Staffing', 'Active', 'Wrapping Up', 'Closed',
  ],
  inbox_tab: [
    'recommendations', 'rules', 'activity',
  ],
  recommendation_type: [
    'staffing', 'training', 'cert_renewal', 'backfill', 'next_step',
  ],
} as const

// ─── Intent Manifest ────────────────────────────────────────────────────────

export const INTENT_MANIFEST: IntentMapping[] = [
  // ── Navigation Intents ──────────────────────────────────────────────────
  {
    intent: 'nav.dashboard',
    description: 'Navigate to ops dashboard',
    examples: ['show me the dashboard', 'go to dashboard', 'home'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_DASHBOARD }],
    roles: ['ops'],
  },
  {
    intent: 'nav.technicians',
    description: 'Navigate to technician directory',
    examples: ['show me all technicians', 'open technician list', 'go to technicians'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_TECHNICIANS }],
    roles: ['ops'],
  },
  {
    intent: 'nav.technician_profile',
    description: 'Navigate to a specific technician profile',
    examples: ['show me John Smith', 'open technician profile for tech-5'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_TECHNICIAN_PROFILE, params: { id: '' } }],
    roles: ['ops'],
  },
  {
    intent: 'nav.training',
    description: 'Navigate to training pipeline',
    examples: ['open training pipeline', 'show training board', 'go to training'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_TRAINING }],
    roles: ['ops'],
  },
  {
    intent: 'nav.projects',
    description: 'Navigate to project staffing',
    examples: ['show me projects', 'open project staffing', 'go to projects'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_PROJECTS }],
    roles: ['ops'],
  },
  {
    intent: 'nav.project_detail',
    description: 'Navigate to a specific project',
    examples: ['show me the Austin project', 'open project proj-3'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_PROJECT_DETAIL, params: { id: '' } }],
    roles: ['ops', 'partner'],
  },
  {
    intent: 'nav.inbox',
    description: 'Navigate to agent inbox',
    examples: ['show my inbox', 'open agent recommendations', 'go to inbox'],
    commands: [{ type: 'navigate', target: ROUTES.OPS_INBOX }],
    roles: ['ops'],
  },
  {
    intent: 'nav.tech_portal',
    description: 'Navigate to technician portal',
    examples: ['open my portal', 'go to my profile', 'show my assignments'],
    commands: [{ type: 'navigate', target: ROUTES.TECH_PORTAL }],
    roles: ['technician'],
  },

  // ── Filter Intents ────────────────────────────────────────────────────────
  {
    intent: 'filter.technicians_by_status',
    description: 'Filter technicians by deployability status',
    examples: [
      'show me ready now technicians',
      'find techs who are missing certs',
      'who is rolling off soon?',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: { deployability_status: '' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.technicians_by_stage',
    description: 'Filter technicians by career stage',
    examples: [
      'show me deployed technicians',
      'find techs in training',
      'who is awaiting assignment?',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: { career_stage: '' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.technicians_by_skill',
    description: 'Filter technicians by skill',
    examples: [
      'find fiber splicers',
      'who knows OTDR testing?',
      'show techs with cable pulling skills',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: { skill: '' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.technicians_by_region',
    description: 'Filter technicians by region',
    examples: [
      'show me Texas technicians',
      'who is available in the Southeast?',
      'find techs in California',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: { region: '' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.technicians_by_availability',
    description: 'Filter technicians by availability date',
    examples: [
      'who is available next week?',
      'find techs available before March',
      'show available technicians',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: { available_before: '' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.technicians_compound',
    description: 'Filter technicians by multiple criteria',
    examples: [
      'find ready now fiber splicers in Texas',
      'show deployed techs in the Southeast with OTDR skills',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: {} },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.projects_by_status',
    description: 'Filter projects by status',
    examples: [
      'show active projects',
      'which projects are staffing?',
      'find closed projects',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_PROJECTS },
      { type: 'filter', target: ROUTES.OPS_PROJECTS, params: { status: '' } },
    ],
    roles: ['ops', 'partner'],
  },
  {
    intent: 'filter.add_technician',
    description: 'Add a filter to current technician view without removing existing filters',
    examples: [
      'also filter by Ready Now',
      'add skill filter for fiber splicing',
      'narrow down to Southeast region',
      'also show only those in training',
    ],
    commands: [
      { type: 'add_filter', target: ROUTES.OPS_TECHNICIANS, params: {} },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.remove_technician',
    description: 'Remove a specific filter from current technician view',
    examples: [
      'remove the region filter',
      'drop the skill filter',
      'remove the status filter',
      'stop filtering by career stage',
    ],
    commands: [
      { type: 'remove_filter', target: ROUTES.OPS_TECHNICIANS, params: {} },
    ],
    roles: ['ops'],
  },
  {
    intent: 'filter.add_project',
    description: 'Add a filter to current project view without removing existing filters',
    examples: [
      'also filter by active status',
      'narrow to Southeast projects',
    ],
    commands: [
      { type: 'add_filter', target: ROUTES.OPS_PROJECTS, params: {} },
    ],
    roles: ['ops', 'partner'],
  },
  {
    intent: 'filter.remove_project',
    description: 'Remove a specific filter from current project view',
    examples: [
      'remove the status filter',
      'drop the region filter from projects',
    ],
    commands: [
      { type: 'remove_filter', target: ROUTES.OPS_PROJECTS, params: {} },
    ],
    roles: ['ops', 'partner'],
  },
  {
    intent: 'filter.clear',
    description: 'Clear all active filters',
    examples: [
      'clear filters',
      'reset filters',
      'show all',
      'remove filters',
    ],
    commands: [{ type: 'clear_filters', target: 'current' }],
    roles: ['ops', 'technician', 'partner'],
  },

  // ── Detail / Highlight Intents ────────────────────────────────────────────
  {
    intent: 'detail.technician_search',
    description: 'Search for a specific technician by name',
    examples: [
      'find John Smith',
      'search for Maria Garcia',
      'look up tech named Alex',
    ],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_TECHNICIANS },
      { type: 'filter', target: ROUTES.OPS_TECHNICIANS, params: { search: '' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'detail.open_technician',
    description: 'Open a specific technician profile',
    examples: [
      'open John Smith\'s profile',
      'show me details for tech-12',
    ],
    commands: [
      { type: 'open_detail', target: ROUTES.OPS_TECHNICIAN_PROFILE, params: { id: '' } },
    ],
    roles: ['ops'],
  },

  // ── Tab / Section Intents ─────────────────────────────────────────────────
  {
    intent: 'tab.inbox_recommendations',
    description: 'Switch inbox to recommendations tab',
    examples: ['show pending recommendations', 'open recommendation inbox'],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_INBOX },
      { type: 'set_tab', target: ROUTES.OPS_INBOX, params: { tab: 'recommendations' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'tab.inbox_rules',
    description: 'Switch inbox to preference rules tab',
    examples: ['show my preference rules', 'open rules tab'],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_INBOX },
      { type: 'set_tab', target: ROUTES.OPS_INBOX, params: { tab: 'rules' } },
    ],
    roles: ['ops'],
  },
  {
    intent: 'tab.inbox_activity',
    description: 'Switch inbox to activity log tab',
    examples: ['show activity log', 'what happened recently?'],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_INBOX },
      { type: 'set_tab', target: ROUTES.OPS_INBOX, params: { tab: 'activity' } },
    ],
    roles: ['ops'],
  },

  // ── Headcount Intents ───────────────────────────────────────────────────
  {
    intent: 'action.headcount_request',
    description: 'Create a headcount request via natural language',
    examples: [
      'I need 3 fiber splicers in Austin',
      'Request 5 technicians for the Dallas project',
      'Can we get 2 more cable pullers?',
      'We need more splicers for Houston',
      'Hire 4 lead technicians in Texas',
    ],
    commands: [
      {
        type: 'toast',
        target: 'info',
        params: { message: 'Headcount request preview' },
      },
    ],
    roles: ['ops', 'partner'],
  },
  {
    intent: 'action.headcount_confirm',
    description: 'Confirm a pending headcount request from chat',
    examples: ['yes', 'confirm', 'submit', 'go ahead'],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_INBOX },
      {
        type: 'toast',
        target: 'success',
        params: { message: 'Headcount request created' },
      },
    ],
    roles: ['ops', 'partner'],
  },
  {
    intent: 'action.headcount_form',
    description: 'Open headcount request form for structured input',
    examples: ['edit', 'use form', 'open headcount form'],
    commands: [
      { type: 'navigate', target: ROUTES.OPS_PROJECTS },
    ],
    roles: ['ops', 'partner'],
  },

  // ── Toast / Notification Intents ──────────────────────────────────────────
  {
    intent: 'info.help',
    description: 'Show help information about available commands',
    examples: ['help', 'what can you do?', 'what commands are available?'],
    commands: [
      {
        type: 'toast',
        target: 'info',
        params: {
          message: 'I can help you navigate, filter technicians, view projects, and more. Try asking me to show ready technicians or find fiber splicers!',
        },
      },
    ],
    roles: ['ops', 'technician', 'partner'],
  },
]

// ─── Manifest Lookup Helpers ────────────────────────────────────────────────

/**
 * Get all intents available for a given role
 */
export function getIntentsForRole(role: string): IntentMapping[] {
  return INTENT_MANIFEST.filter((m) => m.roles.includes(role as 'ops' | 'technician' | 'partner'))
}

/**
 * Get the manifest as a compact string for inclusion in LLM prompts
 */
export function getManifestPromptContext(role: string): string {
  const intents = getIntentsForRole(role)
  const lines = intents.map((m) => {
    const paramKeys = m.commands
      .filter((c) => c.params)
      .flatMap((c) => Object.keys(c.params || {}))
    const paramStr = paramKeys.length > 0 ? ` [params: ${paramKeys.join(', ')}]` : ''
    return `- ${m.intent}: ${m.description}${paramStr}`
  })
  return [
    'Available UI commands (return as JSON array in ui_commands field):',
    ...lines,
    '',
    'Command format: { "type": "navigate"|"filter"|"add_filter"|"remove_filter"|"clear_filters"|"open_detail"|"set_tab"|"toast", "target": "<route>", "params": { ... } }',
    '',
    'Incremental filter rules:',
    '  - "filter" replaces all params on target page (full reset of filters)',
    '  - "add_filter" merges new params into existing URL filters (additive)',
    '  - "remove_filter" removes specified param keys from URL filters (subtractive)',
    '  - "clear_filters" removes all URL filters',
    '',
    'Valid filter values:',
    `  career_stage: ${VALID_VALUES.career_stage.join(', ')}`,
    `  deployability_status: ${VALID_VALUES.deployability_status.join(', ')}`,
    `  project_status: ${VALID_VALUES.project_status.join(', ')}`,
    `  inbox_tab: ${VALID_VALUES.inbox_tab.join(', ')}`,
    '',
    'Routes:',
    ...Object.entries(ROUTES).map(([k, v]) => `  ${k}: ${v}`),
  ].join('\n')
}

/**
 * Validate a command against the manifest schema
 */
export function validateCommand(cmd: UICommand): { valid: boolean; error?: string } {
  const validTypes: UICommandType[] = [
    'navigate', 'filter', 'add_filter', 'remove_filter', 'clear_filters', 'highlight',
    'open_detail', 'set_tab', 'scroll_to', 'toast',
  ]

  if (!validTypes.includes(cmd.type)) {
    return { valid: false, error: `Invalid command type: ${cmd.type}` }
  }

  if (!cmd.target) {
    return { valid: false, error: 'Command must have a target' }
  }

  // Validate filter values if provided
  if ((cmd.type === 'filter' || cmd.type === 'add_filter') && cmd.params) {
    const { career_stage, deployability_status, status, tab } = cmd.params as Record<string, string>

    if (career_stage && !VALID_VALUES.career_stage.includes(career_stage as typeof VALID_VALUES.career_stage[number])) {
      return { valid: false, error: `Invalid career_stage: ${career_stage}` }
    }
    if (deployability_status && !VALID_VALUES.deployability_status.includes(deployability_status as typeof VALID_VALUES.deployability_status[number])) {
      return { valid: false, error: `Invalid deployability_status: ${deployability_status}` }
    }
    if (status && !VALID_VALUES.project_status.includes(status as typeof VALID_VALUES.project_status[number])) {
      return { valid: false, error: `Invalid project status: ${status}` }
    }
    if (tab && !VALID_VALUES.inbox_tab.includes(tab as typeof VALID_VALUES.inbox_tab[number])) {
      return { valid: false, error: `Invalid tab: ${tab}` }
    }
  }

  return { valid: true }
}
