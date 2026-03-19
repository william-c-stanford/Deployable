/**
 * useRealtimeSync — Master hook that subscribes to WebSocket topics
 * based on the current user role and routes events to the appropriate
 * Zustand stores for real-time state sync.
 *
 * Usage: Place once in AppLayout or App root.
 *
 * Topics by role:
 * - ops:        recommendations, dashboard, technicians, confirmations, timesheets
 * - technician: technician-portal, training, assignments
 * - partner:    confirmations, timesheets
 * - all roles:  notifications (via "all" topic)
 */

import { useEffect, useCallback, useRef } from "react";
import { useAuthStore, type Role } from "@/stores/authStore";
import { useNotificationStore } from "@/stores/notificationStore";
import { useAgentInboxStore } from "@/stores/agentInboxStore";
import { useDashboardStore } from "@/stores/dashboardStore";
import { usePartnerStore } from "@/stores/partnerStore";
import { wsManager } from "@/lib/wsManager";
import type { WSEvent, Recommendation } from "@/types";

/** Topics each role should subscribe to */
const ROLE_TOPICS: Record<Role, string[]> = {
  ops: ["recommendations", "dashboard", "technicians", "confirmations", "timesheets", "notifications"],
  technician: ["technician-portal", "training", "assignments", "notifications"],
  partner: ["confirmations", "timesheets", "notifications"],
};

/**
 * Central real-time sync hook.
 * Subscribes to WebSocket topics based on current role,
 * routes messages to the appropriate stores,
 * and manages subscription lifecycle on role switch.
 */
export function useRealtimeSync() {
  const role = useAuthStore((s) => s.role);
  const token = useAuthStore((s) => s.token);

  // Use refs to avoid stale closures in handlers
  const roleRef = useRef(role);
  roleRef.current = role;

  // ---- Event Handlers by Topic ----

  const handleRecommendationEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();
    const inboxStore = useAgentInboxStore.getState();

    switch (event.event_type) {
      case "recommendation.created": {
        if (event.recommendation) {
          const rec = event.recommendation as Recommendation;
          // Add to pending recommendations if not already present
          const existing = inboxStore.pendingRecommendations.find(
            (r) => r.id === rec.id,
          );
          if (!existing) {
            inboxStore.setPendingRecommendations([
              rec,
              ...inboxStore.pendingRecommendations,
            ]);
          }
        }
        addFromWSEvent(event);
        break;
      }

      case "recommendation.updated": {
        if (event.recommendation) {
          const rec = event.recommendation as Recommendation;
          const updated = inboxStore.pendingRecommendations.map((r) =>
            r.id === rec.id ? { ...r, ...rec } : r,
          );
          inboxStore.setPendingRecommendations(updated);
        }
        break;
      }

      case "recommendation.status_changed": {
        if (event.recommendation) {
          const rec = event.recommendation as Recommendation;
          const updated = inboxStore.pendingRecommendations.map((r) =>
            r.id === rec.id ? { ...r, status: rec.status } : r,
          );
          inboxStore.setPendingRecommendations(updated);
        }
        addFromWSEvent(event);
        break;
      }

      case "recommendation.batch_refreshed": {
        // Full refresh of recommendation list
        const data = event.data as { recommendations?: Recommendation[] } | undefined;
        if (data?.recommendations) {
          inboxStore.setPendingRecommendations(data.recommendations);
        }
        addFromWSEvent(event);
        break;
      }

      case "recommendation.list_refresh": {
        // Signal from server that recommendations have changed (batch, rule change, etc.)
        // Trigger a re-fetch of recommendations from the API so the inbox updates
        inboxStore.fetchRecommendations();
        // The notification store handles badge count updates
        addFromWSEvent(event);
        break;
      }

      case "recommendation.executed":
      case "recommendation.rejected": {
        // Status change from backend worker — update badge counts
        addFromWSEvent(event);
        break;
      }

      default:
        // Forward unknown recommendation events as notifications
        addFromWSEvent(event);
    }
  }, []);

  const handleDashboardEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();
    const dashStore = useDashboardStore.getState();

    switch (event.event_type) {
      case "dashboard.kpi_updated": {
        // Refresh KPIs from the server
        dashStore.refreshKPIs();
        break;
      }

      case "dashboard.activity": {
        // Add activity to the recent activity list
        const data = event.data as { activity?: unknown } | undefined;
        if (data?.activity) {
          // Trigger a full dashboard refresh to pick up the new activity
          dashStore.fetchDashboard();
        }
        break;
      }

      case "dashboard.suggested_action": {
        // Route to the enhanced suggested actions handler in the store
        const data = event.data as Record<string, unknown> | undefined;
        if (data) {
          const subAction = (data.action as string) || "refresh";

          if (subAction === "created" && data.suggested_action) {
            dashStore.setWsUpdating(true);
            dashStore.addSuggestedAction(data.suggested_action as any);
            setTimeout(() => dashStore.setWsUpdating(false), 800);
          } else if (subAction === "removed" && data.action_id) {
            dashStore.removeSuggestedAction(data.action_id as string);
          } else if (subAction === "updated" && data.action_id && data.updates) {
            dashStore.updateSuggestedAction(
              data.action_id as string,
              data.updates as any
            );
          } else if (subAction === "batch_refresh" && data.actions) {
            dashStore.setWsUpdating(true);
            dashStore.replaceSuggestedActions(data.actions as any[]);
            setTimeout(() => dashStore.setWsUpdating(false), 800);
          } else {
            // Fallback: refresh from API
            dashStore.fetchSuggestedActions();
          }
        } else {
          // No data payload — full refresh
          dashStore.fetchSuggestedActions();
        }
        addFromWSEvent(event);
        break;
      }

      default:
        addFromWSEvent(event);
    }
  }, []);

  const handleTechnicianEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();

    switch (event.event_type) {
      case "technician.training_advanced":
      case "technician.cert_expiring":
      case "technician.deployability_changed":
      case "technician.updated":
        // These are informational pushes — notify and let the user refresh
        addFromWSEvent(event);
        break;

      default:
        addFromWSEvent(event);
    }
  }, []);

  const handleConfirmationEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();
    const partnerStore = usePartnerStore.getState();

    // Route confirmation events to the partner store
    if (event.event_type?.startsWith("confirmation.")) {
      partnerStore.handleWsEvent({
        event_type: event.event_type,
        topic: event.topic,
        confirmation: event.confirmation as any,
        timestamp: event.timestamp,
      });
    }

    addFromWSEvent(event);
  }, []);

  const handleTimesheetEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();

    switch (event.event_type) {
      case "timesheet.submitted":
      case "timesheet.approved":
      case "timesheet.flagged":
      case "timesheet.dispute_opened":
      case "timesheet.dispute_resolved":
        addFromWSEvent(event);
        break;

      default:
        addFromWSEvent(event);
    }
  }, []);

  const handleTrainingEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();

    switch (event.event_type) {
      case "training.hours_logged":
      case "training.advancement":
        addFromWSEvent(event);
        break;

      default:
        addFromWSEvent(event);
    }
  }, []);

  const handleAssignmentEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();
    addFromWSEvent(event);
  }, []);

  const handleTechPortalEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();
    addFromWSEvent(event);
  }, []);

  const handleNotificationEvent = useCallback((event: WSEvent<unknown>) => {
    const { addFromWSEvent } = useNotificationStore.getState();
    // The notification store's addFromWSEvent handles:
    // - badge_count.updated → updates server badge counts
    // - notification.created → creates notification entry
    // - recommendation.list_refresh → signals recommendation re-fetch
    addFromWSEvent(event);
  }, []);

  /** Route a message to the right handler based on its topic */
  const routeMessage = useCallback(
    (topic: string, data: WSEvent<unknown>) => {
      switch (topic) {
        case "recommendations":
          handleRecommendationEvent(data);
          break;
        case "dashboard":
          handleDashboardEvent(data);
          break;
        case "technicians":
          handleTechnicianEvent(data);
          break;
        case "confirmations":
          handleConfirmationEvent(data);
          break;
        case "timesheets":
          handleTimesheetEvent(data);
          break;
        case "training":
          handleTrainingEvent(data);
          break;
        case "assignments":
          handleAssignmentEvent(data);
          break;
        case "technician-portal":
          handleTechPortalEvent(data);
          break;
        case "notifications":
          handleNotificationEvent(data);
          break;
        default:
          // Unknown topic — still generate a notification
          useNotificationStore.getState().addFromWSEvent(data);
      }
    },
    [
      handleRecommendationEvent,
      handleDashboardEvent,
      handleTechnicianEvent,
      handleConfirmationEvent,
      handleTimesheetEvent,
      handleTrainingEvent,
      handleAssignmentEvent,
      handleTechPortalEvent,
      handleNotificationEvent,
    ],
  );

  // ---- Subscription Lifecycle ----

  useEffect(() => {
    if (!token) return;

    const topics = ROLE_TOPICS[role] || [];
    const unregisters: Array<() => void> = [];

    // Register notification store reset on role switch
    const unregisterReset = useAuthStore
      .getState()
      .registerStoreReset(() => {
        useNotificationStore.getState().reset();
      });

    // Subscribe to each topic for the current role
    for (const topic of topics) {
      const connectionId = `realtime-${role}-${topic}`;
      const unregister = wsManager.register(
        connectionId,
        topic,
        (data: WSEvent<unknown>) => routeMessage(topic, data),
      );
      unregisters.push(unregister);
    }

    return () => {
      // Cleanup all subscriptions when role or token changes
      for (const unreg of unregisters) {
        unreg();
      }
      unregisterReset();
    };
  }, [role, token, routeMessage]);

  // Return current subscription info for debugging/display
  return {
    role,
    topics: ROLE_TOPICS[role] || [],
    subscriptions: wsManager.getSubscriptions(),
  };
}

/**
 * Hook to get notification badge count for a specific category.
 * Lightweight selector — re-renders only when the count changes.
 */
export function useNotificationBadge(
  category: "recommendations" | "confirmations" | "timesheets" | "training" | "escalations" | "general",
): number {
  return useNotificationStore((s) => s.badgeCounts[category]);
}

/**
 * Hook to get the total unread notification count.
 */
export function useTotalUnreadCount(): number {
  return useNotificationStore((s) => s.totalUnread);
}
