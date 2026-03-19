import { useEffect, useState, useCallback } from "react";
import { usePartnerStore } from "@/stores/partnerStore";
import { useAuthStore } from "@/stores/auth";
import { useWebSocket, useMultiTopicWebSocket } from "@/hooks/useWebSocket";
import { PartnerStats } from "@/components/partner/PartnerStats";
import { PendingConfirmations } from "@/components/partner/PendingConfirmations";
import { TimesheetReviewQueue } from "@/components/partner/TimesheetReviewQueue";
import { AssignmentsTimeline } from "@/components/partner/AssignmentsTimeline";
import { PartnerProjects } from "@/components/partner/PartnerProjects";
import { PartnerNotifications } from "@/components/partner/PartnerNotifications";
import { HeadcountRequestForm } from "@/components/partner/HeadcountRequestForm";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { SkillBreakdownWSEvent } from "@/types";

export function PartnerPortal() {
  const { role } = useAuthStore();
  const {
    partnerName,
    isLoading,
    error,
    fetchDashboard,
    handleWsEvent,
    confirmations,
    stats,
  } = usePartnerStore();

  const [skillBreakdownNotification, setSkillBreakdownNotification] = useState<string | null>(null);
  const [pendingBreakdownCount, setPendingBreakdownCount] = useState(0);

  // Fetch data on mount
  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  // Handle skill breakdown WebSocket events for the partner dashboard
  const handleSkillBreakdownWsEvent = useCallback((data: any) => {
    const event = data as SkillBreakdownWSEvent;
    if (!event?.event_type?.startsWith("skill_breakdown.")) return;

    switch (event.event_type) {
      case "skill_breakdown.submitted":
        // A technician submitted a new skill breakdown — partner needs to review
        setPendingBreakdownCount((prev) => prev + 1);
        setSkillBreakdownNotification(
          `New skill breakdown submitted for review`
        );
        setTimeout(() => setSkillBreakdownNotification(null), 5000);
        break;
      case "skill_breakdown.approved":
      case "skill_breakdown.rejected":
      case "skill_breakdown.revision_requested":
        // These are partner-initiated actions — update counts
        setPendingBreakdownCount((prev) => Math.max(0, prev - 1));
        break;
    }
  }, []);

  // Subscribe to real-time updates via WebSocket (confirmations + skill breakdowns)
  const { anyConnected: connected } = useMultiTopicWebSocket(
    [
      {
        topic: "confirmations",
        id: "partner-confirmations",
        onMessage: handleWsEvent,
      },
      {
        topic: "skill_breakdowns",
        id: "partner-skill-breakdowns",
        onMessage: handleSkillBreakdownWsEvent,
      },
    ],
    role === "partner",
  );

  if (role !== "partner") {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-muted-foreground">Access Restricted</h2>
          <p className="text-sm text-muted-foreground mt-2">
            Switch to the Partner role to access this portal.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-4 md:p-6 max-w-[1400px] mx-auto">
      {/* Real-time skill breakdown notification toast */}
      {skillBreakdownNotification && (
        <div className="fixed top-4 right-4 z-50 max-w-sm animate-in slide-in-from-top-2 fade-in">
          <div className="rounded-lg border border-primary/30 bg-primary/10 p-4 shadow-lg backdrop-blur-sm">
            <p className="text-sm font-medium text-foreground">{skillBreakdownNotification}</p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Partner Portal
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Welcome back, <span className="font-medium text-foreground">{partnerName || "Partner"}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant={connected ? "default" : "secondary"}
            className={connected ? "bg-emerald-600 hover:bg-emerald-700" : ""}
          >
            <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${connected ? "bg-white animate-pulse" : "bg-muted-foreground"}`} />
            {connected ? "Live" : "Connecting..."}
          </Badge>
          {stats.pending_confirmations > 0 && (
            <Badge variant="destructive" className="animate-in fade-in">
              {stats.pending_confirmations} pending
            </Badge>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Stats cards */}
      <PartnerStats isLoading={isLoading} />

      {/* Main content tabs */}
      <Tabs defaultValue="confirmations" className="w-full">
        <TabsList className="grid w-full grid-cols-6 md:w-auto md:inline-flex">
          <TabsTrigger value="confirmations" className="relative">
            Confirmations
            {confirmations.filter((c) => c.status === "pending").length > 0 && (
              <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] text-destructive-foreground font-bold">
                {confirmations.filter((c) => c.status === "pending").length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="timesheets" className="relative">
            Timesheets
          </TabsTrigger>
          <TabsTrigger value="assignments">Timeline</TabsTrigger>
          <TabsTrigger value="projects">Projects</TabsTrigger>
          <TabsTrigger value="skill-reviews" className="relative">
            Skill Reviews
            {pendingBreakdownCount > 0 && (
              <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] text-destructive-foreground font-bold">
                {pendingBreakdownCount}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="notifications">Alerts</TabsTrigger>
          <TabsTrigger value="headcount">Request Staff</TabsTrigger>
        </TabsList>

        <TabsContent value="confirmations" className="mt-4">
          <PendingConfirmations />
        </TabsContent>

        <TabsContent value="timesheets" className="mt-4">
          <TimesheetReviewQueue />
        </TabsContent>

        <TabsContent value="assignments" className="mt-4">
          <AssignmentsTimeline />
        </TabsContent>

        <TabsContent value="projects" className="mt-4">
          <PartnerProjects />
        </TabsContent>

        <TabsContent value="notifications" className="mt-4">
          <PartnerNotifications />
        </TabsContent>

        <TabsContent value="headcount" className="mt-4">
          <HeadcountRequestForm />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default PartnerPortal;
