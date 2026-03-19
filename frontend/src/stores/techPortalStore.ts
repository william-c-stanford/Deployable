import { create } from 'zustand'
import api from '@/lib/api'
import type {
  Technician,
  Assignment,
  Timesheet,
  NextStepCard,
  SkillBreakdown,
  SkillBreakdownSubmission,
  SkillBreakdownResponse,
  NextStepCardUpdateEvent,
} from '@/types'

// ============================================================
// Technician Portal Store — the tech-facing entry point state
// ============================================================

interface TechPortalStore {
  // Current technician
  technician: Technician | null
  setTechnician: (tech: Technician) => void

  // Assignments
  assignments: Assignment[]
  setAssignments: (assignments: Assignment[]) => void
  getActiveAssignment: () => Assignment | undefined
  getUpcomingAssignments: () => Assignment[]
  getCompletableAssignment: () => Assignment | undefined

  // Timesheets
  timesheets: Timesheet[]
  setTimesheets: (timesheets: Timesheet[]) => void
  getPendingTimesheets: () => Timesheet[]

  // Next step card (agent-generated)
  nextStepCard: NextStepCard | null
  nextStepLoading: boolean
  nextStepError: string | null
  nextStepAnimating: boolean
  setNextStepCard: (card: NextStepCard | null) => void
  fetchNextStepCard: (technicianId: string) => Promise<void>
  dismissNextStep: (cardId: string) => Promise<void>
  handleNextStepWSUpdate: (event: NextStepCardUpdateEvent) => void

  // Skill breakdowns
  skillBreakdowns: SkillBreakdown[]
  setSkillBreakdowns: (breakdowns: SkillBreakdown[]) => void

  // Hours submission form
  hoursFormOpen: boolean
  setHoursFormOpen: (open: boolean) => void
  hoursFormData: { assignment_id: string; hours: number; week_start: string; skill_name?: string }
  setHoursFormData: (data: Partial<{ assignment_id: string; hours: number; week_start: string; skill_name?: string }>) => void
  submitHours: () => void
  submitHoursToAPI: () => Promise<void>
  isSubmitting: boolean
  submitError: string | null

  // Skill breakdown form
  skillBreakdownFormOpen: boolean
  skillBreakdownAssignmentId: string | null
  skillBreakdownSubmitting: boolean
  skillBreakdownError: string | null
  skillBreakdownSuccess: boolean
  openSkillBreakdownForm: (assignmentId: string) => void
  closeSkillBreakdownForm: () => void
  markAssignmentComplete: (assignmentId: string) => Promise<void>
  submitSkillBreakdown: (assignmentId: string, data: SkillBreakdownSubmission) => Promise<void>

  // Computed
  getTrainingProgress: () => { skill_name: string; hours: number; level: string; nextLevel: string | null; progressPct: number }[]
  getExpiringCerts: () => { cert_name: string; expiry_date: string; days_left: number }[]
  getMissingDocs: () => { doc_type: string; status: string }[]
}

export const useTechPortalStore = create<TechPortalStore>((set, get) => ({
  technician: null,
  setTechnician: (tech) => set({ technician: tech }),

  assignments: [],
  setAssignments: (assignments) => set({ assignments }),
  getActiveAssignment: () => get().assignments.find((a) => a.assignment_type === 'active' && a.status !== 'Completed'),
  getUpcomingAssignments: () => get().assignments.filter((a) => a.assignment_type === 'pre-booked'),
  getCompletableAssignment: () => get().assignments.find((a) => a.assignment_type === 'active' && a.status === 'Active'),

  timesheets: [],
  setTimesheets: (timesheets) => set({ timesheets }),
  getPendingTimesheets: () => get().timesheets.filter((t) => t.status === 'Submitted'),

  nextStepCard: null,
  nextStepLoading: false,
  nextStepError: null,
  nextStepAnimating: false,
  setNextStepCard: (card) => set({ nextStepCard: card }),

  fetchNextStepCard: async (technicianId: string) => {
    set({ nextStepLoading: true, nextStepError: null })
    try {
      // Fetch next_step recommendations for this technician
      const response = await api.get('/recommendations', {
        params: {
          recommendation_type: 'next_step',
          technician_id: technicianId,
          status: 'Pending',
        },
      })
      const recs = response.data?.recommendations || response.data?.items || []
      if (recs.length > 0) {
        // Pick the highest-priority recommendation
        const rec = recs[0]
        const card: NextStepCard = {
          id: rec.id,
          action: rec.title || rec.action || rec.summary || 'Review recommendation',
          reasoning: rec.explanation || rec.reasoning || rec.description || '',
          priority: rec.priority || 'medium',
          type: rec.next_step_type || rec.sub_type || 'general',
          link: rec.link || undefined,
          recommendation_id: rec.id,
          generated_at: rec.created_at,
          deadline: rec.deadline || undefined,
          metadata: rec.metadata || undefined,
        }
        set({ nextStepCard: card, nextStepLoading: false })
      } else {
        set({ nextStepLoading: false })
      }
    } catch {
      // Silently fall through — seed data will be used as fallback
      set({ nextStepLoading: false })
    }
  },

  dismissNextStep: async (cardId: string) => {
    const currentCard = get().nextStepCard
    if (!currentCard || currentCard.id !== cardId) return

    // Optimistic update
    set({ nextStepCard: { ...currentCard, dismissed: true } })

    try {
      if (currentCard.recommendation_id) {
        await api.post(`/recommendations/${currentCard.recommendation_id}/dismiss`)
      }
      // Remove after animation
      setTimeout(() => {
        set({ nextStepCard: null })
      }, 300)
    } catch {
      // Revert on failure
      set({ nextStepCard: currentCard })
    }
  },

  handleNextStepWSUpdate: (event: NextStepCardUpdateEvent) => {
    const tech = get().technician
    if (!tech || event.technician_id !== tech.id) return

    if (event.next_step) {
      // Animate the card update
      set({ nextStepAnimating: true })
      setTimeout(() => {
        set({ nextStepCard: event.next_step, nextStepAnimating: false })
      }, 200)
    } else {
      // Card was cleared (e.g., action completed)
      set({ nextStepAnimating: true })
      setTimeout(() => {
        set({ nextStepCard: null, nextStepAnimating: false })
      }, 200)
    }
  },

  skillBreakdowns: [],
  setSkillBreakdowns: (breakdowns) => set({ skillBreakdowns: breakdowns }),

  hoursFormOpen: false,
  setHoursFormOpen: (open) => set({ hoursFormOpen: open }),
  hoursFormData: { assignment_id: '', hours: 0, week_start: '' },
  setHoursFormData: (data) =>
    set((state) => ({
      hoursFormData: { ...state.hoursFormData, ...data },
    })),
  isSubmitting: false,
  submitError: null,

  submitHours: () => {
    const { hoursFormData, timesheets } = get()
    if (!hoursFormData.assignment_id || !hoursFormData.hours) return

    const newTimesheet: Timesheet = {
      id: `ts-${Date.now()}`,
      assignment_id: hoursFormData.assignment_id,
      technician_id: get().technician?.id || '',
      technician_name: get().technician?.name || '',
      project_name: get().assignments.find((a) => a.id === hoursFormData.assignment_id)?.project_name || '',
      week_start: hoursFormData.week_start || new Date().toISOString().split('T')[0],
      week_end: '',
      hours: hoursFormData.hours,
      status: 'Submitted',
      submitted_at: new Date().toISOString(),
    }

    set({
      timesheets: [newTimesheet, ...timesheets],
      hoursFormOpen: false,
      hoursFormData: { assignment_id: '', hours: 0, week_start: '' },
      submitError: null,
    })
  },

  submitHoursToAPI: async () => {
    const { hoursFormData, timesheets } = get()
    if (!hoursFormData.assignment_id || !hoursFormData.hours) return

    set({ isSubmitting: true, submitError: null })

    try {
      const response = await api.post('/timesheets', {
        assignment_id: hoursFormData.assignment_id,
        week_start: hoursFormData.week_start || new Date().toISOString().split('T')[0],
        hours: hoursFormData.hours,
        skill_name: hoursFormData.skill_name || null,
      })

      const ts = response.data
      const newTimesheet: Timesheet = {
        id: ts.id,
        assignment_id: ts.assignment_id,
        technician_id: get().technician?.id || '',
        technician_name: get().technician?.name || '',
        project_name:
          get().assignments.find((a) => a.id === ts.assignment_id)?.project_name || '',
        week_start: ts.week_start,
        week_end: '',
        hours: ts.hours,
        status: ts.status,
        flag_comment: ts.flag_comment,
        submitted_at: ts.submitted_at,
      }

      set({
        timesheets: [newTimesheet, ...timesheets],
        hoursFormOpen: false,
        hoursFormData: { assignment_id: '', hours: 0, week_start: '' },
        isSubmitting: false,
      })
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Failed to submit timesheet')
          : 'Failed to submit timesheet'
      set({ isSubmitting: false, submitError: errorMsg })
      // Fall back to local-only submission
      get().submitHours()
    }
  },

  // ── Skill Breakdown Form State ──────────────────────────────────
  skillBreakdownFormOpen: false,
  skillBreakdownAssignmentId: null,
  skillBreakdownSubmitting: false,
  skillBreakdownError: null,
  skillBreakdownSuccess: false,

  openSkillBreakdownForm: (assignmentId: string) => {
    set({
      skillBreakdownFormOpen: true,
      skillBreakdownAssignmentId: assignmentId,
      skillBreakdownError: null,
      skillBreakdownSuccess: false,
    })
  },

  closeSkillBreakdownForm: () => {
    set({
      skillBreakdownFormOpen: false,
      skillBreakdownAssignmentId: null,
      skillBreakdownError: null,
      skillBreakdownSuccess: false,
    })
  },

  markAssignmentComplete: async (assignmentId: string) => {
    set({ skillBreakdownSubmitting: true, skillBreakdownError: null })

    try {
      await api.post(`/assignments/${assignmentId}/complete`)

      // Update assignment status locally
      const assignments = get().assignments.map((a) =>
        a.id === assignmentId ? { ...a, status: 'Completed' as const } : a
      )
      set({ assignments, skillBreakdownSubmitting: false })

      // Open the skill breakdown form
      get().openSkillBreakdownForm(assignmentId)
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Failed to mark assignment complete')
          : 'Failed to mark assignment complete'
      set({ skillBreakdownSubmitting: false, skillBreakdownError: errorMsg })

      // Fallback: update locally and open form
      const assignments = get().assignments.map((a) =>
        a.id === assignmentId ? { ...a, status: 'Completed' as const } : a
      )
      set({ assignments })
      get().openSkillBreakdownForm(assignmentId)
    }
  },

  submitSkillBreakdown: async (assignmentId: string, data: SkillBreakdownSubmission) => {
    set({ skillBreakdownSubmitting: true, skillBreakdownError: null })

    try {
      const response = await api.post(`/assignments/${assignmentId}/skill-breakdown`, data)

      const breakdown = response.data as SkillBreakdownResponse
      const newBreakdown: SkillBreakdown = {
        id: breakdown.id,
        assignment_id: breakdown.assignment_id,
        technician_id: breakdown.technician_id,
        submitted_by: breakdown.technician_id,
        skill_weights: breakdown.items.reduce(
          (acc, item) => ({ ...acc, [item.skill_name]: item.hours_applied || 0 }),
          {} as Record<string, number>
        ),
        status: 'Pending',
        submitted_at: breakdown.submitted_at,
      }

      set({
        skillBreakdowns: [...get().skillBreakdowns, newBreakdown],
        skillBreakdownSubmitting: false,
        skillBreakdownSuccess: true,
      })
    } catch (err: unknown) {
      const errorMsg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Failed to submit skill breakdown')
          : 'Failed to submit skill breakdown'
      set({ skillBreakdownSubmitting: false, skillBreakdownError: errorMsg })

      // Fallback: save locally
      const newBreakdown: SkillBreakdown = {
        id: `sb-${Date.now()}`,
        assignment_id: assignmentId,
        technician_id: get().technician?.id || '',
        submitted_by: get().technician?.id || '',
        skill_weights: data.items.reduce(
          (acc, item) => ({ ...acc, [item.skill_name]: item.hours_applied || 0 }),
          {} as Record<string, number>
        ),
        status: 'Pending',
        submitted_at: new Date().toISOString(),
      }

      set({
        skillBreakdowns: [...get().skillBreakdowns, newBreakdown],
        skillBreakdownSubmitting: false,
        skillBreakdownSuccess: true,
        skillBreakdownError: null,
      })
    }
  },

  getTrainingProgress: () => {
    const tech = get().technician
    if (!tech) return []
    return tech.skills.map((s) => {
      const nextLevel =
        s.proficiency_level === 'Beginner'
          ? 'Intermediate'
          : s.proficiency_level === 'Intermediate'
            ? 'Advanced'
            : null
      const target = nextLevel === 'Intermediate' ? 100 : nextLevel === 'Advanced' ? 300 : 300
      const progressPct = Math.min(100, Math.round((s.training_hours_accumulated / target) * 100))
      return {
        skill_name: s.skill_name,
        hours: s.training_hours_accumulated,
        level: s.proficiency_level,
        nextLevel,
        progressPct,
      }
    })
  },

  getExpiringCerts: () => {
    const tech = get().technician
    if (!tech) return []
    const now = Date.now()
    return tech.certifications
      .map((c) => ({
        cert_name: c.cert_name,
        expiry_date: c.expiry_date,
        days_left: Math.ceil((new Date(c.expiry_date).getTime() - now) / 86400000),
      }))
      .filter((c) => c.days_left <= 90 && c.days_left > 0)
      .sort((a, b) => a.days_left - b.days_left)
  },

  getMissingDocs: () => {
    const tech = get().technician
    if (!tech) return []
    return tech.documents
      .filter((d) => d.verification_status !== 'Verified')
      .map((d) => ({ doc_type: d.doc_type, status: d.verification_status }))
  },
}))
