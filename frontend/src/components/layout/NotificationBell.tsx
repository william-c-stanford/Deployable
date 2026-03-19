/**
 * NotificationBell — Displays unread notification count badge
 * and opens a notification tray dropdown.
 */

import { Bell, Check, X, ExternalLink } from "lucide-react";
import { useNotificationStore } from "@/stores/notificationStore";
import { useTotalUnreadCount } from "@/hooks/useRealtimeSync";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";

export function NotificationBell() {
  const totalUnread = useTotalUnreadCount();
  const isTrayOpen = useNotificationStore((s) => s.isTrayOpen);
  const toggleTray = useNotificationStore((s) => s.toggleTray);
  const setTrayOpen = useNotificationStore((s) => s.setTrayOpen);
  const notifications = useNotificationStore((s) => s.notifications);
  const markRead = useNotificationStore((s) => s.markRead);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const navigate = useNavigate();

  const recentNotifications = notifications.slice(0, 10);

  const handleNotificationClick = (notif: (typeof notifications)[0]) => {
    markRead(notif.id);
    if (notif.action_url) {
      navigate(notif.action_url);
      setTrayOpen(false);
    }
  };

  const severityColors: Record<string, string> = {
    info: "bg-blue-500",
    warning: "bg-yellow-500",
    success: "bg-green-500",
    error: "bg-red-500",
  };

  return (
    <div className="relative">
      {/* Bell Button */}
      <button
        onClick={toggleTray}
        className="relative p-2 rounded-lg hover:bg-muted transition-colors"
        aria-label={`Notifications${totalUnread > 0 ? ` (${totalUnread} unread)` : ""}`}
      >
        <Bell className="h-5 w-5 text-muted-foreground" />
        {totalUnread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-bold text-white bg-red-500 rounded-full animate-in fade-in zoom-in duration-200">
            {totalUnread > 99 ? "99+" : totalUnread}
          </span>
        )}
      </button>

      {/* Dropdown Tray */}
      {isTrayOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setTrayOpen(false)}
          />

          {/* Tray */}
          <div className="absolute right-0 top-full mt-2 w-80 sm:w-96 max-h-[480px] z-50 bg-popover border border-border rounded-xl shadow-xl overflow-hidden animate-in slide-in-from-top-2 duration-200">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/50">
              <h3 className="text-sm font-semibold">Notifications</h3>
              <div className="flex items-center gap-2">
                {totalUnread > 0 && (
                  <button
                    onClick={markAllRead}
                    className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
                  >
                    <Check className="h-3 w-3" />
                    Mark all read
                  </button>
                )}
                <button
                  onClick={() => setTrayOpen(false)}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            {/* Notification List */}
            <div className="overflow-y-auto max-h-[400px]">
              {recentNotifications.length === 0 ? (
                <div className="px-4 py-8 text-center text-muted-foreground text-sm">
                  <Bell className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p>No notifications yet</p>
                  <p className="text-xs mt-1">
                    Real-time updates will appear here
                  </p>
                </div>
              ) : (
                recentNotifications.map((notif) => (
                  <button
                    key={notif.id}
                    onClick={() => handleNotificationClick(notif)}
                    className={cn(
                      "w-full text-left px-4 py-3 border-b border-border/50 hover:bg-muted/50 transition-colors",
                      !notif.read && "bg-primary/5",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      {/* Severity dot */}
                      <div
                        className={cn(
                          "mt-1.5 h-2 w-2 rounded-full flex-shrink-0",
                          severityColors[notif.severity] || "bg-blue-500",
                        )}
                      />

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <p
                            className={cn(
                              "text-sm truncate",
                              !notif.read
                                ? "font-semibold text-foreground"
                                : "font-medium text-muted-foreground",
                            )}
                          >
                            {notif.title}
                          </p>
                          <span className="text-[10px] text-muted-foreground flex-shrink-0">
                            {formatTimeAgo(notif.created_at)}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                          {notif.message}
                        </p>
                        {notif.action_url && (
                          <span className="inline-flex items-center gap-1 text-[10px] text-primary mt-1">
                            <ExternalLink className="h-2.5 w-2.5" />
                            View details
                          </span>
                        )}
                      </div>

                      {/* Unread indicator */}
                      {!notif.read && (
                        <div className="mt-1.5 h-2 w-2 rounded-full bg-primary flex-shrink-0" />
                      )}
                    </div>
                  </button>
                ))
              )}
            </div>

            {/* Footer */}
            {notifications.length > 10 && (
              <div className="px-4 py-2 border-t border-border bg-muted/30 text-center">
                <span className="text-xs text-muted-foreground">
                  Showing {Math.min(10, recentNotifications.length)} of{" "}
                  {notifications.length} notifications
                </span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Format a timestamp as a relative time string */
function formatTimeAgo(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 60) return "now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
  return `${Math.floor(diffSec / 86400)}d`;
}
