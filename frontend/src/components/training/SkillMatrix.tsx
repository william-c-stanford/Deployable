import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { Technician, Skill } from '@/types';
import {
  X, GraduationCap, Award, MapPin, Clock,
  ShieldCheck, FileText, Star, TrendingUp, Calendar,
  AlertTriangle, CheckCircle2, XCircle, ChevronDown,
  ChevronUp, BarChart3, Target,
} from 'lucide-react';

interface SkillMatrixProps {
  technician: Technician;
  onClose: () => void;
}

const proficiencyConfig = {
  Beginner: { color: 'bg-warning', badge: 'warning' as const, level: 1, label: 'Apprentice' },
  Intermediate: { color: 'bg-info', badge: 'info' as const, level: 2, label: 'Intermediate' },
  Advanced: { color: 'bg-success', badge: 'success' as const, level: 3, label: 'Advanced' },
};

type TabId = 'skills' | 'certs' | 'docs' | 'overview';

export function SkillMatrix({ technician, onClose }: SkillMatrixProps) {
  const [activeTab, setActiveTab] = useState<TabId>('skills');
  const [sortBy, setSortBy] = useState<'hours' | 'name' | 'level'>('hours');

  const totalHours = technician.skills.reduce((sum, s) => sum + s.training_hours_accumulated, 0);
  const advancedCount = technician.skills.filter(s => s.proficiency_level === 'Advanced').length;
  const intermediateCount = technician.skills.filter(s => s.proficiency_level === 'Intermediate').length;
  const beginnerCount = technician.skills.filter(s => s.proficiency_level === 'Beginner').length;

  const sortedSkills = [...technician.skills].sort((a, b) => {
    if (sortBy === 'hours') return b.training_hours_accumulated - a.training_hours_accumulated;
    if (sortBy === 'name') return a.skill_name.localeCompare(b.skill_name);
    // Sort by level desc
    const levels = { Advanced: 3, Intermediate: 2, Beginner: 1 };
    return (levels[b.proficiency_level] || 0) - (levels[a.proficiency_level] || 0);
  });

  const getNextMilestone = (skill: Skill) => {
    if (skill.proficiency_level === 'Beginner') {
      const remaining = skill.target_hours_intermediate - skill.training_hours_accumulated;
      return remaining > 0
        ? { label: 'Intermediate', hoursRemaining: remaining, target: skill.target_hours_intermediate }
        : { label: 'Ready for Intermediate!', hoursRemaining: 0, target: skill.target_hours_intermediate };
    }
    if (skill.proficiency_level === 'Intermediate') {
      const remaining = skill.target_hours_advanced - skill.training_hours_accumulated;
      return remaining > 0
        ? { label: 'Advanced', hoursRemaining: remaining, target: skill.target_hours_advanced }
        : { label: 'Ready for Advanced!', hoursRemaining: 0, target: skill.target_hours_advanced };
    }
    return { label: 'Mastered', hoursRemaining: 0, target: skill.target_hours_advanced };
  };

  // Calculate overall readiness score
  const readinessScore = (() => {
    if (technician.skills.length === 0) return 0;
    const maxPossible = technician.skills.length * 3;
    const actual = technician.skills.reduce((sum, s) => {
      return sum + (s.proficiency_level === 'Advanced' ? 3 : s.proficiency_level === 'Intermediate' ? 2 : 1);
    }, 0);
    return Math.round((actual / maxPossible) * 100);
  })();

  const tabs: { id: TabId; label: string; count?: number }[] = [
    { id: 'skills', label: 'Skills', count: technician.skills.length },
    { id: 'certs', label: 'Certs', count: technician.certifications.length },
    { id: 'docs', label: 'Docs', count: technician.documents.length },
    { id: 'overview', label: 'Overview' },
  ];

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-start justify-end" onClick={onClose}>
      <div
        className="w-full max-w-2xl h-full bg-background border-l overflow-y-auto"
        style={{ animation: 'slideInFromRight 0.3s ease-out' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm border-b">
          <div className="p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center text-lg font-bold text-primary">
                  {technician.name.split(' ').map(n => n[0]).join('')}
                </div>
                <div>
                  <h2 className="text-lg font-semibold">{technician.name}</h2>
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <MapPin className="w-3.5 h-3.5" />
                    {technician.home_base_city}
                    <span className="text-border">|</span>
                    <Badge variant={
                      technician.career_stage === 'Deployed' ? 'success' :
                      technician.career_stage === 'In Training' ? 'warning' : 'secondary'
                    }>
                      {technician.career_stage}
                    </Badge>
                    <Badge variant={
                      technician.deployability_status === 'Ready Now' ? 'success' :
                      technician.deployability_status === 'In Training' ? 'warning' :
                      technician.deployability_status === 'Missing Cert' ? 'destructive' : 'secondary'
                    }>
                      {technician.deployability_status}
                    </Badge>
                  </div>
                </div>
              </div>
              <Button variant="ghost" size="icon" onClick={onClose}>
                <X className="w-5 h-5" />
              </Button>
            </div>
          </div>

          {/* Tab Navigation */}
          <div className="flex border-t px-4">
            {tabs.map(tab => (
              <button
                key={tab.id}
                className={cn(
                  'px-4 py-2 text-sm font-medium border-b-2 transition-colors',
                  activeTab === tab.id
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                )}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
                {tab.count !== undefined && (
                  <span className="ml-1.5 px-1.5 py-0.5 text-[10px] rounded-full bg-secondary">
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="p-4 space-y-4">
          {/* Summary Stats - Always visible */}
          <div className="grid grid-cols-4 gap-3">
            <Card className="p-3">
              <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
                <Clock className="w-3.5 h-3.5" />
                Total Hours
              </div>
              <div className="text-xl font-bold">{totalHours}</div>
            </Card>
            <Card className="p-3">
              <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
                <GraduationCap className="w-3.5 h-3.5" />
                Skills
              </div>
              <div className="text-xl font-bold">{technician.skills.length}</div>
            </Card>
            <Card className="p-3">
              <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
                <Star className="w-3.5 h-3.5" />
                Advanced
              </div>
              <div className="text-xl font-bold text-success">{advancedCount}</div>
            </Card>
            <Card className="p-3">
              <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
                <Target className="w-3.5 h-3.5" />
                Readiness
              </div>
              <div className={cn(
                'text-xl font-bold',
                readinessScore >= 75 ? 'text-success' :
                readinessScore >= 50 ? 'text-info' :
                readinessScore >= 25 ? 'text-warning' : 'text-destructive'
              )}>
                {readinessScore}%
              </div>
            </Card>
          </div>

          {/* Skill Distribution Mini Chart */}
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground w-20">Distribution:</span>
            <div className="flex-1 flex h-3 rounded-full overflow-hidden bg-secondary">
              {advancedCount > 0 && (
                <div
                  className="bg-success transition-all"
                  style={{ width: `${(advancedCount / technician.skills.length) * 100}%` }}
                  title={`${advancedCount} Advanced`}
                />
              )}
              {intermediateCount > 0 && (
                <div
                  className="bg-info transition-all"
                  style={{ width: `${(intermediateCount / technician.skills.length) * 100}%` }}
                  title={`${intermediateCount} Intermediate`}
                />
              )}
              {beginnerCount > 0 && (
                <div
                  className="bg-warning transition-all"
                  style={{ width: `${(beginnerCount / technician.skills.length) * 100}%` }}
                  title={`${beginnerCount} Beginner`}
                />
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-success" /> {advancedCount}
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-info" /> {intermediateCount}
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-warning" /> {beginnerCount}
              </span>
            </div>
          </div>

          {/* TAB: Skills */}
          {activeTab === 'skills' && (
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base flex items-center gap-2">
                    <GraduationCap className="w-5 h-5" />
                    Skill Matrix
                  </CardTitle>
                  <div className="flex items-center gap-1">
                    {(['hours', 'name', 'level'] as const).map(s => (
                      <Button
                        key={s}
                        variant={sortBy === s ? 'default' : 'ghost'}
                        size="sm"
                        className="h-6 text-[10px] px-2"
                        onClick={() => setSortBy(s)}
                      >
                        {s === 'hours' ? 'Hours' : s === 'name' ? 'Name' : 'Level'}
                      </Button>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {sortedSkills.map((skill) => {
                  const config = proficiencyConfig[skill.proficiency_level];
                  const milestone = getNextMilestone(skill);
                  const progressTarget = skill.proficiency_level === 'Advanced'
                    ? skill.target_hours_advanced
                    : skill.proficiency_level === 'Intermediate'
                    ? skill.target_hours_advanced
                    : skill.target_hours_intermediate;
                  const progressPercent = Math.min(100, (skill.training_hours_accumulated / progressTarget) * 100);

                  return (
                    <div key={skill.skill_name} className="rounded-md border p-3 hover:bg-secondary/30 transition-colors">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{skill.skill_name}</span>
                          <Badge variant={config.badge} className="text-xs">
                            {skill.proficiency_level}
                          </Badge>
                        </div>
                        <span className="text-sm font-mono text-muted-foreground">
                          {skill.training_hours_accumulated}h
                        </span>
                      </div>

                      {/* Proficiency Level Indicator */}
                      <div className="flex items-center gap-1 mb-2">
                        {[1, 2, 3].map((level) => (
                          <div
                            key={level}
                            className={cn(
                              'h-1.5 flex-1 rounded-full transition-colors',
                              level <= config.level ? config.color : 'bg-secondary'
                            )}
                          />
                        ))}
                      </div>

                      {/* Progress to Next Level */}
                      <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                        <span>Progress to {milestone.label}</span>
                        <span>{Math.round(progressPercent)}%</span>
                      </div>
                      <Progress
                        value={progressPercent}
                        max={100}
                        className="h-1.5"
                        indicatorClassName={config.color}
                      />

                      {milestone.hoursRemaining > 0 && (
                        <div className="mt-1.5 flex items-center gap-1 text-xs">
                          <TrendingUp className="w-3 h-3 text-muted-foreground" />
                          <span className="text-muted-foreground">
                            {milestone.hoursRemaining}h to {milestone.label}
                          </span>
                        </div>
                      )}
                      {milestone.hoursRemaining === 0 && milestone.label !== 'Mastered' && (
                        <div className="mt-1.5 flex items-center gap-1 text-xs text-success">
                          <CheckCircle2 className="w-3 h-3" />
                          <span>{milestone.label}</span>
                        </div>
                      )}
                      {milestone.label === 'Mastered' && (
                        <div className="mt-1.5 flex items-center gap-1 text-xs text-success">
                          <Star className="w-3 h-3" />
                          <span>Mastered - all advancement thresholds met</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          )}

          {/* TAB: Certifications */}
          {activeTab === 'certs' && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5" />
                  Certifications
                </CardTitle>
              </CardHeader>
              <CardContent>
                {technician.certifications.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No certifications on file</p>
                ) : (
                  <div className="space-y-2">
                    {technician.certifications.map((cert) => (
                      <div
                        key={cert.cert_name}
                        className="flex items-center justify-between p-3 rounded-md border hover:bg-secondary/30 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          {cert.status === 'Active' && <CheckCircle2 className="w-4 h-4 text-success" />}
                          {cert.status === 'Expiring Soon' && <AlertTriangle className="w-4 h-4 text-warning" />}
                          {cert.status === 'Expired' && <XCircle className="w-4 h-4 text-destructive" />}
                          {cert.status === 'Pending' && <Clock className="w-4 h-4 text-muted-foreground" />}
                          <div>
                            <span className="text-sm font-medium">{cert.cert_name}</span>
                            <div className="text-[11px] text-muted-foreground flex items-center gap-1 mt-0.5">
                              <Calendar className="w-3 h-3" />
                              Issued: {cert.issue_date}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <div className="text-right">
                            <div className="text-[11px]">Expires: {cert.expiry_date}</div>
                          </div>
                          <Badge
                            variant={
                              cert.status === 'Active' ? 'success' :
                              cert.status === 'Expiring Soon' ? 'warning' : 'destructive'
                            }
                          >
                            {cert.status}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* TAB: Documents */}
          {activeTab === 'docs' && (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <FileText className="w-5 h-5" />
                    Documents
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {technician.documents.map((doc) => (
                      <div
                        key={doc.doc_type}
                        className="flex items-center justify-between p-3 rounded-md border hover:bg-secondary/30 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          <FileText className="w-4 h-4 text-muted-foreground" />
                          <span className="text-sm">{doc.doc_type}</span>
                        </div>
                        <Badge
                          variant={
                            doc.verification_status === 'Verified' ? 'success' :
                            doc.verification_status === 'Pending Review' ? 'warning' :
                            doc.verification_status === 'Expired' ? 'destructive' : 'secondary'
                          }
                        >
                          {doc.verification_status}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Badges */}
              {(technician.site_badges.length > 0 || technician.milestone_badges.length > 0) && (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Award className="w-5 h-5" />
                      Badges
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="flex flex-wrap gap-2">
                      {technician.site_badges.map((b) => (
                        <Badge key={b} variant="outline" className="gap-1">
                          <ShieldCheck className="w-3 h-3" />
                          {b}
                        </Badge>
                      ))}
                      {technician.milestone_badges.map((b) => (
                        <Badge key={b} variant="default" className="gap-1">
                          <Star className="w-3 h-3" />
                          {b}
                        </Badge>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}
            </>
          )}

          {/* TAB: Overview */}
          {activeTab === 'overview' && (
            <>
              {/* Regions */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    <MapPin className="w-5 h-5" />
                    Approved Regions
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {technician.approved_regions.map((region) => (
                      <Badge key={region} variant="outline">{region}</Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Contact */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Contact & Details</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {technician.email && (
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Email</span>
                      <span>{technician.email}</span>
                    </div>
                  )}
                  {technician.phone && (
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Phone</span>
                      <span>{technician.phone}</span>
                    </div>
                  )}
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Available From</span>
                    <span>{technician.available_from}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Home Base</span>
                    <span>{technician.home_base_city}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Status Locked</span>
                    <Badge variant={technician.deployability_locked ? 'warning' : 'secondary'}>
                      {technician.deployability_locked ? 'Locked' : 'Auto'}
                    </Badge>
                  </div>
                </CardContent>
              </Card>

              {/* All Badges */}
              {(technician.site_badges.length > 0 || technician.milestone_badges.length > 0) && (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Award className="w-5 h-5" />
                      All Badges
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="flex flex-wrap gap-2">
                      {technician.site_badges.map((b) => (
                        <Badge key={b} variant="outline" className="gap-1">
                          <ShieldCheck className="w-3 h-3" />
                          {b}
                        </Badge>
                      ))}
                      {technician.milestone_badges.map((b) => (
                        <Badge key={b} variant="default" className="gap-1">
                          <Star className="w-3 h-3" />
                          {b}
                        </Badge>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </div>
      </div>

      <style>{`
        @keyframes slideInFromRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}
