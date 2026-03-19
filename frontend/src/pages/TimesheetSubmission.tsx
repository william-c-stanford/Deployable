import { useEffect, useState, useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
// Status filtering uses custom tab buttons below
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Separator } from '@/components/ui/separator'
import { useTechPortalStore } from '@/stores/techPortalStore'
import {
  seedTechnician,
  seedAssignments,
  seedTimesheets,
  seedNextStepCard,
  seedSkillBreakdowns,
} from '@/lib/techPortalSeedData'
import type { Assignment, Timesheet } from '@/types'

// ============================================================
// Timesheet Submission — mobile-first weekly hours entry
// ============================================================

export function TimesheetSubmission() {
  const store = useTechPortalStore()
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [showAllHistory, setShowAllHistory] = useState(false)

  useEffect(() => {
    // Initialize store with seed data if not already loaded
    if (!store.technician) {
      store.setTechnician(seedTechnician)
      store.setAssignments(seedAssignments)
      store.setTimesheets(seedTimesheets)
      store.setNextStepCard(seedNextStepCard)
      store.setSkillBreakdowns(seedSkillBreakdowns)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const tech = store.technician
  if (!tech) return null

  const activeAssignment = store.getActiveAssignment()
  const allAssignments = store.assignments

  // Filter timesheets
  const filteredTimesheets = useMemo(() => {
    let items = [...store.timesheets]
    if (statusFilter !== 'all') {
      items = items.filter((ts) => ts.status === statusFilter)
    }
    items.sort(
      (a, b) =>
        new Date(b.submitted_at).getTime() - new Date(a.submitted_at).getTime()
    )
    return items
  }, [store.timesheets, statusFilter])

  // Stats
  const stats = useMemo(() => {
    const all = store.timesheets
    return {
      total: all.length,
      submitted: all.filter((t) => t.status === 'Submitted').length,
      approved: all.filter((t) => t.status === 'Approved').length,
      flagged: all.filter((t) => t.status === 'Flagged').length,
      totalHours: all
        .filter((t) => t.status === 'Approved')
        .reduce((sum, t) => sum + t.hours, 0),
      pendingHours: all
        .filter((t) => t.status === 'Submitted')
        .reduce((sum, t) => sum + t.hours, 0),
    }
  }, [store.timesheets])

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-foreground tracking-tight">
            Timesheets
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Submit weekly hours and track approval status
          </p>
        </div>
        {activeAssignment && (
          <Button
            size="lg"
            onClick={() => store.setHoursFormOpen(true)}
            className="touch-manipulation w-full sm:w-auto"
          >
            <span className="mr-2">+</span> Submit Hours
          </Button>
        )}
      </div>

      {/* Stats Row — mobile scrollable */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="Pending Review"
          value={stats.submitted}
          hours={stats.pendingHours}
          variant="default"
        />
        <StatCard
          label="Approved"
          value={stats.approved}
          hours={stats.totalHours}
          variant="success"
        />
        <StatCard
          label="Flagged"
          value={stats.flagged}
          variant="destructive"
        />
        <StatCard
          label="Total Entries"
          value={stats.total}
          variant="secondary"
        />
      </div>

      {/* Submission Form — slides open when triggered */}
      {store.hoursFormOpen && (
        <WeeklyHoursEntryForm
          assignments={allAssignments.filter(
            (a) => a.assignment_type === 'active'
          )}
          skills={tech.skills.map((s) => s.skill_name)}
          defaultAssignment={activeAssignment}
          onSubmit={(data) => {
            store.setHoursFormData(data)
            store.submitHours()
          }}
          onCancel={() => store.setHoursFormOpen(false)}
        />
      )}

      {/* Flagged Timesheets Alert */}
      {stats.flagged > 0 && (
        <FlaggedAlert timesheets={store.timesheets.filter((t) => t.status === 'Flagged')} />
      )}

      {/* Submission History */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base font-semibold">
                Submission History
              </CardTitle>
              <CardDescription>
                {filteredTimesheets.length} timesheet
                {filteredTimesheets.length !== 1 ? 's' : ''}
                {statusFilter !== 'all' && ` (${statusFilter})`}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <StatusFilterTabs
                value={statusFilter}
                onChange={setStatusFilter}
                counts={{
                  all: stats.total,
                  Submitted: stats.submitted,
                  Approved: stats.approved,
                  Flagged: stats.flagged,
                }}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {/* Mobile card view */}
          <div className="block sm:hidden space-y-3">
            {(showAllHistory
              ? filteredTimesheets
              : filteredTimesheets.slice(0, 6)
            ).map((ts) => (
              <TimesheetMobileCard key={ts.id} timesheet={ts} />
            ))}
          </div>

          {/* Desktop table view */}
          <div className="hidden sm:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Week</TableHead>
                  <TableHead>Project</TableHead>
                  <TableHead className="text-right">Hours</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Submitted</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(showAllHistory
                  ? filteredTimesheets
                  : filteredTimesheets.slice(0, 8)
                ).map((ts) => (
                  <TimesheetTableRow key={ts.id} timesheet={ts} />
                ))}
              </TableBody>
            </Table>
          </div>

          {filteredTimesheets.length === 0 && (
            <div className="text-center py-8">
              <p className="text-muted-foreground text-sm">
                {statusFilter === 'all'
                  ? 'No timesheets submitted yet'
                  : `No ${statusFilter.toLowerCase()} timesheets`}
              </p>
            </div>
          )}

          {!showAllHistory &&
            filteredTimesheets.length > (typeof window !== 'undefined' && window.innerWidth < 640 ? 6 : 8) && (
              <div className="pt-4 text-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowAllHistory(true)}
                  className="touch-manipulation"
                >
                  View All ({filteredTimesheets.length} entries)
                </Button>
              </div>
            )}
        </CardContent>
      </Card>

      {/* Weekly Summary */}
      <WeeklySummary timesheets={store.timesheets} />
    </div>
  )
}

// ============================================================
// Sub-components
// ============================================================

function StatCard({
  label,
  value,
  hours,
  variant,
}: {
  label: string
  value: number
  hours?: number
  variant: 'default' | 'success' | 'destructive' | 'secondary'
}) {
  const borderStyles: Record<string, string> = {
    default: 'border-primary/30',
    success: 'border-green-500/30',
    destructive: 'border-destructive/30',
    secondary: 'border-border',
  }
  const numberStyles: Record<string, string> = {
    default: 'text-primary',
    success: 'text-green-500',
    destructive: 'text-destructive',
    secondary: 'text-foreground',
  }

  return (
    <Card className={borderStyles[variant]}>
      <CardContent className="p-4">
        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
          {label}
        </p>
        <p className={`text-2xl font-bold mt-1 ${numberStyles[variant]}`}>
          {value}
        </p>
        {hours !== undefined && (
          <p className="text-xs text-muted-foreground mt-0.5">
            {hours.toLocaleString()} hrs
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function StatusFilterTabs({
  value,
  onChange,
  counts,
}: {
  value: string
  onChange: (val: string) => void
  counts: Record<string, number>
}) {
  const filters = [
    { key: 'all', label: 'All' },
    { key: 'Submitted', label: 'Pending' },
    { key: 'Approved', label: 'Approved' },
    { key: 'Flagged', label: 'Flagged' },
  ]

  return (
    <div className="flex gap-1 bg-muted p-1 rounded-lg overflow-x-auto">
      {filters.map((f) => (
        <button
          key={f.key}
          onClick={() => onChange(f.key)}
          className={`px-3 py-1.5 text-xs font-medium rounded-md whitespace-nowrap transition-colors touch-manipulation ${
            value === f.key
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          }`}
        >
          {f.label}
          {counts[f.key] > 0 && (
            <span className="ml-1.5 text-xs opacity-70">
              {counts[f.key]}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

function WeeklyHoursEntryForm({
  assignments,
  skills,
  defaultAssignment,
  onSubmit,
  onCancel,
}: {
  assignments: Assignment[]
  skills: string[]
  defaultAssignment?: Assignment
  onSubmit: (data: {
    assignment_id: string
    hours: number
    week_start: string
    skill_name?: string
  }) => void
  onCancel: () => void
}) {
  const [assignmentId, setAssignmentId] = useState(
    defaultAssignment?.id || ''
  )
  const [weekStart, setWeekStart] = useState(getDefaultWeekStart())
  const [hours, setHours] = useState<string>('')
  const [skillName, setSkillName] = useState<string>('')
  const [errors, setErrors] = useState<Record<string, string>>({})

  const selectedAssignment = assignments.find((a) => a.id === assignmentId)

  function validate(): boolean {
    const newErrors: Record<string, string> = {}
    if (!assignmentId) newErrors.assignment = 'Select an assignment'
    if (!weekStart) newErrors.week_start = 'Select a week'
    const h = parseFloat(hours)
    if (!hours || isNaN(h) || h <= 0) {
      newErrors.hours = 'Enter valid hours'
    } else if (h > 80) {
      newErrors.hours = 'Cannot exceed 80 hours per week'
    }
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  function handleSubmit() {
    if (!validate()) return
    onSubmit({
      assignment_id: assignmentId,
      hours: parseFloat(hours),
      week_start: weekStart,
      skill_name: skillName || undefined,
    })
    // Reset form
    setHours('')
    setSkillName('')
  }

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-primary animate-pulse" />
          Submit Weekly Hours
        </CardTitle>
        <CardDescription>
          Enter your hours for the week. Hours will be submitted for ops review.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Assignment Selection */}
        <div className="space-y-2">
          <Label htmlFor="assignment" className="text-sm font-medium">
            Assignment
          </Label>
          {assignments.length === 1 ? (
            <div className="flex items-center gap-2 p-3 bg-muted/50 rounded-md">
              <span className="text-primary font-medium text-sm">
                {assignments[0].project_name}
              </span>
              <Badge variant="secondary" className="text-xs">
                {assignments[0].role_name}
              </Badge>
            </div>
          ) : (
            <Select value={assignmentId} onValueChange={setAssignmentId}>
              <SelectTrigger
                id="assignment"
                className={`touch-manipulation ${
                  errors.assignment ? 'border-destructive' : ''
                }`}
              >
                <SelectValue placeholder="Select assignment" />
              </SelectTrigger>
              <SelectContent>
                {assignments.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.project_name} — {a.role_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {errors.assignment && (
            <p className="text-xs text-destructive">{errors.assignment}</p>
          )}
        </div>

        {/* Assignment info strip */}
        {selectedAssignment && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground bg-muted/50 p-3 rounded-md">
            <span>
              Rate: <strong className="text-foreground">${selectedAssignment.hourly_rate}/hr</strong>
            </span>
            <span>
              Per Diem: <strong className="text-foreground">${selectedAssignment.per_diem}/day</strong>
            </span>
            <span>
              Period: {formatDate(selectedAssignment.start_date)} —{' '}
              {formatDate(selectedAssignment.end_date)}
            </span>
          </div>
        )}

        {/* Week + Hours Row */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="week_start" className="text-sm font-medium">
              Week Starting (Monday)
            </Label>
            <Input
              id="week_start"
              type="date"
              className={`touch-manipulation h-12 sm:h-10 ${
                errors.week_start ? 'border-destructive' : ''
              }`}
              value={weekStart}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                setWeekStart(e.target.value)
              }
            />
            {errors.week_start && (
              <p className="text-xs text-destructive">{errors.week_start}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="hours" className="text-sm font-medium">
              Total Hours
            </Label>
            <Input
              id="hours"
              type="number"
              placeholder="e.g. 40"
              min={0}
              max={80}
              step={0.5}
              className={`touch-manipulation h-12 sm:h-10 text-lg font-semibold ${
                errors.hours ? 'border-destructive' : ''
              }`}
              value={hours}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                setHours(e.target.value)
              }
            />
            {errors.hours && (
              <p className="text-xs text-destructive">{errors.hours}</p>
            )}
            {hours && !errors.hours && selectedAssignment && (
              <p className="text-xs text-muted-foreground">
                Est. earnings: $
                {(
                  parseFloat(hours) * selectedAssignment.hourly_rate
                ).toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </p>
            )}
          </div>
        </div>

        {/* Skill Attribution (optional) */}
        <div className="space-y-2">
          <Label htmlFor="skill" className="text-sm font-medium">
            Skill Attribution{' '}
            <span className="text-muted-foreground font-normal">(optional)</span>
          </Label>
          <Select value={skillName} onValueChange={setSkillName}>
            <SelectTrigger id="skill" className="touch-manipulation">
              <SelectValue placeholder="Attribute hours to a skill for training credit" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">None</SelectItem>
              {skills.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            Attributed hours count toward skill advancement when approved
          </p>
        </div>

        <Separator />

        {/* Submit Actions */}
        <div className="flex flex-col-reverse sm:flex-row gap-2 sm:justify-end">
          <Button
            variant="ghost"
            onClick={onCancel}
            className="touch-manipulation h-12 sm:h-10"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!assignmentId || !hours}
            className="touch-manipulation h-12 sm:h-10"
          >
            Submit Hours for Review
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function FlaggedAlert({ timesheets }: { timesheets: Timesheet[] }) {
  return (
    <Card className="border-destructive/30 bg-destructive/5">
      <CardContent className="py-4">
        <div className="flex items-start gap-3">
          <div className="text-xl flex-shrink-0">&#9888;&#65039;</div>
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-destructive text-sm">
              {timesheets.length} Flagged Timesheet
              {timesheets.length > 1 ? 's' : ''} — Action Required
            </h3>
            <div className="mt-2 space-y-2">
              {timesheets.map((ts) => (
                <div
                  key={ts.id}
                  className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 p-2 bg-background/50 rounded-md text-sm"
                >
                  <span className="font-medium text-foreground">
                    Week of {formatDate(ts.week_start)}
                  </span>
                  <span className="text-muted-foreground text-xs sm:text-sm">
                    {ts.hours} hrs
                  </span>
                  {ts.flag_comment && (
                    <span className="text-destructive text-xs italic">
                      {ts.flag_comment}
                    </span>
                  )}
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              Contact your ops coordinator to resolve flagged timesheets
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function TimesheetMobileCard({ timesheet: ts }: { timesheet: Timesheet }) {
  return (
    <div className="p-4 bg-muted/30 border border-border rounded-lg space-y-2 touch-manipulation">
      <div className="flex items-center justify-between">
        <span className="font-medium text-sm text-foreground">
          Week of {formatDate(ts.week_start)}
        </span>
        <TimesheetStatusBadge status={ts.status} />
      </div>
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">
          {ts.project_name || 'Assignment'}
        </span>
        <span className="font-bold text-foreground text-lg">{ts.hours}h</span>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Submitted {formatDateTime(ts.submitted_at)}</span>
      </div>
      {ts.flag_comment && (
        <div className="p-2 bg-destructive/10 border border-destructive/20 rounded text-xs text-destructive">
          {ts.flag_comment}
        </div>
      )}
    </div>
  )
}

function TimesheetTableRow({ timesheet: ts }: { timesheet: Timesheet }) {
  return (
    <TableRow>
      <TableCell className="font-medium">
        {formatDate(ts.week_start)}
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        {ts.project_name || '—'}
      </TableCell>
      <TableCell className="text-right font-semibold">{ts.hours}h</TableCell>
      <TableCell>
        <TimesheetStatusBadge status={ts.status} />
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">
        {formatDateTime(ts.submitted_at)}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate">
        {ts.flag_comment || '—'}
      </TableCell>
    </TableRow>
  )
}

function TimesheetStatusBadge({ status }: { status: string }) {
  const config: Record<
    string,
    {
      variant: 'success' | 'warning' | 'destructive' | 'secondary' | 'default'
      icon: string
      label: string
    }
  > = {
    Submitted: {
      variant: 'secondary',
      icon: '\u23F3',
      label: 'Pending',
    },
    Approved: {
      variant: 'success',
      icon: '\u2713',
      label: 'Approved',
    },
    Flagged: {
      variant: 'destructive',
      icon: '\u26A0',
      label: 'Flagged',
    },
    Resolved: {
      variant: 'warning',
      icon: '\u2714',
      label: 'Resolved',
    },
  }

  const c = config[status] || {
    variant: 'secondary' as const,
    icon: '',
    label: status,
  }

  return (
    <Badge variant={c.variant} className="text-xs gap-1">
      <span>{c.icon}</span>
      {c.label}
    </Badge>
  )
}

function WeeklySummary({ timesheets }: { timesheets: Timesheet[] }) {
  // Group by week
  const weeklyData = useMemo(() => {
    const grouped = new Map<
      string,
      { week: string; hours: number; status: string }
    >()
    for (const ts of timesheets) {
      const key = ts.week_start
      const existing = grouped.get(key)
      if (existing) {
        existing.hours += ts.hours
      } else {
        grouped.set(key, {
          week: ts.week_start,
          hours: ts.hours,
          status: ts.status,
        })
      }
    }
    return Array.from(grouped.values()).sort(
      (a, b) => new Date(b.week).getTime() - new Date(a.week).getTime()
    )
  }, [timesheets])

  if (weeklyData.length === 0) return null

  const maxHours = Math.max(...weeklyData.map((w) => w.hours), 40)

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">
          Weekly Hours Overview
        </CardTitle>
        <CardDescription>
          Visual summary of your recent weekly hours
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {weeklyData.slice(0, 8).map((w) => (
            <div key={w.week} className="space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">
                  {formatDate(w.week)}
                </span>
                <div className="flex items-center gap-2">
                  <span className="font-medium text-foreground">
                    {w.hours}h
                  </span>
                  <TimesheetStatusBadge status={w.status} />
                </div>
              </div>
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    w.status === 'Approved'
                      ? 'bg-green-500'
                      : w.status === 'Flagged'
                        ? 'bg-destructive'
                        : 'bg-primary'
                  }`}
                  style={{
                    width: `${Math.min(100, (w.hours / maxHours) * 100)}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
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

function formatDateTime(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function getDefaultWeekStart(): string {
  const today = new Date()
  const day = today.getDay()
  // Roll back to Monday
  const diff = day === 0 ? 6 : day - 1
  const monday = new Date(today)
  monday.setDate(today.getDate() - diff)
  return monday.toISOString().split('T')[0]
}
