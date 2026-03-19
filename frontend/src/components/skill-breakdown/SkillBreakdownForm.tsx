import { useState, useCallback, useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
import { Progress } from '@/components/ui/progress'
import { useTechPortalStore } from '@/stores/techPortalStore'
import type { SkillBreakdownItem, SkillProficiencyRating, SkillBreakdownSubmission, Assignment } from '@/types'

// ============================================================
// Skill Breakdown Multi-Step Form
// ============================================================
// Steps:
// 1. Skill Selection — pick skills used during the assignment
// 2. Proficiency Rating — self-rate proficiency for each selected skill
// 3. Time Per Skill — allocate hours across selected skills
// 4. Notes & Review — add notes and review before submission
// ============================================================

const PROFICIENCY_RATINGS: { value: SkillProficiencyRating; label: string; description: string; color: string }[] = [
  {
    value: 'Below Expectations',
    label: 'Below Expectations',
    description: 'Still learning, needed significant guidance',
    color: 'bg-destructive/10 text-destructive border-destructive/30',
  },
  {
    value: 'Meets Expectations',
    label: 'Meets Expectations',
    description: 'Performed competently with standard supervision',
    color: 'bg-secondary/50 text-foreground border-border',
  },
  {
    value: 'Exceeds Expectations',
    label: 'Exceeds Expectations',
    description: 'Performed above average, worked independently',
    color: 'bg-warning/10 text-warning border-warning/30',
  },
  {
    value: 'Expert',
    label: 'Expert',
    description: 'Exceptional performance, mentored others',
    color: 'bg-success/10 text-success border-success/30',
  },
]

// Common fiber/data-center skills
const COMMON_SKILLS = [
  'Fiber Splicing',
  'OTDR Testing',
  'Cable Pulling',
  'Network Termination',
  'Aerial Installation',
  'Underground Installation',
  'Conduit Installation',
  'Cable Routing',
  'Connector Installation',
  'Power Systems',
  'Rack & Stack',
  'Equipment Mounting',
  'Safety Compliance',
  'Quality Assurance Testing',
  'Documentation & Labeling',
  'Troubleshooting',
  'Site Survey',
  'Project Coordination',
]

interface SkillBreakdownFormProps {
  assignment: Assignment
  onClose: () => void
}

type FormStep = 'skills' | 'proficiency' | 'hours' | 'review'

const STEPS: { key: FormStep; label: string; number: number }[] = [
  { key: 'skills', label: 'Select Skills', number: 1 },
  { key: 'proficiency', label: 'Rate Proficiency', number: 2 },
  { key: 'hours', label: 'Time Allocation', number: 3 },
  { key: 'review', label: 'Review & Submit', number: 4 },
]

interface SkillEntry {
  skill_name: string
  proficiency_rating: SkillProficiencyRating | null
  hours_applied: number | null
  notes: string
}

export function SkillBreakdownForm({ assignment, onClose }: SkillBreakdownFormProps) {
  const store = useTechPortalStore()
  const tech = store.technician

  const [currentStep, setCurrentStep] = useState<FormStep>('skills')
  const [selectedSkills, setSelectedSkills] = useState<string[]>(
    // Pre-populate with technician's known skills
    tech?.skills.map((s) => s.skill_name) || []
  )
  const [skillEntries, setSkillEntries] = useState<Record<string, SkillEntry>>({})
  const [overallNotes, setOverallNotes] = useState('')
  const [overallRating, setOverallRating] = useState<SkillProficiencyRating | null>(null)
  const [customSkill, setCustomSkill] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})

  // Get unique skill options: technician's skills + common skills
  const availableSkills = useMemo(() => {
    const techSkills = tech?.skills.map((s) => s.skill_name) || []
    const allSkills = [...new Set([...techSkills, ...COMMON_SKILLS])]
    return allSkills.sort()
  }, [tech])

  const currentStepIndex = STEPS.findIndex((s) => s.key === currentStep)
  const progressPct = ((currentStepIndex + 1) / STEPS.length) * 100

  // Ensure skill entries exist for all selected skills
  const getSkillEntry = useCallback(
    (skillName: string): SkillEntry => {
      return (
        skillEntries[skillName] || {
          skill_name: skillName,
          proficiency_rating: null,
          hours_applied: null,
          notes: '',
        }
      )
    },
    [skillEntries]
  )

  const updateSkillEntry = useCallback(
    (skillName: string, updates: Partial<SkillEntry>) => {
      setSkillEntries((prev) => ({
        ...prev,
        [skillName]: { ...getSkillEntry(skillName), ...updates },
      }))
    },
    [getSkillEntry]
  )

  const toggleSkill = (skillName: string) => {
    setSelectedSkills((prev) =>
      prev.includes(skillName)
        ? prev.filter((s) => s !== skillName)
        : [...prev, skillName]
    )
    setErrors((prev) => {
      const next = { ...prev }
      delete next.skills
      return next
    })
  }

  const addCustomSkill = () => {
    const trimmed = customSkill.trim()
    if (trimmed && !selectedSkills.includes(trimmed)) {
      setSelectedSkills((prev) => [...prev, trimmed])
      setCustomSkill('')
    }
  }

  // Validation per step
  const validateStep = (step: FormStep): boolean => {
    const newErrors: Record<string, string> = {}

    switch (step) {
      case 'skills':
        if (selectedSkills.length === 0) {
          newErrors.skills = 'Select at least one skill'
        }
        break
      case 'proficiency':
        for (const skillName of selectedSkills) {
          const entry = getSkillEntry(skillName)
          if (!entry.proficiency_rating) {
            newErrors[`proficiency_${skillName}`] = `Rate your proficiency for ${skillName}`
          }
        }
        if (Object.keys(newErrors).length > 0) {
          newErrors.proficiency = 'Please rate all skills before continuing'
        }
        break
      case 'hours': {
        const totalHours = selectedSkills.reduce((sum, s) => sum + (getSkillEntry(s).hours_applied || 0), 0)
        if (totalHours <= 0) {
          newErrors.hours = 'Total hours must be greater than zero'
        }
        for (const skillName of selectedSkills) {
          const entry = getSkillEntry(skillName)
          if (entry.hours_applied !== null && entry.hours_applied < 0) {
            newErrors[`hours_${skillName}`] = 'Hours cannot be negative'
          }
        }
        break
      }
      case 'review':
        // No additional validation needed
        break
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const goNext = () => {
    if (!validateStep(currentStep)) return
    const nextIdx = currentStepIndex + 1
    if (nextIdx < STEPS.length) {
      setCurrentStep(STEPS[nextIdx].key)
    }
  }

  const goBack = () => {
    const prevIdx = currentStepIndex - 1
    if (prevIdx >= 0) {
      setCurrentStep(STEPS[prevIdx].key)
    }
  }

  const handleSubmit = async () => {
    if (!validateStep('review')) return

    const items: SkillBreakdownItem[] = selectedSkills.map((skillName) => {
      const entry = getSkillEntry(skillName)
      return {
        skill_name: skillName,
        hours_applied: entry.hours_applied,
        proficiency_rating: entry.proficiency_rating!,
        notes: entry.notes || undefined,
      }
    })

    const submission: SkillBreakdownSubmission = {
      items,
      overall_notes: overallNotes || undefined,
      overall_rating: overallRating || undefined,
    }

    await store.submitSkillBreakdown(assignment.id, submission)
  }

  const totalAllocatedHours = selectedSkills.reduce(
    (sum, s) => sum + (getSkillEntry(s).hours_applied || 0),
    0
  )

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-2xl max-h-[90vh] bg-background border border-border rounded-t-2xl sm:rounded-2xl shadow-xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border bg-muted/30">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-lg font-bold text-foreground">Skill Breakdown</h2>
              <p className="text-sm text-muted-foreground">
                {assignment.project_name} — {assignment.role_name}
              </p>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose} className="touch-manipulation">
              ✕
            </Button>
          </div>

          {/* Step Indicator */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              {STEPS.map((step, idx) => (
                <div key={step.key} className="flex items-center gap-1.5 flex-1">
                  <div
                    className={`
                      flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0
                      ${idx < currentStepIndex
                        ? 'bg-success text-success-foreground'
                        : idx === currentStepIndex
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-muted text-muted-foreground'
                      }
                    `}
                  >
                    {idx < currentStepIndex ? '✓' : step.number}
                  </div>
                  <span
                    className={`text-xs font-medium hidden sm:inline ${
                      idx === currentStepIndex ? 'text-foreground' : 'text-muted-foreground'
                    }`}
                  >
                    {step.label}
                  </span>
                  {idx < STEPS.length - 1 && (
                    <div className={`flex-1 h-0.5 rounded ${
                      idx < currentStepIndex ? 'bg-success' : 'bg-border'
                    }`} />
                  )}
                </div>
              ))}
            </div>
            <Progress value={progressPct} className="h-1" />
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* Success State */}
          {store.skillBreakdownSuccess && (
            <div className="flex flex-col items-center justify-center py-12 space-y-4">
              <div className="w-16 h-16 rounded-full bg-success/20 flex items-center justify-center text-3xl">
                ✓
              </div>
              <h3 className="text-xl font-bold text-foreground">Skill Breakdown Submitted</h3>
              <p className="text-sm text-muted-foreground text-center max-w-sm">
                Your skill breakdown for {assignment.project_name} has been submitted successfully.
                Your training hours will be updated accordingly.
              </p>
              <Button onClick={onClose} className="touch-manipulation mt-4">
                Done
              </Button>
            </div>
          )}

          {/* Error Display */}
          {store.skillBreakdownError && !store.skillBreakdownSuccess && (
            <div className="mb-4 p-3 bg-destructive/10 border border-destructive/30 rounded-lg">
              <p className="text-sm text-destructive font-medium">{store.skillBreakdownError}</p>
            </div>
          )}

          {/* Step 1: Skill Selection */}
          {currentStep === 'skills' && !store.skillBreakdownSuccess && (
            <div className="space-y-4">
              <div>
                <h3 className="text-base font-semibold text-foreground mb-1">
                  Which skills did you use on this assignment?
                </h3>
                <p className="text-sm text-muted-foreground">
                  Select all skills you performed during this project. Your known skills are pre-selected.
                </p>
              </div>

              {errors.skills && (
                <p className="text-sm text-destructive font-medium">{errors.skills}</p>
              )}

              {/* Pre-selected (technician's known skills) */}
              {tech && tech.skills.length > 0 && (
                <div>
                  <Label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
                    Your Skills
                  </Label>
                  <div className="flex flex-wrap gap-2">
                    {tech.skills.map((s) => (
                      <button
                        key={s.skill_name}
                        type="button"
                        onClick={() => toggleSkill(s.skill_name)}
                        className={`
                          px-3 py-2 rounded-lg border text-sm font-medium transition-all
                          touch-manipulation cursor-pointer
                          ${selectedSkills.includes(s.skill_name)
                            ? 'bg-primary text-primary-foreground border-primary shadow-sm'
                            : 'bg-background text-foreground border-border hover:border-primary/50'
                          }
                        `}
                      >
                        {s.skill_name}
                        <span className="ml-1 text-xs opacity-70">({s.proficiency_level})</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Other common skills */}
              <div>
                <Label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
                  Other Skills
                </Label>
                <div className="flex flex-wrap gap-2">
                  {availableSkills
                    .filter((s) => !tech?.skills.some((ts) => ts.skill_name === s))
                    .map((skill) => (
                      <button
                        key={skill}
                        type="button"
                        onClick={() => toggleSkill(skill)}
                        className={`
                          px-3 py-2 rounded-lg border text-sm font-medium transition-all
                          touch-manipulation cursor-pointer
                          ${selectedSkills.includes(skill)
                            ? 'bg-primary text-primary-foreground border-primary shadow-sm'
                            : 'bg-background text-foreground border-border hover:border-primary/50'
                          }
                        `}
                      >
                        {skill}
                      </button>
                    ))}
                </div>
              </div>

              {/* Custom skill input */}
              <div>
                <Label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
                  Add Custom Skill
                </Label>
                <div className="flex gap-2">
                  <Input
                    value={customSkill}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCustomSkill(e.target.value)}
                    placeholder="Enter skill name..."
                    className="touch-manipulation"
                    onKeyDown={(e: React.KeyboardEvent) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addCustomSkill()
                      }
                    }}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={addCustomSkill}
                    disabled={!customSkill.trim()}
                    className="touch-manipulation flex-shrink-0"
                  >
                    + Add
                  </Button>
                </div>
              </div>

              {/* Selected count */}
              <div className="text-sm text-muted-foreground">
                {selectedSkills.length} skill{selectedSkills.length !== 1 ? 's' : ''} selected
              </div>
            </div>
          )}

          {/* Step 2: Proficiency Rating */}
          {currentStep === 'proficiency' && !store.skillBreakdownSuccess && (
            <div className="space-y-4">
              <div>
                <h3 className="text-base font-semibold text-foreground mb-1">
                  Rate your proficiency for each skill
                </h3>
                <p className="text-sm text-muted-foreground">
                  Be honest — this helps us track your growth and match you to better assignments.
                </p>
              </div>

              {errors.proficiency && (
                <p className="text-sm text-destructive font-medium">{errors.proficiency}</p>
              )}

              <div className="space-y-5">
                {selectedSkills.map((skillName) => {
                  const entry = getSkillEntry(skillName)
                  return (
                    <div key={skillName} className="space-y-2">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm text-foreground">{skillName}</span>
                        {entry.proficiency_rating && (
                          <Badge variant="outline" className="text-xs">
                            {entry.proficiency_rating}
                          </Badge>
                        )}
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                        {PROFICIENCY_RATINGS.map((rating) => (
                          <button
                            key={rating.value}
                            type="button"
                            onClick={() => updateSkillEntry(skillName, { proficiency_rating: rating.value })}
                            className={`
                              p-2.5 rounded-lg border text-left transition-all touch-manipulation cursor-pointer
                              ${entry.proficiency_rating === rating.value
                                ? `${rating.color} ring-2 ring-offset-1 ring-offset-background`
                                : 'bg-background border-border hover:bg-muted/50'
                              }
                            `}
                          >
                            <span className="text-xs font-semibold block leading-tight">{rating.label}</span>
                            <span className="text-[10px] text-muted-foreground leading-tight mt-0.5 block">
                              {rating.description}
                            </span>
                          </button>
                        ))}
                      </div>
                      {errors[`proficiency_${skillName}`] && (
                        <p className="text-xs text-destructive">{errors[`proficiency_${skillName}`]}</p>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Step 3: Time Allocation */}
          {currentStep === 'hours' && !store.skillBreakdownSuccess && (
            <div className="space-y-4">
              <div>
                <h3 className="text-base font-semibold text-foreground mb-1">
                  How many hours did you spend on each skill?
                </h3>
                <p className="text-sm text-muted-foreground">
                  Estimate the hours spent per skill during this assignment. These hours count toward your training advancement.
                </p>
              </div>

              {errors.hours && (
                <p className="text-sm text-destructive font-medium">{errors.hours}</p>
              )}

              <div className="space-y-3">
                {selectedSkills.map((skillName) => {
                  const entry = getSkillEntry(skillName)
                  return (
                    <div
                      key={skillName}
                      className="flex items-center gap-3 p-3 bg-muted/30 rounded-lg border border-border"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-foreground truncate">
                            {skillName}
                          </span>
                          <Badge variant="outline" className="text-[10px] flex-shrink-0">
                            {entry.proficiency_rating || 'Unrated'}
                          </Badge>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <Input
                          type="number"
                          min={0}
                          max={5000}
                          step={0.5}
                          placeholder="0"
                          value={entry.hours_applied ?? ''}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                            const val = e.target.value === '' ? null : parseFloat(e.target.value)
                            updateSkillEntry(skillName, { hours_applied: val })
                            setErrors((prev) => {
                              const next = { ...prev }
                              delete next.hours
                              delete next[`hours_${skillName}`]
                              return next
                            })
                          }}
                          className="w-20 text-center touch-manipulation"
                        />
                        <span className="text-xs text-muted-foreground">hrs</span>
                      </div>
                      {errors[`hours_${skillName}`] && (
                        <p className="text-xs text-destructive">{errors[`hours_${skillName}`]}</p>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* Total hours summary */}
              <div className="flex items-center justify-between p-3 bg-primary/5 border border-primary/20 rounded-lg">
                <span className="text-sm font-semibold text-foreground">Total Hours</span>
                <span className="text-lg font-bold text-primary">
                  {totalAllocatedHours.toFixed(1)} hrs
                </span>
              </div>
            </div>
          )}

          {/* Step 4: Review & Submit */}
          {currentStep === 'review' && !store.skillBreakdownSuccess && (
            <div className="space-y-4">
              <div>
                <h3 className="text-base font-semibold text-foreground mb-1">
                  Review & Submit
                </h3>
                <p className="text-sm text-muted-foreground">
                  Review your skill breakdown before submitting. You can go back to make changes.
                </p>
              </div>

              {/* Assignment summary */}
              <Card className="bg-muted/30">
                <CardContent className="py-3">
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <span className="text-muted-foreground">Project</span>
                      <p className="font-medium">{assignment.project_name}</p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Role</span>
                      <p className="font-medium">{assignment.role_name}</p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Period</span>
                      <p className="font-medium">
                        {formatDate(assignment.start_date)} — {formatDate(assignment.end_date)}
                      </p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Total Hours</span>
                      <p className="font-bold text-primary">{totalAllocatedHours.toFixed(1)} hrs</p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Skills summary table */}
              <div className="border border-border rounded-lg overflow-hidden">
                <div className="bg-muted/50 px-3 py-2 border-b border-border">
                  <div className="grid grid-cols-12 gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    <span className="col-span-4">Skill</span>
                    <span className="col-span-4">Proficiency</span>
                    <span className="col-span-2 text-right">Hours</span>
                    <span className="col-span-2 text-right">Notes</span>
                  </div>
                </div>
                {selectedSkills.map((skillName) => {
                  const entry = getSkillEntry(skillName)
                  return (
                    <div
                      key={skillName}
                      className="px-3 py-2.5 border-b border-border last:border-0"
                    >
                      <div className="grid grid-cols-12 gap-2 text-sm items-center">
                        <span className="col-span-4 font-medium text-foreground truncate">
                          {skillName}
                        </span>
                        <span className="col-span-4">
                          <Badge
                            variant={
                              entry.proficiency_rating === 'Expert'
                                ? 'success'
                                : entry.proficiency_rating === 'Exceeds Expectations'
                                  ? 'warning'
                                  : entry.proficiency_rating === 'Below Expectations'
                                    ? 'destructive'
                                    : 'secondary'
                            }
                            className="text-xs"
                          >
                            {entry.proficiency_rating}
                          </Badge>
                        </span>
                        <span className="col-span-2 text-right font-medium">
                          {entry.hours_applied ?? 0}
                        </span>
                        <span className="col-span-2 text-right text-muted-foreground text-xs truncate">
                          {entry.notes ? '📝' : '—'}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Per-skill notes */}
              <div>
                <Label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
                  Skill Notes (optional)
                </Label>
                <div className="space-y-2">
                  {selectedSkills.map((skillName) => {
                    const entry = getSkillEntry(skillName)
                    return (
                      <div key={skillName} className="flex items-start gap-2">
                        <span className="text-sm font-medium text-foreground w-32 flex-shrink-0 pt-2 truncate">
                          {skillName}
                        </span>
                        <Input
                          placeholder={`Notes for ${skillName}...`}
                          value={entry.notes}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                            updateSkillEntry(skillName, { notes: e.target.value })
                          }
                          className="touch-manipulation text-sm"
                        />
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Overall rating */}
              <div>
                <Label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
                  Overall Self-Assessment (optional)
                </Label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {PROFICIENCY_RATINGS.map((rating) => (
                    <button
                      key={rating.value}
                      type="button"
                      onClick={() => setOverallRating(
                        overallRating === rating.value ? null : rating.value
                      )}
                      className={`
                        p-2 rounded-lg border text-center transition-all touch-manipulation cursor-pointer
                        ${overallRating === rating.value
                          ? `${rating.color} ring-2 ring-offset-1 ring-offset-background`
                          : 'bg-background border-border hover:bg-muted/50'
                        }
                      `}
                    >
                      <span className="text-xs font-semibold">{rating.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Overall notes */}
              <div>
                <Label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
                  Overall Notes (optional)
                </Label>
                <Textarea
                  placeholder="Any additional notes about your performance, challenges, or achievements on this assignment..."
                  value={overallNotes}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setOverallNotes(e.target.value)}
                  rows={3}
                  className="touch-manipulation"
                />
              </div>
            </div>
          )}
        </div>

        {/* Footer Navigation */}
        {!store.skillBreakdownSuccess && (
          <div className="px-5 py-4 border-t border-border bg-muted/20 flex items-center justify-between">
            <div>
              {currentStepIndex > 0 && (
                <Button
                  variant="ghost"
                  onClick={goBack}
                  className="touch-manipulation"
                >
                  ← Back
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                onClick={onClose}
                className="touch-manipulation"
              >
                Cancel
              </Button>
              {currentStep !== 'review' ? (
                <Button onClick={goNext} className="touch-manipulation">
                  Next →
                </Button>
              ) : (
                <Button
                  onClick={handleSubmit}
                  disabled={store.skillBreakdownSubmitting}
                  className="touch-manipulation min-w-[140px]"
                >
                  {store.skillBreakdownSubmitting ? (
                    <span className="flex items-center gap-2">
                      <span className="animate-spin inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
                      Submitting...
                    </span>
                  ) : (
                    'Submit Breakdown'
                  )}
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// Helpers
// ============================================================

function formatDate(dateStr: string): string {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}
