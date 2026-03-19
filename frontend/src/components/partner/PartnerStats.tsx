import { usePartnerStore } from "@/stores/partnerStore";
import { Card, CardContent } from "@/components/ui/card";
import { Briefcase, Users, Clock, CalendarCheck } from "lucide-react";

interface PartnerStatsProps {
  isLoading: boolean;
}

const statConfig = [
  {
    key: "active_projects" as const,
    label: "Active Projects",
    icon: Briefcase,
    color: "text-blue-500",
    bg: "bg-blue-500/10",
  },
  {
    key: "total_assignments" as const,
    label: "Total Assignments",
    icon: Users,
    color: "text-emerald-500",
    bg: "bg-emerald-500/10",
  },
  {
    key: "pending_confirmations" as const,
    label: "Pending Confirmations",
    icon: Clock,
    color: "text-amber-500",
    bg: "bg-amber-500/10",
  },
  {
    key: "upcoming_starts" as const,
    label: "Upcoming Starts",
    icon: CalendarCheck,
    color: "text-purple-500",
    bg: "bg-purple-500/10",
  },
];

export function PartnerStats({ isLoading }: PartnerStatsProps) {
  const stats = usePartnerStore((s) => s.stats);

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      {statConfig.map((cfg) => (
        <Card key={cfg.key} className="border bg-card">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${cfg.bg}`}>
                <cfg.icon className={`h-5 w-5 ${cfg.color}`} />
              </div>
              <div>
                {isLoading ? (
                  <div className="h-7 w-10 rounded bg-muted animate-pulse" />
                ) : (
                  <p className="text-2xl font-bold">{stats[cfg.key]}</p>
                )}
                <p className="text-xs text-muted-foreground">{cfg.label}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
