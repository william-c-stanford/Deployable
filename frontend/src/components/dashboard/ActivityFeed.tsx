import { Activity } from "lucide-react";
import type { ActivityEntry } from "@/types/dashboard";

interface ActivityFeedProps {
  entries: ActivityEntry[];
}

function formatTime(dateStr?: string): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function getActionIcon(action: string): string {
  if (action.includes("recommend")) return "🤖";
  if (action.includes("assign")) return "👤";
  if (action.includes("approve")) return "✅";
  if (action.includes("reject")) return "❌";
  if (action.includes("create")) return "➕";
  if (action.includes("update")) return "✏️";
  return "📋";
}

export function ActivityFeed({ entries }: ActivityFeedProps) {
  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-6">
        <div className="flex items-center gap-2 mb-4">
          <Activity className="h-5 w-5 text-cyan-400" />
          <h3 className="text-lg font-semibold">Recent Activity</h3>
        </div>
        <p className="text-sm text-muted-foreground">No recent activity.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card p-6">
      <div className="flex items-center gap-2 mb-4">
        <Activity className="h-5 w-5 text-cyan-400" />
        <h3 className="text-lg font-semibold">Recent Activity</h3>
      </div>
      <div className="space-y-3">
        {entries.map((entry) => (
          <div key={entry.id} className="flex items-start gap-3 text-sm">
            <span className="text-base mt-0.5">{getActionIcon(entry.action)}</span>
            <div className="min-w-0 flex-1">
              <p className="text-foreground">
                <span className="font-medium">{entry.action}</span>
                {entry.entity_type && (
                  <span className="text-muted-foreground"> on {entry.entity_type}</span>
                )}
              </p>
              {entry.agent_name && (
                <p className="text-xs text-muted-foreground mt-0.5">by {entry.agent_name}</p>
              )}
            </div>
            <span className="text-xs text-muted-foreground whitespace-nowrap">
              {formatTime(entry.created_at)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
