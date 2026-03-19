import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Zap,
  ArrowRight,
  Users,
  Award,
  Clock,
  GraduationCap,
  AlertTriangle,
  Briefcase,
  UserPlus,
  Shield,
  X,
  RefreshCw,
  Wifi,
  WifiOff,
  ChevronDown,
  ChevronUp,
  Sparkles,
} from "lucide-react";
import type { SuggestedAction } from "@/types/dashboard";
import { useDashboardStore } from "@/stores/dashboardStore";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import type { WSEvent } from "@/types";

// ============================================================
// Action type configuration — icon, color, label
// ============================================================

interface ActionTypeConfig {
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  bgColor: string;
  borderColor: string;
  label: string;
}

const ACTION_TYPE_CONFIG: Record<string, ActionTypeConfig> = {
  staffing: {
    icon: Users,
    color: "text-blue-400",
    bgColor: "bg-blue-500/10",
    borderColor: "border-blue-500/20",
    label: "Staffing",
  },
  cert_renewal: {
    icon: Award,
    color: "text-orange-400",
    bgColor: "bg-orange-500/10",
    borderColor: "border-orange-500/20",
    label: "Certification",
  },
  timesheet: {
    icon: Clock,
    color: "text-cyan-400",
    bgColor: "bg-cyan-500/10",
    borderColor: "border-cyan-500/20",
    label: "Timesheet",
  },
  training: {
    icon: GraduationCap,
    color: "text-violet-400",
    bgColor: "bg-violet-500/10",
    borderColor: "border-violet-500/20",
    label: "Training",
  },
  cert_expiry: {
    icon: AlertTriangle,
    color: "text-amber-400",
    bgColor: "bg-amber-500/10",
    borderColor: "border-amber-500/20",
    label: "Cert Expiry",
  },
  backfill: {
    icon: UserPlus,
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10",
    borderColor: "border-emerald-500/20",
    label: "Backfill",
  },
  project: {
    icon: Briefcase,
    color: "text-indigo-400",
    bgColor: "bg-indigo-500/10",
    borderColor: "border-indigo-500/20",
    label: "Project",
  },
  escalation: {
    icon: AlertTriangle,
    color: "text-red-400",
    bgColor: "bg-red-500/10",
    borderColor: "border-red-500/20",
    label: "Escalation",
  },
  compliance: {
    icon: Shield,
    color: "text-rose-400",
    bgColor: "bg-rose-500/10",
    borderColor: "border-rose-500/20",
    label: "Compliance",
  },
};

const DEFAULT_CONFIG: ActionTypeConfig = {
  icon: Zap,
  color: "text-amber-400",
  bgColor: "bg-amber-500/10",
  borderColor: "border-amber-500/20",
  label: "Action",
};

function getActionConfig(actionType: string): ActionTypeConfig {
  return ACTION_TYPE_CONFIG[actionType] || DEFAULT_CONFIG;
}

// ============================================================
// Priority badge component
// ============================================================

function PriorityBadge({ priority }: { priority: number }) {
  if (priority < 4) return null;

  const isUrgent = priority >= 5;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider",
        isUrgent
          ? "bg-red-500/15 text-red-400 border border-red-500/30"
          : "bg-amber-500/15 text-amber-400 border border-amber-500/30"
      )}
    >
      {isUrgent ? "Urgent" : "High"}
    </span>
  );
}

// ============================================================
// Relative time helper
// ============================================================

function relativeTime(dateStr?: string): string {
  if (!dateStr) return "";
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ============================================================
// Individual Action Card
// ============================================================

interface ActionCardProps {
  action: SuggestedAction;
  isNew: boolean;
  onNavigate: (action: SuggestedAction) => void;
  onDismiss: (actionId: string) => void;
  isDismissing: boolean;
}

function ActionCard({ action, isNew, onNavigate, onDismiss, isDismissing }: ActionCardProps) {
  const config = getActionConfig(action.action_type);
  const IconComponent = config.icon;
  const [showDismiss, setShowDismiss] = useState(false);

  return (
    <div
      className={cn(
        "group relative flex items-start gap-3 p-3 rounded-lg transition-all duration-300",
        "hover:bg-muted/80 cursor-pointer",
        "border border-transparent",
        isDismissing && "opacity-0 scale-95 h-0 p-0 overflow-hidden",
        isNew && "animate-slideIn border-primary/20 bg-primary/5"
      )}
      onMouseEnter={() => setShowDismiss(true)}
      onMouseLeave={() => setShowDismiss(false)}
      onClick={() => onNavigate(action)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onNavigate(action);
        }
      }}
    >
      {/* Action type icon */}
      <div
        className={cn(
          "flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center",
          config.bgColor,
          "border",
          config.borderColor
        )}
      >
        <IconComponent className={cn("h-4 w-4", config.color)} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <p className="text-sm font-medium truncate">{action.title}</p>
          <PriorityBadge priority={action.priority} />
          {isNew && (
            <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-primary/15 text-primary text-[10px] font-semibold animate-pulse">
              <Sparkles className="h-2.5 w-2.5" />
              NEW
            </span>
          )}
        </div>

        {action.description && (
          <p className="text-xs text-muted-foreground truncate">{action.description}</p>
        )}

        <div className="flex items-center gap-2 mt-1">
          <span
            className={cn(
              "text-[10px] font-medium px-1.5 py-0.5 rounded",
              config.bgColor,
              config.color
            )}
          >
            {config.label}
          </span>
          {action.agent_name && (
            <span className="text-[10px] text-muted-foreground">
              via {action.agent_name}
            </span>
          )}
          {action.created_at && (
            <span className="text-[10px] text-muted-foreground">
              {relativeTime(action.created_at)}
            </span>
          )}
        </div>
      </div>

      {/* Right side: dismiss button + arrow */}
      <div className="flex items-center gap-1 flex-shrink-0">
        {/* Dismiss button (appears on hover) */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDismiss(action.id);
          }}
          className={cn(
            "p-1 rounded-md hover:bg-destructive/10 hover:text-destructive transition-all",
            "text-muted-foreground/50",
            showDismiss ? "opacity-100" : "opacity-0"
          )}
          title="Dismiss"
          aria-label={`Dismiss action: ${action.title}`}
        >
          <X className="h-3.5 w-3.5" />
        </button>

        {/* Navigate arrow */}
        <ArrowRight
          className={cn(
            "h-4 w-4 text-muted-foreground transition-all",
            "opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5"
          )}
        />
      </div>
    </div>
  );
}

// ============================================================
// Loading skeleton
// ============================================================

function ActionSkeleton() {
  return (
    <div className="flex items-start gap-3 p-3 animate-pulse">
      <div className="w-9 h-9 rounded-lg bg-muted" />
      <div className="flex-1 space-y-2">
        <div className="h-4 bg-muted rounded w-3/4" />
        <div className="h-3 bg-muted rounded w-1/2" />
        <div className="h-3 bg-muted rounded w-1/4" />
      </div>
    </div>
  );
}

// ============================================================
// Main Widget Component
// ============================================================

interface SuggestedActionsWidgetProps {
  /** Override actions (for standalone use or testing) */
  actions?: SuggestedAction[];
  /** Max actions to show before "show more" */
  maxVisible?: number;
  /** Whether to subscribe to WebSocket for live updates */
  enableRealtime?: boolean;
  /** CSS class override */
  className?: string;
}

export function SuggestedActionsWidget({
  actions: propActions,
  maxVisible = 5,
  enableRealtime = true,
  className,
}: SuggestedActionsWidgetProps) {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.role);

  // Store state
  const storeActions = useDashboardStore((s) => s.suggestedActions);
  const newActionIds = useDashboardStore((s) => s.newActionIds);
  const dismissedActionIds = useDashboardStore((s) => s.dismissedActionIds);
  const isLoading = useDashboardStore((s) => s.isLoading);
  const isLoadingActions = useDashboardStore((s) => s.isLoadingActions);
  const isWsUpdating = useDashboardStore((s) => s.isWsUpdating);
  const lastWsRefresh = useDashboardStore((s) => s.lastWsRefresh);
  const {
    dismissAction,
    actOnAction,
    clearNewMarker,
    fetchSuggestedActions,
    addSuggestedAction,
    replaceSuggestedActions,
    removeSuggestedAction,
    updateSuggestedAction,
    setWsUpdating,
  } = useDashboardStore.getState();

  // Local UI state
  const [expanded, setExpanded] = useState(false);
  const [dismissingIds, setDismissingIds] = useState<Set<string>>(new Set());
  const prevActionsRef = useRef<string>("");

  // Use prop actions or store actions
  const rawActions = propActions ?? storeActions;

  // Filter out dismissed actions and sort by priority
  const visibleActions = useMemo(() => {
    return rawActions
      .filter((a) => !dismissedActionIds.has(a.id))
      .sort((a, b) => (b.priority || 0) - (a.priority || 0));
  }, [rawActions, dismissedActionIds]);

  const displayActions = expanded
    ? visibleActions
    : visibleActions.slice(0, maxVisible);

  const hasMore = visibleActions.length > maxVisible;
  const hiddenCount = visibleActions.length - maxVisible;

  // ---- WebSocket handler for real-time suggested action updates ----
  const handleWsMessage = useCallback(
    (event: WSEvent) => {
      if (!event.event_type) return;

      switch (event.event_type) {
        case "dashboard.suggested_action": {
          const data = event.data as Record<string, unknown> | undefined;
          if (!data) {
            // Simple refresh signal
            fetchSuggestedActions();
            break;
          }

          const subAction = (data.action as string) || "refresh";

          if (subAction === "created" && data.suggested_action) {
            const sa = data.suggested_action as SuggestedAction;
            setWsUpdating(true);
            addSuggestedAction(sa);
            setTimeout(() => setWsUpdating(false), 800);
          } else if (subAction === "removed" && data.action_id) {
            removeSuggestedAction(data.action_id as string);
          } else if (subAction === "updated" && data.action_id && data.updates) {
            updateSuggestedAction(
              data.action_id as string,
              data.updates as Partial<SuggestedAction>
            );
          } else if (subAction === "batch_refresh" && data.actions) {
            setWsUpdating(true);
            replaceSuggestedActions(data.actions as SuggestedAction[]);
            setTimeout(() => setWsUpdating(false), 800);
          } else {
            // Fallback: full re-fetch
            fetchSuggestedActions();
          }
          break;
        }

        case "recommendation.created":
        case "recommendation.batch_refreshed":
        case "recommendation.list_refresh": {
          // Recommendations changed → suggested actions may need refresh
          fetchSuggestedActions();
          break;
        }

        default:
          break;
      }
    },
    [
      fetchSuggestedActions,
      addSuggestedAction,
      replaceSuggestedActions,
      removeSuggestedAction,
      updateSuggestedAction,
      setWsUpdating,
    ]
  );

  // Subscribe to dashboard WS topic for real-time updates
  const { connected: wsConnected } = useWebSocket({
    topic: "dashboard",
    onMessage: handleWsMessage,
    enabled: enableRealtime && role === "ops",
    id: "suggested-actions-ws",
  });

  // ---- Handlers ----

  const handleNavigate = useCallback(
    (action: SuggestedAction) => {
      clearNewMarker(action.id);
      if (action.link) {
        actOnAction(action.id);
        navigate(action.link);
      }
    },
    [navigate, clearNewMarker, actOnAction]
  );

  const handleDismiss = useCallback(
    async (actionId: string) => {
      setDismissingIds((prev) => new Set(prev).add(actionId));

      // Wait for animation
      setTimeout(async () => {
        await dismissAction(actionId);
        setDismissingIds((prev) => {
          const next = new Set(prev);
          next.delete(actionId);
          return next;
        });
      }, 300);
    },
    [dismissAction]
  );

  const handleRefresh = useCallback(() => {
    fetchSuggestedActions();
  }, [fetchSuggestedActions]);

  // ---- Render ----

  const showLoading = (isLoading || isLoadingActions) && rawActions.length === 0;
  const newCount = visibleActions.filter((a) => newActionIds.has(a.id)).length;

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card overflow-hidden transition-all duration-300",
        isWsUpdating && "ring-1 ring-primary/30",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-4 pb-2">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Zap className="h-5 w-5 text-amber-400" />
            {newCount > 0 && (
              <span className="absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full bg-primary text-[9px] font-bold text-primary-foreground flex items-center justify-center animate-bounce">
                {newCount}
              </span>
            )}
          </div>
          <h3 className="text-lg font-semibold">Suggested Actions</h3>
          {visibleActions.length > 0 && (
            <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
              {visibleActions.length}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {/* Connection indicator */}
          {enableRealtime && (
            <div
              className={cn(
                "flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full",
                wsConnected
                  ? "text-emerald-400 bg-emerald-500/10"
                  : "text-muted-foreground bg-muted"
              )}
              title={wsConnected ? "Live updates active" : "Connecting..."}
            >
              {wsConnected ? (
                <Wifi className="h-2.5 w-2.5" />
              ) : (
                <WifiOff className="h-2.5 w-2.5" />
              )}
              <span className="hidden sm:inline">{wsConnected ? "Live" : "Offline"}</span>
            </div>
          )}

          {/* Refresh button */}
          <button
            onClick={handleRefresh}
            disabled={isLoadingActions}
            className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            title="Refresh suggested actions"
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", isLoadingActions && "animate-spin")}
            />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="px-2 pb-2">
        {showLoading ? (
          <div className="space-y-1">
            <ActionSkeleton />
            <ActionSkeleton />
            <ActionSkeleton />
          </div>
        ) : visibleActions.length === 0 ? (
          <div className="py-8 text-center">
            <Zap className="h-8 w-8 text-muted-foreground/30 mx-auto mb-2" />
            <p className="text-sm text-muted-foreground">
              No suggested actions at this time.
            </p>
            <p className="text-xs text-muted-foreground/60 mt-1">
              Actions will appear when agents detect opportunities.
            </p>
          </div>
        ) : (
          <>
            <div className="space-y-0.5">
              {displayActions.map((action) => (
                <ActionCard
                  key={action.id}
                  action={action}
                  isNew={newActionIds.has(action.id)}
                  onNavigate={handleNavigate}
                  onDismiss={handleDismiss}
                  isDismissing={dismissingIds.has(action.id)}
                />
              ))}
            </div>

            {/* Show more / less toggle */}
            {hasMore && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="w-full flex items-center justify-center gap-1 py-2 mt-1 text-xs text-muted-foreground hover:text-foreground transition-colors rounded-md hover:bg-muted/50"
              >
                {expanded ? (
                  <>
                    <ChevronUp className="h-3 w-3" />
                    Show less
                  </>
                ) : (
                  <>
                    <ChevronDown className="h-3 w-3" />
                    Show {hiddenCount} more
                  </>
                )}
              </button>
            )}
          </>
        )}
      </div>

      {/* Real-time update indicator bar */}
      {lastWsRefresh > 0 && (
        <div className="px-4 py-1.5 border-t border-border/50 flex items-center justify-between">
          <span className="text-[10px] text-muted-foreground/60">
            Last updated {relativeTime(new Date(lastWsRefresh).toISOString())}
          </span>
          {wsConnected && (
            <span className="flex items-center gap-1 text-[10px] text-emerald-400/70">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Watching for changes
            </span>
          )}
        </div>
      )}
    </div>
  );
}
