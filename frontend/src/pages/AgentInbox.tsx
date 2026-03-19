import React, { useEffect, useState, useCallback } from 'react'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip'
import { useAgentInboxStore } from '@/stores/agentInboxStore'
import {
  seedPendingRecommendations,
  seedActiveRules,
  seedProposedRules,
  seedActivityLog,
} from '@/lib/agentInboxSeedData'
import type { Recommendation, PreferenceRule, ActivityLogEntry } from '@/types'

// ============================================================
// Agent Inbox — Pending, Active Rules, Activity Log
// ============================================================

export function AgentInbox() {
  const store = useAgentInboxStore()

  useEffect(() => {
    // Attempt to fetch from API first; fall back to seed data
    store.fetchRecommendations().then(() => {
      if (store.pendingRecommendations.length === 0) {
        store.setPendingRecommendations(seedPendingRecommendations)
      }
    })
    store.fetchRules().then(() => {
      if (store.activeRules.length === 0) {
        store.setActiveRules(seedActiveRules)
      }
      if (store.proposedRules.length === 0) {
        store.setProposedRules(seedProposedRules)
      }
    })
    if (store.activityLog.length === 0) {
      store.setActivityLog(seedActivityLog)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pendingCount = store.getPendingCount()
  const stats = store.getStats()

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-primary/10">
            <BotIcon />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-foreground tracking-tight">Agent Inbox</h1>
            <p className="text-sm text-muted-foreground">
              Review AI-generated recommendations, manage preference rules, and track agent activity
            </p>
          </div>
        </div>
        {pendingCount > 0 && (
          <Badge variant="destructive" className="self-start sm:self-auto text-sm px-3 py-1">
            {pendingCount} Pending
          </Badge>
        )}
      </div>

      {/* Quick Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatCard label="Pending" value={stats.totalPending} variant="warning" />
        <StatCard label="Avg Score" value={stats.avgScore} suffix="pts" variant={stats.avgScore >= 70 ? 'success' : 'warning'} />
        <StatCard label="Approved Today" value={stats.approvedToday} variant="success" />
        <StatCard label="Rejected Today" value={stats.rejectedToday} variant="destructive" />
        <StatCard label="Active Rules" value={store.activeRules.filter(r => r.active).length} variant="default" />
      </div>

      {/* Main Tabs */}
      <Tabs defaultValue="pending" onValueChange={store.setActiveTab}>
        <TabsList className="w-full sm:w-auto mb-4">
          <TabsTrigger value="pending">
            <span className="flex items-center gap-2">
              <ZapIcon />
              Pending
              {pendingCount > 0 && (
                <span className="ml-1 bg-destructive/20 text-destructive text-xs font-bold px-2 py-0.5 rounded-full">
                  {pendingCount}
                </span>
              )}
            </span>
          </TabsTrigger>
          <TabsTrigger value="rules">
            <span className="flex items-center gap-2">
              <ShieldIcon />
              Active Rules
              <span className="ml-1 bg-muted text-muted-foreground text-xs font-bold px-2 py-0.5 rounded-full">
                {store.activeRules.filter((r) => r.active).length}
              </span>
              {store.proposedRules.length > 0 && (
                <span className="ml-1 bg-warning/20 text-warning text-xs font-bold px-2 py-0.5 rounded-full">
                  {store.proposedRules.length} new
                </span>
              )}
            </span>
          </TabsTrigger>
          <TabsTrigger value="activity">
            <span className="flex items-center gap-2">
              <ActivityIcon />
              Activity Log
            </span>
          </TabsTrigger>
        </TabsList>

        {/* Search and filters */}
        <div className="flex flex-col sm:flex-row gap-3 mb-6">
          <div className="relative flex-1">
            <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2" />
            <Input
              placeholder="Search recommendations, rules, or activity..."
              className="pl-10"
              value={store.filters.search}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => store.setFilters({ search: e.target.value })}
            />
          </div>
          <div className="flex gap-2">
            <select
              className="bg-secondary text-secondary-foreground border border-input rounded-md px-3 py-2 text-sm"
              value={store.filters.typeFilter}
              onChange={(e) => store.setFilters({ typeFilter: e.target.value })}
            >
              <option value="">All Types</option>
              <option value="staffing">Staffing</option>
              <option value="cert_renewal">Cert Renewal</option>
              <option value="training">Training</option>
              <option value="backfill">Backfill</option>
              <option value="next_step">Next Step</option>
            </select>
            <select
              className="bg-secondary text-secondary-foreground border border-input rounded-md px-3 py-2 text-sm"
              value={store.filters.dateRange}
              onChange={(e) =>
                store.setFilters({ dateRange: e.target.value as 'all' | '24h' | '7d' | '30d' })
              }
            >
              <option value="all">All Time</option>
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7 Days</option>
              <option value="30d">Last 30 Days</option>
            </select>
          </div>
        </div>

        <TabsContent value="pending">
          <PendingTab />
        </TabsContent>

        <TabsContent value="rules">
          <ActiveRulesTab />
        </TabsContent>

        <TabsContent value="activity">
          <ActivityLogTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// ============================================================
// Quick Stat Card
// ============================================================

function StatCard({
  label,
  value,
  suffix,
  variant,
}: {
  label: string
  value: number
  suffix?: string
  variant: 'success' | 'warning' | 'destructive' | 'default'
}) {
  const colors: Record<string, string> = {
    success: 'text-success',
    warning: 'text-warning',
    destructive: 'text-destructive',
    default: 'text-primary',
  }
  return (
    <Card className="p-3">
      <p className="text-xs text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className={`text-xl font-bold mt-1 ${colors[variant]}`}>
        {value}{suffix ? ` ${suffix}` : ''}
      </p>
    </Card>
  )
}

// ============================================================
// Pending Recommendations Tab
// ============================================================

function PendingTab() {
  const store = useAgentInboxStore()
  const filtered = store.getFilteredRecommendations()
  const selectedCount = store.selectedIds.size

  if (store.isLoadingRecommendations) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <div className="mx-auto mb-3 animate-spin text-primary">
            <LoaderIcon size={32} />
          </div>
          <p className="text-muted-foreground">Loading recommendations...</p>
        </CardContent>
      </Card>
    )
  }

  if (filtered.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <div className="mx-auto mb-3 text-success">
            <CheckCircleIcon size={48} />
          </div>
          <h3 className="text-lg font-medium text-foreground">All caught up!</h3>
          <p className="text-muted-foreground mt-1">No pending recommendations to review.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {/* Batch action bar */}
      <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg border border-border">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => selectedCount > 0 ? store.clearSelection() : store.selectAll()}
          >
            {selectedCount > 0 ? `Deselect All (${selectedCount})` : `Select All (${filtered.length})`}
          </Button>
          {selectedCount > 0 && (
            <span className="text-sm text-muted-foreground">
              {selectedCount} selected
            </span>
          )}
        </div>
        {selectedCount > 0 && (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={store.batchDismiss}
              className="text-muted-foreground"
            >
              Dismiss Selected
            </Button>
            <Button
              size="sm"
              onClick={store.batchApprove}
              className="bg-success text-white hover:bg-success/90"
            >
              Approve Selected ({selectedCount})
            </Button>
          </div>
        )}
      </div>

      {filtered.map((rec) => (
        <RecommendationCard key={rec.id} recommendation={rec} />
      ))}
    </div>
  )
}

function RecommendationCard({ recommendation: rec }: { recommendation: Recommendation }) {
  const [expanded, setExpanded] = useState(false)
  const store = useAgentInboxStore()
  const isSelected = store.selectedIds.has(rec.id)

  const typeLabels: Record<string, string> = {
    staffing: 'Staffing',
    cert_renewal: 'Cert Renewal',
    training: 'Training',
    backfill: 'Backfill',
    next_step: 'Next Step',
  }
  const typeBadgeVariant: Record<string, 'default' | 'warning' | 'success' | 'destructive' | 'secondary'> = {
    staffing: 'default',
    cert_renewal: 'warning',
    training: 'success',
    backfill: 'destructive',
    next_step: 'secondary',
  }

  const context = rec.context as Record<string, unknown> | undefined

  return (
    <Card className={`overflow-hidden transition-colors ${isSelected ? 'border-primary ring-1 ring-primary/30' : 'hover:border-primary/30'}`}>
      <div className="p-4 sm:p-6">
        {/* Header row */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            {/* Selection checkbox */}
            <button
              onClick={() => store.toggleSelected(rec.id)}
              className={`mt-1 flex-shrink-0 w-5 h-5 rounded border-2 transition-colors flex items-center justify-center ${
                isSelected
                  ? 'bg-primary border-primary text-primary-foreground'
                  : 'border-muted-foreground/40 hover:border-primary'
              }`}
            >
              {isSelected && (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              )}
            </button>

            <div className="p-2 rounded-lg bg-secondary border border-border flex-shrink-0">
              <RecommendationTypeIcon type={rec.type} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant={typeBadgeVariant[rec.type] || 'default'}>
                  {typeLabels[rec.type] || rec.type}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  by {rec.agent.replace(/_/g, ' ')}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatRelativeTime(rec.created_at)}
                </span>
              </div>
              <h3 className="mt-1 font-semibold text-foreground truncate">
                {rec.target_name}
                {context?.project_name ? (
                  <span className="font-normal text-muted-foreground">
                    {' → '}{String(context.project_name)}
                    {context?.role_name ? ` — ${String(context.role_name)}` : null}
                  </span>
                ) : null}
              </h3>
              <p className="mt-1 text-sm text-muted-foreground line-clamp-2">{rec.explanation}</p>
            </div>
          </div>

          {/* Overall score badge */}
          {rec.scorecard && rec.scorecard.overall_score > 0 && (
            <div className="flex-shrink-0 text-center hidden sm:block">
              <div
                className={`text-2xl font-bold ${
                  rec.scorecard.overall_score >= 80
                    ? 'text-success'
                    : rec.scorecard.overall_score >= 60
                      ? 'text-warning'
                      : 'text-destructive'
                }`}
              >
                {rec.scorecard.overall_score}
              </div>
              <div className="text-xs text-muted-foreground">Score</div>
            </div>
          )}
        </div>

        {/* Expandable scorecard */}
        {expanded && rec.scorecard && (
          <div className="mt-4 pt-4 border-t border-border">
            <h4 className="text-sm font-medium text-foreground mb-3">5-Dimension Scorecard</h4>
            <div className="grid grid-cols-1 sm:grid-cols-5 gap-3">
              {(['skill_match', 'availability', 'certification', 'location', 'experience'] as const).map(
                (key) => {
                  const dim = rec.scorecard![key]
                  return (
                    <ScorecardDimensionCard
                      key={key}
                      label={key.replace(/_/g, ' ')}
                      score={dim.score}
                      status={dim.status}
                      detail={dim.detail}
                    />
                  )
                }
              )}
            </div>
            <div className="mt-4 p-3 bg-muted rounded-lg">
              <p className="text-sm text-foreground leading-relaxed">{rec.explanation}</p>
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="mt-4 flex items-center justify-between gap-2 flex-wrap">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded(!expanded)}
            className="text-muted-foreground"
          >
            {expanded ? '▲ Less' : '▼ Details'}
          </Button>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => store.dismissRecommendation(rec.id)}
              className="text-muted-foreground hover:text-foreground"
            >
              Dismiss
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => store.rejectRecommendation(rec.id)}
              className="text-destructive border-destructive/30 hover:bg-destructive/10"
            >
              ✕ Reject
            </Button>
            <Button
              size="sm"
              onClick={() => store.approveRecommendation(rec.id)}
              className="bg-success text-white hover:bg-success/90"
            >
              ✓ Approve
            </Button>
          </div>
        </div>
      </div>
    </Card>
  )
}

function ScorecardDimensionCard({
  label,
  score,
  status,
  detail,
}: {
  label: string
  score: number
  status: string
  detail: string
}) {
  const statusColors: Record<string, string> = {
    pass: 'border-success/30 bg-success/5',
    warn: 'border-warning/30 bg-warning/5',
    fail: 'border-destructive/30 bg-destructive/5',
  }
  const statusText: Record<string, string> = {
    pass: 'text-success',
    warn: 'text-warning',
    fail: 'text-destructive',
  }

  return (
    <div className={`rounded-lg border p-3 ${statusColors[status] || ''}`}>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground capitalize">
        {label}
      </div>
      <div className={`text-lg font-bold mt-1 ${statusText[status] || ''}`}>
        {score > 0 ? score : '—'}
      </div>
      <div className="text-xs mt-1 text-muted-foreground line-clamp-2">{detail}</div>
    </div>
  )
}

// ============================================================
// Active Rules Tab
// ============================================================

function ActiveRulesTab() {
  const store = useAgentInboxStore()
  const rules = store.activeRules
  const proposed = store.proposedRules

  return (
    <div className="space-y-6">
      {/* Proposed Rules Section — agent-generated, awaiting approval */}
      {proposed.length > 0 && (
        <div className="space-y-4">
          <Card className="border-warning/30 bg-warning/5">
            <CardHeader className="pb-2">
              <CardTitle className="text-lg flex items-center gap-2">
                <LightbulbIcon />
                Agent-Proposed Rules
                <Badge variant="warning" className="ml-2">{proposed.length} pending</Badge>
              </CardTitle>
              <CardDescription>
                The AI has proposed new rules based on patterns in your rejection decisions. Review and approve or reject each one.
              </CardDescription>
            </CardHeader>
          </Card>

          {proposed.map((rule) => (
            <ProposedRuleCard key={rule.id} rule={rule} />
          ))}
        </div>
      )}

      {/* Active Rules Section */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Preference Rules</CardTitle>
          <CardDescription>
            Rules that modify how the staffing agent ranks and filters candidates. Active rules are applied
            in real-time to all new recommendations. Rules are proposed by the agent when you reject
            recommendations.
          </CardDescription>
        </CardHeader>
      </Card>

      {rules.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <div className="mx-auto mb-3 text-muted-foreground">
              <ShieldIcon size={48} />
            </div>
            <h3 className="text-lg font-medium text-foreground">No rules configured</h3>
            <p className="text-muted-foreground mt-1">
              Rules are proposed by the agent when you reject recommendations.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <RuleCard key={rule.id} rule={rule} />
          ))}
        </div>
      )}
    </div>
  )
}

function ProposedRuleCard({ rule }: { rule: PreferenceRule }) {
  const store = useAgentInboxStore()

  return (
    <Card className="border-warning/30 bg-gradient-to-r from-warning/5 to-transparent">
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1">
            <div className="p-2 rounded-lg bg-warning/10 flex-shrink-0">
              <LightbulbIcon />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="warning">Proposed</Badge>
                <Badge variant={rule.effect === 'exclude' ? 'destructive' : rule.effect === 'boost' ? 'success' : 'secondary'}>
                  {rule.effect === 'exclude' ? 'Hard Filter' : rule.effect === 'boost' ? 'Boost' : 'Soft Demote'}
                </Badge>
                <Badge variant="outline" className="capitalize">
                  {rule.rule_type.replace(/_/g, ' ')}
                </Badge>
              </div>
              <p className="mt-2 text-sm font-medium text-foreground">{rule.description}</p>
              {rule.proposed_reason && (
                <div className="mt-2 p-3 bg-muted rounded-lg">
                  <p className="text-xs text-muted-foreground italic">
                    <span className="font-semibold not-italic">Agent reasoning: </span>
                    {rule.proposed_reason}
                  </p>
                </div>
              )}
              <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
                <span>Threshold: <span className="font-mono text-foreground">{rule.threshold}</span></span>
                <span>Scope: {rule.scope}</span>
                {rule.score_modifier !== null && rule.score_modifier !== 0 && (
                  <span>
                    Score modifier: <span className={rule.score_modifier > 0 ? 'text-success' : 'text-destructive'}>
                      {rule.score_modifier > 0 ? '+' : ''}{rule.score_modifier}
                    </span>
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => store.rejectProposedRule(rule.id)}
            className="text-destructive border-destructive/30 hover:bg-destructive/10"
          >
            ✕ Reject Rule
          </Button>
          <Button
            size="sm"
            onClick={() => store.approveProposedRule(rule.id)}
            className="bg-success text-white hover:bg-success/90"
          >
            ✓ Activate Rule
          </Button>
        </div>
      </div>
    </Card>
  )
}

function RuleCard({ rule }: { rule: PreferenceRule }) {
  const store = useAgentInboxStore()
  const [editing, setEditing] = useState(false)
  const [editThreshold, setEditThreshold] = useState(rule.threshold)

  const handleSave = () => {
    store.updateRule(rule.id, { threshold: editThreshold })
    setEditing(false)
  }

  return (
    <Card className={`transition-all ${!rule.active ? 'opacity-50' : ''}`}>
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1">
            <div className="p-2 rounded-lg bg-secondary flex-shrink-0">
              <ShieldIcon />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant={rule.effect === 'exclude' ? 'destructive' : rule.effect === 'boost' ? 'success' : 'warning'}>
                  {rule.effect === 'exclude' ? 'Hard Filter' : rule.effect === 'boost' ? 'Boost' : 'Soft Demote'}
                </Badge>
                <Badge variant="outline" className="capitalize">
                  {rule.rule_type.replace(/_/g, ' ')}
                </Badge>
                <span className="text-xs text-muted-foreground">Scope: {rule.scope}</span>
                {rule.created_by_type === 'agent' && (
                  <Badge variant="secondary" className="text-xs">AI-generated</Badge>
                )}
              </div>
              <p className="mt-2 text-sm text-foreground">{rule.description}</p>
              {rule.score_modifier !== null && rule.score_modifier !== 0 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Score modifier: <span className={`font-mono ${rule.score_modifier > 0 ? 'text-success' : 'text-destructive'}`}>
                    {rule.score_modifier > 0 ? '+' : ''}{rule.score_modifier}
                  </span>
                </p>
              )}

              {editing ? (
                <div className="mt-3 flex items-center gap-2 flex-wrap">
                  <label className="text-xs text-muted-foreground">Threshold:</label>
                  <Input
                    value={editThreshold || ''}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEditThreshold(e.target.value)}
                    className="max-w-xs h-8 text-sm"
                  />
                  <Button size="sm" onClick={handleSave}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <div className="mt-2 text-xs text-muted-foreground">
                  Threshold: <span className="font-mono text-foreground">{rule.threshold}</span>
                  {' · '}Created {formatRelativeTime(rule.created_at)}
                </div>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div>
                    <Switch
                      checked={rule.active}
                      onCheckedChange={() => store.toggleRule(rule.id)}
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  {rule.active ? 'Disable rule' : 'Enable rule'}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(!editing)}
              title="Edit threshold"
            >
              ✎
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => store.deleteRule(rule.id)}
              title="Delete rule"
              className="text-destructive hover:text-destructive"
            >
              ✕
            </Button>
          </div>
        </div>
      </div>
    </Card>
  )
}

// ============================================================
// Activity Log Tab
// ============================================================

function ActivityLogTab() {
  const store = useAgentInboxStore()
  const filtered = store.getFilteredActivityLog()

  const actionBadgeVariants: Record<string, 'success' | 'destructive' | 'warning' | 'default' | 'secondary' | 'outline'> = {
    approved: 'success',
    rejected: 'destructive',
    dismissed: 'secondary',
    created: 'default',
    rule_applied: 'outline',
    auto_advanced: 'success',
    escalated: 'warning',
  }

  const actionIcons: Record<string, React.ReactElement> = {
    approved: <CheckCircleSmallIcon className="text-success" />,
    rejected: <XCircleIcon className="text-destructive" />,
    dismissed: <MinusCircleIcon className="text-muted-foreground" />,
    created: <PlusCircleIcon className="text-primary" />,
    rule_applied: <ShieldIcon size={14} />,
    auto_advanced: <ArrowUpIcon className="text-success" />,
    escalated: <AlertIcon className="text-warning" />,
  }

  if (filtered.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <div className="mx-auto mb-3 text-muted-foreground">
            <ActivityIcon size={48} />
          </div>
          <h3 className="text-lg font-medium text-foreground">No activity yet</h3>
          <p className="text-muted-foreground mt-1">
            Agent actions and user decisions will appear here.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {/* Summary banner */}
      <Card className="p-4">
        <div className="flex items-center gap-4 text-sm text-muted-foreground">
          <span>{filtered.length} entries</span>
          <span className="text-border">|</span>
          <span className="text-success">
            {filtered.filter(e => e.action === 'approved').length} approved
          </span>
          <span className="text-destructive">
            {filtered.filter(e => e.action === 'rejected').length} rejected
          </span>
          <span>
            {filtered.filter(e => e.action === 'created').length} agent actions
          </span>
        </div>
      </Card>

      <Card>
        <CardContent className="p-0">
          <div className="divide-y divide-border">
            {filtered.map((entry) => (
              <ActivityLogRow
                key={entry.id}
                entry={entry}
                badgeVariant={actionBadgeVariants[entry.action] || 'outline'}
                icon={actionIcons[entry.action]}
              />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function ActivityLogRow({
  entry,
  badgeVariant,
  icon,
}: {
  entry: ActivityLogEntry
  badgeVariant: 'success' | 'destructive' | 'warning' | 'default' | 'secondary' | 'outline'
  icon?: React.ReactElement
}) {
  return (
    <div className="flex gap-4 py-3 px-4 sm:px-6 hover:bg-muted/50 transition-colors">
      {/* Timeline dot with icon */}
      <div className="flex flex-col items-center pt-1">
        {icon || <div className="w-2 h-2 rounded-full bg-primary" />}
        <div className="w-px flex-1 bg-border mt-1" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 pb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={badgeVariant} className="capitalize text-xs">
            {entry.action.replace(/_/g, ' ')}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {entry.agent.replace(/_/g, ' ')}
          </span>
          {entry.user_name && (
            <span className="text-xs text-muted-foreground">by {entry.user_name}</span>
          )}
          <span className="text-xs text-muted-foreground ml-auto">
            {formatRelativeTime(entry.created_at)}
          </span>
        </div>
        <p className="mt-1 text-sm text-foreground">{entry.description}</p>
        {entry.metadata && Object.keys(entry.metadata).length > 0 && (
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            {Object.entries(entry.metadata).map(([key, val]) => (
              <span key={key} className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded">
                {key.replace(/_/g, ' ')}: {typeof val === 'object' ? JSON.stringify(val) : String(val)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// Inline SVG Icons
// ============================================================

function BotIcon({ size = 24 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2" /><path d="M2 14h2" /><path d="M20 14h2" /><path d="M15 13v2" /><path d="M9 13v2" />
    </svg>
  )
}

function ZapIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />
    </svg>
  )
}

function ShieldIcon({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
    </svg>
  )
}

function ActivityIcon({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />
    </svg>
  )
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
    </svg>
  )
}

function CheckCircleIcon({ size = 24 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="inline-block">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><path d="m9 11 3 3L22 4" />
    </svg>
  )
}

function CheckCircleSmallIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><path d="m9 11 3 3L22 4" />
    </svg>
  )
}

function XCircleIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="12" cy="12" r="10" /><path d="m15 9-6 6" /><path d="m9 9 6 6" />
    </svg>
  )
}

function MinusCircleIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="12" cy="12" r="10" /><path d="M8 12h8" />
    </svg>
  )
}

function PlusCircleIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="12" cy="12" r="10" /><path d="M8 12h8" /><path d="M12 8v8" />
    </svg>
  )
}

function ArrowUpIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="m5 12 7-7 7 7" /><path d="M12 19V5" />
    </svg>
  )
}

function AlertIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" />
    </svg>
  )
}

function LightbulbIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-warning">
      <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" />
      <path d="M9 18h6" /><path d="M10 22h4" />
    </svg>
  )
}

function LoaderIcon({ size = 24 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  )
}

function RecommendationTypeIcon({ type }: { type: string }) {
  switch (type) {
    case 'staffing':
      return (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><polyline points="16 11 18 13 22 9" />
        </svg>
      )
    case 'cert_renewal':
      return (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-warning">
          <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" />
        </svg>
      )
    case 'training':
      return (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-info">
          <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
        </svg>
      )
    case 'backfill':
      return (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-destructive">
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><line x1="19" x2="19" y1="8" y2="14" /><line x1="22" x2="16" y1="11" y2="11" />
        </svg>
      )
    default:
      return (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-success">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><path d="m9 11 3 3L22 4" />
        </svg>
      )
  }
}

// ============================================================
// Helpers
// ============================================================

function formatRelativeTime(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffMs = now - then
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return new Date(dateStr).toLocaleDateString()
}
