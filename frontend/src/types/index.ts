export interface Technician {
  id: string
  name: string
  home_base_city: string
  approved_regions: string[]
  career_stage: string
  deployability_status: string
  deployability_locked: boolean
  available_from: string
  skills: Skill[]
  certifications: Certification[]
  documents: TechDocument[]
  site_badges: string[]
  milestone_badges: string[]
  avatar_url?: string
  email?: string
  phone?: string
  ops_notes?: string
  years_experience?: number
  total_project_count?: number
  total_approved_hours?: number
  hire_date?: string
}

export type CareerStage =
  | 'Sourced'
  | 'Screened'
  | 'In Training'
  | 'Training Completed'
  | 'Awaiting Assignment'
  | 'Deployed'

export type ProficiencyLevel = 'Beginner' | 'Intermediate' | 'Advanced'

export type DeployabilityStatus =
  | 'Ready Now'
  | 'In Training'
  | 'Currently Assigned'
  | 'Missing Cert'
  | 'Missing Docs'
  | 'Rolling Off Soon'
  | 'Inactive'

export interface Skill {
  skill_name: string
  proficiency_level: ProficiencyLevel
  training_hours_accumulated: number
  target_hours_intermediate: number
  target_hours_advanced: number
}

export interface Certification {
  cert_name: string
  issue_date: string
  expiry_date: string
  status: 'Active' | 'Expiring Soon' | 'Expired' | 'Pending'
}

export interface TechDocument {
  doc_type: string
  verification_status: 'Not Submitted' | 'Pending Review' | 'Verified' | 'Expired'
}

export interface Project {
  id: string
  name: string
  status: 'Draft' | 'Staffing' | 'Active' | 'Wrapping Up' | 'Closed'
  location_region: string
  location_city?: string
  start_date: string
  end_date?: string
  partner_id: string
  partner_name: string
  description?: string
  roles: ProjectRole[]
  assignments: Assignment[]
  timesheets?: Timesheet[]
  escalations?: Escalation[]
}

export interface ProjectRole {
  id: string
  project_id: string
  role_name: string
  skill_bundle: RoleSkillRequirement[]
  required_certs: string[]
  skill_weights: Record<string, number>
  quantity: number
  filled: number
  hourly_rate?: number
  per_diem?: number
}

export interface RoleSkillRequirement {
  skill_name: string
  min_proficiency: 'Beginner' | 'Intermediate' | 'Advanced'
}

export interface Assignment {
  id: string
  project_id: string
  project_name?: string
  role_id: string
  technician_id: string
  technician_name: string
  role_name: string
  start_date: string
  end_date: string
  hourly_rate: number
  per_diem: number
  assignment_type: 'active' | 'pre-booked'
  status: 'Pending Confirmation' | 'Active' | 'Completed' | 'Cancelled'
  partner_confirmed?: boolean
}

export interface Timesheet {
  id: string
  assignment_id: string
  technician_id: string
  technician_name: string
  project_name?: string
  week_start: string
  week_end: string
  hours: number
  status: 'Submitted' | 'Approved' | 'Flagged' | 'Resolved'
  flag_comment?: string
  submitted_at: string
}

export interface Recommendation {
  id: string
  type: 'staffing' | 'training' | 'cert_renewal' | 'backfill' | 'next_step'
  target_id: string
  target_name: string
  scorecard: Scorecard
  explanation: string
  status: 'Pending' | 'Approved' | 'Rejected' | 'Dismissed' | 'Superseded'
  agent: string
  created_at: string
  context?: Record<string, unknown>
}

export interface Scorecard {
  skill_match: ScorecardDimension
  availability: ScorecardDimension
  certification: ScorecardDimension
  location: ScorecardDimension
  experience: ScorecardDimension
  overall_score: number
}

export interface ScorecardDimension {
  score: number
  status: 'pass' | 'warn' | 'fail'
  detail: string
}

export interface Partner {
  id: string
  name: string
  contact_name?: string
  contact_email?: string
  projects: string[]
}

export type PreferenceRuleTemplateType =
  | 'skill_minimum'
  | 'cert_required'
  | 'cert_recency'
  | 'region_preference'
  | 'region_exclusion'
  | 'availability_window'
  | 'experience_minimum'
  | 'project_history'
  | 'travel_willingness'
  | 'client_history'
  | 'score_threshold'
  | 'custom'

export type PreferenceRuleStatus = 'proposed' | 'active' | 'disabled' | 'archived'

export type PreferenceRuleCreatedByType = 'agent' | 'ops'

export interface PreferenceRule {
  id: string
  template_type: PreferenceRuleTemplateType
  rule_type: string
  description: string | null
  threshold: string | null
  scope: string
  scope_target_id: string | null
  effect: 'exclude' | 'demote' | 'boost'
  score_modifier: number | null
  priority: number
  parameters: Record<string, unknown>
  status: PreferenceRuleStatus
  active: boolean
  rejection_id: string | null
  source_recommendation_id: string | null
  proposed_reason: string | null
  created_by_type: PreferenceRuleCreatedByType
  created_by_id: string | null
  approved_by_id: string | null
  approved_at: string | null
  created_at: string
  updated_at: string | null
}

export interface PendingHeadcountRequest {
  id: string
  partner_id: string
  partner_name: string | null
  project_id: string | null
  project_name: string | null
  role_name: string
  quantity: number
  priority: 'low' | 'normal' | 'high' | 'urgent'
  start_date: string | null
  end_date: string | null
  required_skills: Array<{ skill: string; min_level?: string }>
  required_certs: string[]
  constraints: string | null
  notes: string | null
  status: 'Pending' | 'Approved' | 'Rejected' | 'Cancelled'
  reviewed_by: string | null
  reviewed_at: string | null
  rejection_reason: string | null
  created_at: string
  updated_at: string
}

export interface User {
  id: string
  name: string
  role: 'ops' | 'technician' | 'partner'
  scoped_to?: string
  avatar_url?: string
}

export interface Escalation {
  id: string
  project_id: string
  assignment_id: string
  technician_id?: string
  technician_name: string
  type: 'confirmation' | 'timesheet_dispute' | 'assignment_end'
  description: string
  status: 'Open' | 'Resolved'
  created_at: string
  due_date: string
  // Enhanced escalation fields
  partner_id?: string
  partner_name?: string
  role_id?: string
  role_name?: string
  hours_waiting?: number
  escalation_status?: 'escalated' | 'ops_reviewing' | 'resolved_confirmed' | 'resolved_reassigned' | 'resolved_cancelled'
  resolution_note?: string
  resolved_at?: string
  resolved_by?: string
  confirmation_type?: 'start_date' | 'end_date'
  requested_date?: string
}

export interface ReassignmentCandidate {
  technician_id: string
  technician_name: string
  home_base_city?: string
  career_stage?: string
  deployability_status?: string
  available_from?: string
  matching_skills: string[]
  matching_certs: string[]
}

export interface EscalationResolution {
  resolution: 'confirm' | 'reassign' | 'cancel'
  resolution_note?: string
  new_technician_id?: string
  new_start_date?: string
}

export interface ActivityLogEntry {
  id: string
  action: 'approved' | 'rejected' | 'dismissed' | 'created' | 'rule_applied' | 'auto_advanced' | 'escalated'
  agent: string
  description: string
  recommendation_id?: string
  user_id?: string
  user_name?: string
  created_at: string
  metadata?: Record<string, unknown>
}

export interface NextStepCard {
  id: string
  action: string
  reasoning: string
  priority: 'high' | 'medium' | 'low'
  link?: string
  type: 'training' | 'certification' | 'document' | 'assignment' | 'timesheet' | 'general'
  /** Optional recommendation id this card was derived from */
  recommendation_id?: string
  /** When the card was generated */
  generated_at?: string
  /** Optional deadline for the action */
  deadline?: string
  /** Whether this card has been dismissed by the technician */
  dismissed?: boolean
  /** Additional context metadata from the agent */
  metadata?: Record<string, unknown>
}

/** WebSocket event payload for next step card updates */
export interface NextStepCardUpdateEvent {
  technician_id: string
  next_step: NextStepCard | null
  previous_step_id?: string
  reason?: string
}

// Career Passport Token types
export interface CareerPassportToken {
  id: string
  technician_id: string
  token: string
  label: string | null
  revoked: boolean
  expires_at: string
  created_at: string
  created_by_role: string
  is_active: boolean
  share_url: string | null
}

export interface CareerPassportTokenListResponse {
  tokens: CareerPassportToken[]
  count: number
}

export interface SkillBreakdown {
  id: string
  assignment_id: string
  technician_id: string
  submitted_by: string
  overall_notes?: string
  overall_rating?: string
  skill_weights?: Record<string, number>
  status?: 'Pending' | 'Approved' | 'Rejected' | 'Defaulted'
  submitted_at: string
  updated_at?: string
  items?: SkillBreakdownItem[]
  partner_review_status?: PartnerSkillReviewStatus
  partner_review_note?: string
  partner_reviewed_at?: string
  partner_reviewed_by?: string
}

export interface SkillBreakdownWSEvent {
  event_type:
    | 'skill_breakdown.submitted'
    | 'skill_breakdown.approved'
    | 'skill_breakdown.rejected'
    | 'skill_breakdown.revision_requested'
  topic: string
  skill_breakdown: SkillBreakdown
  technician_id?: string
  partner_id?: string
  timestamp: string
}

export type PartnerSkillReviewStatus = 'Pending' | 'Approved' | 'Rejected' | 'Revision Requested'

export interface PartnerSkillBreakdownSummary {
  id: string
  overall_rating?: SkillProficiencyRating | null
  partner_review_status?: PartnerSkillReviewStatus | null
  partner_review_note?: string | null
  partner_reviewed_at?: string | null
  items: SkillBreakdownItem[]
}

export type SkillProficiencyRating =
  | 'Below Expectations'
  | 'Meets Expectations'
  | 'Exceeds Expectations'
  | 'Expert'

export interface SkillBreakdownItem {
  id?: string
  skill_name: string
  skill_id?: string
  hours_applied: number | null
  proficiency_rating: SkillProficiencyRating
  notes?: string
}

export interface SkillBreakdownSubmission {
  items: SkillBreakdownItem[]
  overall_notes?: string
  overall_rating?: SkillProficiencyRating
}

export interface SkillBreakdownResponse {
  id: string
  assignment_id: string
  technician_id: string
  submitted_by: string
  overall_notes?: string
  overall_rating?: string
  submitted_at: string
  updated_at: string
  items: SkillBreakdownItem[]
  partner_review_status?: PartnerSkillReviewStatus
  partner_review_note?: string
  partner_reviewed_at?: string
  partner_reviewed_by?: string
}

// Partner timesheet review types
export interface PartnerTimesheetReview {
  id: string
  timesheet_id: string
  assignment_id: string
  project_id: string
  project_name: string
  technician_id: string
  technician_name: string
  role_name: string
  week_start: string
  week_end: string
  hours: number
  status: 'pending_review' | 'approved' | 'flagged'
  flag_reason?: string
  flag_category?: 'hours_discrepancy' | 'unauthorized_overtime' | 'no_site_access' | 'quality_concern' | 'other'
  partner_note?: string
  reviewed_at?: string
  reviewed_by?: string
  skill_breakdown?: PartnerSkillBreakdownSummary | null
}

// Ops dispute types for flagged timesheets
export interface TimesheetDispute {
  id: string
  timesheet_id: string
  project_id: string
  project_name: string
  partner_id: string
  partner_name: string
  technician_id: string
  technician_name: string
  role_name: string
  week_start: string
  week_end: string
  reported_hours: number
  flag_reason: string
  flag_category: 'hours_discrepancy' | 'unauthorized_overtime' | 'no_site_access' | 'quality_concern' | 'other'
  partner_note?: string
  flagged_at: string
  flagged_by: string
  dispute_status: 'open' | 'investigating' | 'resolved_approved' | 'resolved_adjusted' | 'resolved_rejected'
  ops_note?: string
  adjusted_hours?: number
  resolved_at?: string
  resolved_by?: string
}

// ============================================================
// WebSocket Event Types
// ============================================================

/** All possible WebSocket event types from the backend */
export type WSEventType =
  // Recommendation events
  | 'recommendation.created'
  | 'recommendation.updated'
  | 'recommendation.status_changed'
  | 'recommendation.batch_refreshed'
  // Dashboard events
  | 'dashboard.kpi_updated'
  | 'dashboard.activity'
  | 'dashboard.suggested_action'
  // Technician events
  | 'technician.updated'
  | 'technician.training_advanced'
  | 'technician.cert_expiring'
  | 'technician.deployability_changed'
  // Assignment events
  | 'assignment.created'
  | 'assignment.updated'
  | 'assignment.status_changed'
  // Confirmation events
  | 'confirmation.created'
  | 'confirmation.responded'
  | 'confirmation.escalated'
  // Timesheet events
  | 'timesheet.submitted'
  | 'timesheet.approved'
  | 'timesheet.flagged'
  | 'timesheet.dispute_opened'
  | 'timesheet.dispute_resolved'
  // Training events
  | 'training.hours_logged'
  | 'training.advancement'
  // Badge events
  | 'badge.granted'
  | 'badge.revoked'
  // Next step events
  | 'next_step.updated'
  | 'next_step.dismissed'
  // Forward staffing events
  | 'forward_staffing.gap_detected'
  | 'forward_staffing.recommendation'
  // Agent events
  | 'agent.rule_proposed'
  | 'agent.rule_applied'
  // Notification / badge events
  | 'badge_count.updated'
  | 'notification.created'
  | 'recommendation.list_refresh'
  | 'recommendation.executed'
  | 'recommendation.rejected'
  // Portal events (tech next steps + ops suggested actions)
  | 'portal.next_step_updated'
  | 'portal.next_step_acknowledged'
  | 'portal.suggested_action_updated'
  | 'portal.suggested_action_completed'
  // Generic
  | 'notification'
  | 'pong'

/** Base WebSocket event envelope */
export interface WSEvent<T = unknown> {
  event_type: WSEventType | string
  topic: string
  timestamp: string
  data?: T
  // Common nested payloads
  recommendation?: Recommendation
  technician?: Partial<Technician>
  assignment?: Partial<Assignment>
  confirmation?: unknown
  notification?: WSNotification
  // Badge count fields (from badge_count.updated events)
  badge_type?: string
  count?: number
  role?: string
  // Recommendation list refresh fields
  pending_count?: number
  summary?: Record<string, any>
  // Server notification fields
  notification_type?: string
  title?: string
  message?: string
  severity?: string
  link?: string
  entity_type?: string
  entity_id?: string
  // Portal event fields
  technician_id?: string
  next_step?: NextStepItem
  removed_step_id?: string
  total_steps?: number
  action?: OpsActionItem
  removed_action_id?: string
  total_actions?: number
}

/** Real-time notification from WebSocket */
export interface WSNotification {
  id: string
  type: WSEventType | string
  title: string
  message: string
  severity: 'info' | 'warning' | 'success' | 'error'
  action_url?: string
  action_label?: string
  link?: string
  entity_type?: string
  entity_id?: string
  created_at: string
  read: boolean
  role_scope?: 'ops' | 'technician' | 'partner' | 'all'
}

/** Server-pushed badge count event */
export interface WSBadgeCountEvent {
  event_type: 'badge_count.updated'
  badge_type: string
  count: number
  role: string
  timestamp?: string
}

/** Server-pushed recommendation list refresh signal */
export interface WSRecommendationRefreshEvent {
  event_type: 'recommendation.list_refresh'
  topic: string
  role_id?: string
  project_id?: string
  pending_count?: number
  summary?: Record<string, any>
  timestamp?: string
}

/** WebSocket connection status */
export type WSConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'reconnecting'

/** Topic subscription descriptor */
export interface TopicSubscription {
  topic: string
  id: string
  status: WSConnectionStatus
}

/** Merge action types for recommendation merge history */
export type MergeAction = 'added' | 'removed' | 'retained' | 'superseded' | 'score_updated' | 'rank_changed'

/** Batch job status */
export type BatchJobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'partial'

/** Batch job types */
export type BatchJobType =
  | 'nightly_refresh'
  | 'score_refresh'
  | 'cert_expiry_scan'
  | 'forward_staffing_scan'
  | 'preference_rule_refresh'
  | 'escalation_scan'

/** Recommendation merge history entry */
export interface RecommendationMergeHistoryEntry {
  id: string
  batch_job_id: string
  batch_id?: string
  role_id: string
  project_id?: string
  technician_id: string
  recommendation_id?: string
  action: MergeAction
  reason?: string
  previous_score?: number
  new_score?: number
  previous_rank?: number
  new_rank?: number
  scorecard_snapshot?: Record<string, number>
  disqualification_reasons?: string[]
  metadata?: Record<string, unknown>
  created_at: string
}

/** Batch job execution audit record */
export interface BatchJobExecution {
  id: string
  job_type: BatchJobType
  job_name?: string
  trigger?: string
  correlation_id?: string
  project_id?: string
  role_id?: string
  status: BatchJobStatus
  started_at?: string
  completed_at?: string
  duration_seconds?: number
  roles_processed: number
  recommendations_added: number
  recommendations_removed: number
  recommendations_retained: number
  recommendations_superseded: number
  scores_updated: number
  total_candidates_evaluated: number
  error_message?: string
  error_details?: Record<string, unknown>
  warnings?: string[]
  results_summary?: Record<string, unknown>
  initiated_by?: string
  metadata?: Record<string, unknown>
  created_at: string
}

/** Summary of merge actions for a batch job */
export interface MergeSummary {
  batch_job_id: string
  action_counts: Record<MergeAction, number>
  per_role: Record<string, Record<MergeAction, number>>
  total_entries: number
}

// ============================================================
// Deployability Status Types
// ============================================================

export type StatusChangeSource =
  | 'auto_computed'
  | 'manual_override'
  | 'training_advancement'
  | 'event_triggered'
  | 'batch_refresh'
  | 'system'

export interface DeployabilityStatusHistoryEntry {
  id: string
  technician_id: string
  old_status: string | null
  new_status: string
  source: StatusChangeSource
  reason: string | null
  actor_id: string | null
  actor_name: string | null
  readiness_score_at_change: number | null
  dimension_scores: Record<string, number> | null
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface ReadinessDimensionScores {
  certification: number
  training: number
  assignment_history: number
  documentation: number
}

export interface DeployabilityStatusResponse {
  technician_id: string
  technician_name: string
  current_status: string
  is_locked: boolean
  is_manual_override: boolean
  locked_at: string | null
  locked_by: string | null
  lock_reason: string | null
  career_stage: string
  available_from: string | null
  readiness: {
    overall_score: number
    suggested_status: string
    status_change_recommended: boolean
    status_change_reason: string | null
    dimension_scores: ReadinessDimensionScores
    certification_summary: string
    training_summary: string
    assignment_summary: string
    documentation_summary: string
  } | null
  auto_computed_status: string | null
  status_divergent: boolean
  last_change: DeployabilityStatusHistoryEntry | null
}

export interface DeployabilityStatusHistoryResponse {
  technician_id: string
  technician_name: string
  current_status: string
  total: number
  offset: number
  limit: number
  history: DeployabilityStatusHistoryEntry[]
}

export interface DeployabilitySummaryResponse {
  total_technicians: number
  status_counts: Record<string, number>
  locked_count: number
  recent_changes: DeployabilityStatusHistoryEntry[]
}

// ============================================================
// Multi-User State Sync Types
// ============================================================

/** Actor who triggered a state change */
export interface SyncActor {
  userId: string
  name: string
  role: string
}

/** Sync event received via WebSocket for multi-user coordination */
export interface SyncEvent {
  event_type: string
  topic: string
  entity_type: string
  entity_id: string
  actor: SyncActor
  version: number
  data: Record<string, unknown>
  timestamp: string
  correlation_id?: string
}

/** Entity version tracking for conflict detection */
export interface EntityVersion {
  entity_type: string
  entity_id: string
  version: number
  last_modified_by: SyncActor | null
  last_modified_at: string
}

/** Conflict resolution strategy */
export type ConflictResolution = 'server_wins' | 'client_wins' | 'merge'

/** Toast notification with user attribution */
export interface AttributedToast {
  type: 'success' | 'error' | 'warning' | 'info' | 'sync'
  title: string
  description?: string
  actor?: SyncActor
  duration?: number
}

// ============================================================
// Portal Types (Tech Next Steps + Ops Suggested Actions)
// ============================================================

/** A single next-step recommendation for a technician */
export interface NextStepItem {
  id: string
  recommendation_type: string
  title: string
  description?: string
  explanation?: string
  priority: number
  action_type: string
  action_link?: string
  scorecard?: Record<string, number>
  overall_score?: number
  status: string
  metadata?: Record<string, any>
  created_at?: string
}

/** Response from /api/portal/tech/next-steps */
export interface NextStepResponse {
  technician_id: string
  technician_name?: string
  career_stage?: string
  deployability_status?: string
  next_steps: NextStepItem[]
  total: number
  pending_trainings: number
  expiring_certs: number
  available_assignments: number
}

/** A single suggested action for the ops dashboard */
export interface OpsActionItem {
  id: string
  action_type: string
  title: string
  description?: string
  link?: string
  priority: number
  category: string
  entity_type?: string
  entity_id?: string
  target_role: string
  metadata?: Record<string, any>
  created_at?: string
}

/** Response from /api/portal/ops/suggested-actions */
export interface SuggestedActionsResponse {
  actions: OpsActionItem[]
  total: number
  by_category: Record<string, number>
  urgent_count: number
  high_count: number
  normal_count: number
}

/** Summary of a pending recommendation for ops review */
export interface PendingRecommendationSummary {
  id: string
  recommendation_type: string
  technician_id?: string
  technician_name?: string
  project_id?: string
  project_name?: string
  role_id?: string
  role_title?: string
  overall_score?: number
  rank?: string
  scorecard?: Record<string, any>
  explanation?: string
  agent_name?: string
  created_at?: string
}

/** Response from /api/portal/ops/pending-recommendations */
export interface PendingRecommendationsResponse {
  recommendations: PendingRecommendationSummary[]
  total: number
  by_type: Record<string, number>
  by_project: Record<string, PendingRecommendationSummary[]>
}

/** Partner's view of project recommendations */
export interface PartnerRecommendationItem {
  id: string
  recommendation_type: string
  role_title?: string
  technician_summary?: string
  overall_score?: number
  scorecard?: Record<string, any>
  status: string
  explanation?: string
  created_at?: string
}

/** Response from /api/portal/partner/recommendations */
export interface PartnerRecommendationsResponse {
  partner_id: string
  project_id?: string
  recommendations: PartnerRecommendationItem[]
  total: number
}
