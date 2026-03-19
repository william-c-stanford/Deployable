import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Edit2,
  Save,
  X,
  MapPin,
  Calendar,
  Mail,
  Phone,
  Shield,
  Award,
  FileText,
  Plus,
  Trash2,
  Lock,
  Unlock,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  Clock,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
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
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useTechnicianStore,
  SKILLS_TAXONOMY,
  CERT_NAMES,
  DOC_TYPES,
  REGIONS,
} from "@/stores/technicianStore";
import { cn, formatDate } from "@/lib/utils";
import { CareerPassportPanel } from "@/components/career-passport";
import { ManualBadgeManager } from "@/components/badges";
import { DeployabilityStatusPanel } from "@/components/deployability";
import type {
  Technician,
  Skill,
  Certification,
  TechDocument,
  DeployabilityStatus,
  CareerStage,
} from "@/types/index";

// ---- Helper Components ----

function getInitials(name: string): string {
  return name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "Ready Now" || status === "Active" || status === "Verified"
      ? "bg-emerald-500"
      : status === "In Training" || status === "Pending" || status === "Pending Review"
      ? "bg-blue-500"
      : status === "Currently Assigned"
      ? "bg-primary"
      : status === "Rolling Off Soon" || status === "Expiring Soon"
      ? "bg-amber-500"
      : status === "Missing Cert" || status === "Missing Docs" || status === "Expired" || status === "Not Submitted"
      ? "bg-red-500"
      : "bg-muted-foreground";
  return <span className={cn("h-2 w-2 rounded-full inline-block", color)} />;
}

function getDeployabilityColor(status: DeployabilityStatus): string {
  switch (status) {
    case "Ready Now":
      return "text-emerald-500 bg-emerald-500/10 border-emerald-500/30";
    case "In Training":
      return "text-blue-400 bg-blue-500/10 border-blue-500/30";
    case "Currently Assigned":
      return "text-primary bg-primary/10 border-primary/30";
    case "Rolling Off Soon":
      return "text-amber-400 bg-amber-500/10 border-amber-500/30";
    case "Missing Cert":
    case "Missing Docs":
      return "text-red-400 bg-red-500/10 border-red-500/30";
    case "Inactive":
      return "text-muted-foreground bg-muted border-border";
    default:
      return "";
  }
}

// ---- Overview Tab ----

function OverviewTab({
  tech,
  isEditing,
  editForm,
  setEditForm,
}: {
  tech: Technician;
  isEditing: boolean;
  editForm: Partial<Technician>;
  setEditForm: React.Dispatch<React.SetStateAction<Partial<Technician>>>;
}) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Personal Information */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Mail className="h-4 w-4" />
            Contact Information
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isEditing ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">First Name</Label>
                  <Input
                    value={(editForm.name || "").split(" ")[0]}
                    onChange={(e) =>
                      setEditForm((f) => ({
                        ...f,
                        name: `${e.target.value} ${(f.name || "").split(" ").slice(1).join(" ")}`,
                      }))
                    }
                  />
                </div>
                <div>
                  <Label className="text-xs">Last Name</Label>
                  <Input
                    value={(editForm.name || "").split(" ").slice(1).join(" ")}
                    onChange={(e) =>
                      setEditForm((f) => ({
                        ...f,
                        name: `${(f.name || "").split(" ")[0]} ${e.target.value}`,
                      }))
                    }
                  />
                </div>
              </div>
              <div>
                <Label className="text-xs">Email</Label>
                <Input
                  type="email"
                  value={editForm.email || ""}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, email: e.target.value }))
                  }
                />
              </div>
              <div>
                <Label className="text-xs">Phone</Label>
                <Input
                  value={editForm.phone || ""}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, phone: e.target.value }))
                  }
                />
              </div>
              <div>
                <Label className="text-xs">Home Base City</Label>
                <Input
                  value={editForm.home_base_city || ""}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, home_base_city: e.target.value }))
                  }
                />
              </div>
            </>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <Mail className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm">{tech.email || "—"}</span>
              </div>
              <div className="flex items-center gap-3">
                <Phone className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm">{tech.phone || "—"}</span>
              </div>
              <div className="flex items-center gap-3">
                <MapPin className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm">{tech.home_base_city}</span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Status & Regions */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Shield className="h-4 w-4" />
            Status & Availability
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isEditing ? (
            <>
              <div>
                <Label className="text-xs">Career Stage</Label>
                <Select
                  value={editForm.career_stage || tech.career_stage}
                  onValueChange={(v) =>
                    setEditForm((f) => ({ ...f, career_stage: v }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[
                      "Sourced",
                      "Screened",
                      "In Training",
                      "Training Completed",
                      "Awaiting Assignment",
                      "Deployed",
                    ].map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">Deployability Status</Label>
                <Select
                  value={editForm.deployability_status || tech.deployability_status}
                  onValueChange={(v) =>
                    setEditForm((f) => ({ ...f, deployability_status: v }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[
                      "Ready Now",
                      "In Training",
                      "Currently Assigned",
                      "Missing Cert",
                      "Missing Docs",
                      "Rolling Off Soon",
                      "Inactive",
                    ].map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">Available From</Label>
                <Input
                  type="date"
                  value={editForm.available_from || tech.available_from}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, available_from: e.target.value }))
                  }
                />
              </div>
            </>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Career Stage</span>
                <Badge variant="outline">{tech.career_stage}</Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Deployability</span>
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
                    getDeployabilityColor(tech.deployability_status as DeployabilityStatus)
                  )}
                >
                  <StatusDot status={tech.deployability_status} />
                  {tech.deployability_status}
                  {tech.deployability_locked && <Lock className="h-3 w-3" />}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Available From</span>
                <span className="text-sm">{formatDate(tech.available_from)}</span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Approved Regions */}
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <MapPin className="h-4 w-4" />
            Approved Regions
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isEditing ? (
            <div className="flex flex-wrap gap-2">
              {REGIONS.map((region) => {
                const regions = editForm.approved_regions || tech.approved_regions;
                const isSelected = regions.includes(region);
                return (
                  <Button
                    key={region}
                    variant={isSelected ? "default" : "outline"}
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => {
                      setEditForm((f) => ({
                        ...f,
                        approved_regions: isSelected
                          ? (f.approved_regions || tech.approved_regions).filter(
                              (r) => r !== region
                            )
                          : [...(f.approved_regions || tech.approved_regions), region],
                      }));
                    }}
                  >
                    {region}
                  </Button>
                );
              })}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {tech.approved_regions.map((region) => (
                <Badge key={region} variant="secondary">
                  {region}
                </Badge>
              ))}
              {tech.approved_regions.length === 0 && (
                <span className="text-sm text-muted-foreground">No regions set</span>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Experience & Metrics */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Award className="h-4 w-4" />
            Experience
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Years Experience</span>
            <span className="text-sm font-medium">
              {tech.years_experience != null ? `${tech.years_experience} yrs` : "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Total Projects</span>
            <span className="text-sm font-medium">
              {tech.total_project_count ?? "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Approved Hours</span>
            <span className="text-sm font-medium">
              {tech.total_approved_hours != null
                ? `${tech.total_approved_hours.toLocaleString()}h`
                : "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Hire Date</span>
            <span className="text-sm font-medium">
              {tech.hire_date ? formatDate(tech.hire_date) : "—"}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Ops Notes */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Ops Notes
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isEditing ? (
            <Textarea
              placeholder="Internal notes about this technician..."
              value={editForm.ops_notes ?? tech.ops_notes ?? ""}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, ops_notes: e.target.value }))
              }
              rows={4}
              className="resize-y"
            />
          ) : (
            <p className="text-sm text-muted-foreground whitespace-pre-wrap">
              {tech.ops_notes || "No ops notes recorded."}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---- Skills Tab ----

function SkillsTab({ tech }: { tech: Technician }) {
  const { addSkill, removeSkill } = useTechnicianStore();
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [newSkillName, setNewSkillName] = useState("");
  const [newSkillLevel, setNewSkillLevel] = useState("Beginner");

  const existingSkillNames = tech.skills.map((s) => s.skill_name);
  const availableSkills = SKILLS_TAXONOMY.filter(
    (s) => !existingSkillNames.includes(s)
  );

  const handleAddSkill = () => {
    if (!newSkillName) return;
    addSkill(tech.id, {
      skill_name: newSkillName,
      proficiency_level: newSkillLevel as Skill["proficiency_level"],
      training_hours_accumulated: 0,
    });
    setNewSkillName("");
    setNewSkillLevel("Beginner");
    setShowAddDialog(false);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold">Skills Matrix</h3>
          <p className="text-sm text-muted-foreground">
            {tech.skills.length} skills tracked
          </p>
        </div>
        <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="mr-1 h-4 w-4" /> Add Skill
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add Skill</DialogTitle>
              <DialogDescription>
                Add a new skill to {tech.name}'s profile
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div>
                <Label>Skill Name</Label>
                <Select value={newSkillName} onValueChange={setNewSkillName}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a skill" />
                  </SelectTrigger>
                  <SelectContent>
                    {availableSkills.map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Proficiency Level</Label>
                <Select value={newSkillLevel} onValueChange={setNewSkillLevel}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Beginner">Beginner</SelectItem>
                    <SelectItem value="Intermediate">Intermediate</SelectItem>
                    <SelectItem value="Advanced">Advanced</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowAddDialog(false)}>
                Cancel
              </Button>
              <Button onClick={handleAddSkill} disabled={!newSkillName}>
                Add Skill
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Skill</TableHead>
              <TableHead>Proficiency</TableHead>
              <TableHead>Training Hours</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead className="w-[60px]" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tech.skills.map((skill) => {
              const nextThreshold =
                skill.proficiency_level === "Beginner"
                  ? skill.target_hours_intermediate
                  : skill.proficiency_level === "Intermediate"
                  ? skill.target_hours_advanced
                  : null;
              const progress = nextThreshold
                ? Math.min(
                    100,
                    Math.round(
                      (skill.training_hours_accumulated / nextThreshold) * 100
                    )
                  )
                : 100;

              return (
                <TableRow key={skill.skill_name}>
                  <TableCell className="font-medium">{skill.skill_name}</TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        skill.proficiency_level === "Advanced"
                          ? "success"
                          : skill.proficiency_level === "Intermediate"
                          ? "info"
                          : "secondary"
                      }
                    >
                      {skill.proficiency_level}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <span className="text-sm">
                      {skill.training_hours_accumulated}h
                      {nextThreshold && (
                        <span className="text-muted-foreground">
                          {" "}
                          / {nextThreshold}h
                        </span>
                      )}
                    </span>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="w-24 h-2 rounded-full bg-muted overflow-hidden">
                        <div
                          className={cn(
                            "h-full rounded-full transition-all",
                            progress >= 100
                              ? "bg-emerald-500"
                              : progress >= 75
                              ? "bg-blue-500"
                              : progress >= 50
                              ? "bg-amber-500"
                              : "bg-muted-foreground"
                          )}
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground w-10">
                        {progress}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 text-muted-foreground hover:text-destructive"
                      onClick={() => removeSkill(tech.id, skill.skill_name)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
            {tech.skills.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                  No skills recorded yet
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// ---- Certifications Tab ----

function CertificationsTab({ tech }: { tech: Technician }) {
  const { addCertification, removeCertification } = useTechnicianStore();
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [newCert, setNewCert] = useState({
    cert_name: "",
    issue_date: "",
    expiry_date: "",
    status: "Active" as Certification["status"],
  });

  const handleAdd = () => {
    if (!newCert.cert_name) return;
    addCertification(tech.id, newCert);
    setNewCert({ cert_name: "", issue_date: "", expiry_date: "", status: "Active" });
    setShowAddDialog(false);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold">Certifications</h3>
          <p className="text-sm text-muted-foreground">
            {tech.certifications.length} certifications tracked
          </p>
        </div>
        <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="mr-1 h-4 w-4" /> Add Certification
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add Certification</DialogTitle>
              <DialogDescription>
                Add a new certification to {tech.name}'s profile
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div>
                <Label>Certification</Label>
                <Select
                  value={newCert.cert_name}
                  onValueChange={(v) => setNewCert((c) => ({ ...c, cert_name: v }))}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select certification" />
                  </SelectTrigger>
                  <SelectContent>
                    {CERT_NAMES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Issue Date</Label>
                  <Input
                    type="date"
                    value={newCert.issue_date}
                    onChange={(e) =>
                      setNewCert((c) => ({ ...c, issue_date: e.target.value }))
                    }
                  />
                </div>
                <div>
                  <Label>Expiry Date</Label>
                  <Input
                    type="date"
                    value={newCert.expiry_date}
                    onChange={(e) =>
                      setNewCert((c) => ({ ...c, expiry_date: e.target.value }))
                    }
                  />
                </div>
              </div>
              <div>
                <Label>Status</Label>
                <Select
                  value={newCert.status}
                  onValueChange={(v) =>
                    setNewCert((c) => ({ ...c, status: v as Certification["status"] }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Active">Active</SelectItem>
                    <SelectItem value="Expiring Soon">Expiring Soon</SelectItem>
                    <SelectItem value="Expired">Expired</SelectItem>
                    <SelectItem value="Pending">Pending</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowAddDialog(false)}>
                Cancel
              </Button>
              <Button onClick={handleAdd} disabled={!newCert.cert_name}>
                Add Certification
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {tech.certifications.map((cert) => (
          <Card key={cert.cert_name} className="relative">
            <CardContent className="pt-4 pb-3">
              <div className="flex items-start justify-between">
                <div className="flex items-start gap-3">
                  <div
                    className={cn(
                      "mt-0.5 rounded-full p-1.5",
                      cert.status === "Active"
                        ? "bg-emerald-500/10 text-emerald-500"
                        : cert.status === "Expiring Soon"
                        ? "bg-amber-500/10 text-amber-500"
                        : cert.status === "Expired"
                        ? "bg-red-500/10 text-red-500"
                        : "bg-blue-500/10 text-blue-500"
                    )}
                  >
                    {cert.status === "Active" ? (
                      <CheckCircle2 className="h-4 w-4" />
                    ) : cert.status === "Expiring Soon" ? (
                      <AlertTriangle className="h-4 w-4" />
                    ) : cert.status === "Expired" ? (
                      <XCircle className="h-4 w-4" />
                    ) : (
                      <Clock className="h-4 w-4" />
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-medium">{cert.cert_name}</p>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                      {cert.issue_date && (
                        <span>Issued: {formatDate(cert.issue_date)}</span>
                      )}
                      {cert.expiry_date && (
                        <span>Expires: {formatDate(cert.expiry_date)}</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Badge
                    variant={
                      cert.status === "Active"
                        ? "success"
                        : cert.status === "Expiring Soon"
                        ? "warning"
                        : cert.status === "Expired"
                        ? "destructive"
                        : "secondary"
                    }
                    className="text-[10px]"
                  >
                    {cert.status}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    onClick={() => removeCertification(tech.id, cert.cert_name)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
        {tech.certifications.length === 0 && (
          <div className="col-span-2 text-center py-8 text-muted-foreground">
            <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p>No certifications recorded</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Documents Tab ----

function DocumentsTab({ tech }: { tech: Technician }) {
  const { addDocument, updateDocument } = useTechnicianStore();
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [newDoc, setNewDoc] = useState({
    doc_type: "",
    verification_status: "Not Submitted" as TechDocument["verification_status"],
  });

  const existingDocTypes = tech.documents.map((d) => d.doc_type);
  const availableDocTypes = DOC_TYPES.filter(
    (d) => !existingDocTypes.includes(d)
  );

  const handleAdd = () => {
    if (!newDoc.doc_type) return;
    addDocument(tech.id, newDoc);
    setNewDoc({ doc_type: "", verification_status: "Not Submitted" });
    setShowAddDialog(false);
  };

  const statusIcon = (status: TechDocument["verification_status"]) => {
    switch (status) {
      case "Verified":
        return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
      case "Pending Review":
        return <Clock className="h-4 w-4 text-blue-400" />;
      case "Expired":
        return <AlertTriangle className="h-4 w-4 text-red-500" />;
      default:
        return <XCircle className="h-4 w-4 text-muted-foreground" />;
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold">Required Documents</h3>
          <p className="text-sm text-muted-foreground">
            {tech.documents.filter((d) => d.verification_status === "Verified").length}/
            {tech.documents.length} verified
          </p>
        </div>
        <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
          <DialogTrigger asChild>
            <Button size="sm" disabled={availableDocTypes.length === 0}>
              <Plus className="mr-1 h-4 w-4" /> Add Document
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add Document</DialogTitle>
              <DialogDescription>
                Track a new required document for {tech.name}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div>
                <Label>Document Type</Label>
                <Select
                  value={newDoc.doc_type}
                  onValueChange={(v) => setNewDoc((d) => ({ ...d, doc_type: v }))}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select document type" />
                  </SelectTrigger>
                  <SelectContent>
                    {availableDocTypes.map((d) => (
                      <SelectItem key={d} value={d}>
                        {d}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Initial Status</Label>
                <Select
                  value={newDoc.verification_status}
                  onValueChange={(v) =>
                    setNewDoc((d) => ({
                      ...d,
                      verification_status: v as TechDocument["verification_status"],
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Not Submitted">Not Submitted</SelectItem>
                    <SelectItem value="Pending Review">Pending Review</SelectItem>
                    <SelectItem value="Verified">Verified</SelectItem>
                    <SelectItem value="Expired">Expired</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowAddDialog(false)}>
                Cancel
              </Button>
              <Button onClick={handleAdd} disabled={!newDoc.doc_type}>
                Add Document
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-[200px]">Change Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tech.documents.map((doc) => (
              <TableRow key={doc.doc_type}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">{doc.doc_type}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    {statusIcon(doc.verification_status)}
                    <span className="text-sm">{doc.verification_status}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <Select
                    value={doc.verification_status}
                    onValueChange={(v) =>
                      updateDocument(tech.id, doc.doc_type, {
                        verification_status: v as TechDocument["verification_status"],
                      })
                    }
                  >
                    <SelectTrigger className="h-8">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="Not Submitted">Not Submitted</SelectItem>
                      <SelectItem value="Pending Review">Pending Review</SelectItem>
                      <SelectItem value="Verified">Verified</SelectItem>
                      <SelectItem value="Expired">Expired</SelectItem>
                    </SelectContent>
                  </Select>
                </TableCell>
              </TableRow>
            ))}
            {tech.documents.length === 0 && (
              <TableRow>
                <TableCell colSpan={3} className="text-center py-8 text-muted-foreground">
                  <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  No documents tracked
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// ---- Badges Tab ----

function BadgesTab({ tech }: { tech: Technician }) {
  const { addBadge, removeBadge } = useTechnicianStore();

  return (
    <ManualBadgeManager
      technicianId={tech.id}
      technicianName={tech.name}
      siteBadges={tech.site_badges}
      milestoneBadges={tech.milestone_badges}
      onAddBadge={addBadge}
      onRemoveBadge={removeBadge}
      canManage={true}
    />
  );
}

// ---- Main Profile Page ----

export function TechnicianProfile() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { selectedTechnician: tech, isLoading, fetchTechnician, updateTechnician } =
    useTechnicianStore();
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<Partial<Technician>>({});
  const [activeTab, setActiveTab] = useState("overview");

  useEffect(() => {
    if (id) fetchTechnician(id);
  }, [id, fetchTechnician]);

  useEffect(() => {
    if (tech) {
      setEditForm({
        name: tech.name,
        email: tech.email,
        phone: tech.phone,
        home_base_city: tech.home_base_city,
        career_stage: tech.career_stage,
        deployability_status: tech.deployability_status,
        available_from: tech.available_from,
        approved_regions: [...tech.approved_regions],
        ops_notes: tech.ops_notes || "",
      });
    }
  }, [tech]);

  const handleSave = async () => {
    if (!tech) return;
    await updateTechnician(tech.id, editForm);
    setIsEditing(false);
  };

  const handleCancel = () => {
    if (tech) {
      setEditForm({
        name: tech.name,
        email: tech.email,
        phone: tech.phone,
        home_base_city: tech.home_base_city,
        career_stage: tech.career_stage,
        deployability_status: tech.deployability_status,
        available_from: tech.available_from,
        approved_regions: [...tech.approved_regions],
        ops_notes: tech.ops_notes || "",
      });
    }
    setIsEditing(false);
  };

  const handleToggleLock = async () => {
    if (!tech) return;
    await updateTechnician(tech.id, {
      deployability_locked: !tech.deployability_locked,
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">Loading profile...</p>
        </div>
      </div>
    );
  }

  if (!tech) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <h3 className="text-lg font-semibold">Technician Not Found</h3>
        <p className="text-sm text-muted-foreground mt-1">
          The technician you're looking for doesn't exist.
        </p>
        <Button
          variant="outline"
          className="mt-4"
          onClick={() => navigate("/ops/technicians")}
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Directory
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2"
          onClick={() => navigate("/ops/technicians")}
        >
          <ArrowLeft className="mr-1 h-3.5 w-3.5" />
          Technicians
        </Button>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="text-foreground">{tech.name}</span>
      </div>

      {/* Profile Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div className="flex items-center gap-4">
          <Avatar className="h-16 w-16">
            <AvatarFallback className="text-xl bg-primary/10 text-primary">
              {getInitials(tech.name)}
            </AvatarFallback>
          </Avatar>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{tech.name}</h1>
            <div className="flex items-center gap-3 mt-1">
              <div className="flex items-center gap-1 text-sm text-muted-foreground">
                <MapPin className="h-3.5 w-3.5" />
                {tech.home_base_city}
              </div>
              <Separator orientation="vertical" className="h-4" />
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
                  getDeployabilityColor(
                    tech.deployability_status as DeployabilityStatus
                  )
                )}
              >
                <StatusDot status={tech.deployability_status} />
                {tech.deployability_status}
                {tech.deployability_locked && <Lock className="h-3 w-3" />}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleToggleLock}
            className={cn(
              tech.deployability_locked &&
                "border-amber-500/50 text-amber-500 hover:text-amber-400"
            )}
          >
            {tech.deployability_locked ? (
              <>
                <Lock className="mr-1 h-4 w-4" /> Locked
              </>
            ) : (
              <>
                <Unlock className="mr-1 h-4 w-4" /> Lock Status
              </>
            )}
          </Button>

          {isEditing ? (
            <>
              <Button variant="outline" size="sm" onClick={handleCancel}>
                <X className="mr-1 h-4 w-4" /> Cancel
              </Button>
              <Button size="sm" onClick={handleSave}>
                <Save className="mr-1 h-4 w-4" /> Save Changes
              </Button>
            </>
          ) : (
            <Button size="sm" onClick={() => setIsEditing(true)}>
              <Edit2 className="mr-1 h-4 w-4" /> Edit Profile
            </Button>
          )}
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Skills</p>
            <p className="text-2xl font-bold">{tech.skills.length}</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {tech.skills.filter((s) => s.proficiency_level === "Advanced").length} advanced
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Certifications</p>
            <p className="text-2xl font-bold">{tech.certifications.length}</p>
            <p className="text-xs mt-0.5">
              {tech.certifications.some((c) => c.status === "Expired") ? (
                <span className="text-red-400">Has expired certs</span>
              ) : (
                <span className="text-muted-foreground">All current</span>
              )}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Documents</p>
            <p className="text-2xl font-bold">
              {tech.documents.filter((d) => d.verification_status === "Verified").length}/
              {tech.documents.length}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">verified</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Badges</p>
            <p className="text-2xl font-bold">
              {tech.site_badges.length + tech.milestone_badges.length}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {tech.site_badges.length} site, {tech.milestone_badges.length} milestone
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Deployability Status Panel */}
      <DeployabilityStatusPanel technicianId={tech.id} />

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="w-full justify-start">
          <TabsTrigger value="overview" className="gap-1.5">
            <Mail className="h-3.5 w-3.5" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="skills" className="gap-1.5">
            <Award className="h-3.5 w-3.5" />
            Skills
          </TabsTrigger>
          <TabsTrigger value="certifications" className="gap-1.5">
            <Shield className="h-3.5 w-3.5" />
            Certifications
          </TabsTrigger>
          <TabsTrigger value="documents" className="gap-1.5">
            <FileText className="h-3.5 w-3.5" />
            Documents
          </TabsTrigger>
          <TabsTrigger value="badges" className="gap-1.5">
            <Award className="h-3.5 w-3.5" />
            Badges
          </TabsTrigger>
          <TabsTrigger value="passport" className="gap-1.5">
            <Shield className="h-3.5 w-3.5" />
            Passport
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-6">
          <OverviewTab
            tech={tech}
            isEditing={isEditing}
            editForm={editForm}
            setEditForm={setEditForm}
          />
        </TabsContent>

        <TabsContent value="skills" className="mt-6">
          <SkillsTab tech={tech} />
        </TabsContent>

        <TabsContent value="certifications" className="mt-6">
          <CertificationsTab tech={tech} />
        </TabsContent>

        <TabsContent value="documents" className="mt-6">
          <DocumentsTab tech={tech} />
        </TabsContent>

        <TabsContent value="badges" className="mt-6">
          <BadgesTab tech={tech} />
        </TabsContent>

        <TabsContent value="passport" className="mt-6">
          <CareerPassportPanel
            technicianId={tech.id}
            technicianName={tech.name}
            role="ops"
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
