import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { useTechPortalStore } from '@/stores/techPortalStore'
import { CareerPassportPanel } from '@/components/career-passport'
import { DeployabilityStatusPanel } from '@/components/deployability'
import { SkillBreakdownForm } from '@/components/skill-breakdown'
import { useSkillBreakdownWebSocket } from '@/hooks/useSkillBreakdownWebSocket'
import { NextStepCard } from '@/components/next-step'
import {
  seedTechnician,
  seedAssignments,
  seedTimesheets,
  seedNextStepCard,
  seedSkillBreakdowns,
} from '@/lib/techPortalSeedData'
import api from '@/lib/api'
import type { Assignment, Timesheet, SkillBreakdownWSEvent } from '@/types'

// ============================================================
// Technician Portal — mobile-first tech-facing entry point
// ============================================================

export function TechnicianPortal() {
  const store = useTechPortalStore()
  const navigate = useNavigate()
  const [breakdownNotification, setBreakdownNotification] = useState<string | null>(null)
  const [portalTab, setPortalTab] = useState<'overview' | 'skills' | 'docs'>('overview')
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    // Try API first, then seed
    const loadData = async () => {
      setIsLoading(true)
      try {
        // Try to fetch technician profile from API
        const res = await api.get('/portal/technician/profile')
        if (res.data) {
          store.setTechnician(res.data)
        } else {
          store.setTechnician(seedTechnician)
        }
      } catch {
        store.setTechnician(seedTechnician)
      }

      try {
        const res = await api.get('/portal/technician/assignments')
        if (res.data?.length > 0) {
          store.setAssignments(res.data)
        } else {
          store.setAssignments(seedAssignments)
        }
      } catch {
        store.setAssignments(seedAssignments)
      }

      try {
        const res = await api.get('/portal/technician/timesheets')
        if (res.data?.length > 0) {
          store.setTimesheets(res.data)
        } else {
          store.setTimesheets(seedTimesheets)
        }
      } catch {
        store.setTimesheets(seedTimesheets)
      }

      store.setNextStepCard(seedNextStepCard)
      store.setSkillBreakdowns(seedSkillBreakdowns)
      setIsLoading(false)
    }
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Real-time WebSocket subscription for skill breakdown events
  const handleBreakdownApproved = useCallback((event: SkillBreakdownWSEvent) => {
    setBreakdownNotification(`Skill breakdown approved by partner`)
    // Update breakdown status in local store
    const sb = event.skill_breakdown
    if (sb) {
      const updated = store.skillBreakdowns.map((b) =>
        b.id === sb.id ? { ...b, partner_review_status: 'Approved' as const } : b
      )
      store.setSkillBreakdowns(updated)
    }
    setTimeout(() => setBreakdownNotification(null), 5000)
  }, [store])

  const handleBreakdownRejected = useCallback((event: SkillBreakdownWSEvent) => {
    setBreakdownNotification(`Skill breakdown was rejected: ${event.skill_breakdown?.partner_review_note || 'No reason provided'}`)
    const sb = event.skill_breakdown
    if (sb) {
      const updated = store.skillBreakdowns.map((b) =>
        b.id === sb.id ? { ...b, partner_review_status: 'Rejected' as const } : b
      )
      store.setSkillBreakdowns(updated)
    }
    setTimeout(() => setBreakdownNotification(null), 8000)
  }, [store])

  const handleBreakdownRevisionRequested = useCallback((event: SkillBreakdownWSEvent) => {
    setBreakdownNotification(`Partner requested revisions: ${event.skill_breakdown?.partner_review_note || 'Please revise'}`)
    const sb = event.skill_breakdown
    if (sb) {
      const updated = store.skillBreakdowns.map((b) =>
        b.id === sb.id ? { ...b, partner_review_status: 'Revision Requested' as const } : b
      )
      store.setSkillBreakdowns(updated)
    }
    setTimeout(() => setBreakdownNotification(null), 8000)
  }, [store])

  useSkillBreakdownWebSocket({
    onApproved: handleBreakdownApproved,
    onRejected: handleBreakdownRejected,
    onRevisionRequested: handleBreakdownRevisionRequested,
  })

  const tech = store.technician

  // Loading state
  if (isLoading || !tech) {
    return (
      <div className="space-y-6 max-w-4xl mx-auto">
        <div className="flex items-center gap-3 animate-pulse">
          <div className="h-14 w-14 rounded-full bg-muted" />
          <div className="space-y-2">
            <div className="h-6 w-48 bg-muted rounded" />
            <div className="h-4 w-32 bg-muted rounded" />
          </div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="h-48 bg-muted rounded-lg animate-pulse" />
          <div className="h-48 bg-muted rounded-lg animate-pulse" />
        </div>
        <div className="h-32 bg-muted rounded-lg animate-pulse" />
      </div>
    )
  }

  const activeAssignment = store.getActiveAssignment()
  const completableAssignment = store.getCompletableAssignment()
  const upcomingAssignments = store.getUpcomingAssignments()
  const trainingProgress = store.getTrainingProgress()
  const expiringCerts = store.getExpiringCerts()
  const missingDocs = store.getMissingDocs()

  // Find the assignment for the skill breakdown form
  const breakdownAssignment = store.skillBreakdownAssignmentId
    ? store.assignments.find((a) => a.id === store.skillBreakdownAssignmentId)
    : null

  // Alert count for the badge on docs tab
  const alertCount = expiringCerts.length + missingDocs.length

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Real-time skill breakdown notification toast */}
      {breakdownNotification && (
        <div className="fixed top-4 right-4 z-50 max-w-sm animate-in slide-in-from-top-2 fade-in">
          <div className="rounded-lg border border-primary/30 bg-primary/10 p-4 shadow-lg backdrop-blur-sm">
            <p className="text-sm font-medium text-foreground">{breakdownNotification}</p>
          </div>
        </div>
      )}

      {/* Skill Breakdown Form Overlay */}
      {store.skillBreakdownFormOpen && breakdownAssignment && (
        <SkillBreakdownForm
          assignment={breakdownAssignment}
          onClose={store.closeSkillBreakdownForm}
        />
      )}

      {/* Welcome Header — mobile first */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-4">
        <div className="flex items-center gap-3 flex-1">
          <div className="h-14 w-14 sm:h-16 sm:w-16 rounded-full bg-primary/20 flex items-center justify-center text-primary font-bold text-xl flex-shrink-0">
            {tech.name.split(' ').map(n => n[0]).join('')}
          </div>
          <div>
            <h1 className="text-xl sm:text-2xl font-bold text-foreground tracking-tight">
              Welcome back, {tech.name.split(' ')[0]}
            </h1>
            <p className="text-sm text-muted-foreground">
              {tech.home_base_city} · {tech.career_stage}
            </p>
          </div>
        </div>
        <div className="flex gap-2 self-start sm:self-auto">
          <DeployabilityBadge status={tech.deployability_status} />
        </div>
      </div>

      {/* Deployability Status — compact view for technician */}
      <DeployabilityStatusPanel technicianId={tech.id} compact />

      {/* Your Next Step Card — agent-generated, API-fed with real-time WS updates */}
      <NextStepCard
        technicianId={tech.id}
        fallbackCard={seedNextStepCard}
      />

      {/* Tabbed Portal Content */}
      <Tabs value={portalTab} onValueChange={(v) => setPortalTab(v as any)}>
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="overview">
            <span className="flex items-center gap-1.5">
              <HomeIcon />
              Overview
            </span>
          </TabsTrigger>
          <TabsTrigger value="skills">
            <span className="flex items-center gap-1.5">
              <ChartIcon />
              Skills & Training
            </span>
          </TabsTrigger>
          <TabsTrigger value="docs">
            <span className="flex items-center gap-1.5">
              <FileIcon />
              Certs & Docs
              {alertCount > 0 && (
                <span className="bg-destructive/20 text-destructive text-xs font-bold px-1.5 py-0.5 rounded-full">
                  {alertCount}
                </span>
              )}
            </span>
          </TabsTrigger>
        </TabsList>

        {/* ── Overview Tab ── */}
        <TabsContent value="overview" className="space-y-4 mt-4">
          {/* Grid: Active Assignment + Hours Submission */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Active Assignment */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <span className="text-primary">●</span> Current Assignment
                </CardTitle>
              </CardHeader>
              <CardContent>
                {activeAssignment ? (
                  <ActiveAssignmentCard assignment={activeAssignment} />
                ) : (() => {
                  // Show recently completed assignment needing skill breakdown
                  const recentlyCompleted = store.assignments.find(
                    (a) => a.status === 'Completed' && a.assignment_type === 'active'
                      && !store.skillBreakdowns.some((sb) => sb.assignment_id === a.id)
                  )
                  if (recentlyCompleted) {
                    return <ActiveAssignmentCard assignment={recentlyCompleted} />
                  }
                  return (
                    <div className="text-center py-6">
                      <p className="text-muted-foreground text-sm">No active assignment</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        Available from: {tech.available_from}
                      </p>
                    </div>
                  )
                })()}
              </CardContent>
            </Card>

            {/* Weekly Hours Submission */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center justify-between">
                  <span>Weekly Hours</span>
                  <div className="flex gap-2">
                    {activeAssignment && (
                      <Button
                        size="sm"
                        onClick={() => store.setHoursFormOpen(!store.hoursFormOpen)}
                        className="touch-manipulation"
                      >
                        + Submit Hours
                      </Button>
                    )}
                  </div>
                </CardTitle>
              </CardHeader>
              <CardContent>
                {store.hoursFormOpen && activeAssignment && (
                  <HoursSubmissionForm
                    assignmentId={activeAssignment.id}
                    projectName={activeAssignment.project_name || ''}
                  />
                )}
                <RecentTimesheets timesheets={store.timesheets.slice(0, 4)} />
                {store.timesheets.length > 0 && (
                  <div className="pt-3 text-center">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate('/tech/timesheets')}
                      className="touch-manipulation text-primary"
                    >
                      View All Timesheets ({store.timesheets.length})
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Upcoming Assignments */}
          {upcomingAssignments.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold">Upcoming Assignments</CardTitle>
                <CardDescription>Pre-booked assignments in your pipeline</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {upcomingAssignments.map((a) => (
                    <UpcomingAssignmentRow key={a.id} assignment={a} />
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Badges */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold">Badges & Achievements</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {tech.site_badges.map((badge) => (
                  <Badge key={badge} variant="outline" className="py-1.5 px-3 text-sm">
                    <span className="mr-1">🏢</span> {badge}
                  </Badge>
                ))}
                {tech.milestone_badges.map((badge) => (
                  <Badge key={badge} variant="default" className="py-1.5 px-3 text-sm bg-primary/10 text-primary border-primary/30">
                    <span className="mr-1">🏆</span> {badge}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Skills & Training Tab ── */}
        <TabsContent value="skills" className="space-y-4 mt-4">
          {/* Skills & Training Progress */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold">Skills & Training Progress</CardTitle>
              <CardDescription>Track your skill advancement. Auto-advances at 100hrs (Intermediate) and 300hrs (Advanced).</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {trainingProgress.map((s) => (
                  <div key={s.skill_name} className="space-y-1.5">
                    <div className="flex items-center justify-between text-sm">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-foreground">{s.skill_name}</span>
                        <Badge variant={
                          s.level === 'Advanced' ? 'success' :
                          s.level === 'Intermediate' ? 'warning' : 'secondary'
                        } className="text-xs">
                          {s.level}
                        </Badge>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {s.hours}hrs
                        {s.nextLevel && ` → ${s.nextLevel}`}
                      </span>
                    </div>
                    <Progress
                      value={s.progressPct}
                      className="h-2"
                      indicatorClassName={
                        s.level === 'Advanced' ? 'bg-success' :
                        s.progressPct >= 90 ? 'bg-warning' : 'bg-primary'
                      }
                    />
                    {/* Milestone info text */}
                    {s.nextLevel && s.progressPct >= 80 && (
                      <p className="text-xs text-warning">
                        Almost there! {s.nextLevel === 'Intermediate' ? `${100 - s.hours}hrs` : `${300 - s.hours}hrs`} remaining to {s.nextLevel}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Skill Breakdown History */}
          {store.skillBreakdowns.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold">Skill Breakdown History</CardTitle>
                <CardDescription>Past assignment skill breakdowns and partner review status</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {store.skillBreakdowns.map((sb) => (
                    <div key={sb.id} className="p-3 bg-muted/50 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium text-foreground">
                          Assignment: {sb.assignment_id}
                        </span>
                        <Badge variant={
                          sb.status === 'Approved' ? 'success' :
                          sb.status === 'Rejected' ? 'destructive' : 'secondary'
                        }>
                          {sb.status}
                        </Badge>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(sb.skill_weights || {}).map(([skill, weight]) => (
                          <span key={skill} className="text-xs bg-muted px-2 py-1 rounded">
                            {skill}: {typeof weight === 'number' && weight < 1 ? `${Math.round(weight * 100)}%` : `${weight}hrs`}
                          </span>
                        ))}
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        Submitted: {new Date(sb.submitted_at).toLocaleDateString()}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ── Certs & Docs Tab ── */}
        <TabsContent value="docs" className="space-y-4 mt-4">
          {/* Two-column: Certifications + Documents */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Certifications */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold">Certifications</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {tech.certifications.map((cert) => {
                    const daysLeft = Math.ceil(
                      (new Date(cert.expiry_date).getTime() - Date.now()) / 86400000
                    )
                    return (
                      <div
                        key={cert.cert_name}
                        className="flex items-center justify-between py-2 border-b border-border last:border-0"
                      >
                        <div>
                          <p className="text-sm font-medium text-foreground">{cert.cert_name}</p>
                          <p className="text-xs text-muted-foreground">
                            Expires: {new Date(cert.expiry_date).toLocaleDateString()}
                          </p>
                        </div>
                        <Badge
                          variant={
                            cert.status === 'Active' ? 'success' :
                            cert.status === 'Expiring Soon' ? 'warning' : 'destructive'
                          }
                        >
                          {cert.status === 'Expiring Soon' ? `${daysLeft}d left` : cert.status}
                        </Badge>
                      </div>
                    )
                  })}
                </div>
                {expiringCerts.length > 0 && (
                  <div className="mt-3 p-3 bg-warning/10 border border-warning/30 rounded-lg">
                    <p className="text-xs text-warning font-medium">
                      ⚠ {expiringCerts.length} certification{expiringCerts.length > 1 ? 's' : ''} expiring within 90 days
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Documents */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold">Documents</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {tech.documents.map((doc) => (
                    <div
                      key={doc.doc_type}
                      className="flex items-center justify-between py-2 border-b border-border last:border-0"
                    >
                      <p className="text-sm font-medium text-foreground">{doc.doc_type}</p>
                      <Badge
                        variant={
                          doc.verification_status === 'Verified' ? 'success' :
                          doc.verification_status === 'Expired' ? 'destructive' :
                          doc.verification_status === 'Pending Review' ? 'warning' : 'secondary'
                        }
                      >
                        {doc.verification_status}
                      </Badge>
                    </div>
                  ))}
                </div>
                {missingDocs.length > 0 && (
                  <div className="mt-3 p-3 bg-destructive/10 border border-destructive/30 rounded-lg">
                    <p className="text-xs text-destructive font-medium">
                      ⚠ {missingDocs.length} document{missingDocs.length > 1 ? 's' : ''} need attention
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      {/* Career Passport — compact panel with PDF download, share links, revocation */}
      <CareerPassportPanel
        technicianId={tech.id}
        technicianName={tech.name}
        compact
        role="technician"
      />
    </div>
  )
}

// ============================================================
// Sub-components
// ============================================================

function DeployabilityBadge({ status }: { status: string }) {
  const variants: Record<string, 'success' | 'warning' | 'destructive' | 'secondary' | 'default'> = {
    'Ready Now': 'success',
    'In Training': 'warning',
    'Currently Assigned': 'default',
    'Missing Cert': 'destructive',
    'Missing Docs': 'destructive',
    'Rolling Off Soon': 'warning',
    'Inactive': 'secondary',
  }
  return (
    <Badge variant={variants[status] || 'secondary'} className="text-xs py-1 px-3">
      {status}
    </Badge>
  )
}

function ActiveAssignmentCard({ assignment }: { assignment: Assignment }) {
  const store = useTechPortalStore()
  const [confirmComplete, setConfirmComplete] = useState(false)
  const daysLeft = Math.ceil(
    (new Date(assignment.end_date).getTime() - Date.now()) / 86400000
  )

  const handleMarkComplete = () => {
    if (!confirmComplete) {
      setConfirmComplete(true)
      return
    }
    store.markAssignmentComplete(assignment.id)
    setConfirmComplete(false)
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-semibold text-foreground">{assignment.project_name}</h3>
        <p className="text-sm text-muted-foreground">{assignment.role_name}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-muted-foreground">Rate</span>
          <p className="font-medium text-foreground">${assignment.hourly_rate}/hr</p>
        </div>
        <div>
          <span className="text-muted-foreground">Per Diem</span>
          <p className="font-medium text-foreground">${assignment.per_diem}/day</p>
        </div>
        <div>
          <span className="text-muted-foreground">Start</span>
          <p className="font-medium text-foreground">{formatDate(assignment.start_date)}</p>
        </div>
        <div>
          <span className="text-muted-foreground">End</span>
          <p className="font-medium text-foreground">{formatDate(assignment.end_date)}</p>
        </div>
      </div>
      <div className="flex items-center justify-between text-xs pt-2 border-t border-border">
        <span className="text-muted-foreground">Time remaining</span>
        <Badge variant={daysLeft <= 14 ? 'warning' : 'secondary'}>
          {daysLeft > 0 ? `${daysLeft} days left` : 'Ending soon'}
        </Badge>
      </div>

      {/* Mark Complete + Skill Breakdown trigger */}
      {assignment.status === 'Active' && (
        <div className="pt-2 border-t border-border space-y-2">
          {confirmComplete ? (
            <div className="p-3 bg-warning/10 border border-warning/30 rounded-lg space-y-2">
              <p className="text-sm text-warning font-medium">
                Mark this assignment as complete?
              </p>
              <p className="text-xs text-muted-foreground">
                You'll be asked to submit a skill breakdown detailing the skills you used and hours spent.
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirmComplete(false)}
                  className="touch-manipulation"
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleMarkComplete}
                  disabled={store.skillBreakdownSubmitting}
                  className="touch-manipulation"
                >
                  {store.skillBreakdownSubmitting ? 'Processing...' : 'Confirm Complete'}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={handleMarkComplete}
              className="w-full touch-manipulation"
            >
              Mark Assignment Complete
            </Button>
          )}
          {store.skillBreakdownError && (
            <p className="text-xs text-destructive">{store.skillBreakdownError}</p>
          )}
        </div>
      )}

      {/* Show if assignment was just completed but no breakdown submitted yet */}
      {assignment.status === 'Completed' && (
        <div className="pt-2 border-t border-border">
          {store.skillBreakdowns.some((sb) => sb.assignment_id === assignment.id) ? (
            <div className="flex items-center gap-2 text-sm text-success">
              <span>✓</span>
              <span className="font-medium">Skill breakdown submitted</span>
            </div>
          ) : (
            <Button
              size="sm"
              onClick={() => store.openSkillBreakdownForm(assignment.id)}
              className="w-full touch-manipulation"
            >
              Submit Skill Breakdown
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

function UpcomingAssignmentRow({ assignment }: { assignment: Assignment }) {
  return (
    <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
      <div>
        <p className="font-medium text-sm text-foreground">{assignment.project_name}</p>
        <p className="text-xs text-muted-foreground">
          {assignment.role_name} · {formatDate(assignment.start_date)} — {formatDate(assignment.end_date)}
        </p>
      </div>
      <Badge variant={assignment.status === 'Pending Confirmation' ? 'warning' : 'secondary'}>
        {assignment.status}
      </Badge>
    </div>
  )
}

function HoursSubmissionForm({
  assignmentId,
  projectName,
}: {
  assignmentId: string
  projectName: string
}) {
  const store = useTechPortalStore()

  return (
    <div className="mb-4 p-4 bg-muted/50 rounded-lg border border-border space-y-3">
      <h4 className="text-sm font-medium text-foreground">Submit Hours — {projectName}</h4>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Week Starting</label>
          <Input
            type="date"
            className="touch-manipulation"
            value={store.hoursFormData.week_start}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              store.setHoursFormData({ week_start: e.target.value, assignment_id: assignmentId })
            }
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Total Hours</label>
          <Input
            type="number"
            placeholder="40"
            min={0}
            max={80}
            className="touch-manipulation"
            value={store.hoursFormData.hours || ''}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              store.setHoursFormData({
                hours: parseFloat(e.target.value) || 0,
                assignment_id: assignmentId,
              })
            }
          />
        </div>
      </div>
      <div className="flex gap-2 justify-end">
        <Button variant="ghost" size="sm" onClick={() => store.setHoursFormOpen(false)}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={store.submitHours}
          disabled={!store.hoursFormData.hours || !store.hoursFormData.week_start}
          className="touch-manipulation"
        >
          Submit
        </Button>
      </div>
    </div>
  )
}

function RecentTimesheets({ timesheets }: { timesheets: Timesheet[] }) {
  if (timesheets.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4">
        No timesheets submitted yet
      </p>
    )
  }

  const statusVariants: Record<string, 'success' | 'warning' | 'destructive' | 'secondary'> = {
    Approved: 'success',
    Submitted: 'secondary',
    Flagged: 'destructive',
    Resolved: 'warning',
  }

  return (
    <div className="space-y-2">
      {timesheets.map((ts) => (
        <div
          key={ts.id}
          className="flex items-center justify-between py-2 border-b border-border last:border-0"
        >
          <div>
            <p className="text-sm font-medium text-foreground">
              Week of {formatDate(ts.week_start)}
            </p>
            <p className="text-xs text-muted-foreground">{ts.hours} hours</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={statusVariants[ts.status] || 'secondary'}>{ts.status}</Badge>
          </div>
        </div>
      ))}
      {timesheets.some((ts) => ts.status === 'Flagged') && (
        <div className="p-3 bg-destructive/10 border border-destructive/30 rounded-lg">
          <p className="text-xs text-destructive font-medium">
            ⚠ You have flagged timesheets that need attention
          </p>
          {timesheets
            .filter((ts) => ts.status === 'Flagged')
            .map((ts) => (
              <p key={ts.id} className="text-xs text-muted-foreground mt-1">
                Week of {formatDate(ts.week_start)}: {ts.flag_comment}
              </p>
            ))}
        </div>
      )}
    </div>
  )
}

// ============================================================
// Inline SVG Icons
// ============================================================

function HomeIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8" /><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  )
}

function ChartIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3v16a2 2 0 0 0 2 2h16" /><path d="m19 9-5 5-4-4-3 3" />
    </svg>
  )
}

function FileIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" />
    </svg>
  )
}

// ============================================================
// Helpers
// ============================================================

function formatDate(dateStr: string): string {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}
