import { useState } from "react";
import { Plus, X, Shield, Award } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { BadgeIcon, type BadgeType } from "./BadgeIcon";
import { BadgeGrid } from "./BadgeGrid";

/** Common site badge presets for quick selection */
const SITE_BADGE_PRESETS = [
  "AT&T Cleared",
  "Google DC Cleared",
  "AWS Facility Access",
  "Meta DC Cleared",
  "Microsoft Azure Cleared",
  "Equinix Cleared",
  "CyrusOne Cleared",
  "Digital Realty Cleared",
  "Lumen Cleared",
  "Crown Castle Cleared",
];

/** Common milestone badge presets */
const MILESTONE_BADGE_PRESETS = [
  "500+ Hours",
  "1000+ Hours",
  "2000+ Hours",
  "5+ Projects",
  "10+ Projects",
  "Zero Safety Incidents",
  "Perfect Attendance",
  "Mentor Certified",
  "Team Lead",
  "Client Commendation",
];

export interface ManualBadgeManagerProps {
  technicianId: string;
  technicianName: string;
  siteBadges: string[];
  milestoneBadges: string[];
  onAddBadge: (techId: string, badge: string, type: BadgeType) => void;
  onRemoveBadge: (techId: string, badge: string, type: BadgeType) => void;
  /** Whether the current user can manage badges (ops role) */
  canManage?: boolean;
  className?: string;
}

/**
 * ManualBadgeManager provides full badge CRUD on technician profiles.
 *
 * Features:
 * - Add badge dialog with type selection and preset quick-picks
 * - Inline remove buttons on hover per badge
 * - Visual distinction between site (Shield/indigo) and milestone (Award/amber) badges
 * - Read-only mode when canManage is false
 */
export function ManualBadgeManager({
  technicianId,
  technicianName,
  siteBadges,
  milestoneBadges,
  onAddBadge,
  onRemoveBadge,
  canManage = true,
  className,
}: ManualBadgeManagerProps) {
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [newBadgeName, setNewBadgeName] = useState("");
  const [newBadgeType, setNewBadgeType] = useState<BadgeType>("site");

  const totalBadges = siteBadges.length + milestoneBadges.length;

  const handleAdd = () => {
    if (!newBadgeName.trim()) return;
    onAddBadge(technicianId, newBadgeName.trim(), newBadgeType);
    setNewBadgeName("");
    setShowAddDialog(false);
  };

  const handlePresetClick = (preset: string) => {
    setNewBadgeName(preset);
  };

  // Filter out already-assigned presets
  const availableSitePresets = SITE_BADGE_PRESETS.filter(
    (p) => !siteBadges.includes(p)
  );
  const availableMilestonePresets = MILESTONE_BADGE_PRESETS.filter(
    (p) => !milestoneBadges.includes(p)
  );
  const activePresets =
    newBadgeType === "site" ? availableSitePresets : availableMilestonePresets;

  return (
    <div className={cn("space-y-6", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold">Badges</h3>
          <p className="text-sm text-muted-foreground">
            {totalBadges} total badge{totalBadges !== 1 ? "s" : ""} —{" "}
            <span className="text-primary">{siteBadges.length} site</span>,{" "}
            <span className="text-amber-500">{milestoneBadges.length} milestone</span>
          </p>
        </div>

        {canManage && (
          <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="mr-1 h-4 w-4" /> Add Badge
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Add Badge</DialogTitle>
                <DialogDescription>
                  Grant a new badge to {technicianName}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-4">
                {/* Badge Type Selector */}
                <div>
                  <Label>Badge Type</Label>
                  <Select
                    value={newBadgeType}
                    onValueChange={(v) => {
                      setNewBadgeType(v as BadgeType);
                      setNewBadgeName("");
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="site">
                        <div className="flex items-center gap-2">
                          <Shield className="h-4 w-4 text-primary" />
                          Site Badge (manually granted)
                        </div>
                      </SelectItem>
                      <SelectItem value="milestone">
                        <div className="flex items-center gap-2">
                          <Award className="h-4 w-4 text-amber-500" />
                          Milestone Badge (achievement)
                        </div>
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Preset Quick Picks */}
                {activePresets.length > 0 && (
                  <div>
                    <Label className="text-xs text-muted-foreground">
                      Quick select
                    </Label>
                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                      {activePresets.slice(0, 6).map((preset) => (
                        <button
                          key={preset}
                          type="button"
                          onClick={() => handlePresetClick(preset)}
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors",
                            newBadgeName === preset
                              ? newBadgeType === "site"
                                ? "border-primary bg-primary/10 text-primary"
                                : "border-amber-500 bg-amber-500/10 text-amber-500"
                              : "border-muted hover:border-foreground/20 text-muted-foreground hover:text-foreground"
                          )}
                        >
                          {newBadgeType === "site" ? (
                            <Shield className="h-3 w-3" />
                          ) : (
                            <Award className="h-3 w-3" />
                          )}
                          {preset}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Custom Badge Name */}
                <div>
                  <Label>Badge Name</Label>
                  <Input
                    placeholder={
                      newBadgeType === "site"
                        ? "e.g., AT&T Cleared, AWS Facility Access"
                        : "e.g., 500+ Hours, Zero Safety Incidents"
                    }
                    value={newBadgeName}
                    onChange={(e) => setNewBadgeName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleAdd();
                    }}
                  />
                </div>

                {/* Preview */}
                {newBadgeName && (
                  <div>
                    <Label className="text-xs text-muted-foreground">Preview</Label>
                    <div className="mt-1.5">
                      <BadgeIcon name={newBadgeName} type={newBadgeType} size="lg" />
                    </div>
                  </div>
                )}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setShowAddDialog(false)}>
                  Cancel
                </Button>
                <Button onClick={handleAdd} disabled={!newBadgeName.trim()}>
                  Add Badge
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>

      {/* Badge Grid with remove capability */}
      <ManageableBadgeSection
        title="Site / Client Badges"
        icon={<Shield className="h-4 w-4 text-primary" />}
        badges={siteBadges}
        type="site"
        canManage={canManage}
        onRemove={(badge) => onRemoveBadge(technicianId, badge, "site")}
        emptyText="No site badges granted yet"
      />

      {(siteBadges.length > 0 || milestoneBadges.length > 0) && (
        <div className="border-t border-border" />
      )}

      <ManageableBadgeSection
        title="Milestone Badges"
        icon={<Award className="h-4 w-4 text-amber-500" />}
        badges={milestoneBadges}
        type="milestone"
        canManage={canManage}
        onRemove={(badge) => onRemoveBadge(technicianId, badge, "milestone")}
        emptyText="No milestone badges earned yet"
      />
    </div>
  );
}

// ---- Manageable Badge Section (with remove buttons) ----

interface ManageableBadgeSectionProps {
  title: string;
  icon: React.ReactNode;
  badges: string[];
  type: BadgeType;
  canManage: boolean;
  onRemove: (badge: string) => void;
  emptyText: string;
}

function ManageableBadgeSection({
  title,
  icon,
  badges,
  type,
  canManage,
  onRemove,
  emptyText,
}: ManageableBadgeSectionProps) {
  const isSite = type === "site";

  return (
    <div>
      <h4 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
        {icon}
        {title}
        {badges.length > 0 && (
          <span
            className={cn(
              "text-xs px-1.5 py-0.5 rounded-full",
              isSite ? "bg-primary/10 text-primary" : "bg-amber-500/10 text-amber-500"
            )}
          >
            {badges.length}
          </span>
        )}
      </h4>
      <div className="flex flex-wrap gap-2">
        {badges.map((badge) => (
          <div
            key={badge}
            className={cn(
              "group inline-flex items-center gap-2 rounded-lg border transition-all duration-200 px-3 py-1.5",
              isSite
                ? "border-primary/30 bg-primary/5"
                : "border-amber-500/30 bg-gradient-to-r from-amber-500/5 to-yellow-500/5"
            )}
          >
            <div
              className={cn(
                "flex items-center justify-center rounded-md p-1",
                isSite ? "bg-primary/10" : "bg-amber-500/10"
              )}
            >
              {isSite ? (
                <Shield className="h-4 w-4 text-primary" />
              ) : (
                <Award className="h-4 w-4 text-amber-500" />
              )}
            </div>
            <span className="text-sm font-medium">{badge}</span>
            {canManage && (
              <Button
                variant="ghost"
                size="icon"
                className="h-5 w-5 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                onClick={() => onRemove(badge)}
              >
                <X className="h-3 w-3" />
              </Button>
            )}
          </div>
        ))}
        {badges.length === 0 && (
          <span className="text-sm text-muted-foreground italic">{emptyText}</span>
        )}
      </div>
    </div>
  );
}

export default ManualBadgeManager;
