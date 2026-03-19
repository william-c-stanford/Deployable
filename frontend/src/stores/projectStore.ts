import { create } from 'zustand'
import type { Project, Assignment, Timesheet, Escalation, Recommendation, ReassignmentCandidate, EscalationResolution, TimesheetDispute } from '@/types'

interface ProjectFilters {
  search: string
  status: string
  region: string
  partner: string
}

interface ProjectStore {
  projects: Project[]
  selectedProjectId: string | null
  filters: ProjectFilters
  loading: boolean
  error: string | null
  disputes: TimesheetDispute[]

  // Actions
  setProjects: (projects: Project[]) => void
  selectProject: (id: string | null) => void
  setFilters: (filters: Partial<ProjectFilters>) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  getFilteredProjects: () => Project[]
  getSelectedProject: () => Project | undefined
  updateAssignment: (projectId: string, assignmentId: string, updates: Partial<Assignment>) => void
  updateTimesheet: (projectId: string, timesheetId: string, updates: Partial<Timesheet>) => void
  resolveEscalation: (projectId: string, escalationId: string, resolution: EscalationResolution, newTechName?: string) => void
  acknowledgeEscalation: (projectId: string, escalationId: string) => void
  getProjectEscalationCounts: () => { projectId: string; open: number; total: number }[]
  getTotalOpenEscalations: () => number
  getProjectDisputes: (projectId: string) => TimesheetDispute[]
  resolveDispute: (disputeId: string, resolution: 'resolved_approved' | 'resolved_adjusted' | 'resolved_rejected', opsNote: string, adjustedHours?: number) => void
  startInvestigation: (disputeId: string) => void
  closeProject: (projectId: string) => boolean
  updateProjectStatus: (projectId: string, status: Project['status']) => void
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  projects: [],
  selectedProjectId: null,
  filters: {
    search: '',
    status: '',
    region: '',
    partner: '',
  },
  loading: false,
  error: null,
  disputes: generateMockDisputes(),

  setProjects: (projects) => set({ projects }),
  selectProject: (id) => set({ selectedProjectId: id }),
  setFilters: (filters) => set((state) => ({ filters: { ...state.filters, ...filters } })),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),

  getFilteredProjects: () => {
    const { projects, filters } = get()
    return projects.filter((p) => {
      if (filters.search && !p.name.toLowerCase().includes(filters.search.toLowerCase()) &&
          !p.partner_name.toLowerCase().includes(filters.search.toLowerCase()) &&
          !p.location_region.toLowerCase().includes(filters.search.toLowerCase())) {
        return false
      }
      if (filters.status && p.status !== filters.status) return false
      if (filters.region && p.location_region !== filters.region) return false
      if (filters.partner && p.partner_id !== filters.partner) return false
      return true
    })
  },

  getSelectedProject: () => {
    const { projects, selectedProjectId } = get()
    return projects.find((p) => p.id === selectedProjectId)
  },

  updateAssignment: (projectId, assignmentId, updates) => {
    set((state) => ({
      projects: state.projects.map((p) =>
        p.id === projectId
          ? {
              ...p,
              assignments: p.assignments.map((a) =>
                a.id === assignmentId ? { ...a, ...updates } : a
              ),
            }
          : p
      ),
    }))
  },

  updateTimesheet: (projectId, timesheetId, updates) => {
    set((state) => ({
      projects: state.projects.map((p) =>
        p.id === projectId
          ? {
              ...p,
              timesheets: (p.timesheets || []).map((t) =>
                t.id === timesheetId ? { ...t, ...updates } : t
              ),
            }
          : p
      ),
    }))
  },

  resolveEscalation: (projectId, escalationId, resolution, newTechName) => {
    set((state) => ({
      projects: state.projects.map((p) => {
        if (p.id !== projectId) return p

        const escalations = (p.escalations || []).map((e) => {
          if (e.id !== escalationId) return e
          const resolvedStatus = resolution.resolution === 'confirm'
            ? 'resolved_confirmed'
            : resolution.resolution === 'reassign'
            ? 'resolved_reassigned'
            : 'resolved_cancelled'
          return {
            ...e,
            status: 'Resolved' as const,
            escalation_status: resolvedStatus as Escalation['escalation_status'],
            resolution_note: resolution.resolution_note,
            resolved_at: new Date().toISOString(),
          }
        })

        // If reassigned, update the assignment status and optionally add new one
        let assignments = p.assignments
        if (resolution.resolution === 'reassign') {
          const escalation = (p.escalations || []).find((e) => e.id === escalationId)
          if (escalation) {
            assignments = assignments.map((a) =>
              a.id === escalation.assignment_id ? { ...a, status: 'Cancelled' as const } : a
            )
            // Add new assignment for replacement tech
            if (resolution.new_technician_id && newTechName) {
              const oldAssignment = p.assignments.find((a) => a.id === escalation.assignment_id)
              if (oldAssignment) {
                assignments = [
                  ...assignments,
                  {
                    ...oldAssignment,
                    id: `asgn-reassign-${Date.now()}`,
                    technician_id: resolution.new_technician_id,
                    technician_name: newTechName,
                    status: 'Active' as const,
                    partner_confirmed: true,
                    start_date: resolution.new_start_date || oldAssignment.start_date,
                  },
                ]
              }
            }
          }
        } else if (resolution.resolution === 'cancel') {
          const escalation = (p.escalations || []).find((e) => e.id === escalationId)
          if (escalation) {
            assignments = assignments.map((a) =>
              a.id === escalation.assignment_id ? { ...a, status: 'Cancelled' as const } : a
            )
          }
        } else if (resolution.resolution === 'confirm') {
          const escalation = (p.escalations || []).find((e) => e.id === escalationId)
          if (escalation) {
            assignments = assignments.map((a) =>
              a.id === escalation.assignment_id
                ? { ...a, status: 'Active' as const, partner_confirmed: true }
                : a
            )
          }
        }

        return { ...p, escalations, assignments }
      }),
    }))
  },

  acknowledgeEscalation: (projectId, escalationId) => {
    set((state) => ({
      projects: state.projects.map((p) =>
        p.id === projectId
          ? {
              ...p,
              escalations: (p.escalations || []).map((e) =>
                e.id === escalationId
                  ? { ...e, escalation_status: 'ops_reviewing' as const }
                  : e
              ),
            }
          : p
      ),
    }))
  },

  getProjectEscalationCounts: () => {
    const { projects } = get()
    return projects
      .filter((p) => (p.escalations || []).length > 0)
      .map((p) => ({
        projectId: p.id,
        open: (p.escalations || []).filter((e) => e.status === 'Open').length,
        total: (p.escalations || []).length,
      }))
  },

  getTotalOpenEscalations: () => {
    const { projects } = get()
    return projects.reduce(
      (sum, p) => sum + (p.escalations || []).filter((e) => e.status === 'Open').length,
      0
    )
  },

  getProjectDisputes: (projectId: string) => {
    const { disputes } = get()
    return disputes.filter((d) => d.project_id === projectId)
  },

  resolveDispute: (disputeId, resolution, opsNote, adjustedHours) => {
    set((state) => ({
      disputes: state.disputes.map((d) =>
        d.id === disputeId
          ? {
              ...d,
              dispute_status: resolution,
              ops_note: opsNote,
              adjusted_hours: adjustedHours,
              resolved_at: new Date().toISOString(),
              resolved_by: 'ops-admin',
            }
          : d
      ),
    }))
  },

  startInvestigation: (disputeId) => {
    set((state) => ({
      disputes: state.disputes.map((d) =>
        d.id === disputeId ? { ...d, dispute_status: 'investigating' as const } : d
      ),
    }))
  },

  closeProject: (projectId) => {
    const { projects, disputes } = get()
    const project = projects.find((p) => p.id === projectId)
    if (!project) return false

    // Check blocking conditions
    const activeAssignments = project.assignments.filter(
      (a) => a.status === 'Active' || a.status === 'Pending Confirmation'
    )
    const openTimesheets = (project.timesheets || []).filter(
      (t) => t.status === 'Submitted' || t.status === 'Flagged'
    )
    const openEscalations = (project.escalations || []).filter(
      (e) => e.status === 'Open'
    )
    const openDisputes = disputes
      .filter((d) => d.project_id === projectId)
      .filter((d) => !d.dispute_status.startsWith('resolved'))

    if (activeAssignments.length > 0 || openTimesheets.length > 0 || openEscalations.length > 0 || openDisputes.length > 0) {
      return false
    }

    set((state) => ({
      projects: state.projects.map((p) =>
        p.id === projectId ? { ...p, status: 'Closed' as const } : p
      ),
    }))
    return true
  },

  updateProjectStatus: (projectId, status) => {
    set((state) => ({
      projects: state.projects.map((p) =>
        p.id === projectId ? { ...p, status } : p
      ),
    }))
  },
}))

// ---------------------------------------------------------------------------
// Mock dispute data for demo
// ---------------------------------------------------------------------------
function generateMockDisputes(): TimesheetDispute[] {
  return [
    {
      id: 'disp-001',
      timesheet_id: 'ts-002',
      project_id: 'proj-001',
      project_name: 'Metro Fiber Expansion - Phoenix',
      partner_id: 'partner-001',
      partner_name: 'Lumen Technologies',
      technician_id: 'tech-008',
      technician_name: 'Sarah Williams',
      role_name: 'Fiber Technician',
      week_start: '2026-03-09',
      week_end: '2026-03-15',
      reported_hours: 48,
      flag_reason: 'Reported 48 hours but site access was restricted on Thursday - please verify',
      flag_category: 'hours_discrepancy',
      partner_note: 'Site access was restricted due to utility work. Tech could not have been on site Thursday afternoon.',
      flagged_at: '2026-03-16T14:00:00Z',
      flagged_by: 'Lumen Technologies',
      dispute_status: 'open',
    },
    {
      id: 'disp-002',
      timesheet_id: 'ts-009',
      project_id: 'proj-001',
      project_name: 'Metro Fiber Expansion - Phoenix',
      partner_id: 'partner-001',
      partner_name: 'Lumen Technologies',
      technician_id: 'tech-012',
      technician_name: 'James Rodriguez',
      role_name: 'Fiber Technician',
      week_start: '2026-03-09',
      week_end: '2026-03-15',
      reported_hours: 42,
      flag_reason: 'Technician was not observed on site Wednesday afternoon — reported full day',
      flag_category: 'hours_discrepancy',
      partner_note: 'Site foreman confirmed tech left at noon on Wednesday. Requesting adjustment to 38 hours.',
      flagged_at: '2026-03-16T14:30:00Z',
      flagged_by: 'Lumen Technologies',
      dispute_status: 'investigating',
      ops_note: 'Contacted technician for their account of Wednesday schedule.',
    },
    {
      id: 'disp-003',
      timesheet_id: 'ts-011',
      project_id: 'proj-001',
      project_name: 'Metro Fiber Expansion - Phoenix',
      partner_id: 'partner-001',
      partner_name: 'Lumen Technologies',
      technician_id: 'tech-001',
      technician_name: 'Marcus Johnson',
      role_name: 'Lead Splicer',
      week_start: '2026-03-02',
      week_end: '2026-03-08',
      reported_hours: 52,
      flag_reason: 'Overtime was not pre-authorized for this week',
      flag_category: 'unauthorized_overtime',
      partner_note: 'No overtime was approved for the week of March 2. Standard 40h max applies.',
      flagged_at: '2026-03-10T09:00:00Z',
      flagged_by: 'Lumen Technologies',
      dispute_status: 'resolved_adjusted',
      ops_note: 'Confirmed with lead splicer. Overtime was approved verbally by site supervisor but not logged. Adjusted to authorized 44h (4h pre-approved OT).',
      adjusted_hours: 44,
      resolved_at: '2026-03-12T16:00:00Z',
      resolved_by: 'ops-admin',
    },
    {
      id: 'disp-004',
      timesheet_id: 'ts-012',
      project_id: 'proj-003',
      project_name: 'FTTH Rollout - Charlotte',
      partner_id: 'partner-003',
      partner_name: 'AT&T',
      technician_id: 'tech-009',
      technician_name: 'Maria Santos',
      role_name: 'FTTH Installer',
      week_start: '2026-03-02',
      week_end: '2026-03-08',
      reported_hours: 40,
      flag_reason: 'Quality concerns - multiple rework requests during this period',
      flag_category: 'quality_concern',
      partner_note: 'Three installations required rework. Questioning if full hours should be billed for incomplete work.',
      flagged_at: '2026-03-11T11:00:00Z',
      flagged_by: 'AT&T',
      dispute_status: 'resolved_approved',
      ops_note: 'Reviewed installation logs. Rework was due to pre-existing site conditions, not technician error. Hours approved in full.',
      resolved_at: '2026-03-13T10:00:00Z',
      resolved_by: 'ops-admin',
    },
  ]
}
