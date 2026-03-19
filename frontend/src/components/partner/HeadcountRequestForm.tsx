/**
 * HeadcountRequestForm — Partner-facing form to request additional technicians.
 *
 * Features:
 * - Project selection (filtered to partner's projects)
 * - Role name, quantity, priority, date range
 * - Required skills and certifications multi-select
 * - Constraints/notes free-text
 * - Client-side validation with clear error messages
 * - Submission to POST /api/headcount-requests
 */

import { useState, useEffect } from "react";
import { usePartnerStore } from "@/stores/partnerStore";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Users,
  Plus,
  X,
  Send,
  CheckCircle2,
  AlertCircle,
  Loader2,
} from "lucide-react";

// Common fiber/data center role names for quick selection
const COMMON_ROLES = [
  "Lead Splicer",
  "Fiber Splicer",
  "Fiber Technician",
  "Cable Puller",
  "OTDR Tester",
  "Site Supervisor",
  "Data Center Technician",
  "OSP Technician",
  "ISP Technician",
  "Tower Climber",
];

// Common required skills
const COMMON_SKILLS = [
  "Fiber Splicing",
  "OTDR Testing",
  "Cable Pulling",
  "Aerial Construction",
  "Underground Construction",
  "Data Center Cabling",
  "Network Troubleshooting",
  "Blueprint Reading",
  "Safety Compliance",
  "Equipment Operation",
];

// Common certifications
const COMMON_CERTS = [
  "FOA CFOT",
  "FOA CFOS/S",
  "FOA CFOS/T",
  "OSHA 10",
  "OSHA 30",
  "BICSI Installer",
  "CompTIA Network+",
  "First Aid/CPR",
  "CDL Class A",
  "CDL Class B",
];

const PRIORITY_OPTIONS = [
  { value: "low", label: "Low", color: "text-muted-foreground" },
  { value: "normal", label: "Normal", color: "text-foreground" },
  { value: "high", label: "High", color: "text-amber-500" },
  { value: "urgent", label: "Urgent", color: "text-destructive" },
];

interface FormData {
  project_id: string;
  role_name: string;
  quantity: number;
  priority: string;
  start_date: string;
  end_date: string;
  required_skills: string[];
  required_certs: string[];
  constraints: string;
  notes: string;
}

interface FormErrors {
  role_name?: string;
  quantity?: string;
  start_date?: string;
  end_date?: string;
  general?: string;
}

export function HeadcountRequestForm() {
  const { projects, partnerId } = usePartnerStore();

  const [form, setForm] = useState<FormData>({
    project_id: "",
    role_name: "",
    quantity: 1,
    priority: "normal",
    start_date: "",
    end_date: "",
    required_skills: [],
    required_certs: [],
    constraints: "",
    notes: "",
  });

  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [customRole, setCustomRole] = useState(false);
  const [skillInput, setSkillInput] = useState("");
  const [certInput, setCertInput] = useState("");

  // Reset submitted state after 5 seconds
  useEffect(() => {
    if (submitted) {
      const timer = setTimeout(() => setSubmitted(false), 5000);
      return () => clearTimeout(timer);
    }
  }, [submitted]);

  const validate = (): boolean => {
    const newErrors: FormErrors = {};

    if (!form.role_name.trim()) {
      newErrors.role_name = "Role name is required";
    }

    if (form.quantity < 1 || form.quantity > 100) {
      newErrors.quantity = "Quantity must be between 1 and 100";
    }

    if (form.start_date && form.end_date && form.start_date > form.end_date) {
      newErrors.end_date = "End date must be after start date";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validate()) return;

    setIsSubmitting(true);
    setErrors({});

    try {
      const payload: Record<string, unknown> = {
        partner_id: partnerId,
        role_name: form.role_name.trim(),
        quantity: form.quantity,
        priority: form.priority,
        required_skills: form.required_skills.map((s) => ({
          skill: s,
          min_level: "Beginner",
        })),
        required_certs: form.required_certs,
      };

      if (form.project_id) payload.project_id = form.project_id;
      if (form.start_date) payload.start_date = form.start_date;
      if (form.end_date) payload.end_date = form.end_date;
      if (form.constraints.trim()) payload.constraints = form.constraints.trim();
      if (form.notes.trim()) payload.notes = form.notes.trim();

      await api.post("/headcount-requests", payload);

      // Reset form on success
      setForm({
        project_id: "",
        role_name: "",
        quantity: 1,
        priority: "normal",
        start_date: "",
        end_date: "",
        required_skills: [],
        required_certs: [],
        constraints: "",
        notes: "",
      });
      setCustomRole(false);
      setSubmitted(true);
    } catch (err: any) {
      setErrors({
        general:
          err?.response?.data?.detail || "Failed to submit request. Please try again.",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const addSkill = (skill: string) => {
    if (skill && !form.required_skills.includes(skill)) {
      setForm((f) => ({ ...f, required_skills: [...f.required_skills, skill] }));
    }
    setSkillInput("");
  };

  const removeSkill = (skill: string) => {
    setForm((f) => ({
      ...f,
      required_skills: f.required_skills.filter((s) => s !== skill),
    }));
  };

  const addCert = (cert: string) => {
    if (cert && !form.required_certs.includes(cert)) {
      setForm((f) => ({ ...f, required_certs: [...f.required_certs, cert] }));
    }
    setCertInput("");
  };

  const removeCert = (cert: string) => {
    setForm((f) => ({
      ...f,
      required_certs: f.required_certs.filter((c) => c !== cert),
    }));
  };

  if (submitted) {
    return (
      <Card className="border-emerald-500/30 bg-emerald-500/5">
        <CardContent className="flex flex-col items-center justify-center py-12 gap-4">
          <CheckCircle2 className="h-12 w-12 text-emerald-500" />
          <div className="text-center">
            <h3 className="text-lg font-semibold">Request Submitted</h3>
            <p className="text-sm text-muted-foreground mt-1">
              Your headcount request has been submitted for ops review. You&apos;ll be
              notified when it&apos;s approved.
            </p>
          </div>
          <Button variant="outline" onClick={() => setSubmitted(false)}>
            Submit Another Request
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Users className="h-5 w-5" />
          Request Additional Technicians
        </CardTitle>
        <CardDescription>
          Submit a headcount request for your project. Ops will review and approve
          before staffing begins.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* General error */}
          {errors.general && (
            <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {errors.general}
            </div>
          )}

          {/* Project selection */}
          <div className="space-y-2">
            <Label htmlFor="project">Project (optional)</Label>
            <Select
              value={form.project_id}
              onValueChange={(v) => setForm((f) => ({ ...f, project_id: v }))}
            >
              <SelectTrigger id="project">
                <SelectValue placeholder="Select a project..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No specific project</SelectItem>
                {projects.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name} — {p.location_region}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Role name */}
          <div className="space-y-2">
            <Label htmlFor="role_name">
              Role Name <span className="text-destructive">*</span>
            </Label>
            {customRole ? (
              <div className="flex gap-2">
                <Input
                  id="role_name"
                  value={form.role_name}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, role_name: e.target.value }))
                  }
                  placeholder="Enter custom role name..."
                  className={errors.role_name ? "border-destructive" : ""}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setCustomRole(false);
                    setForm((f) => ({ ...f, role_name: "" }));
                  }}
                >
                  Cancel
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                <Select
                  value={form.role_name}
                  onValueChange={(v) => setForm((f) => ({ ...f, role_name: v }))}
                >
                  <SelectTrigger className={errors.role_name ? "border-destructive" : ""}>
                    <SelectValue placeholder="Select a role..." />
                  </SelectTrigger>
                  <SelectContent>
                    {COMMON_ROLES.map((role) => (
                      <SelectItem key={role} value={role}>
                        {role}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="h-auto p-0 text-xs"
                  onClick={() => setCustomRole(true)}
                >
                  + Custom role name
                </Button>
              </div>
            )}
            {errors.role_name && (
              <p className="text-xs text-destructive">{errors.role_name}</p>
            )}
          </div>

          {/* Quantity + Priority row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="quantity">
                Quantity <span className="text-destructive">*</span>
              </Label>
              <Input
                id="quantity"
                type="number"
                min={1}
                max={100}
                value={form.quantity}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    quantity: parseInt(e.target.value) || 1,
                  }))
                }
                className={errors.quantity ? "border-destructive" : ""}
              />
              {errors.quantity && (
                <p className="text-xs text-destructive">{errors.quantity}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="priority">Priority</Label>
              <Select
                value={form.priority}
                onValueChange={(v) => setForm((f) => ({ ...f, priority: v }))}
              >
                <SelectTrigger id="priority">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      <span className={opt.color}>{opt.label}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Date range */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="start_date">Desired Start Date</Label>
              <Input
                id="start_date"
                type="date"
                value={form.start_date}
                onChange={(e) =>
                  setForm((f) => ({ ...f, start_date: e.target.value }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="end_date">Estimated End Date</Label>
              <Input
                id="end_date"
                type="date"
                value={form.end_date}
                onChange={(e) =>
                  setForm((f) => ({ ...f, end_date: e.target.value }))
                }
                className={errors.end_date ? "border-destructive" : ""}
              />
              {errors.end_date && (
                <p className="text-xs text-destructive">{errors.end_date}</p>
              )}
            </div>
          </div>

          {/* Required Skills */}
          <div className="space-y-2">
            <Label>Required Skills</Label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {form.required_skills.map((skill) => (
                <Badge
                  key={skill}
                  variant="secondary"
                  className="flex items-center gap-1 cursor-pointer hover:bg-destructive/20"
                  onClick={() => removeSkill(skill)}
                >
                  {skill}
                  <X className="h-3 w-3" />
                </Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Select
                value={skillInput}
                onValueChange={(v) => addSkill(v)}
              >
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder="Add a skill..." />
                </SelectTrigger>
                <SelectContent>
                  {COMMON_SKILLS.filter(
                    (s) => !form.required_skills.includes(s)
                  ).map((skill) => (
                    <SelectItem key={skill} value={skill}>
                      {skill}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Required Certifications */}
          <div className="space-y-2">
            <Label>Required Certifications</Label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {form.required_certs.map((cert) => (
                <Badge
                  key={cert}
                  variant="outline"
                  className="flex items-center gap-1 cursor-pointer hover:bg-destructive/20"
                  onClick={() => removeCert(cert)}
                >
                  {cert}
                  <X className="h-3 w-3" />
                </Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Select
                value={certInput}
                onValueChange={(v) => addCert(v)}
              >
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder="Add a certification..." />
                </SelectTrigger>
                <SelectContent>
                  {COMMON_CERTS.filter(
                    (c) => !form.required_certs.includes(c)
                  ).map((cert) => (
                    <SelectItem key={cert} value={cert}>
                      {cert}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Constraints */}
          <div className="space-y-2">
            <Label htmlFor="constraints">Constraints</Label>
            <Textarea
              id="constraints"
              value={form.constraints}
              onChange={(e) =>
                setForm((f) => ({ ...f, constraints: e.target.value }))
              }
              placeholder="e.g., Must have clearance for secure facility, bilingual preferred..."
              rows={2}
            />
          </div>

          {/* Notes */}
          <div className="space-y-2">
            <Label htmlFor="notes">Additional Notes</Label>
            <Textarea
              id="notes"
              value={form.notes}
              onChange={(e) =>
                setForm((f) => ({ ...f, notes: e.target.value }))
              }
              placeholder="Any other information for the ops team..."
              rows={2}
            />
          </div>

          {/* Submit */}
          <div className="flex justify-end pt-2">
            <Button type="submit" disabled={isSubmitting} className="min-w-[180px]">
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Submitting...
                </>
              ) : (
                <>
                  <Send className="mr-2 h-4 w-4" />
                  Submit Request
                </>
              )}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export default HeadcountRequestForm;
