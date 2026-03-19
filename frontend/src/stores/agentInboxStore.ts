import { create } from 'zustand'
import api from '@/lib/api'
import type { Recommendation, PreferenceRule, ActivityLogEntry } from '@/types'

// ============================================================
// Agent Inbox Store — manages Pending, Active Rules, Activity Log
// ============================================================

interface AgentInboxFilters {
  search: string
  typeFilter: string
  agentFilter: string
  dateRange: 'all' | '24h' | '7d' | '30d'
}

interface AgentInboxStats {
  totalPending: number
  byType: Record<string, number>
  avgScore: number
  approvedToday: number
  rejectedToday: number
}

interface AgentInboxStore {
  // Pending recommendations tab
  pendingRecommendations: Recommendation[]
  selectedRecommendation: Recommendation | null
  selectedIds: Set<string>

  // Active rules tab
  activeRules: PreferenceRule[]

  // Proposed rules (agent-suggested, awaiting approval)
  proposedRules: PreferenceRule[]

  // Activity log tab
  activityLog: ActivityLogEntry[]

  // Filters
  filters: AgentInboxFilters
  activeTab: string

  // Loading states
  isLoadingRecommendations: boolean
  isLoadingRules: boolean
  isProcessing: boolean
  lastFetchedAt: number | null

  // API fetch actions — pull latest from backend
  fetchRecommendations: () => Promise<void>
  fetchRules: () => Promise<void>

  // Pending actions
  setPendingRecommendations: (recs: Recommendation[]) => void
  selectRecommendation: (rec: Recommendation | null) => void
  approveRecommendation: (id: string) => void
  rejectRecommendation: (id: string, reason?: string) => void
  dismissRecommendation: (id: string) => void

  // Batch actions
  toggleSelected: (id: string) => void
  selectAll: () => void
  clearSelection: () => void
  batchApprove: () => void
  batchDismiss: () => void

  // Rules actions
  setActiveRules: (rules: PreferenceRule[]) => void
  setProposedRules: (rules: PreferenceRule[]) => void
  toggleRule: (id: string) => void
  deleteRule: (id: string) => void
  updateRule: (id: string, updates: Partial<PreferenceRule>) => void
  approveProposedRule: (id: string) => void
  rejectProposedRule: (id: string) => void

  // Activity log actions
  setActivityLog: (entries: ActivityLogEntry[]) => void
  addActivityEntry: (entry: ActivityLogEntry) => void

  // WebSocket handlers for real-time updates
  handleRecommendationCreated: (rec: Recommendation) => void
  handleRecommendationUpdated: (rec: Recommendation) => void

  // Filter actions
  setFilters: (filters: Partial<AgentInboxFilters>) => void
  setActiveTab: (tab: string) => void

  // Computed
  getPendingCount: () => number
  getStats: () => AgentInboxStats
  getFilteredRecommendations: () => Recommendation[]
  getFilteredActivityLog: () => ActivityLogEntry[]
}

export const useAgentInboxStore = create<AgentInboxStore>((set, get) => ({
  pendingRecommendations: [],
  selectedRecommendation: null,
  selectedIds: new Set(),
  activeRules: [],
  proposedRules: [],
  activityLog: [],
  filters: {
    search: '',
    typeFilter: '',
    agentFilter: '',
    dateRange: 'all',
  },
  activeTab: 'pending',
  isLoadingRecommendations: false,
  isLoadingRules: false,
  isProcessing: false,
  lastFetchedAt: null,

  fetchRecommendations: async () => {
    if (get().isLoadingRecommendations) return
    set({ isLoadingRecommendations: true })
    try {
      const res = await api.get('/recommendations/', { params: { status: 'Pending' } })
      const recs: Recommendation[] = (res.data || []).map((r: any) => ({
        id: r.id,
        type: r.recommendation_type || r.type || 'staffing',
        status: r.status || 'Pending',
        target_id: r.target_entity_id || r.technician_id || '',
        target_name: r.metadata_?.project_name
          ? `${r.metadata_.role_name || 'Role'} — ${r.metadata_.project_name}`
          : r.target_entity_id || '',
        agent: r.agent_name || 'staffing_agent',
        score: r.overall_score ?? 0,
        explanation: r.explanation || '',
        scorecard: r.scorecard || {},
        created_at: r.created_at || new Date().toISOString(),
        rank: r.rank,
        technician_id: r.technician_id,
        role_id: r.role_id,
        project_id: r.project_id,
      }))
      set({ pendingRecommendations: recs, lastFetchedAt: Date.now() })
    } catch {
      // Silently fail — frontend seed data remains as fallback
    } finally {
      set({ isLoadingRecommendations: false })
    }
  },

  fetchRules: async () => {
    if (get().isLoadingRules) return
    set({ isLoadingRules: true })
    try {
      const res = await api.get('/recommendations/preference-rules', { params: { active_only: false } })
      const all: PreferenceRule[] = (res.data || []).map((r: any) => ({
        id: r.id,
        rule_type: r.rule_type,
        template_type: r.template_type || 'custom',
        threshold: r.threshold,
        scope: r.scope || 'global',
        scope_target_id: r.scope_target_id || null,
        effect: r.effect || 'demote',
        score_modifier: r.score_modifier || 0,
        priority: r.priority || 0,
        parameters: r.parameters || {},
        description: r.description || null,
        status: r.status || 'active',
        active: r.active ?? true,
        rejection_id: r.rejection_id || null,
        source_recommendation_id: r.source_recommendation_id || null,
        proposed_reason: r.proposed_reason || null,
        created_by_type: r.created_by_type || 'ops',
        created_by_id: r.created_by_id || null,
        approved_by_id: r.approved_by_id || null,
        approved_at: r.approved_at || null,
        created_at: r.created_at || new Date().toISOString(),
        updated_at: r.updated_at || null,
        type: r.rule_type,
      }))
      const activeRules = all.filter((r) => r.status !== 'proposed')
      const proposedRules = all.filter((r) => r.status === 'proposed')
      set({ activeRules, proposedRules })
    } catch {
      // Silently fail — frontend seed data remains as fallback
    } finally {
      set({ isLoadingRules: false })
    }
  },

  setPendingRecommendations: (recs) => set({ pendingRecommendations: recs }),
  selectRecommendation: (rec) => set({ selectedRecommendation: rec }),

  approveRecommendation: (id) => {
    set({ isProcessing: true })
    // Try API call first
    api.post(`/recommendations/${id}/approve`).catch(() => {})
    set((state) => ({
      isProcessing: false,
      pendingRecommendations: state.pendingRecommendations.map((r) =>
        r.id === id ? { ...r, status: 'Approved' as const } : r
      ),
      selectedIds: (() => {
        const next = new Set(state.selectedIds)
        next.delete(id)
        return next
      })(),
      activityLog: [
        {
          id: `log-${Date.now()}`,
          action: 'approved' as const,
          agent: 'ops_user',
          description: `Recommendation ${id} approved`,
          recommendation_id: id,
          user_name: 'Ops User',
          created_at: new Date().toISOString(),
        },
        ...state.activityLog,
      ],
    }))
  },

  rejectRecommendation: (id, _reason) => {
    set({ isProcessing: true })
    api.post(`/recommendations/${id}/reject`, { reason: _reason }).catch(() => {})
    set((state) => ({
      isProcessing: false,
      pendingRecommendations: state.pendingRecommendations.map((r) =>
        r.id === id ? { ...r, status: 'Rejected' as const } : r
      ),
      selectedIds: (() => {
        const next = new Set(state.selectedIds)
        next.delete(id)
        return next
      })(),
      activityLog: [
        {
          id: `log-${Date.now()}`,
          action: 'rejected' as const,
          agent: 'ops_user',
          description: `Recommendation ${id} rejected${_reason ? `: ${_reason}` : ''}`,
          recommendation_id: id,
          user_name: 'Ops User',
          created_at: new Date().toISOString(),
        },
        ...state.activityLog,
      ],
    }))
  },

  dismissRecommendation: (id) => {
    api.post(`/recommendations/${id}/dismiss`).catch(() => {})
    set((state) => ({
      pendingRecommendations: state.pendingRecommendations.map((r) =>
        r.id === id ? { ...r, status: 'Dismissed' as const } : r
      ),
      selectedIds: (() => {
        const next = new Set(state.selectedIds)
        next.delete(id)
        return next
      })(),
      activityLog: [
        {
          id: `log-${Date.now()}`,
          action: 'dismissed' as const,
          agent: 'ops_user',
          description: `Recommendation ${id} dismissed`,
          recommendation_id: id,
          user_name: 'Ops User',
          created_at: new Date().toISOString(),
        },
        ...state.activityLog,
      ],
    }))
  },

  // Batch actions
  toggleSelected: (id) =>
    set((state) => {
      const next = new Set(state.selectedIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selectedIds: next }
    }),

  selectAll: () =>
    set((state) => {
      const pending = state.pendingRecommendations.filter((r) => r.status === 'Pending')
      return { selectedIds: new Set(pending.map((r) => r.id)) }
    }),

  clearSelection: () => set({ selectedIds: new Set() }),

  batchApprove: () => {
    const { selectedIds } = get()
    selectedIds.forEach((id) => get().approveRecommendation(id))
    set({ selectedIds: new Set() })
  },

  batchDismiss: () => {
    const { selectedIds } = get()
    selectedIds.forEach((id) => get().dismissRecommendation(id))
    set({ selectedIds: new Set() })
  },

  setActiveRules: (rules) => set({ activeRules: rules }),
  setProposedRules: (rules) => set({ proposedRules: rules }),

  toggleRule: (id) =>
    set((state) => ({
      activeRules: state.activeRules.map((r) =>
        r.id === id ? { ...r, active: !r.active, status: (!r.active ? 'active' : 'disabled') as any } : r
      ),
    })),

  deleteRule: (id) =>
    set((state) => ({
      activeRules: state.activeRules.filter((r) => r.id !== id),
    })),

  updateRule: (id, updates) =>
    set((state) => ({
      activeRules: state.activeRules.map((r) =>
        r.id === id ? { ...r, ...updates } : r
      ),
    })),

  approveProposedRule: (id) =>
    set((state) => {
      const rule = state.proposedRules.find((r) => r.id === id)
      if (!rule) return state
      const activated = { ...rule, status: 'active' as const, active: true, approved_at: new Date().toISOString() }
      return {
        proposedRules: state.proposedRules.filter((r) => r.id !== id),
        activeRules: [...state.activeRules, activated],
        activityLog: [
          {
            id: `log-${Date.now()}`,
            action: 'approved' as const,
            agent: 'ops_user',
            description: `Approved proposed rule: ${rule.description}`,
            user_name: 'Ops User',
            created_at: new Date().toISOString(),
          },
          ...state.activityLog,
        ],
      }
    }),

  rejectProposedRule: (id) =>
    set((state) => ({
      proposedRules: state.proposedRules.filter((r) => r.id !== id),
      activityLog: [
        {
          id: `log-${Date.now()}`,
          action: 'rejected' as const,
          agent: 'ops_user',
          description: `Rejected proposed rule ${id}`,
          user_name: 'Ops User',
          created_at: new Date().toISOString(),
        },
        ...state.activityLog,
      ],
    })),

  setActivityLog: (entries) => set({ activityLog: entries }),

  addActivityEntry: (entry) =>
    set((state) => ({
      activityLog: [entry, ...state.activityLog],
    })),

  // WebSocket handlers
  handleRecommendationCreated: (rec) =>
    set((state) => ({
      pendingRecommendations: [rec, ...state.pendingRecommendations],
      activityLog: [
        {
          id: `log-ws-${Date.now()}`,
          action: 'created' as const,
          agent: rec.agent || 'staffing_agent',
          description: `New recommendation: ${rec.target_name || rec.type}`,
          recommendation_id: rec.id,
          created_at: new Date().toISOString(),
        },
        ...state.activityLog,
      ],
    })),

  handleRecommendationUpdated: (rec) =>
    set((state) => ({
      pendingRecommendations: state.pendingRecommendations.map((r) =>
        r.id === rec.id ? { ...r, ...rec } : r
      ),
    })),

  setFilters: (newFilters) =>
    set((state) => ({
      filters: { ...state.filters, ...newFilters },
    })),

  setActiveTab: (tab) => set({ activeTab: tab }),

  getPendingCount: () => {
    return get().pendingRecommendations.filter((r) => r.status === 'Pending').length
  },

  getStats: () => {
    const { pendingRecommendations, activityLog } = get()
    const pending = pendingRecommendations.filter((r) => r.status === 'Pending')
    const today = new Date().toISOString().split('T')[0]
    const todayLogs = activityLog.filter((l) => l.created_at.startsWith(today))

    const byType: Record<string, number> = {}
    let totalScore = 0
    let scoreCount = 0
    pending.forEach((r) => {
      byType[r.type] = (byType[r.type] || 0) + 1
      if (r.scorecard?.overall_score) {
        totalScore += r.scorecard.overall_score
        scoreCount++
      }
    })

    return {
      totalPending: pending.length,
      byType,
      avgScore: scoreCount > 0 ? Math.round(totalScore / scoreCount) : 0,
      approvedToday: todayLogs.filter((l) => l.action === 'approved').length,
      rejectedToday: todayLogs.filter((l) => l.action === 'rejected').length,
    }
  },

  getFilteredRecommendations: () => {
    const { pendingRecommendations, filters } = get()
    return pendingRecommendations.filter((r) => {
      if (r.status !== 'Pending') return false
      if (filters.search) {
        const q = filters.search.toLowerCase()
        if (
          !r.explanation.toLowerCase().includes(q) &&
          !(r.target_name || '').toLowerCase().includes(q)
        ) return false
      }
      if (filters.typeFilter && r.type !== filters.typeFilter) return false
      if (filters.agentFilter && r.agent !== filters.agentFilter) return false
      if (filters.dateRange !== 'all') {
        const now = Date.now()
        const entryTime = new Date(r.created_at).getTime()
        const ranges: Record<string, number> = {
          '24h': 86400000,
          '7d': 604800000,
          '30d': 2592000000,
        }
        if (now - entryTime > ranges[filters.dateRange]) return false
      }
      return true
    })
  },

  getFilteredActivityLog: () => {
    const { activityLog, filters } = get()
    return activityLog.filter((entry) => {
      if (filters.search) {
        const q = filters.search.toLowerCase()
        if (!entry.description.toLowerCase().includes(q)) return false
      }
      if (filters.dateRange !== 'all') {
        const now = Date.now()
        const entryTime = new Date(entry.created_at).getTime()
        const ranges: Record<string, number> = {
          '24h': 86400000,
          '7d': 604800000,
          '30d': 2592000000,
        }
        if (now - entryTime > ranges[filters.dateRange]) return false
      }
      return true
    })
  },
}))
