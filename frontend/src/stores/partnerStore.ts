import { create } from "zustand";
import api from "@/lib/api";
import type { PartnerTimesheetReview, PartnerSkillReviewStatus } from "@/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PartnerProjectSummary {
  id: string;
  name: string;
  status: string;
  location_region: string;
  location_city?: string;
  start_date: string;
  end_date?: string;
  total_roles: number;
  filled_roles: number;
  active_assignments: number;
}

export interface PartnerAssignment {
  id: string;
  technician_name: string;
  technician_id: string;
  project_name: string;
  project_id: string;
  role_name: string;
  start_date: string;
  end_date?: string;
  status: string;
  assignment_type: string;
  partner_confirmed_start: boolean;
  partner_confirmed_end: boolean;
}

export interface PartnerConfirmation {
  id: string;
  assignment_id: string;
  partner_id: string;
  confirmation_type: "start_date" | "end_date";
  status: "pending" | "confirmed" | "declined";
  requested_date: string;
  proposed_date?: string;
  response_note?: string;
  requested_at: string;
  responded_at?: string;
  technician_name?: string;
  project_name?: string;
  role_name?: string;
}

export interface PartnerNotification {
  id: string;
  notification_type: string;
  status: string;
  title: string;
  message?: string;
  target_date: string;
  technician_name?: string;
  project_name?: string;
  created_at: string;
}

export interface PartnerStats {
  active_projects: number;
  total_assignments: number;
  pending_confirmations: number;
  upcoming_starts: number;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface PartnerState {
  // Data
  partnerName: string;
  partnerId: string;
  projects: PartnerProjectSummary[];
  assignments: PartnerAssignment[];
  confirmations: PartnerConfirmation[];
  notifications: PartnerNotification[];
  stats: PartnerStats;
  timesheetReviews: PartnerTimesheetReview[];

  // UI state
  isLoading: boolean;
  error: string | null;
  confirmingId: string | null;
  reviewingTimesheetId: string | null;

  // Actions
  fetchDashboard: () => Promise<void>;
  fetchConfirmations: () => Promise<void>;
  fetchTimesheetReviews: () => Promise<void>;
  confirmAssignment: (confirmationId: string, note?: string) => Promise<void>;
  declineAssignment: (confirmationId: string, proposedDate: string, note?: string) => Promise<void>;
  approveTimesheet: (reviewId: string, note?: string) => Promise<void>;
  flagTimesheet: (reviewId: string, reason: string, category: PartnerTimesheetReview['flag_category'], note?: string) => Promise<void>;
  reviewSkillBreakdown: (reviewId: string, action: 'approve' | 'reject' | 'request_revision', note?: string) => Promise<void>;
  handleWsEvent: (event: ConfirmationWsEvent) => void;
}

export interface ConfirmationWsEvent {
  event_type: string;
  topic: string;
  confirmation: PartnerConfirmation;
  timestamp: string;
}

export const usePartnerStore = create<PartnerState>((set, get) => ({
  // Initial state
  partnerName: "",
  partnerId: "",
  projects: [],
  assignments: [],
  confirmations: [],
  notifications: [],
  stats: {
    active_projects: 0,
    total_assignments: 0,
    pending_confirmations: 0,
    upcoming_starts: 0,
  },
  timesheetReviews: [],
  isLoading: false,
  error: null,
  confirmingId: null,
  reviewingTimesheetId: null,

  // Fetch all partner dashboard data
  fetchDashboard: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await api.get("/partner/dashboard");
      const data = res.data;
      set({
        partnerName: data.partner_name,
        partnerId: data.partner_id,
        projects: data.projects,
        assignments: data.assignments,
        notifications: data.notifications,
        stats: data.stats,
        isLoading: false,
      });
      // Also fetch confirmations
      get().fetchConfirmations();
    } catch (err: any) {
      set({
        isLoading: false,
        error: err?.response?.data?.detail || "Failed to load partner data",
      });
    }
  },

  // Fetch pending confirmations
  fetchConfirmations: async () => {
    try {
      const res = await api.get("/partner-confirmations", {
        params: { status: "pending" },
      });
      set({
        confirmations: res.data.confirmations || [],
      });
    } catch {
      // Silently fail - confirmations are supplementary
    }
  },

  // Confirm an assignment date
  confirmAssignment: async (confirmationId: string, note?: string) => {
    set({ confirmingId: confirmationId });
    try {
      await api.post(`/partner-confirmations/${confirmationId}/respond`, {
        action: "confirm",
        response_note: note || null,
      });
      // Update local state
      set((state) => ({
        confirmations: state.confirmations.map((c) =>
          c.id === confirmationId
            ? { ...c, status: "confirmed" as const, response_note: note }
            : c
        ),
        confirmingId: null,
        stats: {
          ...state.stats,
          pending_confirmations: Math.max(0, state.stats.pending_confirmations - 1),
        },
      }));
    } catch (err: any) {
      set({ confirmingId: null, error: err?.response?.data?.detail || "Failed to confirm" });
    }
  },

  // Decline an assignment date with proposed alternative
  declineAssignment: async (confirmationId: string, proposedDate: string, note?: string) => {
    set({ confirmingId: confirmationId });
    try {
      await api.post(`/partner-confirmations/${confirmationId}/respond`, {
        action: "decline",
        proposed_date: proposedDate,
        response_note: note || null,
      });
      set((state) => ({
        confirmations: state.confirmations.map((c) =>
          c.id === confirmationId
            ? { ...c, status: "declined" as const, proposed_date: proposedDate, response_note: note }
            : c
        ),
        confirmingId: null,
      }));
    } catch (err: any) {
      set({ confirmingId: null, error: err?.response?.data?.detail || "Failed to decline" });
    }
  },

  // Fetch timesheet reviews for partner
  fetchTimesheetReviews: async () => {
    try {
      const res = await api.get("/partner/timesheet-reviews");
      set({ timesheetReviews: res.data.reviews || [] });
    } catch {
      // Fall back to mock data if API unavailable
      set({ timesheetReviews: generateMockTimesheetReviews() });
    }
  },

  // Approve a timesheet as partner
  approveTimesheet: async (reviewId: string, note?: string) => {
    set({ reviewingTimesheetId: reviewId });
    try {
      await api.post(`/partner/timesheet-reviews/${reviewId}/respond`, {
        action: "approve",
        note: note || null,
      });
      set((state) => ({
        timesheetReviews: state.timesheetReviews.map((r) =>
          r.id === reviewId
            ? { ...r, status: "approved" as const, partner_note: note, reviewed_at: new Date().toISOString() }
            : r
        ),
        reviewingTimesheetId: null,
      }));
    } catch {
      // Optimistic update for demo
      set((state) => ({
        timesheetReviews: state.timesheetReviews.map((r) =>
          r.id === reviewId
            ? { ...r, status: "approved" as const, partner_note: note, reviewed_at: new Date().toISOString() }
            : r
        ),
        reviewingTimesheetId: null,
      }));
    }
  },

  // Flag a timesheet as partner
  flagTimesheet: async (reviewId: string, reason: string, category: PartnerTimesheetReview['flag_category'], note?: string) => {
    set({ reviewingTimesheetId: reviewId });
    try {
      await api.post(`/partner/timesheet-reviews/${reviewId}/respond`, {
        action: "flag",
        flag_reason: reason,
        flag_category: category,
        note: note || null,
      });
      set((state) => ({
        timesheetReviews: state.timesheetReviews.map((r) =>
          r.id === reviewId
            ? { ...r, status: "flagged" as const, flag_reason: reason, flag_category: category, partner_note: note, reviewed_at: new Date().toISOString() }
            : r
        ),
        reviewingTimesheetId: null,
      }));
    } catch {
      // Optimistic update for demo
      set((state) => ({
        timesheetReviews: state.timesheetReviews.map((r) =>
          r.id === reviewId
            ? { ...r, status: "flagged" as const, flag_reason: reason, flag_category: category, partner_note: note, reviewed_at: new Date().toISOString() }
            : r
        ),
        reviewingTimesheetId: null,
      }));
    }
  },

  // Review a skill breakdown associated with a timesheet
  reviewSkillBreakdown: async (reviewId: string, action: 'approve' | 'reject' | 'request_revision', note?: string) => {
    set({ reviewingTimesheetId: reviewId });
    const statusMap: Record<string, PartnerSkillReviewStatus> = {
      approve: "Approved",
      reject: "Rejected",
      request_revision: "Revision Requested",
    };
    try {
      await api.put(`/partner/timesheets/${reviewId}/skill-breakdown/review`, {
        action,
        note: note || null,
      });
      set((state) => ({
        timesheetReviews: state.timesheetReviews.map((r) =>
          r.id === reviewId && r.skill_breakdown
            ? {
                ...r,
                skill_breakdown: {
                  ...r.skill_breakdown,
                  partner_review_status: statusMap[action],
                  partner_review_note: note || null,
                  partner_reviewed_at: new Date().toISOString(),
                },
              }
            : r
        ),
        reviewingTimesheetId: null,
      }));
    } catch {
      // Optimistic update for demo
      set((state) => ({
        timesheetReviews: state.timesheetReviews.map((r) =>
          r.id === reviewId && r.skill_breakdown
            ? {
                ...r,
                skill_breakdown: {
                  ...r.skill_breakdown,
                  partner_review_status: statusMap[action],
                  partner_review_note: note || null,
                  partner_reviewed_at: new Date().toISOString(),
                },
              }
            : r
        ),
        reviewingTimesheetId: null,
      }));
    }
  },

  // Handle WebSocket event for confirmation updates
  handleWsEvent: (event: ConfirmationWsEvent) => {
    if (!event.confirmation) return;

    set((state) => {
      const existing = state.confirmations.find((c) => c.id === event.confirmation.id);
      if (existing) {
        // Update existing
        return {
          confirmations: state.confirmations.map((c) =>
            c.id === event.confirmation.id ? { ...c, ...event.confirmation } : c
          ),
        };
      }
      // Add new
      return {
        confirmations: [event.confirmation, ...state.confirmations],
        stats: {
          ...state.stats,
          pending_confirmations: state.stats.pending_confirmations + 1,
        },
      };
    });
  },
}));

// ---------------------------------------------------------------------------
// Mock timesheet reviews for demo
// ---------------------------------------------------------------------------
function generateMockTimesheetReviews(): PartnerTimesheetReview[] {
  return [
    {
      id: "ptr-001",
      timesheet_id: "ts-003",
      assignment_id: "asgn-002",
      project_id: "proj-001",
      project_name: "Metro Fiber Expansion - Phoenix",
      technician_id: "tech-005",
      technician_name: "David Chen",
      role_name: "Lead Splicer",
      week_start: "2026-03-09",
      week_end: "2026-03-15",
      hours: 40,
      status: "pending_review",
      skill_breakdown: {
        id: "sb-001",
        overall_rating: "Meets Expectations",
        partner_review_status: null,
        partner_review_note: null,
        partner_reviewed_at: null,
        items: [
          { skill_name: "Fiber Splicing", hours_applied: 24, proficiency_rating: "Meets Expectations" },
          { skill_name: "OTDR Testing", hours_applied: 10, proficiency_rating: "Exceeds Expectations" },
          { skill_name: "Cable Pulling", hours_applied: 6, proficiency_rating: "Meets Expectations" },
        ],
      },
    },
    {
      id: "ptr-002",
      timesheet_id: "ts-002",
      assignment_id: "asgn-003",
      project_id: "proj-001",
      project_name: "Metro Fiber Expansion - Phoenix",
      technician_id: "tech-008",
      technician_name: "Sarah Williams",
      role_name: "Fiber Technician",
      week_start: "2026-03-09",
      week_end: "2026-03-15",
      hours: 48,
      status: "pending_review",
      skill_breakdown: {
        id: "sb-002",
        overall_rating: "Exceeds Expectations",
        partner_review_status: null,
        partner_review_note: null,
        partner_reviewed_at: null,
        items: [
          { skill_name: "Fiber Splicing", hours_applied: 30, proficiency_rating: "Exceeds Expectations" },
          { skill_name: "Connector Termination", hours_applied: 12, proficiency_rating: "Exceeds Expectations" },
          { skill_name: "Documentation", hours_applied: 6, proficiency_rating: "Meets Expectations" },
        ],
      },
    },
    {
      id: "ptr-003",
      timesheet_id: "ts-001",
      assignment_id: "asgn-001",
      project_id: "proj-001",
      project_name: "Metro Fiber Expansion - Phoenix",
      technician_id: "tech-001",
      technician_name: "Marcus Johnson",
      role_name: "Lead Splicer",
      week_start: "2026-03-09",
      week_end: "2026-03-15",
      hours: 44,
      status: "approved",
      partner_note: "Verified with site supervisor",
      reviewed_at: "2026-03-16T10:00:00Z",
      skill_breakdown: {
        id: "sb-003",
        overall_rating: "Exceeds Expectations",
        partner_review_status: "Approved",
        partner_review_note: "Skill assessments match on-site observations",
        partner_reviewed_at: "2026-03-16T10:00:00Z",
        items: [
          { skill_name: "Fiber Splicing", hours_applied: 28, proficiency_rating: "Expert" },
          { skill_name: "Team Leadership", hours_applied: 10, proficiency_rating: "Exceeds Expectations" },
          { skill_name: "Safety Compliance", hours_applied: 6, proficiency_rating: "Meets Expectations" },
        ],
      },
    },
    {
      id: "ptr-004",
      timesheet_id: "ts-009",
      assignment_id: "asgn-004",
      project_id: "proj-001",
      project_name: "Metro Fiber Expansion - Phoenix",
      technician_id: "tech-012",
      technician_name: "James Rodriguez",
      role_name: "Fiber Technician",
      week_start: "2026-03-09",
      week_end: "2026-03-15",
      hours: 42,
      status: "flagged",
      flag_reason: "Technician was not observed on site Wednesday afternoon — reported full day",
      flag_category: "hours_discrepancy",
      partner_note: "Site foreman confirmed tech left at noon on Wednesday. Requesting adjustment to 38 hours.",
      reviewed_at: "2026-03-16T14:00:00Z",
      reviewed_by: "partner-001",
      skill_breakdown: {
        id: "sb-004",
        overall_rating: "Below Expectations",
        partner_review_status: "Rejected",
        partner_review_note: "Skill ratings seem inflated — tech was not performing at this level on site",
        partner_reviewed_at: "2026-03-16T14:00:00Z",
        items: [
          { skill_name: "Fiber Splicing", hours_applied: 20, proficiency_rating: "Meets Expectations", notes: "Slow splice speed, required supervision" },
          { skill_name: "Cable Pulling", hours_applied: 14, proficiency_rating: "Meets Expectations" },
          { skill_name: "Connector Termination", hours_applied: 8, proficiency_rating: "Below Expectations", notes: "Multiple rework required" },
        ],
      },
    },
    {
      id: "ptr-005",
      timesheet_id: "ts-010",
      assignment_id: "asgn-006",
      project_id: "proj-001",
      project_name: "Metro Fiber Expansion - Phoenix",
      technician_id: "tech-020",
      technician_name: "Robert Kim",
      role_name: "OTDR Tester",
      week_start: "2026-03-09",
      week_end: "2026-03-15",
      hours: 46,
      status: "pending_review",
      skill_breakdown: {
        id: "sb-005",
        overall_rating: "Expert",
        partner_review_status: null,
        partner_review_note: null,
        partner_reviewed_at: null,
        items: [
          { skill_name: "OTDR Testing", hours_applied: 32, proficiency_rating: "Expert" },
          { skill_name: "Fiber Characterization", hours_applied: 10, proficiency_rating: "Exceeds Expectations" },
          { skill_name: "Report Generation", hours_applied: 4, proficiency_rating: "Meets Expectations" },
        ],
      },
    },
    {
      id: "ptr-006",
      timesheet_id: "ts-011",
      assignment_id: "asgn-001",
      project_id: "proj-001",
      project_name: "Metro Fiber Expansion - Phoenix",
      technician_id: "tech-001",
      technician_name: "Marcus Johnson",
      role_name: "Lead Splicer",
      week_start: "2026-03-02",
      week_end: "2026-03-08",
      hours: 52,
      status: "flagged",
      flag_reason: "Overtime was not pre-authorized for this week",
      flag_category: "unauthorized_overtime",
      partner_note: "No overtime was approved for the week of March 2. Standard 40h max.",
      reviewed_at: "2026-03-10T09:00:00Z",
      reviewed_by: "partner-001",
    },
  ];
}
