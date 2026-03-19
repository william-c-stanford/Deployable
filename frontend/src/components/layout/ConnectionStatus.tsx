/**
 * ConnectionStatus — Small indicator showing WebSocket connection health.
 * Shows a colored dot with tooltip showing topic subscription details.
 */

import { useState, useEffect } from "react";
import { Wifi, WifiOff } from "lucide-react";
import { wsManager } from "@/lib/wsManager";
import type { WSConnectionStatus } from "@/types";
import { cn } from "@/lib/utils";

export function ConnectionStatus() {
  const [subscriptions, setSubscriptions] = useState<
    Array<{ id: string; topic: string; status: WSConnectionStatus }>
  >([]);
  const [showTooltip, setShowTooltip] = useState(false);

  useEffect(() => {
    // Update on any connection state change
    const unsub = wsManager.subscribe(() => {
      setSubscriptions(wsManager.getSubscriptions());
    });
    // Also update on specific status changes
    const unsubStatus = wsManager.onStatusChange(() => {
      setSubscriptions(wsManager.getSubscriptions());
    });
    // Initial state
    setSubscriptions(wsManager.getSubscriptions());

    return () => {
      unsub();
      unsubStatus();
    };
  }, []);

  const connectedCount = subscriptions.filter(
    (s) => s.status === "connected",
  ).length;
  const totalCount = subscriptions.length;
  const allConnected = totalCount > 0 && connectedCount === totalCount;
  const anyConnected = connectedCount > 0;
  const anyReconnecting = subscriptions.some(
    (s) => s.status === "reconnecting",
  );

  const statusColor = allConnected
    ? "bg-green-500"
    : anyReconnecting
      ? "bg-yellow-500 animate-pulse"
      : anyConnected
        ? "bg-yellow-500"
        : totalCount > 0
          ? "bg-red-500"
          : "bg-gray-400";

  const StatusIcon = anyConnected ? Wifi : WifiOff;

  return (
    <div
      className="relative"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-md hover:bg-muted/50 cursor-default transition-colors">
        <div className={cn("h-2 w-2 rounded-full", statusColor)} />
        <StatusIcon className="h-3.5 w-3.5 text-muted-foreground" />
      </div>

      {/* Tooltip */}
      {showTooltip && subscriptions.length > 0 && (
        <div className="absolute right-0 top-full mt-1 w-56 z-50 bg-popover border border-border rounded-lg shadow-lg p-3 animate-in fade-in duration-100">
          <p className="text-xs font-semibold mb-2">
            WebSocket Connections ({connectedCount}/{totalCount})
          </p>
          <div className="space-y-1.5">
            {subscriptions.map((sub) => (
              <div
                key={sub.id}
                className="flex items-center justify-between text-xs"
              >
                <span className="text-muted-foreground truncate max-w-[140px]">
                  {sub.topic}
                </span>
                <div className="flex items-center gap-1.5">
                  <div
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      sub.status === "connected"
                        ? "bg-green-500"
                        : sub.status === "reconnecting"
                          ? "bg-yellow-500 animate-pulse"
                          : sub.status === "connecting"
                            ? "bg-blue-500 animate-pulse"
                            : "bg-red-500",
                    )}
                  />
                  <span
                    className={cn(
                      sub.status === "connected"
                        ? "text-green-500"
                        : sub.status === "reconnecting"
                          ? "text-yellow-500"
                          : "text-red-500",
                    )}
                  >
                    {sub.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
