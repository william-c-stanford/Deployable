import { useMemo, useState } from 'react';
import { useTrainingStore } from '@/stores/trainingStore';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { getAllSkills } from '@/lib/seedData';
import type { Technician, Skill, CareerStage } from '@/types';
import {
  ChevronRight, ChevronDown, GraduationCap, MapPin,
  ArrowUpDown, Filter, Eye,
} from 'lucide-react';

interface SkillMatrixTableProps {
  onDrillIn: (id: string) => void;
}

type SortField = 'name' | 'stage' | 'hours' | 'skills' | 'advanced';
type SortDir = 'asc' | 'desc';

const stageOrder: Record<string, number> = {
  'Sourced': 0,
  'Screened': 1,
  'In Training': 2,
  'Training Completed': 3,
  'Awaiting Assignment': 4,
  'Deployed': 5,
};

const stageBadgeVariant: Record<string, 'default' | 'secondary' | 'success' | 'warning' | 'destructive' | 'info'> = {
  'Sourced': 'secondary',
  'Screened': 'info',
  'In Training': 'warning',
  'Training Completed': 'success',
  'Awaiting Assignment': 'default',
  'Deployed': 'success',
};

const proficiencyColor: Record<string, string> = {
  'Beginner': 'bg-warning/20 text-warning border-warning/30',
  'Intermediate': 'bg-info/20 text-info border-info/30',
  'Advanced': 'bg-success/20 text-success border-success/30',
};

const proficiencyDot: Record<string, string> = {
  'Beginner': 'bg-warning',
  'Intermediate': 'bg-info',
  'Advanced': 'bg-success',
};

export function SkillMatrixTable({ onDrillIn }: SkillMatrixTableProps) {
  const { technicians, filters } = useTrainingStore();
  const [sortField, setSortField] = useState<SortField>('stage');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [expandedTechId, setExpandedTechId] = useState<string | null>(null);
  const [selectedSkillFilter, setSelectedSkillFilter] = useState<string>('');

  const allSkills = getAllSkills();

  // Apply existing filters from store
  const filteredTechnicians = useMemo(() => {
    return technicians.filter((t) => {
      if (filters.search) {
        const q = filters.search.toLowerCase();
        if (
          !t.name.toLowerCase().includes(q) &&
          !t.home_base_city.toLowerCase().includes(q)
        ) return false;
      }
      if (filters.skillFilter) {
        if (!t.skills.some((s) => s.skill_name === filters.skillFilter)) return false;
      }
      if (filters.regionFilter) {
        if (!t.approved_regions.includes(filters.regionFilter)) return false;
      }
      if (selectedSkillFilter) {
        if (!t.skills.some((s) => s.skill_name === selectedSkillFilter)) return false;
      }
      return true;
    });
  }, [technicians, filters, selectedSkillFilter]);

  // Sort
  const sortedTechnicians = useMemo(() => {
    const sorted = [...filteredTechnicians];
    sorted.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case 'name':
          cmp = a.name.localeCompare(b.name);
          break;
        case 'stage':
          cmp = (stageOrder[a.career_stage] || 0) - (stageOrder[b.career_stage] || 0);
          break;
        case 'hours': {
          const hoursA = a.skills.reduce((s, sk) => s + sk.training_hours_accumulated, 0);
          const hoursB = b.skills.reduce((s, sk) => s + sk.training_hours_accumulated, 0);
          cmp = hoursA - hoursB;
          break;
        }
        case 'skills':
          cmp = a.skills.length - b.skills.length;
          break;
        case 'advanced': {
          const advA = a.skills.filter(s => s.proficiency_level === 'Advanced').length;
          const advB = b.skills.filter(s => s.proficiency_level === 'Advanced').length;
          cmp = advA - advB;
          break;
        }
      }
      return sortDir === 'desc' ? -cmp : cmp;
    });
    return sorted;
  }, [filteredTechnicians, sortField, sortDir]);

  // Collect unique skills across visible technicians for matrix columns
  const matrixSkills = useMemo(() => {
    const skillSet = new Set<string>();
    sortedTechnicians.forEach(t => t.skills.forEach(s => skillSet.add(s.skill_name)));
    // Sort by the order in the taxonomy, or alphabetically
    return Array.from(skillSet).sort((a, b) => {
      const idxA = allSkills.indexOf(a);
      const idxB = allSkills.indexOf(b);
      if (idxA >= 0 && idxB >= 0) return idxA - idxB;
      return a.localeCompare(b);
    });
  }, [sortedTechnicians, allSkills]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('asc');
    }
  };

  const getSkillForTech = (tech: Technician, skillName: string): Skill | undefined => {
    return tech.skills.find(s => s.skill_name === skillName);
  };

  const toggleExpand = (id: string) => {
    setExpandedTechId(prev => prev === id ? null : id);
  };

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <GraduationCap className="w-5 h-5" />
            Skill Matrix Overview
            <Badge variant="secondary" className="ml-1">
              {sortedTechnicians.length} technicians
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-2">
            {/* Quick skill filter for matrix columns */}
            <select
              className="h-8 text-xs rounded-md border bg-background px-2 focus:outline-none focus:ring-1 focus:ring-ring"
              value={selectedSkillFilter}
              onChange={(e) => setSelectedSkillFilter(e.target.value)}
            >
              <option value="">All Skills ({matrixSkills.length})</option>
              {allSkills.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/30">
                <th className="sticky left-0 z-20 bg-muted/30 px-3 py-2 text-left w-8" />
                <SortHeader
                  label="Technician"
                  field="name"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={toggleSort}
                  className="sticky left-8 z-20 bg-muted/30 min-w-[200px]"
                />
                <SortHeader
                  label="Stage"
                  field="stage"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={toggleSort}
                  className="min-w-[140px]"
                />
                <SortHeader
                  label="Hours"
                  field="hours"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={toggleSort}
                  className="min-w-[80px]"
                />
                <SortHeader
                  label="Skills"
                  field="skills"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={toggleSort}
                  className="min-w-[70px]"
                />
                <SortHeader
                  label="Adv."
                  field="advanced"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={toggleSort}
                  className="min-w-[60px]"
                />
                {/* Skill columns */}
                {matrixSkills.map(skill => (
                  <th
                    key={skill}
                    className="px-2 py-2 text-center font-medium text-muted-foreground min-w-[100px]"
                  >
                    <span className="text-xs leading-tight block truncate" title={skill}>
                      {skill}
                    </span>
                  </th>
                ))}
                <th className="px-3 py-2 text-center w-10">
                  <Eye className="w-3.5 h-3.5 mx-auto text-muted-foreground" />
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedTechnicians.map((tech) => {
                const totalHours = tech.skills.reduce((s, sk) => s + sk.training_hours_accumulated, 0);
                const advancedCount = tech.skills.filter(s => s.proficiency_level === 'Advanced').length;
                const isExpanded = expandedTechId === tech.id;

                return (
                  <SkillMatrixRow
                    key={tech.id}
                    tech={tech}
                    totalHours={totalHours}
                    advancedCount={advancedCount}
                    isExpanded={isExpanded}
                    matrixSkills={matrixSkills}
                    getSkillForTech={getSkillForTech}
                    onToggleExpand={toggleExpand}
                    onDrillIn={onDrillIn}
                  />
                );
              })}
            </tbody>
          </table>

          {sortedTechnicians.length === 0 && (
            <div className="flex items-center justify-center h-40 text-muted-foreground">
              No technicians match the current filters
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/* ──── Sort Header ──── */
function SortHeader({
  label,
  field,
  currentField,
  currentDir,
  onSort,
  className,
}: {
  label: string;
  field: SortField;
  currentField: SortField;
  currentDir: SortDir;
  onSort: (f: SortField) => void;
  className?: string;
}) {
  const isActive = currentField === field;
  return (
    <th className={cn('px-3 py-2 text-left', className)}>
      <button
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => onSort(field)}
      >
        {label}
        <ArrowUpDown className={cn(
          'w-3 h-3',
          isActive ? 'text-primary' : 'text-muted-foreground/50'
        )} />
        {isActive && (
          <span className="text-primary text-[10px]">
            {currentDir === 'asc' ? '\u2191' : '\u2193'}
          </span>
        )}
      </button>
    </th>
  );
}

/* ──── Row Component ──── */
function SkillMatrixRow({
  tech,
  totalHours,
  advancedCount,
  isExpanded,
  matrixSkills,
  getSkillForTech,
  onToggleExpand,
  onDrillIn,
}: {
  tech: Technician;
  totalHours: number;
  advancedCount: number;
  isExpanded: boolean;
  matrixSkills: string[];
  getSkillForTech: (tech: Technician, skillName: string) => Skill | undefined;
  onToggleExpand: (id: string) => void;
  onDrillIn: (id: string) => void;
}) {
  return (
    <>
      <tr
        className={cn(
          'border-b hover:bg-muted/20 transition-colors cursor-pointer group',
          isExpanded && 'bg-muted/10'
        )}
        onClick={() => onToggleExpand(tech.id)}
      >
        {/* Expand Toggle */}
        <td className="sticky left-0 z-10 bg-background group-hover:bg-muted/20 px-3 py-2 w-8">
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted-foreground" />
          )}
        </td>

        {/* Technician Name */}
        <td className="sticky left-8 z-10 bg-background group-hover:bg-muted/20 px-3 py-2 min-w-[200px]">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center text-[10px] font-bold text-primary shrink-0">
              {tech.name.split(' ').map(n => n[0]).join('')}
            </div>
            <div className="min-w-0">
              <div className="font-medium text-sm truncate">{tech.name}</div>
              <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                <MapPin className="w-2.5 h-2.5" />
                <span className="truncate">{tech.home_base_city}</span>
              </div>
            </div>
          </div>
        </td>

        {/* Career Stage */}
        <td className="px-3 py-2">
          <Badge variant={stageBadgeVariant[tech.career_stage] || 'secondary'} className="text-[10px]">
            {tech.career_stage}
          </Badge>
        </td>

        {/* Total Hours */}
        <td className="px-3 py-2 text-right font-mono text-xs">
          {totalHours.toLocaleString()}h
        </td>

        {/* Skills Count */}
        <td className="px-3 py-2 text-center text-xs">
          {tech.skills.length}
        </td>

        {/* Advanced Count */}
        <td className="px-3 py-2 text-center">
          {advancedCount > 0 ? (
            <Badge variant="success" className="text-[10px] px-1.5">
              {advancedCount}
            </Badge>
          ) : (
            <span className="text-xs text-muted-foreground">-</span>
          )}
        </td>

        {/* Skill Proficiency Cells */}
        {matrixSkills.map(skillName => {
          const skill = getSkillForTech(tech, skillName);
          if (!skill) {
            return (
              <td key={skillName} className="px-2 py-2 text-center">
                <span className="text-muted-foreground/30 text-xs">-</span>
              </td>
            );
          }
          return (
            <td key={skillName} className="px-2 py-2">
              <SkillCell skill={skill} />
            </td>
          );
        })}

        {/* Actions */}
        <td className="px-3 py-2 text-center">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={(e) => {
              e.stopPropagation();
              onDrillIn(tech.id);
            }}
          >
            <Eye className="w-3.5 h-3.5" />
          </Button>
        </td>
      </tr>

      {/* Expanded Detail Row */}
      {isExpanded && (
        <tr className="border-b bg-muted/5">
          <td colSpan={6 + matrixSkills.length + 1} className="p-4">
            <ExpandedSkillDetail tech={tech} onDrillIn={onDrillIn} />
          </td>
        </tr>
      )}
    </>
  );
}

/* ──── Skill Cell (compact heatmap cell) ──── */
function SkillCell({ skill }: { skill: Skill }) {
  const target = skill.proficiency_level === 'Advanced'
    ? skill.target_hours_advanced
    : skill.proficiency_level === 'Intermediate'
    ? skill.target_hours_advanced
    : skill.target_hours_intermediate;
  const pct = Math.min(100, (skill.training_hours_accumulated / target) * 100);

  return (
    <div className="flex flex-col items-center gap-0.5" title={`${skill.proficiency_level}: ${skill.training_hours_accumulated}h (${Math.round(pct)}%)`}>
      <div className={cn(
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[10px] font-medium',
        proficiencyColor[skill.proficiency_level]
      )}>
        <div className={cn('w-1.5 h-1.5 rounded-full', proficiencyDot[skill.proficiency_level])} />
        {skill.proficiency_level[0]}
      </div>
      <Progress
        value={pct}
        max={100}
        className="h-0.5 w-full max-w-[60px]"
        indicatorClassName={proficiencyDot[skill.proficiency_level]}
      />
    </div>
  );
}

/* ──── Expanded Detail (inline) ──── */
function ExpandedSkillDetail({ tech, onDrillIn }: { tech: Technician; onDrillIn: (id: string) => void }) {
  const totalHours = tech.skills.reduce((s, sk) => s + sk.training_hours_accumulated, 0);
  const sortedSkills = [...tech.skills].sort(
    (a, b) => b.training_hours_accumulated - a.training_hours_accumulated
  );

  return (
    <div className="space-y-3">
      {/* Quick Stats */}
      <div className="flex items-center gap-4 text-xs">
        <span className="text-muted-foreground">
          <strong className="text-foreground">{tech.skills.length}</strong> skills
        </span>
        <span className="text-muted-foreground">
          <strong className="text-foreground">{totalHours}</strong> total hours
        </span>
        <span className="text-muted-foreground">
          <strong className="text-foreground">{tech.certifications.length}</strong> certifications
        </span>
        <span className="text-muted-foreground">
          Regions: <strong className="text-foreground">{tech.approved_regions.join(', ')}</strong>
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-6 text-xs ml-auto"
          onClick={() => onDrillIn(tech.id)}
        >
          Full Profile
        </Button>
      </div>

      {/* Skill Bars */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {sortedSkills.map(skill => {
          const target = skill.proficiency_level === 'Advanced'
            ? skill.target_hours_advanced
            : skill.proficiency_level === 'Intermediate'
            ? skill.target_hours_advanced
            : skill.target_hours_intermediate;
          const pct = Math.min(100, (skill.training_hours_accumulated / target) * 100);

          const nextLevel = skill.proficiency_level === 'Beginner' ? 'Intermediate' :
            skill.proficiency_level === 'Intermediate' ? 'Advanced' : null;
          const hoursRemaining = nextLevel
            ? (skill.proficiency_level === 'Beginner'
              ? skill.target_hours_intermediate - skill.training_hours_accumulated
              : skill.target_hours_advanced - skill.training_hours_accumulated)
            : 0;

          return (
            <div key={skill.skill_name} className="rounded-md border p-2 bg-card">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium truncate">{skill.skill_name}</span>
                <Badge
                  variant={
                    skill.proficiency_level === 'Advanced' ? 'success' :
                    skill.proficiency_level === 'Intermediate' ? 'info' : 'warning'
                  }
                  className="text-[10px] px-1.5 py-0"
                >
                  {skill.proficiency_level}
                </Badge>
              </div>
              <Progress
                value={pct}
                max={100}
                className="h-1.5 mb-1"
                indicatorClassName={proficiencyDot[skill.proficiency_level]}
              />
              <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>{skill.training_hours_accumulated}h / {target}h</span>
                {nextLevel && hoursRemaining > 0 && (
                  <span>{hoursRemaining}h to {nextLevel}</span>
                )}
                {skill.proficiency_level === 'Advanced' && (
                  <span className="text-success">Mastered</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Certs Row */}
      {tech.certifications.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tech.certifications.map(cert => (
            <Badge
              key={cert.cert_name}
              variant={
                cert.status === 'Active' ? 'success' :
                cert.status === 'Expiring Soon' ? 'warning' : 'destructive'
              }
              className="text-[10px]"
            >
              {cert.cert_name} ({cert.status})
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
