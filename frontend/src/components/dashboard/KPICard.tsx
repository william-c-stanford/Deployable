import { useNavigate } from "react-router-dom";
import {
  Users, UserCheck, Briefcase, ClipboardList, Inbox, Clock,
  AlertTriangle, UserPlus, ArrowUpRight, type LucideIcon
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { KPICard as KPICardType } from "@/types/dashboard";

const iconMap: Record<string, LucideIcon> = {
  Users, UserCheck, Briefcase, ClipboardList, Inbox, Clock,
  AlertTriangle, UserPlus,
};

const colorMap: Record<string, { bg: string; text: string; border: string; glow: string }> = {
  blue:    { bg: "bg-blue-500/10",    text: "text-blue-400",    border: "border-blue-500/20",    glow: "hover:shadow-blue-500/5" },
  emerald: { bg: "bg-emerald-500/10", text: "text-emerald-400", border: "border-emerald-500/20", glow: "hover:shadow-emerald-500/5" },
  violet:  { bg: "bg-violet-500/10",  text: "text-violet-400",  border: "border-violet-500/20",  glow: "hover:shadow-violet-500/5" },
  amber:   { bg: "bg-amber-500/10",   text: "text-amber-400",   border: "border-amber-500/20",   glow: "hover:shadow-amber-500/5" },
  rose:    { bg: "bg-rose-500/10",    text: "text-rose-400",    border: "border-rose-500/20",    glow: "hover:shadow-rose-500/5" },
  cyan:    { bg: "bg-cyan-500/10",    text: "text-cyan-400",    border: "border-cyan-500/20",    glow: "hover:shadow-cyan-500/5" },
  orange:  { bg: "bg-orange-500/10",  text: "text-orange-400",  border: "border-orange-500/20",  glow: "hover:shadow-orange-500/5" },
  indigo:  { bg: "bg-indigo-500/10",  text: "text-indigo-400",  border: "border-indigo-500/20",  glow: "hover:shadow-indigo-500/5" },
  red:     { bg: "bg-red-500/10",     text: "text-red-400",     border: "border-red-500/20",     glow: "hover:shadow-red-500/5" },
};

const subItemColorMap: Record<string, string> = {
  emerald: "text-emerald-400",
  amber:   "text-amber-400",
  blue:    "text-blue-400",
  orange:  "text-orange-400",
  red:     "text-red-400",
  violet:  "text-violet-400",
  cyan:    "text-cyan-400",
};

interface KPICardProps {
  card: KPICardType;
}

export function KPICardComponent({ card }: KPICardProps) {
  const navigate = useNavigate();
  const Icon = iconMap[card.icon] || Users;
  const colors = colorMap[card.color] || colorMap.blue;

  const handleClick = () => {
    const [path, search] = card.link.split("?");
    navigate(search ? `${path}?${search}` : path);
  };

  return (
    <button
      onClick={handleClick}
      className={cn(
        "group relative w-full rounded-xl border bg-card p-5 text-left transition-all duration-200",
        "hover:scale-[1.02] hover:shadow-lg cursor-pointer",
        colors.border,
        colors.glow
      )}
    >
      {/* Header row */}
      <div className="flex items-start justify-between mb-3">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg", colors.bg)}>
          <Icon className={cn("h-5 w-5", colors.text)} />
        </div>
        <ArrowUpRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </div>

      {/* Value */}
      <div className="mb-1">
        <span className="text-3xl font-bold tracking-tight">{card.value}</span>
      </div>

      {/* Label */}
      <p className="text-sm text-muted-foreground font-medium">{card.label}</p>

      {/* Sub-items breakdown */}
      {card.sub_items && card.sub_items.length > 0 && (
        <div className="mt-3 pt-3 border-t border-border/50 space-y-1">
          {card.sub_items.map((item, i) => (
            <div key={i} className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{item.label}</span>
              <span className={cn("font-semibold", subItemColorMap[item.color] || "text-foreground")}>
                {item.value}
              </span>
            </div>
          ))}
        </div>
      )}
    </button>
  );
}
