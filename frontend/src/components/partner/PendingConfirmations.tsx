import { useState } from "react";
import { usePartnerStore, type PartnerConfirmation } from "@/stores/partnerStore";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  CheckCircle2,
  XCircle,
  Calendar,
  User,
  Briefcase,
  Clock,
  AlertTriangle,
} from "lucide-react";

function ConfirmationCard({ confirmation }: { confirmation: PartnerConfirmation }) {
  const { confirmAssignment, declineAssignment, confirmingId } = usePartnerStore();
  const [showDecline, setShowDecline] = useState(false);
  const [proposedDate, setProposedDate] = useState("");
  const [note, setNote] = useState("");
  const [confirmNote, setConfirmNote] = useState("");
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);

  const isPending = confirmation.status === "pending";
  const isProcessing = confirmingId === confirmation.id;

  const handleConfirm = async () => {
    await confirmAssignment(confirmation.id, confirmNote || undefined);
    setShowConfirmDialog(false);
    setConfirmNote("");
  };

  const handleDecline = async () => {
    if (!proposedDate) return;
    await declineAssignment(confirmation.id, proposedDate, note || undefined);
    setShowDecline(false);
    setProposedDate("");
    setNote("");
  };

  const statusBadge = {
    pending: { label: "Pending", variant: "default" as const, className: "bg-amber-500 hover:bg-amber-600" },
    confirmed: { label: "Confirmed", variant: "default" as const, className: "bg-emerald-600 hover:bg-emerald-700" },
    declined: { label: "Declined", variant: "destructive" as const, className: "" },
  };

  const badge = statusBadge[confirmation.status] || statusBadge.pending;

  return (
    <>
      <Card className={`border transition-all ${isPending ? "border-amber-500/30 shadow-sm shadow-amber-500/5" : ""}`}>
        <CardContent className="p-4 md:p-5">
          <div className="flex flex-col gap-4">
            {/* Header row */}
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-semibold text-sm truncate">
                    {confirmation.confirmation_type === "start_date" ? "Start Date" : "End Date"} Confirmation
                  </h3>
                  <Badge variant={badge.variant} className={badge.className}>
                    {badge.label}
                  </Badge>
                </div>
              </div>
              <div className="text-xs text-muted-foreground whitespace-nowrap">
                {new Date(confirmation.requested_at).toLocaleDateString()}
              </div>
            </div>

            {/* Details grid */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
              <div className="flex items-center gap-2 text-muted-foreground">
                <User className="h-3.5 w-3.5 flex-shrink-0" />
                <span className="truncate">{confirmation.technician_name || "—"}</span>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Briefcase className="h-3.5 w-3.5 flex-shrink-0" />
                <span className="truncate">{confirmation.project_name || "—"}</span>
              </div>
              <div className="flex items-center gap-2 text-muted-foreground">
                <Clock className="h-3.5 w-3.5 flex-shrink-0" />
                <span className="truncate">{confirmation.role_name || "—"}</span>
              </div>
            </div>

            {/* Requested date */}
            <div className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2">
              <Calendar className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">
                Requested {confirmation.confirmation_type === "start_date" ? "start" : "end"}: {" "}
                <span className="text-foreground">
                  {new Date(confirmation.requested_date).toLocaleDateString("en-US", {
                    weekday: "short",
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </span>
              </span>
            </div>

            {/* Response info for non-pending */}
            {confirmation.status === "confirmed" && confirmation.response_note && (
              <div className="flex items-start gap-2 text-sm text-emerald-600 bg-emerald-500/5 rounded-lg px-3 py-2">
                <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <span>{confirmation.response_note}</span>
              </div>
            )}

            {confirmation.status === "declined" && (
              <div className="flex flex-col gap-1 text-sm text-destructive bg-destructive/5 rounded-lg px-3 py-2">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 flex-shrink-0" />
                  <span>Declined — Proposed: {confirmation.proposed_date}</span>
                </div>
                {confirmation.response_note && (
                  <p className="text-xs text-muted-foreground ml-6">{confirmation.response_note}</p>
                )}
              </div>
            )}

            {/* Action buttons */}
            {isPending && (
              <div className="flex items-center gap-2 pt-1">
                <Button
                  size="sm"
                  className="flex-1 sm:flex-initial bg-emerald-600 hover:bg-emerald-700"
                  disabled={isProcessing}
                  onClick={() => setShowConfirmDialog(true)}
                >
                  <CheckCircle2 className="h-4 w-4 mr-1.5" />
                  {isProcessing ? "Processing..." : "Confirm"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="flex-1 sm:flex-initial border-destructive/30 text-destructive hover:bg-destructive/10"
                  disabled={isProcessing}
                  onClick={() => setShowDecline(true)}
                >
                  <XCircle className="h-4 w-4 mr-1.5" />
                  Decline
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Confirm Dialog */}
      <Dialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Assignment Date</DialogTitle>
            <DialogDescription>
              Confirm the {confirmation.confirmation_type === "start_date" ? "start" : "end"} date
              of {new Date(confirmation.requested_date).toLocaleDateString()} for{" "}
              {confirmation.technician_name} on {confirmation.project_name}?
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <Label htmlFor="confirm-note" className="text-sm">
                Note (optional)
              </Label>
              <Textarea
                id="confirm-note"
                placeholder="Add a note..."
                value={confirmNote}
                onChange={(e) => setConfirmNote(e.target.value)}
                className="mt-1"
                rows={2}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowConfirmDialog(false)}>
              Cancel
            </Button>
            <Button
              className="bg-emerald-600 hover:bg-emerald-700"
              onClick={handleConfirm}
              disabled={isProcessing}
            >
              <CheckCircle2 className="h-4 w-4 mr-1.5" />
              {isProcessing ? "Confirming..." : "Confirm Date"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Decline Dialog */}
      <Dialog open={showDecline} onOpenChange={setShowDecline}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Decline & Propose Alternative</DialogTitle>
            <DialogDescription>
              Decline the requested date and propose an alternative for{" "}
              {confirmation.technician_name}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <Label htmlFor="proposed-date" className="text-sm">
                Proposed Alternative Date *
              </Label>
              <Input
                id="proposed-date"
                type="date"
                value={proposedDate}
                onChange={(e) => setProposedDate(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="decline-note" className="text-sm">
                Reason for declining
              </Label>
              <Textarea
                id="decline-note"
                placeholder="Explain why this date doesn't work..."
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="mt-1"
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowDecline(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDecline}
              disabled={!proposedDate || isProcessing}
            >
              <XCircle className="h-4 w-4 mr-1.5" />
              {isProcessing ? "Declining..." : "Decline & Propose"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function PendingConfirmations() {
  const { confirmations, isLoading } = usePartnerStore();

  const pending = confirmations.filter((c) => c.status === "pending");
  const resolved = confirmations.filter((c) => c.status !== "pending");

  if (isLoading) {
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
      {/* Pending section */}
      {pending.length > 0 ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
              Awaiting Your Response
            </h3>
            <Badge variant="destructive" className="text-[10px]">
              {pending.length}
            </Badge>
          </div>
          {pending.map((c) => (
            <ConfirmationCard key={c.id} confirmation={c} />
          ))}
        </div>
      ) : (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-12">
            <CheckCircle2 className="h-10 w-10 text-emerald-500 mb-3" />
            <h3 className="font-semibold text-lg">All Caught Up</h3>
            <p className="text-sm text-muted-foreground mt-1">
              No pending confirmations at this time.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Recently resolved */}
      {resolved.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Recently Resolved
          </h3>
          {resolved.slice(0, 5).map((c) => (
            <ConfirmationCard key={c.id} confirmation={c} />
          ))}
        </div>
      )}
    </div>
  );
}
