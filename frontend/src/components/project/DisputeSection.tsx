import { useState } from "react";
import { useProjectStore } from "@/stores/projectStore";
import type { TimesheetDispute } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Flag,
  Search as SearchIcon,
  Building2,
  User,
  FileText,
  MessageSquare,
  Scale,
  ArrowRight,
  XCircle,
  Minus,
} from "lucide-react";

function getDisputeStatusBadge(status: TimesheetDispute["dispute_status"]) {
  const map: Record<string, { label: string; className: string }> = {
    open: { label: "Open", className: "bg-red-500/20 text-red-400 border-red-500/30" },
    investigating: { label: "Investigating", className: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
    resolved_approved: { label: "Approved", className: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" },
    resolved_adjusted: { label: "Adjusted", className: "bg-blue-500/20 text-blue-400 border-blue-500/30" },
    resolved_rejected: { label: "Rejected", className: "bg-muted text-muted-foreground" },
  };
  const cfg = map[status] || map.open;
  return (
    <Badge variant="outline" className={cfg.className}>
      {cfg.label}
    </Badge>
  );
}

function getCategoryLabel(category: string) {
  const map: Record<string, string> = {
    hours_discrepancy: "Hours Discrepancy",
    unauthorized_overtime: "Unauth. Overtime",
    no_site_access: "No Site Access",
    quality_concern: "Quality Concern",
    other: "Other",
  };
  return map[category] || category;
}

function DisputeResolveDialog({
  dispute,
  open,
  onOpenChange,
}: {
  dispute: TimesheetDispute;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { resolveDispute } = useProjectStore();
  const [resolution, setResolution] = useState<"resolved_approved" | "resolved_adjusted" | "resolved_rejected">("resolved_approved");
  const [opsNote, setOpsNote] = useState("");
  const [adjustedHours, setAdjustedHours] = useState<string>(dispute.reported_hours.toString());

  const handleResolve = () => {
    if (!opsNote.trim()) return;
    resolveDispute(
      dispute.id,
      resolution,
      opsNote,
      resolution === "resolved_adjusted" ? parseFloat(adjustedHours) : undefined
    );
    onOpenChange(false);
    setOpsNote("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Resolve Dispute</DialogTitle>
          <DialogDescription>
            Review the flagged timesheet for {dispute.technician_name} ({dispute.reported_hours}h reported)
            and determine the resolution.
          </DialogDescription>
        </DialogHeader>

        {/* Dispute context */}
        <div className="rounded-lg bg-muted/50 p-3 space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
            <span>Partner: <strong>{dispute.partner_name}</strong></span>
          </div>
          <div className="flex items-start gap-2">
            <Flag className="h-3.5 w-3.5 text-red-400 mt-0.5" />
            <span className="text-red-400">{dispute.flag_reason}</span>
          </div>
          {dispute.partner_note && (
            <div className="flex items-start gap-2">
              <MessageSquare className="h-3.5 w-3.5 text-muted-foreground mt-0.5" />
              <span className="text-muted-foreground">{dispute.partner_note}</span>
            </div>
          )}
        </div>

        <div className="space-y-4 py-2">
          {/* Resolution type */}
          <div>
            <Label className="text-sm mb-2 block">Resolution</Label>
            <div className="grid grid-cols-3 gap-2">
              <Button
                size="sm"
                variant={resolution === "resolved_approved" ? "default" : "outline"}
                onClick={() => setResolution("resolved_approved")}
                className={resolution === "resolved_approved" ? "bg-emerald-600 hover:bg-emerald-700" : ""}
              >
                <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                Approve
              </Button>
              <Button
                size="sm"
                variant={resolution === "resolved_adjusted" ? "default" : "outline"}
                onClick={() => setResolution("resolved_adjusted")}
                className={resolution === "resolved_adjusted" ? "bg-blue-600 hover:bg-blue-700" : ""}
              >
                <Minus className="h-3.5 w-3.5 mr-1" />
                Adjust
              </Button>
              <Button
                size="sm"
                variant={resolution === "resolved_rejected" ? "default" : "outline"}
                onClick={() => setResolution("resolved_rejected")}
                className={resolution === "resolved_rejected" ? "bg-red-600 hover:bg-red-700" : ""}
              >
                <XCircle className="h-3.5 w-3.5 mr-1" />
                Reject
              </Button>
            </div>
          </div>

          {/* Adjusted hours (only if adjusting) */}
          {resolution === "resolved_adjusted" && (
            <div>
              <Label htmlFor="adjusted-hours" className="text-sm">Adjusted Hours</Label>
              <div className="flex items-center gap-2 mt-1">
                <Input
                  id="adjusted-hours"
                  type="number"
                  min="0"
                  max="168"
                  step="0.5"
                  value={adjustedHours}
                  onChange={(e) => setAdjustedHours(e.target.value)}
                  className="w-24"
                />
                <span className="text-sm text-muted-foreground">
                  (was {dispute.reported_hours}h)
                </span>
              </div>
            </div>
          )}

          {/* Ops note */}
          <div>
            <Label htmlFor="ops-note" className="text-sm">Resolution Note *</Label>
            <Textarea
              id="ops-note"
              placeholder="Explain the resolution decision..."
              value={opsNote}
              onChange={(e) => setOpsNote(e.target.value)}
              className="mt-1"
              rows={3}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleResolve} disabled={!opsNote.trim()}>
            <Scale className="h-4 w-4 mr-1.5" />
            Resolve Dispute
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DisputeRow({ dispute }: { dispute: TimesheetDispute }) {
  const { startInvestigation } = useProjectStore();
  const [showResolve, setShowResolve] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const isOpen = dispute.dispute_status === "open";
  const isInvestigating = dispute.dispute_status === "investigating";
  const isResolved = dispute.dispute_status.startsWith("resolved");

  return (
    <>
      <Card className={`border transition-all ${isOpen ? "border-red-500/30" : isInvestigating ? "border-amber-500/30" : ""}`}>
        <CardContent className="p-4">
          <div className="flex flex-col gap-3">
            {/* Header row */}
            <div className="flex flex-col md:flex-row md:items-start justify-between gap-2">
              <div className="flex items-start gap-3 flex-1 min-w-0">
                <div className="h-10 w-10 rounded-full bg-red-500/10 flex items-center justify-center shrink-0">
                  <Flag className="h-5 w-5 text-red-400" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-sm">{dispute.technician_name}</span>
                    {getDisputeStatusBadge(dispute.dispute_status)}
                    <Badge variant="outline" className="text-[10px]">{getCategoryLabel(dispute.flag_category)}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {dispute.role_name} &middot; {dispute.project_name}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <div className="text-right">
                  <p className="text-lg font-bold">{dispute.reported_hours}h</p>
                  {dispute.adjusted_hours != null && dispute.adjusted_hours !== dispute.reported_hours && (
                    <p className="text-xs">
                      <ArrowRight className="h-3 w-3 inline mr-0.5" />
                      <span className="text-blue-400 font-medium">{dispute.adjusted_hours}h</span>
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* Week info and partner */}
            <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                Week of {new Date(dispute.week_start).toLocaleDateString("en-US", { month: "short", day: "numeric" })} - {new Date(dispute.week_end).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
              </span>
              <span className="flex items-center gap-1">
                <Building2 className="h-3 w-3" />
                Flagged by {dispute.flagged_by}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {new Date(dispute.flagged_at).toLocaleDateString()}
              </span>
            </div>

            {/* Flag reason */}
            <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2">
              <p className="text-sm text-red-400">{dispute.flag_reason}</p>
            </div>

            {/* Partner note */}
            {dispute.partner_note && (
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Partner Note</p>
                <p className="text-sm">{dispute.partner_note}</p>
              </div>
            )}

            {/* Ops resolution note */}
            {dispute.ops_note && (
              <div className={`rounded-lg px-3 py-2 ${
                dispute.dispute_status === "resolved_approved" ? "bg-emerald-500/10 border border-emerald-500/20" :
                dispute.dispute_status === "resolved_adjusted" ? "bg-blue-500/10 border border-blue-500/20" :
                "bg-muted/50"
              }`}>
                <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Ops Resolution</p>
                <p className="text-sm">{dispute.ops_note}</p>
                {dispute.resolved_at && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Resolved {new Date(dispute.resolved_at).toLocaleDateString()} by {dispute.resolved_by}
                  </p>
                )}
              </div>
            )}

            {/* Actions */}
            {!isResolved && (
              <div className="flex items-center gap-2 pt-1">
                {isOpen && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
                    onClick={() => startInvestigation(dispute.id)}
                  >
                    <SearchIcon className="h-3.5 w-3.5 mr-1.5" />
                    Start Investigation
                  </Button>
                )}
                <Button
                  size="sm"
                  onClick={() => setShowResolve(true)}
                >
                  <Scale className="h-3.5 w-3.5 mr-1.5" />
                  Resolve
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <DisputeResolveDialog
        dispute={dispute}
        open={showResolve}
        onOpenChange={setShowResolve}
      />
    </>
  );
}

export function DisputeSection({ projectId }: { projectId: string }) {
  const { getProjectDisputes } = useProjectStore();
  const disputes = getProjectDisputes(projectId);
  const [filter, setFilter] = useState<string>("all");

  const openDisputes = disputes.filter((d) => d.dispute_status === "open");
  const investigatingDisputes = disputes.filter((d) => d.dispute_status === "investigating");
  const resolvedDisputes = disputes.filter(
    (d) => d.dispute_status.startsWith("resolved")
  );

  const filtered =
    filter === "all"
      ? disputes
      : filter === "active"
      ? disputes.filter((d) => !d.dispute_status.startsWith("resolved"))
      : resolvedDisputes;

  if (disputes.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-12">
          <CheckCircle2 className="h-10 w-10 text-emerald-400 mx-auto mb-3" />
          <h3 className="font-semibold text-lg">No Disputes</h3>
          <p className="text-sm text-muted-foreground mt-1">
            No partner-flagged timesheets for this project.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary bar */}
      <div className="grid grid-cols-3 gap-3">
        <Card className="bg-red-500/5 border-red-500/20">
          <CardContent className="p-3 text-center">
            <p className="text-xl font-bold text-red-400">{openDisputes.length}</p>
            <p className="text-xs text-muted-foreground">Open</p>
          </CardContent>
        </Card>
        <Card className="bg-amber-500/5 border-amber-500/20">
          <CardContent className="p-3 text-center">
            <p className="text-xl font-bold text-amber-400">{investigatingDisputes.length}</p>
            <p className="text-xs text-muted-foreground">Investigating</p>
          </CardContent>
        </Card>
        <Card className="bg-emerald-500/5 border-emerald-500/20">
          <CardContent className="p-3 text-center">
            <p className="text-xl font-bold text-emerald-400">{resolvedDisputes.length}</p>
            <p className="text-xs text-muted-foreground">Resolved</p>
          </CardContent>
        </Card>
      </div>

      {/* Filter */}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant={filter === "all" ? "default" : "outline"} onClick={() => setFilter("all")}>
          All ({disputes.length})
        </Button>
        <Button
          size="sm"
          variant={filter === "active" ? "default" : "outline"}
          onClick={() => setFilter("active")}
          className={openDisputes.length > 0 && filter !== "active" ? "border-red-500/50" : ""}
        >
          Active ({openDisputes.length + investigatingDisputes.length})
        </Button>
        <Button size="sm" variant={filter === "resolved" ? "default" : "outline"} onClick={() => setFilter("resolved")}>
          Resolved ({resolvedDisputes.length})
        </Button>
      </div>

      {/* Dispute cards */}
      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="p-8 text-center">
            <FileText className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
            <p className="text-muted-foreground">No disputes match this filter</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {filtered.map((dispute) => (
            <DisputeRow key={dispute.id} dispute={dispute} />
          ))}
        </div>
      )}
    </div>
  );
}
