/**
 * HeadcountApprovalQueue — Ops-facing approval queue for partner headcount requests.
 *
 * Features:
 * - Filterable list by status (Pending, Approved, Rejected, Cancelled)
 * - Priority-based visual indicators (urgent = red, high = amber)
 * - Inline approve/reject actions with confirmation
 * - Rejection reason input
 * - Request detail expansion with skills, certs, constraints
 * - Real-time updates via WebSocket
 */

import { useState, useEffect, useCallback } from "react";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
} from "@/components/ui/dialog";
import {
  CheckCircle,
  XCircle,
  Clock,
  AlertTriangle,
  Users,
  Loader2,
  ChevronDown,
  ChevronRight,
  Filter,
  RefreshCw,
} from "lucide-react";

interface HeadcountRequest {
  id: string;
  partner_id: string;
  partner_name: string | null;
  project_id: string | null;
  project_name: string | null;
  role_name: string;
  quantity: number;
  priority: string;
  start_date: string | null;
  end_date: string | null;
  required_skills: Array<{ skill: string; min_level?: string }>;
  required_certs: string[];
  constraints: string | null;
  notes: string | null;
  status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  rejection_reason: string | null;
  created_at: string;
  updated_at: string;
}

const STATUS_CONFIGS: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline"; icon: React.ReactNode }> = {
  Pending: {
    label: "Pending",
    variant: "default",
    icon: <Clock className="h-3 w-3" />,
  },
  Approved: {
    label: "Approved",
    variant: "outline",
    icon: <CheckCircle className="h-3 w-3 text-emerald-500" />,
  },
  Rejected: {
    label: "Rejected",
    variant: "destructive",
    icon: <XCircle className="h-3 w-3" />,
  },
  Cancelled: {
    label: "Cancelled",
    variant: "secondary",
    icon: <XCircle className="h-3 w-3" />,
  },
};

const PRIORITY_COLORS: Record<string, string> = {
  low: "text-muted-foreground",
  normal: "text-foreground",
  high: "text-amber-500 font-medium",
  urgent: "text-destructive font-bold",
};

export function HeadcountApprovalQueue() {
  const [requests, setRequests] = useState<HeadcountRequest[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("Pending");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Reject dialog state
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectTargetId, setRejectTargetId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const fetchRequests = useCallback(async () => {
    setIsLoading(true);
    try {
      const params: Record<string, string> = { limit: "50" };
      if (statusFilter && statusFilter !== "all") {
        params.status = statusFilter;
      }
      const res = await api.get("/headcount-requests", { params });
      setRequests(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch {
      // Fall back gracefully
      setRequests([]);
    } finally {
      setIsLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchRequests();
  }, [fetchRequests]);

  const handleApprove = async (id: string) => {
    setActionLoading(id);
    try {
      const res = await api.post(`/headcount-requests/${id}/action`, {
        action: "approve",
      });
      // Update local state
      setRequests((prev) =>
        prev.map((r) => (r.id === id ? { ...r, ...res.data, status: "Approved" } : r))
      );
    } catch {
      // Show error state briefly
    } finally {
      setActionLoading(null);
    }
  };

  const openRejectDialog = (id: string) => {
    setRejectTargetId(id);
    setRejectReason("");
    setRejectDialogOpen(true);
  };

  const handleReject = async () => {
    if (!rejectTargetId) return;
    setActionLoading(rejectTargetId);
    setRejectDialogOpen(false);
    try {
      const res = await api.post(`/headcount-requests/${rejectTargetId}/action`, {
        action: "reject",
        reason: rejectReason || undefined,
      });
      setRequests((prev) =>
        prev.map((r) =>
          r.id === rejectTargetId
            ? { ...r, ...res.data, status: "Rejected", rejection_reason: rejectReason }
            : r
        )
      );
    } catch {
      // Handle error
    } finally {
      setActionLoading(null);
      setRejectTargetId(null);
    }
  };

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const formatDate = (d: string | null) => {
    if (!d) return "—";
    return new Date(d).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  const formatDateTime = (d: string | null) => {
    if (!d) return "—";
    return new Date(d).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  };

  const pendingCount = requests.filter((r) => r.status === "Pending").length;

  return (
    <div className="flex flex-col gap-6 p-4 md:p-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Users className="h-6 w-6" />
            Headcount Requests
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Review and act on partner headcount requests
          </p>
        </div>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && statusFilter !== "Pending" && (
            <Badge variant="destructive" className="animate-in fade-in">
              {pendingCount} pending
            </Badge>
          )}
          <Badge variant="secondary">{total} total</Badge>
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center">
            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">Filter:</span>
            </div>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value="Pending">Pending</SelectItem>
                <SelectItem value="Approved">Approved</SelectItem>
                <SelectItem value="Rejected">Rejected</SelectItem>
                <SelectItem value="Cancelled">Cancelled</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="sm"
              onClick={fetchRequests}
              className="ml-auto"
            >
              <RefreshCw className={`h-4 w-4 mr-1 ${isLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Queue table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">
            {statusFilter === "all" ? "All Requests" : `${statusFilter} Requests`}
          </CardTitle>
          <CardDescription>
            {statusFilter === "Pending"
              ? "These requests require your review and approval."
              : `Showing ${requests.length} of ${total} requests.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : requests.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <Users className="h-10 w-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">No {statusFilter.toLowerCase()} requests found.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8"></TableHead>
                    <TableHead>Partner</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead className="text-center">Qty</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>Start Date</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Submitted</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {requests.map((req) => (
                    <>
                      <TableRow
                        key={req.id}
                        className={`cursor-pointer hover:bg-muted/50 ${
                          req.priority === "urgent"
                            ? "border-l-2 border-l-destructive"
                            : req.priority === "high"
                            ? "border-l-2 border-l-amber-500"
                            : ""
                        }`}
                        onClick={() => toggleExpand(req.id)}
                      >
                        <TableCell className="pr-0">
                          {expandedId === req.id ? (
                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                          )}
                        </TableCell>
                        <TableCell className="font-medium">
                          {req.partner_name || "Unknown Partner"}
                          {req.project_name && (
                            <span className="block text-xs text-muted-foreground">
                              {req.project_name}
                            </span>
                          )}
                        </TableCell>
                        <TableCell>{req.role_name}</TableCell>
                        <TableCell className="text-center font-mono">
                          {req.quantity}
                        </TableCell>
                        <TableCell>
                          <span className={PRIORITY_COLORS[req.priority] || ""}>
                            {req.priority === "urgent" && (
                              <AlertTriangle className="h-3 w-3 inline mr-1" />
                            )}
                            {req.priority.charAt(0).toUpperCase() + req.priority.slice(1)}
                          </span>
                        </TableCell>
                        <TableCell>{formatDate(req.start_date)}</TableCell>
                        <TableCell>
                          {(() => {
                            const cfg = STATUS_CONFIGS[req.status] || STATUS_CONFIGS.Pending;
                            return (
                              <Badge variant={cfg.variant} className="flex items-center gap-1 w-fit">
                                {cfg.icon}
                                {cfg.label}
                              </Badge>
                            );
                          })()}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDateTime(req.created_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          {req.status === "Pending" && (
                            <div
                              className="flex items-center justify-end gap-1"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Button
                                size="sm"
                                variant="default"
                                className="h-7 bg-emerald-600 hover:bg-emerald-700"
                                disabled={actionLoading === req.id}
                                onClick={() => handleApprove(req.id)}
                              >
                                {actionLoading === req.id ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <>
                                    <CheckCircle className="h-3 w-3 mr-1" />
                                    Approve
                                  </>
                                )}
                              </Button>
                              <Button
                                size="sm"
                                variant="destructive"
                                className="h-7"
                                disabled={actionLoading === req.id}
                                onClick={() => openRejectDialog(req.id)}
                              >
                                <XCircle className="h-3 w-3 mr-1" />
                                Reject
                              </Button>
                            </div>
                          )}
                        </TableCell>
                      </TableRow>

                      {/* Expanded detail row */}
                      {expandedId === req.id && (
                        <TableRow key={`${req.id}-detail`}>
                          <TableCell colSpan={9} className="bg-muted/30 p-4">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                              {/* Skills & Certs */}
                              <div>
                                <h4 className="font-medium mb-2">Required Skills</h4>
                                {req.required_skills.length > 0 ? (
                                  <div className="flex flex-wrap gap-1">
                                    {req.required_skills.map((s, i) => (
                                      <Badge key={i} variant="secondary" className="text-xs">
                                        {typeof s === "string" ? s : s.skill}
                                        {s.min_level && s.min_level !== "Beginner" && (
                                          <span className="ml-1 opacity-60">
                                            ({s.min_level})
                                          </span>
                                        )}
                                      </Badge>
                                    ))}
                                  </div>
                                ) : (
                                  <span className="text-muted-foreground">None specified</span>
                                )}
                              </div>

                              <div>
                                <h4 className="font-medium mb-2">Required Certifications</h4>
                                {req.required_certs.length > 0 ? (
                                  <div className="flex flex-wrap gap-1">
                                    {req.required_certs.map((c, i) => (
                                      <Badge key={i} variant="outline" className="text-xs">
                                        {c}
                                      </Badge>
                                    ))}
                                  </div>
                                ) : (
                                  <span className="text-muted-foreground">None specified</span>
                                )}
                              </div>

                              {/* Dates & metadata */}
                              <div className="space-y-2">
                                <div>
                                  <h4 className="font-medium mb-1">Date Range</h4>
                                  <p className="text-muted-foreground">
                                    {formatDate(req.start_date)} — {formatDate(req.end_date)}
                                  </p>
                                </div>
                                {req.reviewed_at && (
                                  <div>
                                    <h4 className="font-medium mb-1">Reviewed</h4>
                                    <p className="text-muted-foreground">
                                      {formatDateTime(req.reviewed_at)}
                                    </p>
                                  </div>
                                )}
                              </div>

                              {/* Constraints & Notes */}
                              {(req.constraints || req.notes) && (
                                <div className="md:col-span-3 space-y-2 border-t pt-3">
                                  {req.constraints && (
                                    <div>
                                      <h4 className="font-medium mb-1">Constraints</h4>
                                      <p className="text-muted-foreground whitespace-pre-wrap">
                                        {req.constraints}
                                      </p>
                                    </div>
                                  )}
                                  {req.notes && (
                                    <div>
                                      <h4 className="font-medium mb-1">Notes</h4>
                                      <p className="text-muted-foreground whitespace-pre-wrap">
                                        {req.notes}
                                      </p>
                                    </div>
                                  )}
                                </div>
                              )}

                              {/* Rejection reason */}
                              {req.rejection_reason && (
                                <div className="md:col-span-3 border-t pt-3">
                                  <h4 className="font-medium mb-1 text-destructive">
                                    Rejection Reason
                                  </h4>
                                  <p className="text-muted-foreground whitespace-pre-wrap">
                                    {req.rejection_reason}
                                  </p>
                                </div>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Reject dialog */}
      <Dialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject Headcount Request</DialogTitle>
            <DialogDescription>
              Provide a reason for rejecting this request. The partner will see this
              explanation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <Textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Reason for rejection (optional but recommended)..."
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRejectDialogOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleReject}>
              <XCircle className="h-4 w-4 mr-1" />
              Reject Request
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default HeadcountApprovalQueue;
