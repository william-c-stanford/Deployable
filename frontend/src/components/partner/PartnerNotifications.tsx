import { usePartnerStore } from "@/stores/partnerStore";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Bell,
  CalendarCheck,
  CalendarX,
  AlertTriangle,
} from "lucide-react";

const typeConfig: Record<string, { icon: typeof Bell; color: string; label: string }> = {
  assignment_starting: {
    icon: CalendarCheck,
    color: "text-emerald-500",
    label: "Starting Soon",
  },
  assignment_ending: {
    icon: CalendarX,
    color: "text-amber-500",
    label: "Ending Soon",
  },
};

const statusConfig: Record<string, { variant: "default" | "secondary" | "outline"; className: string }> = {
  pending: { variant: "default", className: "bg-amber-500" },
  confirmed: { variant: "default", className: "bg-emerald-600" },
  dismissed: { variant: "secondary", className: "" },
};

export function PartnerNotifications() {
  const { notifications, isLoading } = usePartnerStore();

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Card key={i}>
            <CardContent className="p-4">
              <div className="space-y-2">
                <div className="h-4 w-48 rounded bg-muted animate-pulse" />
                <div className="h-3 w-32 rounded bg-muted animate-pulse" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (notifications.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-12">
          <Bell className="h-10 w-10 text-muted-foreground mb-3" />
          <h3 className="font-semibold text-lg">No Alerts</h3>
          <p className="text-sm text-muted-foreground mt-1">
            You'll be notified 48 hours before assignment changes.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {notifications.map((notif) => {
        const cfg = typeConfig[notif.notification_type] || {
          icon: AlertTriangle,
          color: "text-muted-foreground",
          label: notif.notification_type,
        };
        const StatusIcon = cfg.icon;
        const sCfg = statusConfig[notif.status] || statusConfig.pending;

        return (
          <Card key={notif.id} className="border">
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                <div className={`mt-0.5 ${cfg.color}`}>
                  <StatusIcon className="h-5 w-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h4 className="text-sm font-semibold">{notif.title}</h4>
                      {notif.message && (
                        <p className="text-xs text-muted-foreground mt-0.5">{notif.message}</p>
                      )}
                    </div>
                    <Badge variant={sCfg.variant} className={`text-[10px] shrink-0 ${sCfg.className}`}>
                      {notif.status}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground">
                    {notif.technician_name && <span>{notif.technician_name}</span>}
                    {notif.project_name && (
                      <>
                        <span className="text-border">|</span>
                        <span>{notif.project_name}</span>
                      </>
                    )}
                    <span className="text-border">|</span>
                    <span>{new Date(notif.target_date).toLocaleDateString()}</span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
