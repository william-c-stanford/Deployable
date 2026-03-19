import { useMemo } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useProjectStore } from '@/stores/projectStore'
import type { Project } from '@/types'
import {
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Users,
  FileText,
  Flag,
  Clock,
  ArrowRight,
  ShieldAlert,
  Lock,
} from 'lucide-react'

export interface BlockingCondition {
  id: string
  category: 'assignments' | 'timesheets' | 'escalations' | 'disputes'
  severity: 'critical' | 'warning'
  title: string
  description: string
  count: number
  items: { id: string; label: string; detail: string }[]
  tabTarget: string
}

interface ProjectCloseDialogProps {
  project: Project
  open: boolean
  onOpenChange: (open: boolean) => void
  onNavigateTab: (tab: string) => void
  onConfirmClose: () => void
}

function useBlockingConditions(project: Project): BlockingCondition[] {
  const { getProjectDisputes } = useProjectStore()

  return useMemo(() => {
    const conditions: BlockingCondition[] = []

    // 1. Active assignments (critical blocker)
    const activeAssignments = project.assignments.filter(
      (a) => a.status === 'Active' || a.status === 'Pending Confirmation'
    )
    if (activeAssignments.length > 0) {
      conditions.push({
        id: 'active-assignments',
        category: 'assignments',
        severity: 'critical',
        title: 'Active Assignments',
        description: `${activeAssignments.length} assignment${activeAssignments.length !== 1 ? 's' : ''} must be completed or cancelled before closing.`,
        count: activeAssignments.length,
        items: activeAssignments.map((a) => ({
          id: a.id,
          label: a.technician_name,
          detail: `${a.role_name} — ${a.status}`,
        })),
        tabTarget: 'assignments',
      })
    }

    // 2. Open/submitted timesheets (critical blocker)
    const openTimesheets = (project.timesheets || []).filter(
      (t) => t.status === 'Submitted' || t.status === 'Flagged'
    )
    if (openTimesheets.length > 0) {
      conditions.push({
        id: 'open-timesheets',
        category: 'timesheets',
        severity: 'critical',
        title: 'Unresolved Timesheets',
        description: `${openTimesheets.length} timesheet${openTimesheets.length !== 1 ? 's' : ''} pending approval or flagged for review.`,
        count: openTimesheets.length,
        items: openTimesheets.map((t) => ({
          id: t.id,
          label: t.technician_name,
          detail: `${t.hours}h — ${t.status}`,
        })),
        tabTarget: 'timesheets',
      })
    }

    // 3. Open escalations (critical blocker)
    const openEscalations = (project.escalations || []).filter(
      (e) => e.status === 'Open'
    )
    if (openEscalations.length > 0) {
      conditions.push({
        id: 'open-escalations',
        category: 'escalations',
        severity: 'critical',
        title: 'Unresolved Escalations',
        description: `${openEscalations.length} escalation${openEscalations.length !== 1 ? 's' : ''} require resolution before closing.`,
        count: openEscalations.length,
        items: openEscalations.map((e) => ({
          id: e.id,
          label: e.technician_name,
          detail: `${e.type} — ${e.escalation_status || e.status}`,
        })),
        tabTarget: 'escalations',
      })
    }

    // 4. Open disputes (critical blocker)
    const disputes = getProjectDisputes(project.id)
    const openDisputes = disputes.filter(
      (d) => !d.dispute_status.startsWith('resolved')
    )
    if (openDisputes.length > 0) {
      conditions.push({
        id: 'open-disputes',
        category: 'disputes',
        severity: 'critical',
        title: 'Open Disputes',
        description: `${openDisputes.length} dispute${openDisputes.length !== 1 ? 's' : ''} must be resolved before closing.`,
        count: openDisputes.length,
        items: openDisputes.map((d) => ({
          id: d.id,
          label: d.technician_name,
          detail: `${d.flag_category.replace(/_/g, ' ')} — ${d.dispute_status}`,
        })),
        tabTarget: 'disputes',
      })
    }

    return conditions
  }, [project, getProjectDisputes])
}

const categoryIcons: Record<string, React.ElementType> = {
  assignments: Users,
  timesheets: FileText,
  escalations: AlertTriangle,
  disputes: Flag,
}

const severityStyles = {
  critical: {
    bg: 'bg-red-500/10 border-red-500/30',
    icon: 'text-red-400',
    badge: 'bg-red-500/20 text-red-400',
  },
  warning: {
    bg: 'bg-amber-500/10 border-amber-500/30',
    icon: 'text-amber-400',
    badge: 'bg-amber-500/20 text-amber-400',
  },
}

export function ProjectCloseDialog({
  project,
  open,
  onOpenChange,
  onNavigateTab,
  onConfirmClose,
}: ProjectCloseDialogProps) {
  const conditions = useBlockingConditions(project)
  const hasBlockers = conditions.length > 0
  const totalBlockers = conditions.reduce((sum, c) => sum + c.count, 0)

  const handleGoToTab = (tab: string) => {
    onOpenChange(false)
    onNavigateTab(tab)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {hasBlockers ? (
              <>
                <ShieldAlert className="h-5 w-5 text-red-400" />
                Cannot Close Project
              </>
            ) : (
              <>
                <CheckCircle2 className="h-5 w-5 text-green-400" />
                Ready to Close Project
              </>
            )}
          </DialogTitle>
          <DialogDescription>
            {hasBlockers ? (
              <>
                <span className="font-medium text-foreground">{project.name}</span> has{' '}
                <span className="font-semibold text-red-400">{totalBlockers} blocking condition{totalBlockers !== 1 ? 's' : ''}</span>{' '}
                that must be resolved before the project can be closed.
              </>
            ) : (
              <>
                All blocking conditions for{' '}
                <span className="font-medium text-foreground">{project.name}</span>{' '}
                have been resolved. The project is ready to be closed.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        {hasBlockers ? (
          <div className="flex-1 overflow-y-auto space-y-3 py-2 pr-1">
            {conditions.map((condition) => {
              const Icon = categoryIcons[condition.category] || AlertTriangle
              const styles = severityStyles[condition.severity]

              return (
                <div
                  key={condition.id}
                  className={cn(
                    'rounded-lg border p-4 space-y-3',
                    styles.bg
                  )}
                >
                  {/* Condition Header */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-start gap-3">
                      <div className={cn('mt-0.5', styles.icon)}>
                        <Icon className="h-5 w-5" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h4 className="font-semibold text-sm">{condition.title}</h4>
                          <Badge className={cn('text-[10px] px-1.5 py-0 border-0', styles.badge)}>
                            {condition.count}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {condition.description}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Blocking Items List */}
                  <div className="ml-8 space-y-1.5">
                    {condition.items.slice(0, 5).map((item) => (
                      <div
                        key={item.id}
                        className="flex items-center gap-2 text-xs"
                      >
                        <XCircle className="h-3 w-3 text-red-400 shrink-0" />
                        <span className="font-medium">{item.label}</span>
                        <span className="text-muted-foreground">—</span>
                        <span className="text-muted-foreground truncate">{item.detail}</span>
                      </div>
                    ))}
                    {condition.items.length > 5 && (
                      <p className="text-xs text-muted-foreground italic">
                        + {condition.items.length - 5} more...
                      </p>
                    )}
                  </div>

                  {/* Navigate to resolution */}
                  <div className="ml-8">
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-xs h-7"
                      onClick={() => handleGoToTab(condition.tabTarget)}
                    >
                      Go to {condition.title}
                      <ArrowRight className="h-3 w-3 ml-1.5" />
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="py-6 text-center space-y-3">
            <div className="flex justify-center">
              <div className="rounded-full bg-green-500/10 p-4">
                <CheckCircle2 className="h-10 w-10 text-green-400" />
              </div>
            </div>
            <div className="space-y-1">
              <p className="text-sm font-medium">All clear</p>
              <p className="text-xs text-muted-foreground">
                No active assignments, open timesheets, unresolved escalations, or disputes.
              </p>
            </div>

            {/* Closure summary */}
            <div className="mt-4 mx-auto max-w-sm rounded-lg border bg-muted/30 p-3 text-left space-y-2">
              <h4 className="text-xs font-semibold flex items-center gap-1.5">
                <Lock className="h-3 w-3" />
                Closing this project will:
              </h4>
              <ul className="text-xs text-muted-foreground space-y-1 ml-5 list-disc">
                <li>Set status to <span className="font-medium text-foreground">Closed</span></li>
                <li>Archive all completed assignments</li>
                <li>Prevent new staffing recommendations</li>
                <li>Release technicians for new assignments</li>
              </ul>
            </div>
          </div>
        )}

        <DialogFooter className="pt-2 border-t gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {hasBlockers ? 'Dismiss' : 'Cancel'}
          </Button>
          {hasBlockers ? (
            <Button
              variant="outline"
              className="border-red-500/30 text-red-400 hover:bg-red-500/10"
              disabled
            >
              <Lock className="h-4 w-4 mr-1.5" />
              Close Project ({totalBlockers} blocker{totalBlockers !== 1 ? 's' : ''})
            </Button>
          ) : (
            <Button
              variant="default"
              className="bg-green-600 hover:bg-green-700 text-white"
              onClick={onConfirmClose}
            >
              <CheckCircle2 className="h-4 w-4 mr-1.5" />
              Confirm Close Project
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
