import { useMemo } from "react";
import { usePartnerStore, type PartnerAssignment } from "@/stores/partnerStore";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  CalendarDays,
  MapPin,
  User,
  ArrowRight,
  CheckCircle2,
  Clock,
  AlertCircle,
} from "lucide-react";

function AssignmentRow({ assignment }: { assignment: PartnerAssignment }) {
  const today = new Date().toISOString().split("T")[0];
  const isUpcoming = assignment.start_date > today;
  const isActive = assignment.status === "Active";
  const isPending = assignment.status === "Pending Confirmation";

  const statusConfig: Record<string, { label: string; className: string; icon: typeof Clock }> = {
    Active: { label: "Active", className: "bg-emerald-600", icon: CheckCircle2 },
    "Pending Confirmation": { label: "Pending", className: "bg-amber-500", icon: Clock },
    Completed: { label: "Completed", className: "bg-muted text-muted-foreground", icon: CheckCircle2 },
    Cancelled: { label: "Cancelled", className: "bg-destructive", icon: AlertCircle },
  };

  const cfg = statusConfig[assignment.status] || statusConfig.Active;
  const StatusIcon = cfg.icon;

  // Calculate timeline position
  const startDate = new Date(assignment.start_date);
  const endDate = assignment.end_date ? new Date(assignment.end_date) : null;

  const daysUntilStart = Math.ceil(
    (startDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24)
  );

  return (
    <div className="group relative flex gap-4">
      {/* Timeline dot and line */}
      <div className="flex flex-col items-center">
        <div
          className={`flex h-8 w-8 items-center justify-center rounded-full border-2 ${
            isPending
              ? "border-amber-500 bg-amber-500/10"
              : isActive
              ? "border-emerald-500 bg-emerald-500/10"
              : "border-muted bg-muted"
          }`}
        >
          <StatusIcon
            className={`h-4 w-4 ${
              isPending ? "text-amber-500" : isActive ? "text-emerald-500" : "text-muted-foreground"
            }`}
          />
        </div>
        <div className="w-px flex-1 bg-border" />
      </div>

      {/* Content */}
      <Card className="flex-1 mb-3 border transition-colors hover:border-primary/20">
        <CardContent className="p-3 md:p-4">
          <div className="flex flex-col gap-2">
            {/* Top row */}
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h4 className="text-sm font-semibold truncate">{assignment.technician_name}</h4>
                  <Badge className={`text-[10px] ${cfg.className}`}>
                    {cfg.label}
                  </Badge>
                  {assignment.assignment_type === "Pre-Booked" && (
                    <Badge variant="outline" className="text-[10px]">Pre-Booked</Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {assignment.role_name} on {assignment.project_name}
                </p>
              </div>

              {isUpcoming && daysUntilStart > 0 && (
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  in {daysUntilStart}d
                </span>
              )}
            </div>

            {/* Date range */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <CalendarDays className="h-3 w-3" />
              <span>{new Date(assignment.start_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</span>
              {assignment.end_date && (
                <>
                  <ArrowRight className="h-3 w-3" />
                  <span>{new Date(assignment.end_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</span>
                </>
              )}
            </div>

            {/* Confirmation status */}
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1">
                {assignment.partner_confirmed_start ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                ) : (
                  <Clock className="h-3 w-3 text-amber-500" />
                )}
                Start {assignment.partner_confirmed_start ? "confirmed" : "pending"}
              </span>
              <span className="flex items-center gap-1">
                {assignment.partner_confirmed_end ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                ) : (
                  <Clock className="h-3 w-3 text-amber-500" />
                )}
                End {assignment.partner_confirmed_end ? "confirmed" : "pending"}
              </span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export function AssignmentsTimeline() {
  const { assignments, isLoading } = usePartnerStore();

  // Sort by start date, group into upcoming / active / past
  const grouped = useMemo(() => {
    const today = new Date().toISOString().split("T")[0];
    const sorted = [...assignments].sort(
      (a, b) => new Date(a.start_date).getTime() - new Date(b.start_date).getTime()
    );

    return {
      upcoming: sorted.filter((a) => a.start_date > today && a.status !== "Completed" && a.status !== "Cancelled"),
      active: sorted.filter((a) => a.status === "Active" || a.status === "Pending Confirmation"),
      past: sorted.filter((a) => a.status === "Completed" || a.status === "Cancelled"),
    };
  }, [assignments]);

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Card key={i}>
            <CardContent className="p-5">
              <div className="space-y-2">
                <div className="h-4 w-40 rounded bg-muted animate-pulse" />
                <div className="h-3 w-24 rounded bg-muted animate-pulse" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (assignments.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-12">
          <CalendarDays className="h-10 w-10 text-muted-foreground mb-3" />
          <h3 className="font-semibold text-lg">No Assignments Yet</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Assignments will appear here once technicians are staffed to your projects.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Active assignments */}
      {grouped.active.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
            Active & Pending ({grouped.active.length})
          </h3>
          <div className="pl-1">
            {grouped.active.map((a) => (
              <AssignmentRow key={a.id} assignment={a} />
            ))}
          </div>
        </div>
      )}

      {/* Upcoming */}
      {grouped.upcoming.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
            Upcoming ({grouped.upcoming.length})
          </h3>
          <div className="pl-1">
            {grouped.upcoming.map((a) => (
              <AssignmentRow key={a.id} assignment={a} />
            ))}
          </div>
        </div>
      )}

      {/* Past */}
      {grouped.past.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-muted-foreground" />
            Completed ({grouped.past.length})
          </h3>
          <div className="pl-1 opacity-60">
            {grouped.past.slice(0, 5).map((a) => (
              <AssignmentRow key={a.id} assignment={a} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
