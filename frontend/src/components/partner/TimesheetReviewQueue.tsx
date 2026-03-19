import { useState, useEffect } from "react";
import { usePartnerStore } from "@/stores/partnerStore";
import type { PartnerTimesheetReview, PartnerSkillBreakdownSummary } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { NativeSelect as Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import {
  CheckCircle2,
  AlertTriangle,
  Clock,
  FileText,
  Flag,
  ThumbsUp,
  ThumbsDown,
  Eye,
  User,
  Calendar,
  Wrench,
  ChevronDown,
  ChevronUp,
  RotateCcw,
  Star,
  BarChart3,
} from "lucide-react";

const FLAG_CATEGORIES: { value: PartnerTimesheetReview["flag_category"]; label: string }[] = [
  { value: "hours_discrepancy", label: "Hours Discrepancy" },
  { value: "unauthorized_overtime", label: "Unauthorized Overtime" },
  { value: "no_site_access", label: "No Site Access" },
  { value: "quality_concern", label: "Quality Concern" },
  { value: "other", label: "Other" },
];

function getCategoryBadge(category?: string) {
  const map: Record<string, { label: string; className: string }> = {
    hours_discrepancy: { label: "Hours Discrepancy", className: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
    unauthorized_overtime: { label: "Unauth. Overtime", className: "bg-red-500/20 text-red-400 border-red-500/30" },
    no_site_access: { label: "No Site Access", className: "bg-orange-500/20 text-orange-400 border-orange-500/30" },
    quality_concern: { label: "Quality Issue", className: "bg-purple-500/20 text-purple-400 border-purple-500/30" },
    other: { label: "Other", className: "bg-muted text-muted-foreground" },
  };
  if (!category) return null;
  const cfg = map[category] || map.other;
  return (
    <Badge variant="outline" className={`text-[10px] ${cfg.className}`}>
      {cfg.label}
    </Badge>
  );
}

function getSkillReviewStatusBadge(status?: string | null) {
  if (!status) return null;
  const map: Record<string, { label: string; className: string }> = {
    Approved: { label: "Skills Approved", className: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" },
    Rejected: { label: "Skills Rejected", className: "bg-red-500/20 text-red-400 border-red-500/30" },
    "Revision Requested": { label: "Revision Requested", className: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
    Pending: { label: "Skills Pending", className: "bg-blue-500/20 text-blue-400 border-blue-500/30" },
  };
  const cfg = map[status] || { label: status, className: "bg-muted text-muted-foreground" };
  return (
    <Badge variant="outline" className={`text-[10px] ${cfg.className}`}>
      <Wrench className="h-3 w-3 mr-1" />
      {cfg.label}
    </Badge>
  );
}

function getProficiencyColor(rating: string): string {
  switch (rating) {
    case "Expert": return "text-violet-400";
    case "Exceeds Expectations": return "text-emerald-400";
    case "Meets Expectations": return "text-blue-400";
    case "Below Expectations": return "text-red-400";
    default: return "text-muted-foreground";
  }
}

function getProficiencyBg(rating: string): string {
  switch (rating) {
    case "Expert": return "bg-violet-500/10 border-violet-500/20";
    case "Exceeds Expectations": return "bg-emerald-500/10 border-emerald-500/20";
    case "Meets Expectations": return "bg-blue-500/10 border-blue-500/20";
    case "Below Expectations": return "bg-red-500/10 border-red-500/20";
    default: return "bg-muted/50";
  }
}

/** Inline skill breakdown panel shown within a timesheet review card */
function SkillBreakdownPanel({
  breakdown,
  reviewId,
  isPending,
  isProcessing,
}: {
  breakdown: PartnerSkillBreakdownSummary;
  reviewId: string;
  isPending: boolean;
  isProcessing: boolean;
}) {
  const { reviewSkillBreakdown } = usePartnerStore();
  const [showReviewDialog, setShowReviewDialog] = useState(false);
  const [reviewAction, setReviewAction] = useState<"approve" | "reject" | "request_revision">("approve");
  const [reviewNote, setReviewNote] = useState("");

  const hasBeenReviewed = !!breakdown.partner_review_status;
  const totalSkillHours = breakdown.items.reduce((sum, item) => sum + (item.hours_applied || 0), 0);

  const handleSubmitReview = async () => {
    await reviewSkillBreakdown(reviewId, reviewAction, reviewNote || undefined);
    setShowReviewDialog(false);
    setReviewNote("");
  };

  const openReviewDialog = (action: "approve" | "reject" | "request_revision") => {
    setReviewAction(action);
    setReviewNote("");
    setShowReviewDialog(true);
  };

  const actionTitles: Record<string, string> = {
    approve: "Approve Skill Breakdown",
    reject: "Reject Skill Breakdown",
    request_revision: "Request Revision",
  };
  const actionDescriptions: Record<string, string> = {
    approve: "Confirm the skill assessment is accurate based on your on-site observations.",
    reject: "Indicate the skill assessment does not match observed performance.",
    request_revision: "Ask ops to revise the skill breakdown based on your feedback.",
  };

  return (
    <>
      <div className="rounded-lg border bg-card/50 overflow-hidden">
        {/* Breakdown header */}
        <div className="flex items-center justify-between px-3 py-2 bg-muted/30 border-b">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-primary" />
            <span className="text-xs font-semibold uppercase tracking-wider">Skill Breakdown</span>
            {breakdown.overall_rating && (
              <Badge variant="outline" className={`text-[10px] ${getProficiencyBg(breakdown.overall_rating)}`}>
                <Star className="h-3 w-3 mr-0.5" />
                {breakdown.overall_rating}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            {getSkillReviewStatusBadge(breakdown.partner_review_status)}
            {totalSkillHours > 0 && (
              <span className="text-[10px] text-muted-foreground">{totalSkillHours}h total</span>
            )}
          </div>
        </div>

        {/* Skill items table */}
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="text-xs h-8 px-3">Skill</TableHead>
                <TableHead className="text-xs h-8 px-3 text-right w-20">Hours</TableHead>
                <TableHead className="text-xs h-8 px-3 w-40">Proficiency</TableHead>
                <TableHead className="text-xs h-8 px-3 hidden md:table-cell">Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {breakdown.items.map((item, idx) => (
                <TableRow key={idx} className="hover:bg-muted/30">
                  <TableCell className="text-sm py-2 px-3 font-medium">{item.skill_name}</TableCell>
                  <TableCell className="text-sm py-2 px-3 text-right tabular-nums">
                    {item.hours_applied != null ? `${item.hours_applied}h` : "—"}
                  </TableCell>
                  <TableCell className="py-2 px-3">
                    <span className={`text-xs font-medium ${getProficiencyColor(item.proficiency_rating)}`}>
                      {item.proficiency_rating}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs py-2 px-3 text-muted-foreground hidden md:table-cell max-w-[200px] truncate">
                    {item.notes || "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        {/* Partner review note if already reviewed */}
        {hasBeenReviewed && breakdown.partner_review_note && (
          <div className="px-3 py-2 border-t bg-muted/20">
            <p className="text-xs text-muted-foreground">
              <span className="font-medium">Your review note:</span> {breakdown.partner_review_note}
            </p>
          </div>
        )}

        {/* Skill review actions — shown when hours are pending or skills haven't been reviewed yet */}
        {!hasBeenReviewed && (
          <div className="flex items-center gap-2 px-3 py-2 border-t bg-muted/10">
            <span className="text-xs text-muted-foreground mr-auto">Review skills:</span>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs bg-emerald-600/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-600/20"
              disabled={isProcessing}
              onClick={() => openReviewDialog("approve")}
            >
              <ThumbsUp className="h-3 w-3 mr-1" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
              disabled={isProcessing}
              onClick={() => openReviewDialog("request_revision")}
            >
              <RotateCcw className="h-3 w-3 mr-1" />
              Revise
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs border-red-500/30 text-red-400 hover:bg-red-500/10"
              disabled={isProcessing}
              onClick={() => openReviewDialog("reject")}
            >
              <ThumbsDown className="h-3 w-3 mr-1" />
              Reject
            </Button>
          </div>
        )}
      </div>

      {/* Skill Review Dialog */}
      <Dialog open={showReviewDialog} onOpenChange={setShowReviewDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{actionTitles[reviewAction]}</DialogTitle>
            <DialogDescription>{actionDescriptions[reviewAction]}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {/* Summary of skills being reviewed */}
            <div className="rounded-lg border bg-muted/30 p-3">
              <p className="text-xs font-semibold text-muted-foreground mb-2">Skills being reviewed:</p>
              <div className="space-y-1">
                {breakdown.items.map((item, idx) => (
                  <div key={idx} className="flex items-center justify-between text-sm">
                    <span>{item.skill_name}</span>
                    <span className={`text-xs ${getProficiencyColor(item.proficiency_rating)}`}>
                      {item.proficiency_rating} {item.hours_applied != null && `(${item.hours_applied}h)`}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <Label htmlFor="skill-review-note" className="text-sm">
                {reviewAction === "request_revision" ? "What should be revised? *" : "Note (optional)"}
              </Label>
              <Textarea
                id="skill-review-note"
                placeholder={
                  reviewAction === "approve"
                    ? "Optional note about the skill assessment..."
                    : reviewAction === "reject"
                    ? "Explain why the skill assessment is inaccurate..."
                    : "Describe what needs to be revised..."
                }
                value={reviewNote}
                onChange={(e) => setReviewNote(e.target.value)}
                className="mt-1"
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowReviewDialog(false)}>Cancel</Button>
            <Button
              className={
                reviewAction === "approve"
                  ? "bg-emerald-600 hover:bg-emerald-700"
                  : reviewAction === "reject"
                  ? "bg-red-600 hover:bg-red-700"
                  : "bg-amber-600 hover:bg-amber-700"
              }
              onClick={handleSubmitReview}
              disabled={isProcessing || (reviewAction === "request_revision" && !reviewNote.trim())}
            >
              {reviewAction === "approve" && <ThumbsUp className="h-4 w-4 mr-1.5" />}
              {reviewAction === "reject" && <ThumbsDown className="h-4 w-4 mr-1.5" />}
              {reviewAction === "request_revision" && <RotateCcw className="h-4 w-4 mr-1.5" />}
              {isProcessing
                ? "Processing..."
                : reviewAction === "approve"
                ? "Approve Skills"
                : reviewAction === "reject"
                ? "Reject Skills"
                : "Request Revision"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function TimesheetReviewCard({ review }: { review: PartnerTimesheetReview }) {
  const { approveTimesheet, flagTimesheet, reviewingTimesheetId } = usePartnerStore();
  const [showApproveDialog, setShowApproveDialog] = useState(false);
  const [showFlagDialog, setShowFlagDialog] = useState(false);
  const [approveNote, setApproveNote] = useState("");
  const [flagReason, setFlagReason] = useState("");
  const [flagCategory, setFlagCategory] = useState<PartnerTimesheetReview["flag_category"]>("hours_discrepancy");
  const [flagNote, setFlagNote] = useState("");
  const [showSkillBreakdown, setShowSkillBreakdown] = useState(false);

  const isPending = review.status === "pending_review";
  const isProcessing = reviewingTimesheetId === review.id;
  const hasSkillBreakdown = !!review.skill_breakdown && review.skill_breakdown.items.length > 0;

  const handleApprove = async () => {
    await approveTimesheet(review.id, approveNote || undefined);
    setShowApproveDialog(false);
    setApproveNote("");
  };

  const handleFlag = async () => {
    if (!flagReason.trim()) return;
    await flagTimesheet(review.id, flagReason, flagCategory, flagNote || undefined);
    setShowFlagDialog(false);
    setFlagReason("");
    setFlagNote("");
  };

  const statusConfig = {
    pending_review: { label: "Pending Review", className: "bg-amber-500 hover:bg-amber-600", icon: Clock },
    approved: { label: "Approved", className: "bg-emerald-600 hover:bg-emerald-700", icon: CheckCircle2 },
    flagged: { label: "Flagged", className: "bg-red-500 hover:bg-red-600", icon: AlertTriangle },
  };

  const cfg = statusConfig[review.status] || statusConfig.pending_review;
  const StatusIcon = cfg.icon;

  return (
    <>
      <Card className={`border transition-all ${isPending ? "border-amber-500/30 shadow-sm shadow-amber-500/5" : review.status === "flagged" ? "border-red-500/30" : ""}`}>
        <CardContent className="p-4 md:p-5">
          <div className="flex flex-col gap-3">
            {/* Header */}
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-semibold text-sm">{review.technician_name}</h3>
                  <Badge className={`text-[10px] ${cfg.className}`}>
                    <StatusIcon className="h-3 w-3 mr-1" />
                    {cfg.label}
                  </Badge>
                  {review.status === "flagged" && getCategoryBadge(review.flag_category)}
                  {hasSkillBreakdown && getSkillReviewStatusBadge(review.skill_breakdown?.partner_review_status)}
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {review.role_name} &middot; {review.project_name}
                </p>
              </div>
              <div className="text-right shrink-0">
                <p className="text-lg font-bold">{review.hours}h</p>
                <p className="text-[10px] text-muted-foreground uppercase">reported</p>
              </div>
            </div>

            {/* Week details */}
            <div className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2">
              <Calendar className="h-4 w-4 text-primary" />
              <span className="text-sm">
                Week of{" "}
                <span className="font-medium">
                  {new Date(review.week_start).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                </span>
                {" - "}
                <span className="font-medium">
                  {new Date(review.week_end).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                </span>
              </span>
            </div>

            {/* Skill breakdown toggle */}
            {hasSkillBreakdown && (
              <button
                type="button"
                className="flex items-center gap-2 text-xs text-primary hover:text-primary/80 transition-colors w-fit"
                onClick={() => setShowSkillBreakdown(!showSkillBreakdown)}
              >
                <Wrench className="h-3.5 w-3.5" />
                <span className="font-medium">
                  {showSkillBreakdown ? "Hide" : "View"} Skill Breakdown ({review.skill_breakdown!.items.length} skills)
                </span>
                {showSkillBreakdown ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
              </button>
            )}

            {/* Skill breakdown panel (collapsible) */}
            {hasSkillBreakdown && showSkillBreakdown && (
              <SkillBreakdownPanel
                breakdown={review.skill_breakdown!}
                reviewId={review.id}
                isPending={isPending}
                isProcessing={isProcessing}
              />
            )}

            {/* Flag details for already-flagged */}
            {review.status === "flagged" && review.flag_reason && (
              <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2">
                <div className="flex items-start gap-2 text-sm">
                  <Flag className="h-4 w-4 text-red-400 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-red-400 font-medium">{review.flag_reason}</p>
                    {review.partner_note && (
                      <p className="text-xs text-muted-foreground mt-1">{review.partner_note}</p>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Approved note */}
            {review.status === "approved" && review.partner_note && (
              <div className="flex items-start gap-2 text-sm text-emerald-600 bg-emerald-500/5 rounded-lg px-3 py-2">
                <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
                <span>{review.partner_note}</span>
              </div>
            )}

            {/* Actions for pending */}
            {isPending && (
              <div className="flex items-center gap-2 pt-1">
                <Button
                  size="sm"
                  className="flex-1 sm:flex-initial bg-emerald-600 hover:bg-emerald-700"
                  disabled={isProcessing}
                  onClick={() => setShowApproveDialog(true)}
                >
                  <ThumbsUp className="h-4 w-4 mr-1.5" />
                  {isProcessing ? "Processing..." : "Approve Hours"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="flex-1 sm:flex-initial border-red-500/30 text-red-400 hover:bg-red-500/10"
                  disabled={isProcessing}
                  onClick={() => setShowFlagDialog(true)}
                >
                  <Flag className="h-4 w-4 mr-1.5" />
                  Flag Issue
                </Button>
                {hasSkillBreakdown && !showSkillBreakdown && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="hidden sm:flex border-primary/30 text-primary hover:bg-primary/10"
                    disabled={isProcessing}
                    onClick={() => setShowSkillBreakdown(true)}
                  >
                    <Wrench className="h-4 w-4 mr-1.5" />
                    Review Skills
                  </Button>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Approve Dialog */}
      <Dialog open={showApproveDialog} onOpenChange={setShowApproveDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Approve Timesheet</DialogTitle>
            <DialogDescription>
              Approve {review.hours} hours for {review.technician_name} ({review.role_name}) for
              the week of {new Date(review.week_start).toLocaleDateString()}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            {/* Show skill breakdown summary in approve dialog if available */}
            {hasSkillBreakdown && (
              <div className="rounded-lg border bg-muted/30 p-3">
                <p className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1.5">
                  <BarChart3 className="h-3.5 w-3.5" />
                  Skill Breakdown Summary
                </p>
                <div className="space-y-1">
                  {review.skill_breakdown!.items.map((item, idx) => (
                    <div key={idx} className="flex items-center justify-between text-sm">
                      <span>{item.skill_name}</span>
                      <div className="flex items-center gap-2">
                        {item.hours_applied != null && (
                          <span className="text-xs text-muted-foreground">{item.hours_applied}h</span>
                        )}
                        <span className={`text-xs font-medium ${getProficiencyColor(item.proficiency_rating)}`}>
                          {item.proficiency_rating}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
                {!review.skill_breakdown?.partner_review_status && (
                  <p className="text-[10px] text-amber-400 mt-2">
                    Skill breakdown has not been reviewed yet. You can review it separately.
                  </p>
                )}
              </div>
            )}
            <div>
              <Label htmlFor="approve-note" className="text-sm">Note (optional)</Label>
              <Textarea
                id="approve-note"
                placeholder="Add a note about this approval..."
                value={approveNote}
                onChange={(e) => setApproveNote(e.target.value)}
                className="mt-1"
                rows={2}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowApproveDialog(false)}>Cancel</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700" onClick={handleApprove} disabled={isProcessing}>
              <ThumbsUp className="h-4 w-4 mr-1.5" />
              {isProcessing ? "Approving..." : "Approve Hours"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Flag Dialog */}
      <Dialog open={showFlagDialog} onOpenChange={setShowFlagDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Flag Timesheet Issue</DialogTitle>
            <DialogDescription>
              Report an issue with {review.technician_name}'s timesheet for the week of{" "}
              {new Date(review.week_start).toLocaleDateString()}. This will be sent to ops for review.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <Label className="text-sm">Issue Category *</Label>
              <Select
                value={flagCategory}
                onChange={(e) => setFlagCategory(e.target.value as PartnerTimesheetReview["flag_category"])}
                className="mt-1"
              >
                {FLAG_CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="flag-reason" className="text-sm">Description of Issue *</Label>
              <Textarea
                id="flag-reason"
                placeholder="Describe what's wrong with this timesheet..."
                value={flagReason}
                onChange={(e) => setFlagReason(e.target.value)}
                className="mt-1"
                rows={3}
              />
            </div>
            <div>
              <Label htmlFor="flag-note" className="text-sm">Additional Notes (optional)</Label>
              <Textarea
                id="flag-note"
                placeholder="Any supporting context or suggested resolution..."
                value={flagNote}
                onChange={(e) => setFlagNote(e.target.value)}
                className="mt-1"
                rows={2}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowFlagDialog(false)}>Cancel</Button>
            <Button
              variant="destructive"
              onClick={handleFlag}
              disabled={!flagReason.trim() || isProcessing}
            >
              <Flag className="h-4 w-4 mr-1.5" />
              {isProcessing ? "Flagging..." : "Submit Flag"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function TimesheetReviewQueue() {
  const { timesheetReviews, fetchTimesheetReviews, isLoading } = usePartnerStore();
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    fetchTimesheetReviews();
  }, [fetchTimesheetReviews]);

  const pending = timesheetReviews.filter((r) => r.status === "pending_review");
  const approved = timesheetReviews.filter((r) => r.status === "approved");
  const flagged = timesheetReviews.filter((r) => r.status === "flagged");

  const filtered =
    filter === "all"
      ? timesheetReviews
      : filter === "pending_review"
      ? pending
      : filter === "approved"
      ? approved
      : flagged;

  if (isLoading && timesheetReviews.length === 0) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Card key={i}>
            <CardContent className="p-5">
              <div className="space-y-3">
                <div className="h-5 w-48 rounded bg-muted animate-pulse" />
                <div className="h-4 w-32 rounded bg-muted animate-pulse" />
                <div className="h-10 w-full rounded bg-muted animate-pulse" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      <div className="grid grid-cols-3 gap-3">
        <Card className={`cursor-pointer transition-colors ${filter === "pending_review" ? "border-amber-500/50" : "hover:border-amber-500/30"}`} onClick={() => setFilter("pending_review")}>
          <CardContent className="p-4 text-center">
            <Clock className="h-5 w-5 text-amber-400 mx-auto mb-1" />
            <p className="text-2xl font-bold">{pending.length}</p>
            <p className="text-xs text-muted-foreground">Pending Review</p>
          </CardContent>
        </Card>
        <Card className={`cursor-pointer transition-colors ${filter === "approved" ? "border-emerald-500/50" : "hover:border-emerald-500/30"}`} onClick={() => setFilter("approved")}>
          <CardContent className="p-4 text-center">
            <CheckCircle2 className="h-5 w-5 text-emerald-400 mx-auto mb-1" />
            <p className="text-2xl font-bold">{approved.length}</p>
            <p className="text-xs text-muted-foreground">Approved</p>
          </CardContent>
        </Card>
        <Card className={`cursor-pointer transition-colors ${filter === "flagged" ? "border-red-500/50" : "hover:border-red-500/30"}`} onClick={() => setFilter("flagged")}>
          <CardContent className="p-4 text-center">
            <Flag className="h-5 w-5 text-red-400 mx-auto mb-1" />
            <p className="text-2xl font-bold">{flagged.length}</p>
            <p className="text-xs text-muted-foreground">Flagged</p>
          </CardContent>
        </Card>
      </div>

      {/* Filter buttons */}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant={filter === "all" ? "default" : "outline"} onClick={() => setFilter("all")}>
          All ({timesheetReviews.length})
        </Button>
        <Button
          size="sm"
          variant={filter === "pending_review" ? "default" : "outline"}
          onClick={() => setFilter("pending_review")}
          className={pending.length > 0 && filter !== "pending_review" ? "border-amber-500/50" : ""}
        >
          Pending ({pending.length})
        </Button>
        <Button size="sm" variant={filter === "approved" ? "default" : "outline"} onClick={() => setFilter("approved")}>
          Approved ({approved.length})
        </Button>
        <Button size="sm" variant={filter === "flagged" ? "default" : "outline"} onClick={() => setFilter("flagged")}>
          Flagged ({flagged.length})
        </Button>
      </div>

      {/* Review cards */}
      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-12">
            <FileText className="h-10 w-10 text-muted-foreground mb-3" />
            <h3 className="font-semibold text-lg">No Timesheets</h3>
            <p className="text-sm text-muted-foreground mt-1">
              {filter === "pending_review"
                ? "No timesheets awaiting your review."
                : filter === "flagged"
                ? "No flagged timesheets."
                : "No timesheets found."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {filtered.map((review) => (
            <TimesheetReviewCard key={review.id} review={review} />
          ))}
        </div>
      )}
    </div>
  );
}
