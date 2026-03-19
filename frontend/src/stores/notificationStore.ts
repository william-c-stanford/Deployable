import { create } from "zustand";
import type { WSNotification, WSEventType, WSEvent } from "@/types";

// ============================================================
// Notification Store — manages real-time notification badges
// and the notification tray for all roles
// ============================================================

/** Notification badge counts by category */
interface BadgeCounts {
  recommendations: number;
  confirmations: number;
  timesheets: number;
  training: number;
  escalations: number;
  general: number;
}

/** Server-pushed badge count (absolute, from backend query) */
interface ServerBadgeCounts {
  pending_recommendations: number;
  pending_confirmations: number;
  pending_timesheets: number;
  expiring_certs: number;
  pending_actions: number;
}

interface NotificationState {
  /** All notifications (most recent first) */
  notifications: WSNotification[];

  /** Badge counts by category (incremented client-side from WS events) */
  badgeCounts: BadgeCounts;

  /** Server-authoritative badge counts (absolute values from backend) */
  serverBadgeCounts: ServerBadgeCounts;

  /** Total unread count (for the main bell icon) */
  totalUnread: number;

  /** Whether the notification tray is open */
  isTrayOpen: boolean;

  /** Max notifications to keep in memory */
  maxNotifications: number;

  /** Timestamp of last recommendation list refresh signal */
  lastRecommendationRefresh: number;

  // ---- Actions ----

  /** Add a notification from a WebSocket event */
  addNotification: (notification: WSNotification) => void;

  /** Create a notification from a raw WS event */
  addFromWSEvent: (event: WSEvent) => void;

  /** Handle server-pushed badge count update (absolute value) */
  handleServerBadgeUpdate: (badgeType: string, count: number) => void;

  /** Handle server-pushed notification event */
  handleServerNotification: (event: Record<string, any>) => void;

  /** Handle recommendation list refresh signal */
  handleRecommendationRefresh: (pendingCount?: number) => void;

  /** Set server badge counts from initial API fetch */
  setServerBadgeCounts: (counts: Partial<ServerBadgeCounts>) => void;

  /** Mark a single notification as read */
  markRead: (id: string) => void;

  /** Mark all notifications as read */
  markAllRead: () => void;

  /** Clear all notifications */
  clearAll: () => void;

  /** Increment a specific badge counter */
  incrementBadge: (category: keyof BadgeCounts, amount?: number) => void;

  /** Decrement a specific badge counter */
  decrementBadge: (category: keyof BadgeCounts, amount?: number) => void;

  /** Reset a specific badge counter */
  resetBadge: (category: keyof BadgeCounts) => void;

  /** Reset all badge counters */
  resetAllBadges: () => void;

  /** Toggle notification tray */
  toggleTray: () => void;
  setTrayOpen: (open: boolean) => void;

  /** Reset store (for role switch) */
  reset: () => void;
}

const INITIAL_BADGES: BadgeCounts = {
  recommendations: 0,
  confirmations: 0,
  timesheets: 0,
  training: 0,
  escalations: 0,
  general: 0,
};

/** Map event types to badge categories */
function eventTypeToCategory(eventType: WSEventType | string): keyof BadgeCounts {
  if (eventType.startsWith("recommendation")) return "recommendations";
  if (eventType.startsWith("confirmation")) return "confirmations";
  if (eventType.startsWith("timesheet")) return "timesheets";
  if (eventType.startsWith("training")) return "training";
  if (
    eventType === "forward_staffing.gap_detected" ||
    eventType === "forward_staffing.recommendation"
  )
    return "recommendations";
  if (eventType.startsWith("skill_breakdown")) return "general";
  if (eventType.startsWith("badge") || eventType.startsWith("technician"))
    return "general";
  return "general";
}

/** Generate a notification from a WS event envelope */
function wsEventToNotification(event: WSEvent): WSNotification | null {
  const eventType = event.event_type;
  if (!eventType || eventType === "pong") return null;

  // If the event already contains a notification payload, use it
  if (event.notification) {
    return event.notification as WSNotification;
  }

  // Auto-generate a notification from the event data
  const titleMap: Partial<Record<WSEventType, string>> = {
    "recommendation.created": "New Recommendation",
    "recommendation.updated": "Recommendation Updated",
    "recommendation.status_changed": "Recommendation Status Changed",
    "recommendation.batch_refreshed": "Recommendations Refreshed",
    "dashboard.kpi_updated": "Dashboard Updated",
    "dashboard.activity": "New Activity",
    "dashboard.suggested_action": "New Suggested Action",
    "technician.updated": "Technician Updated",
    "technician.training_advanced": "Training Advancement",
    "technician.cert_expiring": "Certification Expiring",
    "technician.deployability_changed": "Deployability Changed",
    "assignment.created": "New Assignment",
    "assignment.updated": "Assignment Updated",
    "assignment.status_changed": "Assignment Status Changed",
    "confirmation.created": "Confirmation Required",
    "confirmation.responded": "Confirmation Response",
    "confirmation.escalated": "Confirmation Escalated",
    "timesheet.submitted": "Timesheet Submitted",
    "timesheet.approved": "Timesheet Approved",
    "timesheet.flagged": "Timesheet Flagged",
    "timesheet.dispute_opened": "Dispute Opened",
    "timesheet.dispute_resolved": "Dispute Resolved",
    "training.hours_logged": "Training Hours Logged",
    "training.advancement": "Training Advancement",
    "badge.granted": "Badge Granted",
    "badge.revoked": "Badge Revoked",
    "forward_staffing.gap_detected": "Staffing Gap Detected",
    "forward_staffing.recommendation": "Forward Staffing Recommendation",
    "agent.rule_proposed": "Rule Proposed",
    "agent.rule_applied": "Rule Applied",
    notification: "Notification",
  };

  const severityMap: Partial<Record<WSEventType, WSNotification["severity"]>> = {
    "recommendation.created": "info",
    "technician.cert_expiring": "warning",
    "confirmation.escalated": "error",
    "timesheet.flagged": "warning",
    "timesheet.dispute_opened": "warning",
    "training.advancement": "success",
    "badge.granted": "success",
    "forward_staffing.gap_detected": "warning",
  };

  const title = titleMap[eventType as WSEventType] || "Update";
  const severity = severityMap[eventType as WSEventType] || "info";

  // Extract a message from the event data
  let message = "";
  if (event.recommendation) {
    message = event.recommendation.explanation || `${event.recommendation.type} recommendation`;
  } else if (event.data && typeof event.data === "object") {
    const d = event.data as Record<string, unknown>;
    message =
      (d.message as string) ||
      (d.description as string) ||
      (d.explanation as string) ||
      `${eventType} event`;
  } else {
    message = `${eventType} event received`;
  }

  return {
    id: `notif-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: eventType,
    title,
    message,
    severity,
    created_at: event.timestamp || new Date().toISOString(),
    read: false,
  };
}

const INITIAL_SERVER_BADGES: ServerBadgeCounts = {
  pending_recommendations: 0,
  pending_confirmations: 0,
  pending_timesheets: 0,
  expiring_certs: 0,
  pending_actions: 0,
};

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  badgeCounts: { ...INITIAL_BADGES },
  serverBadgeCounts: { ...INITIAL_SERVER_BADGES },
  totalUnread: 0,
  isTrayOpen: false,
  maxNotifications: 100,
  lastRecommendationRefresh: 0,

  addNotification: (notification) => {
    set((state) => {
      const newNotifications = [notification, ...state.notifications].slice(
        0,
        state.maxNotifications,
      );
      const category = eventTypeToCategory(notification.type);
      const newBadges = { ...state.badgeCounts };
      if (!notification.read) {
        newBadges[category] = (newBadges[category] || 0) + 1;
      }
      const totalUnread = Object.values(newBadges).reduce((a, b) => a + b, 0);

      return {
        notifications: newNotifications,
        badgeCounts: newBadges,
        totalUnread,
      };
    });
  },

  addFromWSEvent: (event) => {
    // Handle server-pushed badge count updates
    if (event.event_type === "badge_count.updated") {
      const e = event as any;
      get().handleServerBadgeUpdate(e.badge_type, e.count);
      return;
    }

    // Handle recommendation list refresh signals
    if (event.event_type === "recommendation.list_refresh") {
      const e = event as any;
      get().handleRecommendationRefresh(e.pending_count);
      return;
    }

    // Handle server-pushed notification events
    if (event.event_type === "notification.created") {
      get().handleServerNotification(event as any);
      return;
    }

    // Default: generate notification from event
    const notification = wsEventToNotification(event);
    if (notification) {
      get().addNotification(notification);
    }
  },

  handleServerBadgeUpdate: (badgeType, count) => {
    set((state) => {
      // Map server badge types to our server badge count keys
      const badgeMap: Record<string, keyof ServerBadgeCounts> = {
        pending_recommendations: "pending_recommendations",
        pending_confirmations: "pending_confirmations",
        pending_timesheets: "pending_timesheets",
        expiring_certs: "expiring_certs",
        pending_actions: "pending_actions",
      };

      const key = badgeMap[badgeType];
      if (!key) return state;

      const newServerBadges = { ...state.serverBadgeCounts, [key]: count };

      // Also update the client-side category badges to match server counts
      const categoryMap: Record<string, keyof BadgeCounts> = {
        pending_recommendations: "recommendations",
        pending_confirmations: "confirmations",
        pending_timesheets: "timesheets",
        expiring_certs: "general",
        pending_actions: "general",
      };
      const category = categoryMap[badgeType];
      const newBadges = { ...state.badgeCounts };
      if (category) {
        newBadges[category] = count;
      }
      const totalUnread = Object.values(newBadges).reduce((a, b) => a + b, 0);

      return {
        serverBadgeCounts: newServerBadges,
        badgeCounts: newBadges,
        totalUnread,
      };
    });
  },

  handleServerNotification: (event) => {
    const notification: WSNotification = {
      id: `notif-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type: event.notification_type || "notification",
      title: event.title || "Notification",
      message: event.message || "",
      severity: event.severity || "info",
      created_at: event.timestamp || new Date().toISOString(),
      read: false,
      link: event.link,
      entity_type: event.entity_type,
      entity_id: event.entity_id,
    };
    get().addNotification(notification);
  },

  handleRecommendationRefresh: (pendingCount) => {
    set((state) => {
      const updates: Partial<NotificationState> = {
        lastRecommendationRefresh: Date.now(),
      };

      if (pendingCount !== undefined) {
        updates.serverBadgeCounts = {
          ...state.serverBadgeCounts,
          pending_recommendations: pendingCount,
        };
        updates.badgeCounts = {
          ...state.badgeCounts,
          recommendations: pendingCount,
        };
        updates.totalUnread =
          Object.values({ ...state.badgeCounts, recommendations: pendingCount }).reduce(
            (a, b) => a + b,
            0,
          );
      }

      return updates as any;
    });
  },

  setServerBadgeCounts: (counts) => {
    set((state) => {
      const newServerBadges = { ...state.serverBadgeCounts, ...counts };
      // Sync relevant categories
      const newBadges = { ...state.badgeCounts };
      if (counts.pending_recommendations !== undefined) {
        newBadges.recommendations = counts.pending_recommendations;
      }
      if (counts.pending_confirmations !== undefined) {
        newBadges.confirmations = counts.pending_confirmations;
      }
      if (counts.pending_timesheets !== undefined) {
        newBadges.timesheets = counts.pending_timesheets;
      }
      const totalUnread = Object.values(newBadges).reduce((a, b) => a + b, 0);
      return {
        serverBadgeCounts: newServerBadges,
        badgeCounts: newBadges,
        totalUnread,
      };
    });
  },

  markRead: (id) => {
    set((state) => {
      const target = state.notifications.find((n) => n.id === id);
      if (!target || target.read) return state;

      const category = eventTypeToCategory(target.type);
      const newBadges = { ...state.badgeCounts };
      newBadges[category] = Math.max(0, newBadges[category] - 1);
      const totalUnread = Object.values(newBadges).reduce((a, b) => a + b, 0);

      return {
        notifications: state.notifications.map((n) =>
          n.id === id ? { ...n, read: true } : n,
        ),
        badgeCounts: newBadges,
        totalUnread,
      };
    });
  },

  markAllRead: () => {
    set((state) => ({
      notifications: state.notifications.map((n) => ({ ...n, read: true })),
      badgeCounts: { ...INITIAL_BADGES },
      totalUnread: 0,
    }));
  },

  clearAll: () => {
    set({
      notifications: [],
      badgeCounts: { ...INITIAL_BADGES },
      totalUnread: 0,
    });
  },

  incrementBadge: (category, amount = 1) => {
    set((state) => {
      const newBadges = { ...state.badgeCounts };
      newBadges[category] = (newBadges[category] || 0) + amount;
      return {
        badgeCounts: newBadges,
        totalUnread: Object.values(newBadges).reduce((a, b) => a + b, 0),
      };
    });
  },

  decrementBadge: (category, amount = 1) => {
    set((state) => {
      const newBadges = { ...state.badgeCounts };
      newBadges[category] = Math.max(0, (newBadges[category] || 0) - amount);
      return {
        badgeCounts: newBadges,
        totalUnread: Object.values(newBadges).reduce((a, b) => a + b, 0),
      };
    });
  },

  resetBadge: (category) => {
    set((state) => {
      const newBadges = { ...state.badgeCounts };
      newBadges[category] = 0;
      return {
        badgeCounts: newBadges,
        totalUnread: Object.values(newBadges).reduce((a, b) => a + b, 0),
      };
    });
  },

  resetAllBadges: () => {
    set({ badgeCounts: { ...INITIAL_BADGES }, totalUnread: 0 });
  },

  toggleTray: () => set((state) => ({ isTrayOpen: !state.isTrayOpen })),
  setTrayOpen: (open) => set({ isTrayOpen: open }),

  reset: () => {
    set({
      notifications: [],
      badgeCounts: { ...INITIAL_BADGES },
      serverBadgeCounts: { ...INITIAL_SERVER_BADGES },
      totalUnread: 0,
      isTrayOpen: false,
      lastRecommendationRefresh: 0,
    });
  },
}));
