import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import type { Technician } from '@/types';
import { GraduationCap, MapPin, Award, ChevronRight, Clock, TrendingUp, AlertTriangle } from 'lucide-react';

interface KanbanCardProps {
  technician: Technician;
  onDrillIn: (id: string) => void;
  isDragging?: boolean;
}

type BadgeVariant = 'success' | 'warning' | 'default' | 'destructive' | 'secondary';

const deployabilityBadge: Record<string, BadgeVariant> = {
  'Ready Now': 'success',
  'In Training': 'warning',
  'Currently Assigned': 'default',
  'Missing Cert': 'destructive',
  'Missing Docs': 'destructive',
  'Rolling Off Soon': 'warning',
  'Inactive': 'secondary',
};

export function KanbanCard({ technician, onDrillIn, isDragging }: KanbanCardProps) {
  const topSkill = technician.skills.length > 0
    ? technician.skills.reduce(
        (best, s) => (s.training_hours_accumulated > best.training_hours_accumulated ? s : best),
        technician.skills[0]
      )
    : null;

  const totalHours = technician.skills.reduce((sum, s) => sum + s.training_hours_accumulated, 0);
  const avgProgress = technician.skills.length > 0
    ? technician.skills.reduce((sum, s) => {
        const target = s.proficiency_level === 'Advanced' ? s.target_hours_advanced :
          s.proficiency_level === 'Intermediate' ? s.target_hours_advanced : s.target_hours_intermediate;
        return sum + Math.min(100, (s.training_hours_accumulated / target) * 100);
      }, 0) / technician.skills.length
    : 0;

  const activeCerts = technician.certifications.filter(c => c.status === 'Active').length;
  const expiringCerts = technician.certifications.filter(c => c.status === 'Expiring Soon').length;
  const advancedSkills = technician.skills.filter(s => s.proficiency_level === 'Advanced').length;

  // Find closest skill to next level advancement
  const nearestAdvancement = technician.skills
    .map(s => {
      if (s.proficiency_level === 'Advanced') return null;
      const target = s.proficiency_level === 'Beginner'
        ? s.target_hours_intermediate
        : s.target_hours_advanced;
      const remaining = target - s.training_hours_accumulated;
      return remaining > 0 ? { skill: s.skill_name, remaining, level: s.proficiency_level === 'Beginner' ? 'Intermediate' : 'Advanced' } : null;
    })
    .filter(Boolean)
    .sort((a, b) => (a?.remaining || 0) - (b?.remaining || 0))[0];

  return (
    <div
      className={cn(
        'group relative rounded-lg border bg-card p-3 cursor-pointer transition-all duration-200',
        'hover:border-primary/50 hover:shadow-lg hover:shadow-primary/5',
        isDragging && 'rotate-2 shadow-xl border-primary opacity-90'
      )}
      onClick={() => onDrillIn(technician.id)}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', technician.id);
        e.dataTransfer.effectAllowed = 'move';
      }}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-xs font-bold text-primary shrink-0">
            {technician.name.split(' ').map(n => n[0]).join('')}
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-medium truncate">{technician.name}</h4>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <MapPin className="w-3 h-3" />
              <span className="truncate">{technician.home_base_city}</span>
            </div>
          </div>
        </div>
        <ChevronRight className="w-4 h-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
      </div>

      {/* Deployability Badge */}
      <div className="mb-2 flex items-center gap-1.5">
        <Badge variant={deployabilityBadge[technician.deployability_status] || 'secondary'} className="text-[10px]">
          {technician.deployability_status}
        </Badge>
        {advancedSkills > 0 && (
          <Badge variant="success" className="text-[10px] gap-0.5">
            <GraduationCap className="w-2.5 h-2.5" />
            {advancedSkills} Adv
          </Badge>
        )}
      </div>

      {/* Top Skill + Progress */}
      {topSkill && (
        <div className="mb-2">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-muted-foreground flex items-center gap-1">
              <GraduationCap className="w-3 h-3" />
              {topSkill.skill_name}
            </span>
            <span className={cn(
              'font-medium text-[10px]',
              topSkill.proficiency_level === 'Advanced' ? 'text-success' :
              topSkill.proficiency_level === 'Intermediate' ? 'text-info' : 'text-warning'
            )}>
              {topSkill.proficiency_level}
            </span>
          </div>
          <Progress
            value={topSkill.training_hours_accumulated}
            max={topSkill.proficiency_level === 'Advanced' ? topSkill.target_hours_advanced : topSkill.target_hours_intermediate}
            className="h-1.5"
            indicatorClassName={
              topSkill.proficiency_level === 'Advanced' ? 'bg-success' :
              topSkill.proficiency_level === 'Intermediate' ? 'bg-info' : 'bg-warning'
            }
          />
        </div>
      )}

      {/* Nearest Advancement Hint */}
      {nearestAdvancement && (
        <div className="mb-2 flex items-center gap-1 text-[10px] text-muted-foreground bg-muted/50 rounded px-1.5 py-1">
          <TrendingUp className="w-3 h-3 text-info shrink-0" />
          <span className="truncate">
            {nearestAdvancement.remaining}h to {nearestAdvancement.level} in {nearestAdvancement.skill}
          </span>
        </div>
      )}

      {/* Footer Stats */}
      <div className="flex items-center justify-between text-xs text-muted-foreground pt-1.5 border-t border-border/50">
        <span className="flex items-center gap-1">
          <GraduationCap className="w-3 h-3" />
          {technician.skills.length}
        </span>
        <span className="flex items-center gap-1">
          <Award className="w-3 h-3" />
          {activeCerts}
          {expiringCerts > 0 && (
            <span className="text-warning flex items-center gap-0.5">
              <AlertTriangle className="w-2.5 h-2.5" />
              {expiringCerts}
            </span>
          )}
        </span>
        <span className="flex items-center gap-1 font-mono">
          <Clock className="w-3 h-3" />
          {totalHours}h
        </span>
      </div>

      {/* Overall progress bar */}
      <div className="mt-2">
        <Progress value={avgProgress} max={100} className="h-1" />
      </div>
    </div>
  );
}
