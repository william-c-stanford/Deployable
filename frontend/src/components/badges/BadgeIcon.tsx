import { Shield, Award, Star, Zap, CheckCircle2, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export type BadgeType = "site" | "milestone";

export interface BadgeIconProps {
  name: string;
  type: BadgeType;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
  interactive?: boolean;
  className?: string;
  onClick?: () => void;
}

/** Map well-known badge names to specific icons for richer visuals */
const BADGE_ICON_MAP: Record<string, LucideIcon> = {
  "AT&T Cleared": Shield,
  "Google DC Cleared": Shield,
  "AWS Facility Access": Shield,
  "Meta DC Cleared": Shield,
  "Microsoft Azure Cleared": Shield,
  "Equinix Cleared": Shield,
  "500+ Hours": Star,
  "1000+ Hours": Star,
  "5+ Projects": Zap,
  "10+ Projects": Zap,
  "Zero Safety Incidents": CheckCircle2,
  "Perfect Attendance": CheckCircle2,
  "Mentor Certified": Award,
  "Team Lead": Award,
};

const sizeClasses = {
  sm: "h-3 w-3",
  md: "h-4 w-4",
  lg: "h-5 w-5",
};

const containerSizeClasses = {
  sm: "h-6 w-6",
  md: "h-8 w-8",
  lg: "h-10 w-10",
};

const textSizeClasses = {
  sm: "text-xs",
  md: "text-sm",
  lg: "text-sm font-medium",
};

/**
 * BadgeIcon renders a single badge with visual distinction between site and milestone types.
 *
 * - Site badges: Shield icon with indigo/primary color scheme and solid border
 * - Milestone badges: Award/Star icon with amber/gold color scheme and gradient background
 */
export function BadgeIcon({
  name,
  type,
  size = "md",
  showLabel = true,
  interactive = false,
  className,
  onClick,
}: BadgeIconProps) {
  const Icon = BADGE_ICON_MAP[name] ?? (type === "site" ? Shield : Award);

  const isSite = type === "site";

  const containerClasses = cn(
    "inline-flex items-center gap-2 rounded-lg border transition-all duration-200",
    // Site badges: primary/indigo accent with solid border
    isSite && [
      "border-primary/30 bg-primary/5",
      "text-primary",
      interactive && "hover:border-primary/50 hover:bg-primary/10 hover:shadow-sm cursor-pointer",
    ],
    // Milestone badges: amber/gold accent with subtle gradient
    !isSite && [
      "border-amber-500/30 bg-gradient-to-r from-amber-500/5 to-yellow-500/5",
      "text-amber-500",
      interactive && "hover:border-amber-500/50 hover:from-amber-500/10 hover:to-yellow-500/10 hover:shadow-sm cursor-pointer",
    ],
    showLabel ? "px-3 py-1.5" : containerSizeClasses[size] + " justify-center",
    className
  );

  const iconClasses = cn(
    sizeClasses[size],
    isSite ? "text-primary" : "text-amber-500"
  );

  const content = (
    <div className={containerClasses} onClick={onClick} role={interactive ? "button" : undefined}>
      <div
        className={cn(
          "flex items-center justify-center rounded-md",
          !showLabel && "p-0",
          showLabel && isSite && "bg-primary/10 p-1 rounded-md",
          showLabel && !isSite && "bg-amber-500/10 p-1 rounded-md"
        )}
      >
        <Icon className={iconClasses} />
      </div>
      {showLabel && (
        <span className={cn(textSizeClasses[size], "whitespace-nowrap")}>
          {name}
        </span>
      )}
    </div>
  );

  // If no label, wrap in tooltip for accessibility
  if (!showLabel) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>{content}</TooltipTrigger>
          <TooltipContent>
            <p className="text-xs">
              <span className="font-medium">{name}</span>
              <span className="text-muted-foreground ml-1">
                ({isSite ? "Site" : "Milestone"})
              </span>
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return content;
}

export default BadgeIcon;
