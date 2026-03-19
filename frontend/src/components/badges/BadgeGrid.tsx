import { Shield, Award } from "lucide-react";
import { cn } from "@/lib/utils";
import { BadgeIcon, type BadgeType } from "./BadgeIcon";
import { Separator } from "@/components/ui/separator";

export interface BadgeGridProps {
  siteBadges: string[];
  milestoneBadges: string[];
  /** Compact mode for summary views (e.g., directory cards) */
  compact?: boolean;
  /** Show section headers */
  showHeaders?: boolean;
  /** Show empty state when no badges */
  showEmpty?: boolean;
  /** Max badges to show per section before "+N more" */
  maxVisible?: number;
  /** Callback when a badge is clicked */
  onBadgeClick?: (name: string, type: BadgeType) => void;
  className?: string;
}

/**
 * BadgeGrid displays site and milestone badges in organized sections
 * with visual distinction between the two types.
 *
 * - Full mode: Two sections with headers and separator
 * - Compact mode: Single-line display with overflow indicator
 */
export function BadgeGrid({
  siteBadges,
  milestoneBadges,
  compact = false,
  showHeaders = true,
  showEmpty = true,
  maxVisible,
  onBadgeClick,
  className,
}: BadgeGridProps) {
  const totalBadges = siteBadges.length + milestoneBadges.length;

  if (totalBadges === 0 && !showEmpty) return null;

  if (compact) {
    return <CompactBadgeGrid
      siteBadges={siteBadges}
      milestoneBadges={milestoneBadges}
      maxVisible={maxVisible ?? 4}
      onBadgeClick={onBadgeClick}
      className={className}
    />;
  }

  return (
    <div className={cn("space-y-5", className)}>
      {/* Site Badges Section */}
      <BadgeSection
        title="Site / Client Badges"
        icon={<Shield className="h-4 w-4 text-primary" />}
        badges={siteBadges}
        type="site"
        showHeader={showHeaders}
        showEmpty={showEmpty}
        maxVisible={maxVisible}
        onBadgeClick={onBadgeClick}
        emptyText="No site badges granted yet"
      />

      {showHeaders && (siteBadges.length > 0 || milestoneBadges.length > 0) && (
        <Separator />
      )}

      {/* Milestone Badges Section */}
      <BadgeSection
        title="Milestone Badges"
        icon={<Award className="h-4 w-4 text-amber-500" />}
        badges={milestoneBadges}
        type="milestone"
        showHeader={showHeaders}
        showEmpty={showEmpty}
        maxVisible={maxVisible}
        onBadgeClick={onBadgeClick}
        emptyText="No milestone badges earned yet"
      />
    </div>
  );
}

// ---- Internal Section Component ----

interface BadgeSectionProps {
  title: string;
  icon: React.ReactNode;
  badges: string[];
  type: BadgeType;
  showHeader: boolean;
  showEmpty: boolean;
  maxVisible?: number;
  onBadgeClick?: (name: string, type: BadgeType) => void;
  emptyText: string;
}

function BadgeSection({
  title,
  icon,
  badges,
  type,
  showHeader,
  showEmpty,
  maxVisible,
  onBadgeClick,
  emptyText,
}: BadgeSectionProps) {
  const visibleBadges = maxVisible ? badges.slice(0, maxVisible) : badges;
  const overflowCount = maxVisible ? Math.max(0, badges.length - maxVisible) : 0;

  return (
    <div>
      {showHeader && (
        <h4 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
          {icon}
          {title}
          {badges.length > 0 && (
            <span className="text-xs bg-muted px-1.5 py-0.5 rounded-full">
              {badges.length}
            </span>
          )}
        </h4>
      )}
      <div className="flex flex-wrap gap-2">
        {visibleBadges.map((badge) => (
          <BadgeIcon
            key={badge}
            name={badge}
            type={type}
            size="md"
            interactive={!!onBadgeClick}
            onClick={onBadgeClick ? () => onBadgeClick(badge, type) : undefined}
          />
        ))}
        {overflowCount > 0 && (
          <div className="inline-flex items-center rounded-lg border border-muted px-3 py-1.5 text-sm text-muted-foreground">
            +{overflowCount} more
          </div>
        )}
        {badges.length === 0 && showEmpty && (
          <span className="text-sm text-muted-foreground italic">{emptyText}</span>
        )}
      </div>
    </div>
  );
}

// ---- Compact Grid ----

interface CompactBadgeGridProps {
  siteBadges: string[];
  milestoneBadges: string[];
  maxVisible: number;
  onBadgeClick?: (name: string, type: BadgeType) => void;
  className?: string;
}

function CompactBadgeGrid({
  siteBadges,
  milestoneBadges,
  maxVisible,
  onBadgeClick,
  className,
}: CompactBadgeGridProps) {
  // Interleave: show site badges first, then milestone
  const allBadges: { name: string; type: BadgeType }[] = [
    ...siteBadges.map((b) => ({ name: b, type: "site" as BadgeType })),
    ...milestoneBadges.map((b) => ({ name: b, type: "milestone" as BadgeType })),
  ];

  const visible = allBadges.slice(0, maxVisible);
  const overflow = allBadges.length - visible.length;

  if (allBadges.length === 0) {
    return (
      <span className={cn("text-xs text-muted-foreground", className)}>
        No badges
      </span>
    );
  }

  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {visible.map((badge) => (
        <BadgeIcon
          key={`${badge.type}-${badge.name}`}
          name={badge.name}
          type={badge.type}
          size="sm"
          showLabel={false}
          interactive={!!onBadgeClick}
          onClick={onBadgeClick ? () => onBadgeClick(badge.name, badge.type) : undefined}
        />
      ))}
      {overflow > 0 && (
        <span className="inline-flex items-center justify-center h-6 w-6 rounded-lg border border-muted text-xs text-muted-foreground">
          +{overflow}
        </span>
      )}
    </div>
  );
}

export default BadgeGrid;
