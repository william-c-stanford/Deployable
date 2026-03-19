import { useEffect, useState, useCallback } from "react";
import {
  Shield,
  Lock,
  Unlock,
  AlertTriangle,
  CheckCircle2,
  Clock,
  History,
  RefreshCw,
  Zap,
  User,
  Bot,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import api from "@/lib/api";
import type {
  DeployabilityStatusResponse,
  DeployabilityStatusHistoryEntry,
  DeployabilityStatusHistoryResponse,
  StatusChangeSource,
} from "@/types/index";

// ---- Status color/icon mapping ----

const STATUS_CONFIG: Record<
  string,
  { color: string; bgColor: string; borderColor: string; icon: typeof CheckCircle2 }
> = {
  "Ready Now": {
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10",
    borderColor: "border-emerald-500/30",
    icon: CheckCircle2,
  },
  "In Training": {
    color: "text-blue-400",
    bgColor: "bg-blue-500/10",
    borderColor: "border-blue-500/30",
    icon: Clock,
  },
  "Currently Assigned": {
    color: "text-violet-400",
    bgColor: "bg-violet-500/10",
    borderColor: "border-violet-500/30",
    icon: Zap,
  },
  "Rolling Off Soon": {
    color: "text-amber-400",
    bgColor: "bg-amber-500/10",
    borderColor: "border-amber-500/30",
    icon: AlertTriangle,
  },
  "Missing Cert": {
    color: "text-red-400",
    bgColor: "bg-red-500/10",
    borderColor: "border-red-500/30",
    icon: AlertTriangle,
  },
  "Missing Docs": {
    color: "text-red-400",
    bgColor: "bg-red-500/10",
    borderColor: "border-red-500/30",
    icon: AlertTriangle,
  },
  Inactive: {
    color: "text-gray-400",
    bgColor: "bg-gray-500/10",
    borderColor: "border-gray-500/30",
    icon: Clock,
  },
  Onboarding: {
    color: "text-cyan-400",
    bgColor: "bg-cyan-500/10",
    borderColor: "border-cyan-500/30",
    icon: Clock,
  },
  "Pending Review": {
    color: "text-yellow-400",
    bgColor: "bg-yellow-500/10",
    borderColor: "border-yellow-500/30",
    icon: Clock,
  },
  Suspended: {
    color: "text-red-400",
    bgColor: "bg-red-500/10",
    borderColor: "border-red-500/30",
    icon: AlertTriangle,
  },
};

const SOURCE_LABELS: Record<StatusChangeSource, { label: string; icon: typeof Bot }> = {
  auto_computed: { label: "Auto-computed", icon: Bot },
  manual_override: { label: "Manual Override", icon: User },
  training_advancement: { label: "Training Gate", icon: Zap },
  event_triggered: { label: "Event Triggered", icon: Zap },
  batch_refresh: { label: "Batch Refresh", icon: RefreshCw },
  system: { label: "System", icon: Bot },
};

function getStatusConfig(status: string) {
  return (
    STATUS_CONFIG[status] || {
      color: "text-muted-foreground",
      bgColor: "bg-muted/30",
      borderColor: "border-muted",
      icon: Clock,
    }
  );
}

function getScoreColor(score: number): string {
  if (score >= 75) return "text-emerald-400";
  if (score >= 50) return "text-amber-400";
  return "text-red-400";
}

function getProgressColor(score: number): string {
  if (score >= 75) return "bg-emerald-500";
  if (score >= 50) return "bg-amber-500";
  return "bg-red-500";
}

// ---- Dimension Score Bar ----

function DimensionBar({
  label,
  score,
  summary,
}: {
  label: string;
  score: number;
  summary: string;
}) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{label}</span>
              <span className={cn("font-mono font-medium", getScoreColor(score))}>
                {score.toFixed(0)}
              </span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-muted/50">
              <div
                className={cn("h-full rounded-full transition-all duration-500", getProgressColor(score))}
                style={{ width: `${Math.min(100, score)}%` }}
              />
            </div>
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs max-w-[200px]">{summary || `${label}: ${score.toFixed(1)}/100`}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ---- History Entry ----

function HistoryEntry({ entry }: { entry: DeployabilityStatusHistoryEntry }) {
  const sourceInfo = SOURCE_LABELS[entry.source] || SOURCE_LABELS.system;
  const SourceIcon = sourceInfo.icon;
  const newConfig = getStatusConfig(entry.new_status);
  const oldConfig = entry.old_status ? getStatusConfig(entry.old_status) : null;

  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-border/40 last:border-0">
      <div className="mt-0.5">
        <SourceIcon className="h-3.5 w-3.5 text-muted-foreground" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {entry.old_status && (
            <>
              <Badge variant="outline" className={cn("text-[10px] px-1.5 py-0", oldConfig?.color)}>
                {entry.old_status}
              </Badge>
              <span className="text-muted-foreground text-xs">&rarr;</span>
            </>
          )}
          <Badge variant="outline" className={cn("text-[10px] px-1.5 py-0", newConfig.color)}>
            {entry.new_status}
          </Badge>
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            {sourceInfo.label}
          </Badge>
        </div>
        {entry.reason && (
          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{entry.reason}</p>
        )}
        <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground">
          <span>{new Date(entry.created_at).toLocaleDateString()}</span>
          <span>{new Date(entry.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
          {entry.actor_name && <span>by {entry.actor_name}</span>}
          {entry.readiness_score_at_change != null && (
            <span className={getScoreColor(entry.readiness_score_at_change)}>
              Score: {entry.readiness_score_at_change.toFixed(0)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Main Panel ----

interface DeployabilityStatusPanelProps {
  technicianId: string;
  compact?: boolean;
  className?: string;
}

export function DeployabilityStatusPanel({
  technicianId,
  compact = false,
  className,
}: DeployabilityStatusPanelProps) {
  const [statusData, setStatusData] = useState<DeployabilityStatusResponse | null>(null);
  const [history, setHistory] = useState<DeployabilityStatusHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideStatus, setOverrideStatus] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideLock, setOverrideLock] = useState(false);
  const [overrideSubmitting, setOverrideSubmitting] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get(`/deployability/${technicianId}/status`);
      setStatusData(res.data);
    } catch (err) {
      console.error("Failed to fetch deployability status:", err);
    } finally {
      setLoading(false);
    }
  }, [technicianId]);

  const fetchHistory = useCallback(async () => {
    try {
      setHistoryLoading(true);
      const res = await api.get<DeployabilityStatusHistoryResponse>(
        `/deployability/${technicianId}/history`,
        { params: { limit: 20 } }
      );
      setHistory(res.data.history);
    } catch (err) {
      console.error("Failed to fetch status history:", err);
    } finally {
      setHistoryLoading(false);
    }
  }, [technicianId]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    if (showHistory) {
      fetchHistory();
    }
  }, [showHistory, fetchHistory]);

  const handleOverride = async () => {
    if (!overrideStatus || !overrideReason.trim()) return;
    try {
      setOverrideSubmitting(true);
      await api.post(`/deployability/${technicianId}/override`, {
        new_status: overrideStatus,
        reason: overrideReason,
        lock_status: overrideLock,
      });
      setOverrideOpen(false);
      setOverrideStatus("");
      setOverrideReason("");
      setOverrideLock(false);
      // Refresh data
      await fetchStatus();
      if (showHistory) await fetchHistory();
    } catch (err) {
      console.error("Failed to apply override:", err);
    } finally {
      setOverrideSubmitting(false);
    }
  };

  const handleUnlock = async () => {
    try {
      await api.post(`/deployability/${technicianId}/unlock`);
      await fetchStatus();
    } catch (err) {
      console.error("Failed to unlock:", err);
    }
  };

  if (loading && !statusData) {
    return (
      <Card className={cn("animate-pulse", className)}>
        <CardContent className="p-4">
          <div className="h-20 bg-muted/30 rounded" />
        </CardContent>
      </Card>
    );
  }

  if (!statusData) return null;

  const config = getStatusConfig(statusData.current_status);
  const StatusIcon = config.icon;
  const readiness = statusData.readiness;

  const ALL_STATUSES = [
    "Ready Now",
    "In Training",
    "Currently Assigned",
    "Missing Cert",
    "Missing Docs",
    "Rolling Off Soon",
    "Inactive",
    "Onboarding",
    "Pending Review",
    "Suspended",
  ];

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="pb-2 px-4 pt-4">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Shield className="h-4 w-4 text-primary" />
            Deployability Status
          </CardTitle>
          <div className="flex items-center gap-1">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fetchStatus}>
                    <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Refresh status</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>
      </CardHeader>

      <CardContent className="px-4 pb-4 space-y-3">
        {/* Current Status */}
        <div
          className={cn(
            "flex items-center gap-3 p-3 rounded-lg border",
            config.bgColor,
            config.borderColor
          )}
        >
          <StatusIcon className={cn("h-5 w-5 shrink-0", config.color)} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className={cn("font-semibold text-sm", config.color)}>
                {statusData.current_status}
              </span>
              {statusData.is_manual_override && (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger>
                      <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-amber-500/40 text-amber-400">
                        <Lock className="h-2.5 w-2.5" />
                        Override
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs">
                        Manually overridden{statusData.locked_by ? ` by ${statusData.locked_by}` : ""}
                        {statusData.lock_reason ? `: ${statusData.lock_reason}` : ""}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
              {!statusData.is_manual_override && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 gap-1 border-blue-500/40 text-blue-400">
                  <Bot className="h-2.5 w-2.5" />
                  Auto
                </Badge>
              )}
            </div>
            {statusData.status_divergent && readiness && (
              <p className="text-[11px] text-amber-400 mt-1 flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" />
                Auto-computed suggests: {readiness.suggested_status}
                {readiness.status_change_reason && (
                  <span className="text-muted-foreground ml-1">
                    ({readiness.status_change_reason})
                  </span>
                )}
              </p>
            )}
          </div>
        </div>

        {/* Readiness Score */}
        {readiness && !compact && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Readiness Score</span>
              <span className={cn("text-lg font-bold font-mono", getScoreColor(readiness.overall_score))}>
                {readiness.overall_score.toFixed(0)}
                <span className="text-xs text-muted-foreground font-normal">/100</span>
              </span>
            </div>

            {/* Dimension Breakdowns */}
            <div className="space-y-2">
              <DimensionBar
                label="Certifications"
                score={readiness.dimension_scores.certification}
                summary={readiness.certification_summary}
              />
              <DimensionBar
                label="Training"
                score={readiness.dimension_scores.training}
                summary={readiness.training_summary}
              />
              <DimensionBar
                label="Assignments"
                score={readiness.dimension_scores.assignment_history}
                summary={readiness.assignment_summary}
              />
              <DimensionBar
                label="Documentation"
                score={readiness.dimension_scores.documentation}
                summary={readiness.documentation_summary}
              />
            </div>
          </div>
        )}

        {/* Compact readiness score */}
        {readiness && compact && (
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">Score:</span>
            <div className="flex-1 h-1.5 rounded-full bg-muted/50">
              <div
                className={cn("h-full rounded-full transition-all", getProgressColor(readiness.overall_score))}
                style={{ width: `${Math.min(100, readiness.overall_score)}%` }}
              />
            </div>
            <span className={cn("text-xs font-mono font-medium", getScoreColor(readiness.overall_score))}>
              {readiness.overall_score.toFixed(0)}
            </span>
          </div>
        )}

        <Separator className="opacity-30" />

        {/* Action buttons */}
        <div className="flex items-center gap-2 flex-wrap">
          {/* Override dialog */}
          <Dialog open={overrideOpen} onOpenChange={setOverrideOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" className="h-7 text-xs gap-1">
                <User className="h-3 w-3" />
                Override
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[425px]">
              <DialogHeader>
                <DialogTitle>Override Deployability Status</DialogTitle>
                <DialogDescription>
                  Manually set the deployability status for{" "}
                  <strong>{statusData.technician_name}</strong>. This creates an
                  audit trail entry.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2">
                  <Label>New Status</Label>
                  <Select value={overrideStatus} onValueChange={setOverrideStatus}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select status..." />
                    </SelectTrigger>
                    <SelectContent>
                      {ALL_STATUSES.map((s) => {
                        const c = getStatusConfig(s);
                        return (
                          <SelectItem key={s} value={s}>
                            <span className={cn("flex items-center gap-2", c.color)}>
                              <span
                                className={cn("h-2 w-2 rounded-full", c.color.replace("text-", "bg-"))}
                              />
                              {s}
                            </span>
                          </SelectItem>
                        );
                      })}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Reason</Label>
                  <Textarea
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    placeholder="Explain why you're overriding this status..."
                    className="min-h-[80px]"
                  />
                </div>
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label className="text-sm">Lock Status</Label>
                    <p className="text-xs text-muted-foreground">
                      Prevent auto-computation from changing this status
                    </p>
                  </div>
                  <Switch checked={overrideLock} onCheckedChange={setOverrideLock} />
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setOverrideOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={handleOverride}
                  disabled={!overrideStatus || !overrideReason.trim() || overrideSubmitting}
                >
                  {overrideSubmitting ? "Applying..." : "Apply Override"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          {/* Unlock button (if locked) */}
          {statusData.is_locked && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1 text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
              onClick={handleUnlock}
            >
              <Unlock className="h-3 w-3" />
              Unlock
            </Button>
          )}

          {/* History toggle */}
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs gap-1 ml-auto"
            onClick={() => setShowHistory(!showHistory)}
          >
            <History className="h-3 w-3" />
            History
            {showHistory ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
          </Button>
        </div>

        {/* History panel */}
        {showHistory && (
          <div className="space-y-1 max-h-[300px] overflow-y-auto">
            {historyLoading ? (
              <div className="text-xs text-muted-foreground text-center py-4">
                Loading history...
              </div>
            ) : history.length === 0 ? (
              <div className="text-xs text-muted-foreground text-center py-4">
                No status changes recorded yet
              </div>
            ) : (
              history.map((entry) => <HistoryEntry key={entry.id} entry={entry} />)
            )}
          </div>
        )}

        {/* Last change summary */}
        {!showHistory && statusData.last_change && (
          <div className="text-[11px] text-muted-foreground">
            Last changed:{" "}
            {new Date(statusData.last_change.created_at).toLocaleDateString()}{" "}
            via {SOURCE_LABELS[statusData.last_change.source]?.label || statusData.last_change.source}
            {statusData.last_change.actor_name && ` by ${statusData.last_change.actor_name}`}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
