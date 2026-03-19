/**
 * useSkillBreakdownWebSocket — subscribes to real-time skill breakdown
 * lifecycle events via WebSocket.
 *
 * Listens on the "skill_breakdowns" topic for:
 * - skill_breakdown.submitted  → new breakdown submitted by technician/ops
 * - skill_breakdown.approved   → partner approved breakdown
 * - skill_breakdown.rejected   → partner rejected breakdown
 * - skill_breakdown.revision_requested → partner requested revisions
 *
 * Callers provide handlers that receive the full SkillBreakdownWSEvent
 * payload for UI updates (toast notifications, list refreshes, etc.).
 */

import { useCallback, useRef } from "react";
import { useWebSocket } from "./useWebSocket";
import { useNotificationStore } from "@/stores/notificationStore";
import type { SkillBreakdownWSEvent } from "@/types";

export interface SkillBreakdownWSHandlers {
  onSubmitted?: (event: SkillBreakdownWSEvent) => void;
  onApproved?: (event: SkillBreakdownWSEvent) => void;
  onRejected?: (event: SkillBreakdownWSEvent) => void;
  onRevisionRequested?: (event: SkillBreakdownWSEvent) => void;
  /** Called for any skill breakdown event */
  onAny?: (event: SkillBreakdownWSEvent) => void;
}

export function useSkillBreakdownWebSocket(
  handlers: SkillBreakdownWSHandlers = {},
  enabled = true,
) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const addFromWSEvent = useNotificationStore((s) => s.addFromWSEvent);

  const onMessage = useCallback(
    (data: any) => {
      const event = data as SkillBreakdownWSEvent;
      if (!event?.event_type?.startsWith("skill_breakdown.")) return;

      // Feed to notification store for badge counts
      addFromWSEvent(data);

      // Call specific handler based on event type
      const h = handlersRef.current;
      switch (event.event_type) {
        case "skill_breakdown.submitted":
          h.onSubmitted?.(event);
          break;
        case "skill_breakdown.approved":
          h.onApproved?.(event);
          break;
        case "skill_breakdown.rejected":
          h.onRejected?.(event);
          break;
        case "skill_breakdown.revision_requested":
          h.onRevisionRequested?.(event);
          break;
      }

      // Always call onAny
      h.onAny?.(event);
    },
    [addFromWSEvent],
  );

  const { connected } = useWebSocket({
    topic: "skill_breakdowns",
    onMessage,
    enabled,
    id: "skill-breakdowns-ws",
  });

  return { connected };
}
