import { useCallback, useEffect } from "react";
import { useWebSocket } from "./useWebSocket";
import { useNotificationStore } from "@/stores/notificationStore";
import type { WSEvent } from "@/types";

const API_BASE = import.meta.env.VITE_API_URL || "";

/**
 * Hook that subscribes to both "notifications" and "recommendations" WebSocket
 * topics and routes events to the notification store for badge count updates,
 * notification creation, and recommendation list refresh signals.
 *
 * Should be mounted once at the app layout level.
 */
export function useNotifications(enabled = true) {
  const { addFromWSEvent, setServerBadgeCounts } = useNotificationStore();

  // Handler for notifications topic
  const handleNotificationEvent = useCallback(
    (data: WSEvent) => {
      addFromWSEvent(data);
    },
    [addFromWSEvent],
  );

  // Handler for recommendations topic (also carries badge counts)
  const handleRecommendationEvent = useCallback(
    (data: WSEvent) => {
      addFromWSEvent(data);
    },
    [addFromWSEvent],
  );

  // Subscribe to notifications topic
  const { connected: notifConnected } = useWebSocket({
    topic: "notifications",
    onMessage: handleNotificationEvent,
    enabled,
    id: "global-notifications",
  });

  // Subscribe to recommendations topic
  const { connected: recsConnected } = useWebSocket({
    topic: "recommendations",
    onMessage: handleRecommendationEvent,
    enabled,
    id: "global-recommendations",
  });

  // Fetch initial badge counts from API on mount
  useEffect(() => {
    if (!enabled) return;

    const fetchBadgeCounts = async () => {
      try {
        const token = localStorage.getItem("deployable_token");
        const headers: Record<string, string> = {};
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
        }

        const res = await fetch(`${API_BASE}/api/notifications/badge-counts`, {
          headers,
        });
        if (res.ok) {
          const data = await res.json();
          if (data.counts) {
            setServerBadgeCounts(data.counts);
          }
        }
      } catch {
        // Silently fail — badge counts will update via WebSocket
      }
    };

    fetchBadgeCounts();
  }, [enabled, setServerBadgeCounts]);

  return {
    notifConnected,
    recsConnected,
    connected: notifConnected || recsConnected,
  };
}
