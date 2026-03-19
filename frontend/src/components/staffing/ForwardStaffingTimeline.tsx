import { useMemo, useState, useRef, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn, formatDate } from '@/lib/utils'
import {
  Calendar,
  ChevronLeft,
  ChevronRight,
  AlertTriangle,
  Clock,
  CheckCircle2,
  User,
  Layers,
  ZoomIn,
  ZoomOut,
  Filter,
  ArrowRight,
} from 'lucide-react'
import type { Assignment, Project } from '@/types'

// ─── Types ──────────────────────────────────────────────────────────────────

export interface TimelineAssignment {
  id: string
  technician_id: string
  technician_name: string
  project_id: string
  project_name: string
  role_name: string
  start_date: string
  end_date: string
  assignment_type: 'active' | 'pre-booked'
  status: 'Pending Confirmation' | 'Active' | 'Completed' | 'Cancelled'
  partner_confirmed?: boolean
  hourly_rate?: number
  per_diem?: number
}

export interface TimelineTechnicianRow {
  technician_id: string
  technician_name: string
  assignments: TimelineAssignment[]
  gaps: TimelineGap[]
  utilization: number // percentage 0-100
}

export interface TimelineGap {
  start_date: string
  end_date: string
  duration_days: number
}

type ZoomLevel = '30d' | '60d' | '90d'

// ─── Utility Functions ──────────────────────────────────────────────────────

function daysBetween(a: Date, b: Date): number {
  return Math.ceil((b.getTime() - a.getTime()) / (1000 * 60 * 60 * 24))
}

function addDays(date: Date, days: number): Date {
  const d = new Date(date)
  d.setDate(d.getDate() + days)
  return d
}

function parseDate(s: string): Date {
  return new Date(s + 'T00:00:00')
}

function formatShortDate(d: Date): string {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// ─── Build timeline data from projects ──────────────────────────────────────

export function buildTimelineData(
  projects: Project[],
  windowStart: Date,
  windowEnd: Date
): TimelineTechnicianRow[] {
  // Collect all assignments across projects
  const techMap = new Map<string, TimelineAssignment[]>()

  for (const project of projects) {
    for (const assignment of project.assignments) {
      if (assignment.status === 'Cancelled') continue

      const asgn: TimelineAssignment = {
        id: assignment.id,
        technician_id: assignment.technician_id,
        technician_name: assignment.technician_name,
        project_id: project.id,
        project_name: project.name,
        role_name: assignment.role_name,
        start_date: assignment.start_date,
        end_date: assignment.end_date,
        assignment_type: assignment.assignment_type,
        status: assignment.status,
        partner_confirmed: assignment.partner_confirmed,
        hourly_rate: assignment.hourly_rate,
        per_diem: assignment.per_diem,
      }

      const existing = techMap.get(assignment.technician_id) || []
      existing.push(asgn)
      techMap.set(assignment.technician_id, existing)
    }
  }

  // Build rows
  const rows: TimelineTechnicianRow[] = []
  const windowDays = daysBetween(windowStart, windowEnd)

  for (const [techId, assignments] of techMap) {
    // Sort by start date
    const sorted = [...assignments].sort(
      (a, b) => parseDate(a.start_date).getTime() - parseDate(b.start_date).getTime()
    )

    // Find gaps between assignments within the window
    const gaps: TimelineGap[] = []
    for (let i = 0; i < sorted.length - 1; i++) {
      const currentEnd = parseDate(sorted[i].end_date)
      const nextStart = parseDate(sorted[i + 1].start_date)
      const gapDays = daysBetween(currentEnd, nextStart)

      if (gapDays > 1) {
        const gapStart = addDays(currentEnd, 1)
        const gapEnd = addDays(nextStart, -1)
        // Only include gaps that overlap with our window
        if (gapEnd >= windowStart && gapStart <= windowEnd) {
          gaps.push({
            start_date: gapStart.toISOString().split('T')[0],
            end_date: gapEnd.toISOString().split('T')[0],
            duration_days: gapDays - 1,
          })
        }
      }
    }

    // Check for gap before first assignment
    if (sorted.length > 0) {
      const firstStart = parseDate(sorted[0].start_date)
      if (firstStart > windowStart) {
        const gapDays = daysBetween(windowStart, firstStart)
        if (gapDays > 0) {
          gaps.unshift({
            start_date: windowStart.toISOString().split('T')[0],
            end_date: addDays(firstStart, -1).toISOString().split('T')[0],
            duration_days: gapDays,
          })
        }
      }
    }

    // Check for gap after last assignment
    if (sorted.length > 0) {
      const lastEnd = parseDate(sorted[sorted.length - 1].end_date)
      if (lastEnd < windowEnd) {
        const gapDays = daysBetween(lastEnd, windowEnd)
        if (gapDays > 1) {
          gaps.push({
            start_date: addDays(lastEnd, 1).toISOString().split('T')[0],
            end_date: windowEnd.toISOString().split('T')[0],
            duration_days: gapDays - 1,
          })
        }
      }
    }

    // Calculate utilization
    let assignedDays = 0
    for (const a of sorted) {
      const aStart = parseDate(a.start_date) < windowStart ? windowStart : parseDate(a.start_date)
      const aEnd = parseDate(a.end_date) > windowEnd ? windowEnd : parseDate(a.end_date)
      if (aEnd > aStart) {
        assignedDays += daysBetween(aStart, aEnd)
      }
    }
    const utilization = windowDays > 0 ? Math.round((assignedDays / windowDays) * 100) : 0

    rows.push({
      technician_id: techId,
      technician_name: sorted[0]?.technician_name || 'Unknown',
      assignments: sorted,
      gaps,
      utilization: Math.min(100, utilization),
    })
  }

  // Sort by utilization descending
  rows.sort((a, b) => b.utilization - a.utilization)
  return rows
}

// ─── Color utilities ────────────────────────────────────────────────────────

function getAssignmentColor(assignment: TimelineAssignment): {
  bg: string
  border: string
  text: string
  label: string
} {
  if (assignment.status === 'Completed') {
    return {
      bg: 'bg-zinc-700/40',
      border: 'border-zinc-600',
      text: 'text-zinc-400',
      label: 'Completed',
    }
  }
  if (assignment.status === 'Pending Confirmation') {
    return {
      bg: 'bg-amber-500/15',
      border: 'border-amber-500/50',
      text: 'text-amber-400',
      label: 'Pending Confirmation',
    }
  }
  if (assignment.assignment_type === 'pre-booked') {
    return {
      bg: 'bg-blue-500/15',
      border: 'border-blue-500/50',
      text: 'text-blue-400',
      label: 'Pre-Booked',
    }
  }
  // Active
  return {
    bg: 'bg-emerald-500/15',
    border: 'border-emerald-500/50',
    text: 'text-emerald-400',
    label: 'Active',
  }
}

function getUtilizationColor(util: number): string {
  if (util >= 80) return 'text-emerald-400'
  if (util >= 50) return 'text-blue-400'
  if (util >= 25) return 'text-amber-400'
  return 'text-red-400'
}

function getUtilizationBarColor(util: number): string {
  if (util >= 80) return 'bg-emerald-500'
  if (util >= 50) return 'bg-blue-500'
  if (util >= 25) return 'bg-amber-500'
  return 'bg-red-500'
}

// ─── Today marker ───────────────────────────────────────────────────────────

function TodayMarker({ leftPercent }: { leftPercent: number }) {
  if (leftPercent < 0 || leftPercent > 100) return null
  return (
    <div
      className="absolute top-0 bottom-0 z-20 pointer-events-none"
      style={{ left: `${leftPercent}%` }}
    >
      <div className="w-px h-full bg-red-500/60" />
      <div className="absolute -top-1 -translate-x-1/2 px-1.5 py-0.5 bg-red-500 text-white text-[9px] font-bold rounded whitespace-nowrap">
        TODAY
      </div>
    </div>
  )
}

// ─── Month headers ──────────────────────────────────────────────────────────

function MonthHeaders({ windowStart, totalDays }: { windowStart: Date; totalDays: number }) {
  const months: { label: string; leftPercent: number; widthPercent: number }[] = []

  let current = new Date(windowStart)
  while (current < addDays(windowStart, totalDays)) {
    const monthStart = new Date(current.getFullYear(), current.getMonth(), 1)
    const monthEnd = new Date(current.getFullYear(), current.getMonth() + 1, 0)
    const label = current.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })

    const effectiveStart = monthStart < windowStart ? windowStart : monthStart
    const effectiveEnd = monthEnd > addDays(windowStart, totalDays) ? addDays(windowStart, totalDays) : monthEnd

    const leftPercent = (daysBetween(windowStart, effectiveStart) / totalDays) * 100
    const widthPercent = (daysBetween(effectiveStart, effectiveEnd) / totalDays) * 100

    months.push({ label, leftPercent, widthPercent })

    // Move to next month
    current = new Date(current.getFullYear(), current.getMonth() + 1, 1)
  }

  return (
    <div className="relative h-6 border-b border-border/50">
      {months.map((m, i) => (
        <div
          key={i}
          className="absolute top-0 h-full flex items-center border-r border-border/30"
          style={{ left: `${m.leftPercent}%`, width: `${m.widthPercent}%` }}
        >
          <span className="text-[10px] font-medium text-muted-foreground px-1.5 truncate">
            {m.label}
          </span>
        </div>
      ))}
    </div>
  )
}

// ─── Week grid lines ────────────────────────────────────────────────────────

function WeekGridLines({ windowStart, totalDays }: { windowStart: Date; totalDays: number }) {
  const lines: number[] = []
  // Find first Monday after windowStart
  let d = new Date(windowStart)
  const dayOfWeek = d.getDay()
  const daysToMonday = dayOfWeek === 0 ? 1 : dayOfWeek === 1 ? 0 : 8 - dayOfWeek
  d = addDays(d, daysToMonday)

  while (d < addDays(windowStart, totalDays)) {
    const pct = (daysBetween(windowStart, d) / totalDays) * 100
    lines.push(pct)
    d = addDays(d, 7)
  }

  return (
    <>
      {lines.map((pct, i) => (
        <div
          key={i}
          className="absolute top-0 bottom-0 w-px bg-border/20 pointer-events-none"
          style={{ left: `${pct}%` }}
        />
      ))}
    </>
  )
}

// ─── Assignment Bar ─────────────────────────────────────────────────────────

function AssignmentBar({
  assignment,
  windowStart,
  totalDays,
  onSelect,
}: {
  assignment: TimelineAssignment
  windowStart: Date
  totalDays: number
  onSelect?: (assignment: TimelineAssignment) => void
}) {
  const aStart = parseDate(assignment.start_date)
  const aEnd = parseDate(assignment.end_date)

  const effectiveStart = aStart < windowStart ? windowStart : aStart
  const effectiveEnd = aEnd > addDays(windowStart, totalDays) ? addDays(windowStart, totalDays) : aEnd

  if (effectiveEnd <= windowStart || effectiveStart >= addDays(windowStart, totalDays)) {
    return null // Out of window
  }

  const leftPercent = Math.max(0, (daysBetween(windowStart, effectiveStart) / totalDays) * 100)
  const widthPercent = Math.max(1, (daysBetween(effectiveStart, effectiveEnd) / totalDays) * 100)

  const colors = getAssignmentColor(assignment)
  const durationDays = daysBetween(aStart, aEnd)

  const isNarrow = widthPercent < 8

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            className={cn(
              'absolute top-1 h-8 rounded-md border cursor-pointer',
              'transition-all duration-150 hover:brightness-125 hover:shadow-lg hover:z-10',
              'flex items-center gap-1 px-1.5 overflow-hidden',
              colors.bg,
              colors.border
            )}
            style={{
              left: `${leftPercent}%`,
              width: `${widthPercent}%`,
              minWidth: '8px',
            }}
            onClick={() => onSelect?.(assignment)}
          >
            {!isNarrow && (
              <>
                {assignment.status === 'Active' && (
                  <CheckCircle2 className={cn('h-3 w-3 shrink-0', colors.text)} />
                )}
                {assignment.status === 'Pending Confirmation' && (
                  <Clock className={cn('h-3 w-3 shrink-0', colors.text)} />
                )}
                {assignment.assignment_type === 'pre-booked' && assignment.status !== 'Pending Confirmation' && (
                  <Layers className={cn('h-3 w-3 shrink-0', colors.text)} />
                )}
                <span className={cn('text-[10px] font-medium truncate', colors.text)}>
                  {assignment.project_name.split(' - ')[0]}
                </span>
              </>
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <div className="space-y-1.5">
            <div className="font-semibold text-sm">{assignment.project_name}</div>
            <div className="text-xs text-muted-foreground">{assignment.role_name}</div>
            <div className="flex items-center gap-2 text-xs">
              <Calendar className="h-3 w-3" />
              <span>
                {formatShortDate(aStart)} → {formatShortDate(aEnd)}
              </span>
              <span className="text-muted-foreground">({durationDays}d)</span>
            </div>
            <div className="flex items-center gap-2">
              <Badge
                className={cn(
                  'text-[10px]',
                  assignment.status === 'Active' && 'bg-emerald-600',
                  assignment.status === 'Pending Confirmation' && 'bg-amber-500',
                  assignment.assignment_type === 'pre-booked' && assignment.status !== 'Pending Confirmation' && 'bg-blue-600'
                )}
              >
                {colors.label}
              </Badge>
              {assignment.partner_confirmed && (
                <span className="text-[10px] text-emerald-400 flex items-center gap-0.5">
                  <CheckCircle2 className="h-3 w-3" /> Partner OK
                </span>
              )}
            </div>
            {(assignment.hourly_rate || assignment.per_diem) && (
              <div className="text-[10px] text-muted-foreground">
                {assignment.hourly_rate && `$${assignment.hourly_rate}/hr`}
                {assignment.hourly_rate && assignment.per_diem && ' · '}
                {assignment.per_diem && `$${assignment.per_diem} per diem`}
              </div>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// ─── Gap Indicator ──────────────────────────────────────────────────────────

function GapIndicator({
  gap,
  windowStart,
  totalDays,
}: {
  gap: TimelineGap
  windowStart: Date
  totalDays: number
}) {
  const gStart = parseDate(gap.start_date)
  const gEnd = parseDate(gap.end_date)

  const effectiveStart = gStart < windowStart ? windowStart : gStart
  const effectiveEnd = gEnd > addDays(windowStart, totalDays) ? addDays(windowStart, totalDays) : gEnd

  if (effectiveEnd <= windowStart || effectiveStart >= addDays(windowStart, totalDays)) {
    return null
  }

  const leftPercent = Math.max(0, (daysBetween(windowStart, effectiveStart) / totalDays) * 100)
  const widthPercent = Math.max(0.5, (daysBetween(effectiveStart, effectiveEnd) / totalDays) * 100)

  // Only show meaningful gaps (> 3 days)
  if (gap.duration_days < 3) return null

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className="absolute top-1 h-8 rounded-md border border-dashed border-red-500/30 bg-red-500/5 flex items-center justify-center cursor-help"
            style={{
              left: `${leftPercent}%`,
              width: `${widthPercent}%`,
              minWidth: '4px',
            }}
          >
            {widthPercent > 4 && (
              <span className="text-[9px] font-medium text-red-400/70">
                {gap.duration_days}d
              </span>
            )}
          </div>
        </TooltipTrigger>
        <TooltipContent side="top">
          <div className="space-y-1">
            <div className="font-semibold text-sm flex items-center gap-1.5 text-red-400">
              <AlertTriangle className="h-3 w-3" />
              Availability Gap
            </div>
            <div className="text-xs">
              {formatShortDate(gStart)} → {formatShortDate(gEnd)}
            </div>
            <div className="text-xs text-muted-foreground">
              {gap.duration_days} days unassigned
            </div>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// ─── Transition Arrow (between assignments) ─────────────────────────────────

function TransitionIndicator({
  fromEnd,
  toStart,
  windowStart,
  totalDays,
}: {
  fromEnd: string
  toStart: string
  windowStart: Date
  totalDays: number
}) {
  const endDate = parseDate(fromEnd)
  const startDate = parseDate(toStart)
  const gapDays = daysBetween(endDate, startDate)

  // Only show for back-to-back or near transitions (≤3 days gap)
  if (gapDays > 3 || gapDays < 0) return null

  const midPoint = addDays(endDate, Math.floor(gapDays / 2))
  const leftPercent = (daysBetween(windowStart, midPoint) / totalDays) * 100

  if (leftPercent < 0 || leftPercent > 100) return null

  return (
    <div
      className="absolute top-2 z-10 pointer-events-none"
      style={{ left: `${leftPercent}%`, transform: 'translateX(-50%)' }}
    >
      <div className="flex items-center justify-center w-5 h-5 rounded-full bg-zinc-800 border border-zinc-600">
        <ArrowRight className="h-3 w-3 text-zinc-400" />
      </div>
    </div>
  )
}

// ─── Technician Row ─────────────────────────────────────────────────────────

function TechnicianTimelineRow({
  row,
  windowStart,
  totalDays,
  onSelectAssignment,
}: {
  row: TimelineTechnicianRow
  windowStart: Date
  totalDays: number
  onSelectAssignment?: (assignment: TimelineAssignment) => void
}) {
  return (
    <div className="flex border-b border-border/30 hover:bg-muted/20 transition-colors group">
      {/* Left: Technician info */}
      <div className="w-48 md:w-56 shrink-0 p-2 pr-3 flex items-center gap-2 border-r border-border/30">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-muted text-xs font-bold shrink-0">
          {row.technician_name.split(' ').map(n => n[0]).join('')}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium truncate">{row.technician_name}</div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className="flex items-center gap-0.5">
              <div
                className={cn(
                  'h-1.5 rounded-full transition-all',
                  getUtilizationBarColor(row.utilization)
                )}
                style={{ width: `${Math.max(4, row.utilization * 0.4)}px` }}
              />
              <span className={cn('text-[10px] font-medium', getUtilizationColor(row.utilization))}>
                {row.utilization}%
              </span>
            </div>
            {row.gaps.filter(g => g.duration_days >= 3).length > 0 && (
              <span className="text-[10px] text-red-400 flex items-center gap-0.5">
                <AlertTriangle className="h-2.5 w-2.5" />
                {row.gaps.filter(g => g.duration_days >= 3).length} gap{row.gaps.filter(g => g.duration_days >= 3).length > 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Right: Timeline bars */}
      <div className="flex-1 relative h-10">
        {/* Gaps */}
        {row.gaps.map((gap, i) => (
          <GapIndicator
            key={`gap-${i}`}
            gap={gap}
            windowStart={windowStart}
            totalDays={totalDays}
          />
        ))}

        {/* Transition arrows */}
        {row.assignments.map((a, i) => {
          if (i < row.assignments.length - 1) {
            return (
              <TransitionIndicator
                key={`trans-${i}`}
                fromEnd={a.end_date}
                toStart={row.assignments[i + 1].start_date}
                windowStart={windowStart}
                totalDays={totalDays}
              />
            )
          }
          return null
        })}

        {/* Assignment bars */}
        {row.assignments.map((assignment) => (
          <AssignmentBar
            key={assignment.id}
            assignment={assignment}
            windowStart={windowStart}
            totalDays={totalDays}
            onSelect={onSelectAssignment}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Legend ──────────────────────────────────────────────────────────────────

function TimelineLegend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px]">
      <div className="flex items-center gap-1.5">
        <div className="w-3 h-3 rounded-sm bg-emerald-500/20 border border-emerald-500/50" />
        <span className="text-muted-foreground">Active</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="w-3 h-3 rounded-sm bg-blue-500/20 border border-blue-500/50" />
        <span className="text-muted-foreground">Pre-Booked</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="w-3 h-3 rounded-sm bg-amber-500/20 border border-amber-500/50" />
        <span className="text-muted-foreground">Pending Confirmation</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="w-3 h-3 rounded-sm bg-zinc-700/40 border border-zinc-600" />
        <span className="text-muted-foreground">Completed</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="w-3 h-3 rounded-sm bg-red-500/5 border border-dashed border-red-500/30" />
        <span className="text-muted-foreground">Gap</span>
      </div>
      <div className="flex items-center gap-1.5">
        <div className="w-px h-3 bg-red-500/60" />
        <span className="text-muted-foreground">Today</span>
      </div>
    </div>
  )
}

// ─── Summary Stats ──────────────────────────────────────────────────────────

function TimelineSummary({ rows }: { rows: TimelineTechnicianRow[] }) {
  const stats = useMemo(() => {
    const totalTechs = rows.length
    const avgUtil = totalTechs > 0 ? Math.round(rows.reduce((s, r) => s + r.utilization, 0) / totalTechs) : 0
    const withGaps = rows.filter(r => r.gaps.some(g => g.duration_days >= 3)).length
    const fullyBooked = rows.filter(r => r.utilization >= 90).length
    const totalAssignments = rows.reduce((s, r) => s + r.assignments.length, 0)
    const pendingConfirmation = rows.reduce(
      (s, r) => s + r.assignments.filter(a => a.status === 'Pending Confirmation').length,
      0
    )

    return { totalTechs, avgUtil, withGaps, fullyBooked, totalAssignments, pendingConfirmation }
  }, [rows])

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <div className="flex flex-col items-center p-2 rounded-lg bg-muted/30">
        <span className="text-lg font-bold">{stats.totalTechs}</span>
        <span className="text-[10px] text-muted-foreground">Technicians</span>
      </div>
      <div className="flex flex-col items-center p-2 rounded-lg bg-muted/30">
        <span className={cn('text-lg font-bold', getUtilizationColor(stats.avgUtil))}>
          {stats.avgUtil}%
        </span>
        <span className="text-[10px] text-muted-foreground">Avg Utilization</span>
      </div>
      <div className="flex flex-col items-center p-2 rounded-lg bg-muted/30">
        <span className="text-lg font-bold text-emerald-400">{stats.fullyBooked}</span>
        <span className="text-[10px] text-muted-foreground">Fully Booked</span>
      </div>
      <div className="flex flex-col items-center p-2 rounded-lg bg-muted/30">
        <span className={cn('text-lg font-bold', stats.withGaps > 0 ? 'text-red-400' : 'text-emerald-400')}>
          {stats.withGaps}
        </span>
        <span className="text-[10px] text-muted-foreground">With Gaps</span>
      </div>
      <div className="flex flex-col items-center p-2 rounded-lg bg-muted/30">
        <span className="text-lg font-bold">{stats.totalAssignments}</span>
        <span className="text-[10px] text-muted-foreground">Assignments</span>
      </div>
      <div className="flex flex-col items-center p-2 rounded-lg bg-muted/30">
        <span className={cn('text-lg font-bold', stats.pendingConfirmation > 0 ? 'text-amber-400' : 'text-emerald-400')}>
          {stats.pendingConfirmation}
        </span>
        <span className="text-[10px] text-muted-foreground">Pending</span>
      </div>
    </div>
  )
}

// ─── Selected Assignment Detail Panel ───────────────────────────────────────

function AssignmentDetailPanel({
  assignment,
  onClose,
}: {
  assignment: TimelineAssignment
  onClose: () => void
}) {
  const colors = getAssignmentColor(assignment)
  const startDate = parseDate(assignment.start_date)
  const endDate = parseDate(assignment.end_date)
  const durationDays = daysBetween(startDate, endDate)

  return (
    <Card className="border-primary/20">
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h4 className="font-semibold text-sm">{assignment.technician_name}</h4>
            <p className="text-xs text-muted-foreground mt-0.5">{assignment.role_name}</p>
          </div>
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={onClose}>
            ×
          </Button>
        </div>

        <div className="space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Project</span>
            <span className="font-medium">{assignment.project_name}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Duration</span>
            <span>
              {formatShortDate(startDate)} → {formatShortDate(endDate)} ({durationDays}d)
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Status</span>
            <Badge className={cn('text-[10px]', colors.bg, colors.border, colors.text)}>
              {colors.label}
            </Badge>
          </div>
          {assignment.hourly_rate && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Rate</span>
              <span>${assignment.hourly_rate}/hr</span>
            </div>
          )}
          {assignment.per_diem && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Per Diem</span>
              <span>${assignment.per_diem}/day</span>
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Partner</span>
            <span className="flex items-center gap-1">
              {assignment.partner_confirmed ? (
                <CheckCircle2 className="h-3 w-3 text-emerald-500" />
              ) : (
                <Clock className="h-3 w-3 text-amber-500" />
              )}
              {assignment.partner_confirmed ? 'Confirmed' : 'Awaiting'}
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// ─── Main Component ─────────────────────────────────────────────────────────

export interface ForwardStaffingTimelineProps {
  projects: Project[]
  className?: string
  onSelectAssignment?: (assignment: TimelineAssignment) => void
  title?: string
  description?: string
  filterByProject?: string // project_id to filter by
  filterByTechnician?: string // technician_id to filter by
}

export function ForwardStaffingTimeline({
  projects,
  className,
  onSelectAssignment,
  title = 'Forward Staffing Timeline',
  description = '90-day view of chained technician assignments, gaps, and transitions',
  filterByProject,
  filterByTechnician,
}: ForwardStaffingTimelineProps) {
  const [zoom, setZoom] = useState<ZoomLevel>('90d')
  const [offset, setOffset] = useState(0) // days offset from today
  const [selectedAssignment, setSelectedAssignment] = useState<TimelineAssignment | null>(null)
  const [filterUtilization, setFilterUtilization] = useState<'all' | 'gaps' | 'booked'>('all')

  const zoomDays: Record<ZoomLevel, number> = { '30d': 30, '60d': 60, '90d': 90 }
  const totalDays = zoomDays[zoom]

  const today = useMemo(() => {
    const d = new Date(2026, 2, 19) // Match seed data date
    d.setHours(0, 0, 0, 0)
    return d
  }, [])

  const windowStart = useMemo(() => addDays(today, offset), [today, offset])
  const windowEnd = useMemo(() => addDays(windowStart, totalDays), [windowStart, totalDays])

  const todayPercent = useMemo(() => {
    return (daysBetween(windowStart, today) / totalDays) * 100
  }, [windowStart, today, totalDays])

  // Filter projects
  const filteredProjects = useMemo(() => {
    if (filterByProject) {
      return projects.filter(p => p.id === filterByProject)
    }
    return projects
  }, [projects, filterByProject])

  // Build timeline data
  const rows = useMemo(() => {
    let data = buildTimelineData(filteredProjects, windowStart, windowEnd)

    // Filter by technician
    if (filterByTechnician) {
      data = data.filter(r => r.technician_id === filterByTechnician)
    }

    // Filter by utilization
    if (filterUtilization === 'gaps') {
      data = data.filter(r => r.gaps.some(g => g.duration_days >= 3))
    } else if (filterUtilization === 'booked') {
      data = data.filter(r => r.utilization >= 80)
    }

    return data
  }, [filteredProjects, windowStart, windowEnd, filterByTechnician, filterUtilization])

  const handleSelectAssignment = useCallback(
    (assignment: TimelineAssignment) => {
      setSelectedAssignment(assignment)
      onSelectAssignment?.(assignment)
    },
    [onSelectAssignment]
  )

  const handlePan = useCallback(
    (direction: 'left' | 'right') => {
      const panDays = Math.floor(totalDays / 3)
      setOffset(prev => prev + (direction === 'right' ? panDays : -panDays))
    },
    [totalDays]
  )

  const handleResetView = useCallback(() => {
    setOffset(0)
    setZoom('90d')
  }, [])

  return (
    <Card className={cn('overflow-hidden', className)}>
      <CardHeader className="pb-3">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <Calendar className="h-5 w-5 text-primary" />
              {title}
            </CardTitle>
            <CardDescription className="mt-1">{description}</CardDescription>
          </div>

          {/* Controls */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Quick filters */}
            <div className="flex items-center rounded-md border border-border/50 overflow-hidden">
              <button
                className={cn(
                  'px-2.5 py-1 text-[11px] font-medium transition-colors',
                  filterUtilization === 'all' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                )}
                onClick={() => setFilterUtilization('all')}
              >
                All
              </button>
              <button
                className={cn(
                  'px-2.5 py-1 text-[11px] font-medium transition-colors border-l border-border/50',
                  filterUtilization === 'gaps' ? 'bg-red-500/20 text-red-400' : 'hover:bg-muted'
                )}
                onClick={() => setFilterUtilization('gaps')}
              >
                <AlertTriangle className="h-3 w-3 inline mr-1" />
                Gaps
              </button>
              <button
                className={cn(
                  'px-2.5 py-1 text-[11px] font-medium transition-colors border-l border-border/50',
                  filterUtilization === 'booked' ? 'bg-emerald-500/20 text-emerald-400' : 'hover:bg-muted'
                )}
                onClick={() => setFilterUtilization('booked')}
              >
                <CheckCircle2 className="h-3 w-3 inline mr-1" />
                Booked
              </button>
            </div>

            {/* Zoom controls */}
            <div className="flex items-center rounded-md border border-border/50 overflow-hidden">
              {(['30d', '60d', '90d'] as ZoomLevel[]).map(z => (
                <button
                  key={z}
                  className={cn(
                    'px-2.5 py-1 text-[11px] font-medium transition-colors',
                    z !== '30d' && 'border-l border-border/50',
                    zoom === z ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                  )}
                  onClick={() => setZoom(z)}
                >
                  {z}
                </button>
              ))}
            </div>

            {/* Pan controls */}
            <div className="flex items-center gap-0.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => handlePan('left')}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[11px]"
                onClick={handleResetView}
              >
                Today
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => handlePan('right')}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        {/* Summary stats */}
        <div className="px-4 pb-3">
          <TimelineSummary rows={rows} />
        </div>

        {/* Legend */}
        <div className="px-4 pb-2">
          <TimelineLegend />
        </div>

        {/* Timeline grid */}
        <div className="border-t border-border/50">
          {/* Header row */}
          <div className="flex">
            <div className="w-48 md:w-56 shrink-0 border-r border-border/30 p-2 flex items-center">
              <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1">
                <User className="h-3 w-3" />
                Technician
              </span>
            </div>
            <div className="flex-1 relative">
              <MonthHeaders windowStart={windowStart} totalDays={totalDays} />
            </div>
          </div>

          {/* Rows */}
          <ScrollArea className="max-h-[480px]">
            <div className="relative">
              {rows.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                  <Calendar className="h-10 w-10 mb-3 opacity-40" />
                  <p className="text-sm font-medium">No assignments in this window</p>
                  <p className="text-xs mt-1">
                    {filterUtilization !== 'all'
                      ? 'Try adjusting filters or zoom level'
                      : 'Adjust the date range or add assignments to projects'}
                  </p>
                </div>
              ) : (
                rows.map(row => (
                  <TechnicianTimelineRow
                    key={row.technician_id}
                    row={row}
                    windowStart={windowStart}
                    totalDays={totalDays}
                    onSelectAssignment={handleSelectAssignment}
                  />
                ))
              )}

              {/* Today marker overlay - positioned relative to the timeline area */}
              {rows.length > 0 && (
                <div
                  className="absolute top-0 bottom-0 pointer-events-none"
                  style={{ left: '192px', right: 0 }} // match w-48 (192px)
                >
                  <TodayMarker leftPercent={todayPercent} />
                </div>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* Selected assignment detail */}
        {selectedAssignment && (
          <div className="border-t border-border/50 p-4">
            <AssignmentDetailPanel
              assignment={selectedAssignment}
              onClose={() => setSelectedAssignment(null)}
            />
          </div>
        )}

        {/* Window info footer */}
        <div className="border-t border-border/30 px-4 py-2 flex items-center justify-between">
          <span className="text-[10px] text-muted-foreground">
            {formatShortDate(windowStart)} — {formatShortDate(windowEnd)} ({totalDays} days)
          </span>
          <span className="text-[10px] text-muted-foreground">
            {rows.length} technician{rows.length !== 1 ? 's' : ''} · {rows.reduce((s, r) => s + r.assignments.length, 0)} assignments
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

export default ForwardStaffingTimeline
