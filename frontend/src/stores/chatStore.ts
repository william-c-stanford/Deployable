import { create } from 'zustand'
import type { UICommand } from '@/lib/commandManifest'
import { captureUIStateFromWindow } from '@/lib/currentUIState'
import type { UIStatePayload } from '@/lib/currentUIState'

/**
 * Chat Store
 *
 * Manages chat sidebar state, message history, SSE streaming, session
 * persistence across navigation, and UI command tracking. Commands from
 * the manifest are attached to assistant messages and executed by the
 * command executor hook in the ChatSidebar component.
 *
 * Chat state persists to localStorage so it survives page navigation.
 */

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  isStreaming?: boolean
  /** UI commands returned by the agent, to be executed client-side */
  ui_commands?: UICommand[]
  /** Whether commands have been executed */
  commands_executed?: boolean
}

interface ChatState {
  isOpen: boolean
  messages: ChatMessage[]
  inputValue: string
  isLoading: boolean
  streamingMessageId: string | null

  // Session persistence
  currentSessionId: string | null

  toggleOpen: () => void
  setOpen: (open: boolean) => void
  setInputValue: (value: string) => void
  addMessage: (message: Omit<ChatMessage, 'id' | 'timestamp'>) => string
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void
  appendToMessage: (id: string, token: string) => void
  markCommandsExecuted: (id: string) => void
  setLoading: (loading: boolean) => void
  setStreamingMessageId: (id: string | null) => void
  clearMessages: () => void
  newSession: () => void
  sendMessage: (content: string) => Promise<void>
}

let messageCounter = 0
function generateId(): string {
  return `msg-${Date.now()}-${++messageCounter}`
}

const WELCOME_MESSAGE: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  content:
    "Hey! I'm Deployable AI \u2014 your workforce operations copilot. Ask me anything about technicians, projects, staffing, or training. I can also navigate and filter the app for you.\n\nTry:\n- *\"Show me ready now technicians\"*\n- *\"Go to active projects\"*\n- *\"Find fiber splicers in Texas\"*\n- *\"Open the training pipeline\"*",
  timestamp: new Date().toISOString(),
}

// ── LocalStorage persistence ────────────────────────────────────────────────

const STORAGE_KEYS = {
  messages: 'deployable_chat_messages',
  sessionId: 'deployable_chat_session_id',
  isOpen: 'deployable_chat_is_open',
}

function saveToStorage(state: Pick<ChatState, 'messages' | 'currentSessionId'>) {
  try {
    // Strip streaming state from persisted messages
    const cleaned = state.messages.map((m) => ({ ...m, isStreaming: false }))
    localStorage.setItem(STORAGE_KEYS.messages, JSON.stringify(cleaned))
    localStorage.setItem(STORAGE_KEYS.sessionId, state.currentSessionId || '')
  } catch {
    // Storage full or unavailable
  }
}

function loadFromStorage(): { messages: ChatMessage[]; sessionId: string | null } {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.messages)
    const sessionId = localStorage.getItem(STORAGE_KEYS.sessionId) || null
    if (raw) {
      const messages = JSON.parse(raw) as ChatMessage[]
      if (Array.isArray(messages) && messages.length > 0) {
        return {
          messages: messages.map((m) => ({ ...m, isStreaming: false })),
          sessionId,
        }
      }
    }
  } catch {
    // Corrupt data
  }
  return { messages: [WELCOME_MESSAGE], sessionId: null }
}

// ── Auth headers helper ─────────────────────────────────────────────────────

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = localStorage.getItem('token')
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  // Demo mode fallback headers
  const role = localStorage.getItem('role')
  const userId = localStorage.getItem('userId')
  if (role) headers['X-Demo-Role'] = role
  if (userId) headers['X-Demo-User-Id'] = userId
  return headers
}

// ── UI State → Backend UIStateContext mapping ────────────────────────────
// The backend expects a compact UIStateContext shape; the frontend captures
// a richer UIStatePayload. We map the relevant fields here.

interface BackendUIStateContext {
  current_route: string | null
  active_filters: Record<string, string> | null
  active_tab: string | null
  selected_entity_id: string | null
  selected_entity_type: string | null
  viewport: string | null
}

function getViewportHint(): 'mobile' | 'tablet' | 'desktop' {
  const w = window.innerWidth
  if (w < 768) return 'mobile'
  if (w < 1024) return 'tablet'
  return 'desktop'
}

function mapUIStateForBackend(state: UIStatePayload): BackendUIStateContext {
  // Flatten filters to string values only (backend expects Dict[str, str])
  const flatFilters: Record<string, string> = {}
  for (const [key, value] of Object.entries(state.filters)) {
    if (value === null || value === undefined) continue
    if (Array.isArray(value)) {
      flatFilters[key] = value.join(',')
    } else {
      flatFilters[key] = String(value)
    }
  }

  return {
    current_route: state.view.route || null,
    active_filters: Object.keys(flatFilters).length > 0 ? flatFilters : null,
    active_tab: state.view.activeTab || null,
    selected_entity_id: state.view.selectedEntityId || null,
    selected_entity_type: state.view.selectedEntityType || null,
    viewport: getViewportHint(),
  }
}

// ── Backend → Frontend command format mapping ───────────────────────────────
// Backend sends: { action: "navigate", target: "..." }
// Frontend expects: { type: "navigate", target: "..." }

function mapBackendCommands(backendCmds: Record<string, unknown>[]): UICommand[] {
  return backendCmds.map((cmd) => ({
    type: (cmd.action || cmd.type || 'navigate') as UICommand['type'],
    target: (cmd.target || '') as string,
    params: (cmd.params || undefined) as Record<string, string> | undefined,
    label: (cmd.label || undefined) as string | undefined,
  }))
}

// ── SSE Event Parser ────────────────────────────────────────────────────────

interface SSEEvent {
  event: string
  data: string
}

function parseSSEBuffer(buffer: string): { events: SSEEvent[]; remaining: string } {
  const events: SSEEvent[] = []
  const blocks = buffer.split('\n\n')

  // The last block might be incomplete
  const remaining = blocks.pop() || ''

  for (const block of blocks) {
    if (!block.trim()) continue
    const lines = block.split('\n')
    let eventType = 'message'
    let data = ''

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        data = line.slice(6)
      }
    }

    if (data) {
      events.push({ event: eventType, data })
    }
  }

  return { events, remaining }
}

// ── Store ────────────────────────────────────────────────────────────────────

const restored = loadFromStorage()

export const useChatStore = create<ChatState>((set, get) => ({
  isOpen: false,
  messages: restored.messages,
  inputValue: '',
  isLoading: false,
  streamingMessageId: null,
  currentSessionId: restored.sessionId,

  toggleOpen: () => set((s) => ({ isOpen: !s.isOpen })),
  setOpen: (open) => set({ isOpen: open }),
  setInputValue: (value) => set({ inputValue: value }),

  addMessage: (message) => {
    const id = generateId()
    const fullMessage: ChatMessage = {
      ...message,
      id,
      timestamp: new Date().toISOString(),
    }
    set((s) => {
      const newMessages = [...s.messages, fullMessage]
      // Persist on add (but not during streaming — too frequent)
      if (!message.isStreaming) {
        saveToStorage({ messages: newMessages, currentSessionId: s.currentSessionId })
      }
      return { messages: newMessages }
    })
    return id
  },

  updateMessage: (id, updates) =>
    set((s) => {
      const newMessages = s.messages.map((m) =>
        m.id === id ? { ...m, ...updates } : m
      )
      // Persist when streaming ends
      if (updates.isStreaming === false) {
        saveToStorage({ messages: newMessages, currentSessionId: s.currentSessionId })
      }
      return { messages: newMessages }
    }),

  appendToMessage: (id, token) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, content: m.content + token } : m
      ),
    })),

  markCommandsExecuted: (id) =>
    set((s) => {
      const newMessages = s.messages.map((m) =>
        m.id === id ? { ...m, commands_executed: true } : m
      )
      saveToStorage({ messages: newMessages, currentSessionId: s.currentSessionId })
      return { messages: newMessages }
    }),

  setLoading: (loading) => set({ isLoading: loading }),
  setStreamingMessageId: (id) => set({ streamingMessageId: id }),

  clearMessages: () => {
    set({ messages: [WELCOME_MESSAGE], currentSessionId: null })
    saveToStorage({ messages: [WELCOME_MESSAGE], currentSessionId: null })
  },

  newSession: () => {
    set({ messages: [WELCOME_MESSAGE], currentSessionId: null })
    saveToStorage({ messages: [WELCOME_MESSAGE], currentSessionId: null })
  },

  sendMessage: async (content: string) => {
    const state = get()
    if (!content.trim() || state.isLoading) return

    // Capture current UI state BEFORE any navigation occurs
    const uiStatePayload = captureUIStateFromWindow()
    const currentUIState = mapUIStateForBackend(uiStatePayload)

    // Add user message
    state.addMessage({ role: 'user', content: content.trim() })
    set({ inputValue: '', isLoading: true })

    // Create assistant placeholder for streaming
    const assistantId = state.addMessage({
      role: 'assistant',
      content: '',
      isStreaming: true,
    })
    set({ streamingMessageId: assistantId })

    try {
      const headers = getAuthHeaders()
      const sessionId = get().currentSessionId

      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          content: content.trim(),
          session_id: sessionId || undefined,
          current_ui_state: currentUIState,
        }),
      })

      if (!response.ok) {
        // Fall back to sync API endpoint
        await fallbackSyncSend(get, assistantId, content.trim(), currentUIState)
        return
      }

      // Capture session_id from response header
      const newSessionId = response.headers.get('X-Session-Id')
      if (newSessionId) {
        set({ currentSessionId: newSessionId })
      }

      const reader = response.body?.getReader()
      if (!reader) {
        await fallbackSyncSend(get, assistantId, content.trim(), currentUIState)
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse complete SSE events from buffer
        const { events, remaining } = parseSSEBuffer(buffer)
        buffer = remaining

        for (const evt of events) {
          try {
            const data = JSON.parse(evt.data)

            switch (evt.event) {
              case 'token':
                if (data.token) {
                  get().appendToMessage(assistantId, data.token)
                }
                break

              case 'ui_command':
                if (data.commands && Array.isArray(data.commands)) {
                  const mapped = mapBackendCommands(data.commands)
                  get().updateMessage(assistantId, { ui_commands: mapped })
                }
                break

              case 'headcount_preview':
                // Headcount entity extraction preview — store in message metadata
                // The preview data is embedded in the streaming message for display
                break

              case 'done':
                // Final event — set full content and commands
                if (data.content) {
                  const finalCommands = data.ui_commands
                    ? mapBackendCommands(data.ui_commands)
                    : undefined
                  get().updateMessage(assistantId, {
                    content: data.content,
                    ui_commands: finalCommands && finalCommands.length > 0 ? finalCommands : undefined,
                    isStreaming: false,
                  })
                }
                break

              default:
                // Legacy format: single data line with token/content
                if (data.token) {
                  get().appendToMessage(assistantId, data.token)
                } else if (data.content) {
                  get().appendToMessage(assistantId, data.content)
                }
                if (data.ui_commands) {
                  get().updateMessage(assistantId, {
                    ui_commands: mapBackendCommands(data.ui_commands),
                  })
                }
                break
            }
          } catch {
            // Non-JSON or partial, skip
          }
        }
      }
    } catch {
      // API not available — use local command resolution fallback
      const fallback = generateLocalResponse(content.trim())
      await streamLocalResponse(get, assistantId, fallback.content, fallback.ui_commands)
    } finally {
      get().updateMessage(assistantId, { isStreaming: false })
      set({ isLoading: false, streamingMessageId: null })
      // Final persist
      const s = get()
      saveToStorage({ messages: s.messages, currentSessionId: s.currentSessionId })
    }
  },
}))

// ── Fallback: sync API send ─────────────────────────────────────────────────

async function fallbackSyncSend(
  get: () => ChatState,
  messageId: string,
  userMessage: string,
  currentUIState?: BackendUIStateContext
) {
  try {
    const headers = getAuthHeaders()
    const sessionId = get().currentSessionId

    const resp = await fetch('/api/chat/messages', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        content: userMessage,
        session_id: sessionId || undefined,
        current_ui_state: currentUIState || undefined,
      }),
    })

    if (!resp.ok) {
      // Fall back to fully local response
      const fallback = generateLocalResponse(userMessage)
      await streamLocalResponse(get, messageId, fallback.content, fallback.ui_commands)
      return
    }

    const data = await resp.json()

    // Update session ID
    if (data.session_id) {
      useChatStore.setState({ currentSessionId: String(data.session_id) })
    }

    // Get assistant message content
    const assistantContent = data.assistant_message?.content || ''
    const backendCommands = data.assistant_message?.ui_commands

    // Stream word-by-word for visual effect
    const words = assistantContent.split(' ')
    for (let i = 0; i < words.length; i++) {
      const token = i === 0 ? words[i] : ' ' + words[i]
      get().appendToMessage(messageId, token)
      await new Promise((r) => setTimeout(r, 15))
    }

    // Set UI commands
    if (backendCommands && Array.isArray(backendCommands) && backendCommands.length > 0) {
      get().updateMessage(messageId, {
        ui_commands: mapBackendCommands(backendCommands),
      })
    }
  } catch {
    // Fully local fallback
    const fallback = generateLocalResponse(userMessage)
    await streamLocalResponse(get, messageId, fallback.content, fallback.ui_commands)
  }
}

// ── Local Response Generator with Command Manifest ──────────────────────────

interface LocalResponse {
  content: string
  ui_commands?: UICommand[]
}

function generateLocalResponse(query: string): LocalResponse {
  const q = query.toLowerCase().trim()

  // Navigation intents
  if (q.match(/\b(dashboard|home|overview)\b/) && q.match(/\b(show|open|go|navigate|view)\b/)) {
    return {
      content: 'Navigating to the **Dashboard** \u2014 your operations overview with KPIs, suggested actions, and recent activity.',
      ui_commands: [{ type: 'navigate', target: '/ops/dashboard' }],
    }
  }

  if (q.match(/\btraining\b/) && q.match(/\b(show|open|go|navigate|view|pipeline)\b/) && !q.match(/\bin\s*training\b/)) {
    return {
      content: 'Opening the **Training Pipeline** \u2014 track technician progress through career stages.',
      ui_commands: [{ type: 'navigate', target: '/ops/training' }],
    }
  }

  if (q.match(/\b(inbox|recommendation)/) && q.match(/\b(show|open|go|navigate|view)\b/)) {
    return {
      content: 'Opening the **Agent Inbox** \u2014 review AI-generated recommendations and take action.',
      ui_commands: [
        { type: 'navigate', target: '/ops/inbox' },
        { type: 'set_tab', target: '/ops/inbox', params: { tab: 'recommendations' } },
      ],
    }
  }

  if (q.match(/\b(preference\s*rule|rules\s*tab|my\s*rules)\b/)) {
    return {
      content: 'Showing your **Preference Rules** \u2014 these modify how the staffing agent scores candidates.',
      ui_commands: [
        { type: 'navigate', target: '/ops/inbox' },
        { type: 'set_tab', target: '/ops/inbox', params: { tab: 'rules' } },
      ],
    }
  }

  if (q.match(/\b(activity|log|history)\b/) && !q.match(/\bproject/)) {
    return {
      content: 'Showing the **Activity Log** \u2014 recent actions, approvals, and system events.',
      ui_commands: [
        { type: 'navigate', target: '/ops/inbox' },
        { type: 'set_tab', target: '/ops/inbox', params: { tab: 'activity' } },
      ],
    }
  }

  if (q.match(/\bportal\b/)) {
    return {
      content: 'Opening the **Technician Portal**.',
      ui_commands: [{ type: 'navigate', target: '/tech/portal' }],
    }
  }

  // Technician filter intents
  const isTechQuery = q.match(/\b(technician|tech|who|find|show|list|search)\b/)

  if (isTechQuery || q.match(/\bready\s*(now)?\b/) || q.match(/\bmissing/) || q.match(/\brolling\s*off/)) {
    const commands: UICommand[] = []
    const filterParams: Record<string, string> = {}
    const descriptions: string[] = []

    // Status filters
    if (q.match(/\bready\s*(now)?\b/)) {
      filterParams.deployability_status = 'Ready Now'
      descriptions.push('**Ready Now** status')
    } else if (q.match(/\bmissing\s*cert/)) {
      filterParams.deployability_status = 'Missing Cert'
      descriptions.push('**Missing Cert** status')
    } else if (q.match(/\bmissing\s*doc/)) {
      filterParams.deployability_status = 'Missing Docs'
      descriptions.push('**Missing Docs** status')
    } else if (q.match(/\brolling\s*off/)) {
      filterParams.deployability_status = 'Rolling Off Soon'
      descriptions.push('**Rolling Off Soon** status')
    } else if (q.match(/\bin\s*training\b/)) {
      filterParams.deployability_status = 'In Training'
      descriptions.push('**In Training** status')
    } else if (q.match(/\binactive\b/)) {
      filterParams.deployability_status = 'Inactive'
      descriptions.push('**Inactive** status')
    } else if (q.match(/\bcurrently\s*assigned\b/)) {
      filterParams.deployability_status = 'Currently Assigned'
      descriptions.push('**Currently Assigned** status')
    }

    // Career stage
    if (q.match(/\bdeployed\b/) && !filterParams.deployability_status) {
      filterParams.career_stage = 'Deployed'
      descriptions.push('**Deployed** career stage')
    } else if (q.match(/\bsourced\b/)) {
      filterParams.career_stage = 'Sourced'
      descriptions.push('**Sourced** career stage')
    } else if (q.match(/\bscreened\b/)) {
      filterParams.career_stage = 'Screened'
      descriptions.push('**Screened** career stage')
    } else if (q.match(/\bawaiting\s*assignment\b/)) {
      filterParams.career_stage = 'Awaiting Assignment'
      descriptions.push('**Awaiting Assignment** career stage')
    }

    // Skill filters
    const skillMatch = q.match(/\b(fiber\s*splic|otdr|cable\s*pull|structured\s*cabling|aerial|underground|testing|termination)/i)
    if (skillMatch) {
      filterParams.skill = skillMatch[1]
      descriptions.push(`**${skillMatch[1]}** skill`)
    }

    // Region filters
    const regionMatch = q.match(/\bin\s+(texas|california|florida|new york|southeast|northeast|midwest|southwest|northwest|[\w\s]+(?:region|state))/i)
    if (regionMatch) {
      filterParams.region = regionMatch[1]
      descriptions.push(`**${regionMatch[1]}** region`)
    }

    // Name search
    const nameMatch = q.match(/(?:find|search|look\s*up|named?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/i)
    if (nameMatch && !nameMatch[1].match(/^(fiber|otdr|cable|tech|ready|deployed|active)/i)) {
      filterParams.search = nameMatch[1]
      descriptions.push(`name matching **"${nameMatch[1]}"**`)
    }

    if (Object.keys(filterParams).length > 0) {
      commands.push(
        { type: 'navigate', target: '/ops/technicians' },
        { type: 'filter', target: '/ops/technicians', params: filterParams }
      )

      const desc = descriptions.length > 0
        ? `Filtering technicians by ${descriptions.join(', ')}.`
        : 'Opening the technician directory.'

      return { content: desc, ui_commands: commands }
    }

    // Plain technician navigation
    if (isTechQuery) {
      return {
        content: 'Opening the **Technician Directory** \u2014 browse, search, and filter all technicians.',
        ui_commands: [{ type: 'navigate', target: '/ops/technicians' }],
      }
    }
  }

  // Project filters
  if (q.match(/\bproject/) && q.match(/\b(show|open|go|navigate|view|active|staffing|draft|closed)\b/)) {
    const commands: UICommand[] = [{ type: 'navigate', target: '/ops/projects' }]

    if (q.match(/\bactive\b/)) {
      commands.push({ type: 'filter', target: '/ops/projects', params: { status: 'Active' } })
      return { content: 'Showing **Active** projects.', ui_commands: commands }
    }
    if (q.match(/\bstaffing\b/)) {
      commands.push({ type: 'filter', target: '/ops/projects', params: { status: 'Staffing' } })
      return { content: 'Showing projects in **Staffing** phase.', ui_commands: commands }
    }
    if (q.match(/\bdraft\b/)) {
      commands.push({ type: 'filter', target: '/ops/projects', params: { status: 'Draft' } })
      return { content: 'Showing **Draft** projects.', ui_commands: commands }
    }
    if (q.match(/\bclosed\b/)) {
      commands.push({ type: 'filter', target: '/ops/projects', params: { status: 'Closed' } })
      return { content: 'Showing **Closed** projects.', ui_commands: commands }
    }

    return {
      content: 'Opening **Project Staffing** \u2014 view all projects, roles, and assignments.',
      ui_commands: commands,
    }
  }

  // Incremental filter: add (must be before clear_filters check)
  const alsoMatch = q.match(/\b(?:also|additionally|and also|narrow|add)\b.*\b(?:filter|show|include|by)\b/)
  if (alsoMatch) {
    const addParams: Record<string, string> = {}
    const addDescriptions: string[] = []

    if (q.match(/\bready\s*(now)?\b/)) {
      addParams.deployability_status = 'Ready Now'
      addDescriptions.push('**Ready Now** status')
    } else if (q.match(/\bin\s*training\b/)) {
      addParams.deployability_status = 'In Training'
      addDescriptions.push('**In Training** status')
    } else if (q.match(/\bmissing\s*cert/)) {
      addParams.deployability_status = 'Missing Cert'
      addDescriptions.push('**Missing Cert** status')
    } else if (q.match(/\brolling\s*off/)) {
      addParams.deployability_status = 'Rolling Off Soon'
      addDescriptions.push('**Rolling Off Soon** status')
    }

    const addSkillMatch = q.match(/\b(fiber\s*splic|otdr|cable\s*pull|structured\s*cabling|aerial|underground|testing|termination)/i)
    if (addSkillMatch) {
      addParams.skill = addSkillMatch[1]
      addDescriptions.push(`**${addSkillMatch[1]}** skill`)
    }

    const addRegionMatch = q.match(/\bin\s+(texas|california|florida|new york|southeast|northeast|midwest|southwest|northwest)/i)
    if (addRegionMatch) {
      addParams.region = addRegionMatch[1]
      addDescriptions.push(`**${addRegionMatch[1]}** region`)
    }

    if (Object.keys(addParams).length > 0) {
      return {
        content: `Adding filter: ${addDescriptions.join(', ')}. Existing filters are preserved.`,
        ui_commands: [
          { type: 'add_filter' as UICommand['type'], target: '/ops/technicians', params: addParams },
        ],
      }
    }
  }

  // Incremental filter: remove specific filter
  const removeMatch = q.match(/\b(?:remove|drop|delete|stop\s*filtering)\b.*\b(?:filter|the|by)\b/)
  if (removeMatch) {
    const removeKeys: Record<string, string> = {}
    const removeDescriptions: string[] = []

    if (q.match(/\b(?:region)\b/)) {
      removeKeys.region = ''
      removeDescriptions.push('**region**')
    }
    if (q.match(/\b(?:skill)\b/)) {
      removeKeys.skill = ''
      removeDescriptions.push('**skill**')
    }
    if (q.match(/\b(?:status|deployability)\b/)) {
      removeKeys.deployability_status = ''
      removeDescriptions.push('**status**')
    }
    if (q.match(/\b(?:career|stage)\b/)) {
      removeKeys.career_stage = ''
      removeDescriptions.push('**career stage**')
    }
    if (q.match(/\b(?:search|name)\b/)) {
      removeKeys.search = ''
      removeDescriptions.push('**search**')
    }

    if (Object.keys(removeKeys).length > 0) {
      return {
        content: `Removed ${removeDescriptions.join(', ')} filter${removeDescriptions.length > 1 ? 's' : ''}. Other filters remain active.`,
        ui_commands: [
          { type: 'remove_filter' as UICommand['type'], target: '/ops/technicians', params: removeKeys },
        ],
      }
    }
  }

  // Clear filters
  if (q.match(/\b(clear|reset)\b.*\bfilter/)) {
    return {
      content: 'Filters cleared. Showing all results.',
      ui_commands: [{ type: 'clear_filters', target: 'current' }],
    }
  }

  // Help
  if (q.match(/\b(help|what can|commands|how\s+do|what\s+do)\b/)) {
    return {
      content: `I can help you navigate and filter Deployable. Here's what I can do:

**Navigation**
- "Go to dashboard" \u2014 Operations overview
- "Show technicians" \u2014 Technician directory
- "Open training" \u2014 Training pipeline
- "Show projects" \u2014 Project staffing
- "Open inbox" \u2014 Agent recommendations

**Filtering**
- "Show ready now technicians" \u2014 Filter by deployability status
- "Find deployed techs" \u2014 Filter by career stage
- "Show active projects" \u2014 Filter projects
- "Find fiber splicers in Texas" \u2014 Compound filter

**Headcount Requests**
- "I need 3 fiber splicers in Austin" \u2014 Create headcount request
- "Request 5 technicians for Dallas" \u2014 Staffing request

**Incremental Filtering**
- "Also filter by Ready Now" \u2014 Add filter without resetting others
- "Also show fiber splicers" \u2014 Add skill filter to existing view
- "Remove the region filter" \u2014 Remove just one filter
- "Drop the skill filter" \u2014 Remove a specific filter

**Tabs & Details**
- "Show preference rules" \u2014 Inbox rules tab
- "Show activity log" \u2014 Recent activity

**Reset**
- "Clear filters" \u2014 Remove all active filters

Just ask naturally \u2014 I'll navigate and filter for you!`,
    }
  }

  // Headcount requests — detect NL headcount intent
  const headcountMatch = q.match(
    /(?:(?:i\s+)?need|request|add|hire|staff|get\s+me|bring\s+on)\s+(\d+|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:more\s+)?)?(.+?)(?:\s+(?:in|for|at|near)\s+(.+?))?$/
  )
  if (
    headcountMatch &&
    q.match(/(?:tech|splicer|puller|lead|installer|tester|supervisor|engineer|inspector|cable|fiber)/)
  ) {
    const countStr = headcountMatch[1]?.trim()
    const roleStr = headcountMatch[2]?.trim()
    const locationStr = headcountMatch[3]?.trim()

    const numberWords: Record<string, number> = {
      a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5,
      six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
    }
    let count = 1
    if (countStr) {
      const digit = parseInt(countStr)
      if (!isNaN(digit)) count = digit
      else {
        const word = countStr.replace(/\s+more\s*$/, '').trim()
        count = numberWords[word] || 1
      }
    }

    const role = roleStr ? roleStr.replace(/s$/, '').replace(/^\s+|\s+$/g, '') : null
    const location = locationStr || null

    const roleCap = role ? role.charAt(0).toUpperCase() + role.slice(1) : 'Technician'
    const locStr = location ? ` in **${location.charAt(0).toUpperCase() + location.slice(1)}**` : ''

    return {
      content:
        `I'll create a headcount request with these details:\n\n` +
        `- **Role:** ${roleCap}\n` +
        `- **Quantity:** ${count}\n` +
        `- **Location:** ${location ? location.charAt(0).toUpperCase() + location.slice(1) : 'Not specified'}\n` +
        `- **Start date:** ~2 weeks from now\n\n` +
        `**Would you like to confirm this request?** ` +
        `Reply "yes" or "confirm" to submit, or "edit" to modify the details.`,
      ui_commands: [
        {
          type: 'toast' as UICommand['type'],
          target: 'info',
          params: { message: `Headcount request: ${count} ${roleCap}${locStr}` },
        },
      ],
    }
  }

  // Greeting
  if (q.match(/^(hi|hello|hey|good\s*(morning|afternoon|evening))/)) {
    return {
      content: "Hello! I'm the Deployable assistant. I can help you navigate the system, " +
        'filter technicians, check project status, and more. What would you like to do?',
    }
  }

  // Default
  return {
    content: `I can help you navigate Deployable. Try asking me to:
- **Show technicians** with specific filters
- **Open a project** or filter by status
- **Go to the dashboard** or training pipeline
- **Request headcount** — e.g., "I need 3 fiber splicers in Austin"
- **Clear filters** to reset the view

Type **"help"** to see all available commands.`,
  }
}

/**
 * Stream a local response token-by-token for a realistic feel
 */
async function streamLocalResponse(
  get: () => ChatState,
  messageId: string,
  content: string,
  ui_commands?: UICommand[]
): Promise<void> {
  const tokens = content.split(' ')
  for (let i = 0; i < tokens.length; i++) {
    await new Promise((r) => setTimeout(r, 15 + Math.random() * 25))
    const prefix = i === 0 ? '' : ' '
    get().appendToMessage(messageId, prefix + tokens[i])
  }

  // Attach UI commands after content is done streaming
  if (ui_commands && ui_commands.length > 0) {
    get().updateMessage(messageId, { ui_commands })
  }
}
