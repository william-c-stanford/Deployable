import { useEffect, useRef, useCallback, useState, useMemo } from "react";
import { wsManager } from "@/lib/wsManager";
import type { WSConnectionStatus } from "@/types";

interface UseWebSocketOptions {
  /** Topic to subscribe to (e.g. "recommendations", "dashboard", "confirmations", "all") */
  topic: string;
  /** Handler for incoming messages */
  onMessage: (data: any) => void;
  /** Enable/disable the connection (default: true) */
  enabled?: boolean;
  /** Unique id for this subscription. Defaults to topic. */
  id?: string;
}

interface UseWebSocketReturn {
  /** Whether the WebSocket is currently connected */
  connected: boolean;
  /** Detailed connection status */
  status: WSConnectionStatus;
}

/**
 * Custom hook for WebSocket connection with topic-based subscriptions.
 *
 * Uses the centralized wsManager so connections are properly torn down
 * and re-established when the auth token changes (role switch).
 *
 * Connection status is event-driven (no polling).
 */
export function useWebSocket({
  topic,
  onMessage,
  enabled = true,
  id,
}: UseWebSocketOptions): UseWebSocketReturn {
  const onMessageRef = useRef(onMessage);
  const [status, setStatus] = useState<WSConnectionStatus>("disconnected");

  // Keep callback ref fresh without triggering reconnects
  onMessageRef.current = onMessage;

  // Stable message handler that delegates to the ref
  const stableHandler = useCallback((data: any) => {
    onMessageRef.current(data);
  }, []);

  // Compute a stable connection id
  const connectionId = useMemo(
    () => id || `ws-hook-${topic}`,
    [id, topic],
  );

  useEffect(() => {
    if (!enabled) {
      setStatus("disconnected");
      return;
    }

    // Listen for status changes on this connection
    const unsubscribeStatus = wsManager.onStatusChange(
      (changedId, newStatus) => {
        if (changedId === connectionId) {
          setStatus(newStatus);
        }
      },
    );

    // Register with the centralized WebSocket manager
    const unregister = wsManager.register(
      connectionId,
      topic,
      stableHandler,
    );

    // Set initial status
    setStatus(wsManager.getStatus(connectionId));

    return () => {
      unsubscribeStatus();
      unregister();
      setStatus("disconnected");
    };
  }, [connectionId, topic, enabled, stableHandler]);

  return {
    connected: status === "connected",
    status,
  };
}

/**
 * Hook to subscribe to multiple topics at once.
 * Returns combined connection status.
 */
export function useMultiTopicWebSocket(
  subscriptions: Array<{
    topic: string;
    id: string;
    onMessage: (data: any) => void;
  }>,
  enabled: boolean = true,
): {
  statuses: Record<string, WSConnectionStatus>;
  allConnected: boolean;
  anyConnected: boolean;
} {
  const [statuses, setStatuses] = useState<Record<string, WSConnectionStatus>>({});
  const handlersRef = useRef(subscriptions);
  handlersRef.current = subscriptions;

  useEffect(() => {
    if (!enabled || subscriptions.length === 0) return;

    const unregisters: Array<() => void> = [];

    // Listen for status changes
    const unsubscribeStatus = wsManager.onStatusChange((changedId, newStatus) => {
      setStatuses((prev) => {
        if (prev[changedId] === newStatus) return prev;
        return { ...prev, [changedId]: newStatus };
      });
    });

    // Register each subscription
    for (const sub of handlersRef.current) {
      const stableHandler = (data: any) => {
        // Find current handler (may have changed via ref)
        const current = handlersRef.current.find((s) => s.id === sub.id);
        if (current) current.onMessage(data);
      };

      const unregister = wsManager.register(sub.id, sub.topic, stableHandler);
      unregisters.push(unregister);

      setStatuses((prev) => ({
        ...prev,
        [sub.id]: wsManager.getStatus(sub.id),
      }));
    }

    return () => {
      unsubscribeStatus();
      for (const unreg of unregisters) {
        unreg();
      }
      setStatuses({});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, subscriptions.map((s) => `${s.id}:${s.topic}`).join(",")]);

  const values = Object.values(statuses);
  return {
    statuses,
    allConnected: values.length > 0 && values.every((s) => s === "connected"),
    anyConnected: values.some((s) => s === "connected"),
  };
}
