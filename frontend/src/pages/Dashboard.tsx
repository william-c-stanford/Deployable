import { useEffect } from "react";
import { useDashboardStore } from "@/stores/dashboardStore";
import { KPICardComponent } from "@/components/dashboard/KPICard";
import { SuggestedActionsWidget } from "@/components/dashboard/SuggestedActions";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { RefreshCw } from "lucide-react";
import type { KPICard } from "@/types/dashboard";

// Fallback demo data when backend is unavailable
const demoKPICards: KPICard[] = [
  {
    id: "total-technicians",
    label: "Total Technicians",
    value: 54,
    icon: "Users",
    color: "blue",
    link: "/ops/technicians",
    sub_items: [
      { label: "Ready Now", value: 18, color: "emerald" },
      { label: "In Training", value: 12, color: "amber" },
      { label: "Currently Assigned", value: 16, color: "blue" },
      { label: "Rolling Off Soon", value: 4, color: "orange" },
    ],
  },
  {
    id: "ready-to-deploy",
    label: "Ready to Deploy",
    value: 18,
    icon: "UserCheck",
    color: "emerald",
    link: "/ops/technicians?status=Ready+Now",
  },
  {
    id: "active-projects",
    label: "Active Projects",
    value: 5,
    icon: "Briefcase",
    color: "violet",
    link: "/ops/projects?status=Active",
    sub_items: [
      { label: "Staffing", value: 3, color: "amber" },
      { label: "Wrapping Up", value: 1, color: "orange" },
    ],
  },
  {
    id: "open-roles",
    label: "Open Roles",
    value: 12,
    icon: "ClipboardList",
    color: "amber",
    link: "/ops/projects?tab=staffing",
  },
  {
    id: "pending-recommendations",
    label: "Pending Actions",
    value: 8,
    icon: "Inbox",
    color: "rose",
    link: "/ops/inbox",
  },
  {
    id: "pending-timesheets",
    label: "Timesheets to Review",
    value: 23,
    icon: "Clock",
    color: "cyan",
    link: "/ops/projects?tab=timesheets",
    sub_items: [{ label: "Flagged", value: 3, color: "red" }],
  },
  {
    id: "expiring-certs",
    label: "Certs Expiring (30d)",
    value: 6,
    icon: "AlertTriangle",
    color: "orange",
    link: "/ops/technicians?filter=expiring_certs",
  },
  {
    id: "headcount-requests",
    label: "Headcount Requests",
    value: 4,
    icon: "UserPlus",
    color: "indigo",
    link: "/ops/inbox?tab=headcount",
  },
];

const demoSuggestedActions = [
  {
    id: "sa-1",
    action_type: "staffing",
    title: "Review 3 new staffing recommendations",
    description: "Fiber splicers needed for Metro Connect Phoenix",
    link: "/ops/inbox",
    priority: 5,
    agent_name: "Staffing Agent",
    created_at: new Date(Date.now() - 600000).toISOString(),
  },
  {
    id: "sa-2",
    action_type: "cert_renewal",
    title: "2 OSHA certs expiring this week",
    description: "Marcus Chen, Priya Patel need renewal",
    link: "/ops/technicians?filter=expiring_certs",
    priority: 4,
    agent_name: "Cert Monitor",
    created_at: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    id: "sa-3",
    action_type: "timesheet",
    title: "3 flagged timesheets need resolution",
    description: "DataVault Chicago project disputes",
    link: "/ops/projects?tab=timesheets",
    priority: 3,
    agent_name: "Timesheet Agent",
    created_at: new Date(Date.now() - 7200000).toISOString(),
  },
  {
    id: "sa-4",
    action_type: "training",
    title: "4 technicians near advancement threshold",
    description: "Ready for Intermediate level promotion",
    link: "/ops/training",
    priority: 2,
    agent_name: "Training Agent",
    created_at: new Date(Date.now() - 14400000).toISOString(),
  },
  {
    id: "sa-5",
    action_type: "escalation",
    title: "Partner confirmation overdue 48h",
    description: "CloudScale Denver — awaiting start date confirmation",
    link: "/ops/inbox?tab=escalations",
    priority: 5,
    agent_name: "Escalation Agent",
    created_at: new Date(Date.now() - 1800000).toISOString(),
  },
  {
    id: "sa-6",
    action_type: "backfill",
    title: "Backfill needed: Metro Connect Phoenix",
    description: "1 fiber splicer rolling off in 2 weeks",
    link: "/ops/projects/proj-1?tab=staffing",
    priority: 3,
    agent_name: "Forward Staffing Agent",
    created_at: new Date(Date.now() - 10800000).toISOString(),
  },
];

const demoActivity = [
  {
    id: "act-1",
    action: "Generated staffing recommendations",
    entity_type: "project",
    entity_id: "proj-1",
    agent_name: "Staffing Agent",
    created_at: new Date(Date.now() - 300000).toISOString(),
  },
  {
    id: "act-2",
    action: "Approved timesheet",
    entity_type: "timesheet",
    entity_id: "ts-12",
    created_at: new Date(Date.now() - 1800000).toISOString(),
  },
  {
    id: "act-3",
    action: "Training advancement triggered",
    entity_type: "technician",
    entity_id: "tech-5",
    agent_name: "Training Agent",
    created_at: new Date(Date.now() - 3600000).toISOString(),
  },
  {
    id: "act-4",
    action: "New headcount request submitted",
    entity_type: "headcount_request",
    entity_id: "hcr-3",
    created_at: new Date(Date.now() - 7200000).toISOString(),
  },
  {
    id: "act-5",
    action: "Cert expiry alert generated",
    entity_type: "certification",
    entity_id: "cert-8",
    agent_name: "Cert Monitor Agent",
    created_at: new Date(Date.now() - 14400000).toISOString(),
  },
];

export function Dashboard() {
  const {
    kpiCards,
    suggestedActions,
    recentActivity,
    isLoading,
    fetchDashboard,
    refreshKPIs,
  } = useDashboardStore();

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  // Use real data if available, otherwise fallback to demo
  const displayCards = kpiCards.length > 0 ? kpiCards : demoKPICards;
  const displayActivity = recentActivity.length > 0 ? recentActivity : demoActivity;

  // For suggested actions: if no data from API, seed the store with demo actions
  const hasSuggestedActions = suggestedActions.length > 0;

  useEffect(() => {
    if (!hasSuggestedActions && !isLoading) {
      // Seed the store with demo actions so the widget can work with them
      useDashboardStore.getState().replaceSuggestedActions(demoSuggestedActions);
    }
  }, [hasSuggestedActions, isLoading]);

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Operations Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Real-time workforce overview and actionable insights
          </p>
        </div>
        <button
          onClick={refreshKPIs}
          disabled={isLoading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors text-sm font-medium disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {displayCards.map((card) => (
          <KPICardComponent key={card.id} card={card} />
        ))}
      </div>

      {/* Lower section: Suggested Actions + Activity Feed */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SuggestedActionsWidget enableRealtime={true} />
        <ActivityFeed entries={displayActivity} />
      </div>
    </div>
  );
}
