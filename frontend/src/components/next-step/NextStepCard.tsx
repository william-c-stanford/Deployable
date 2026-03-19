import { useEffect, useCallback, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTechPortalStore } from '@/stores/techPortalStore'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { NextStepCard as NextStepCardType, WSEvent, NextStepCardUpdateEvent } from '@/types'

// ============================================================
// NextStepCard — agent-generated action card for technician portal
// Fetches data via API, receives real-time updates via WebSocket
// ============================================================

interface NextStepCardProps {
  technicianId: string
  /** Fallback card from seed data if API returns nothing */
  fallbackCard?: NextStepCardType | null
}

const TYPE_CONFIG: Record<string, { emoji: string; label: string; color: string }> = {
  training: { emoji: '\uD83D\uDCDA', label: 'Training', color: 'text-blue-400' },
  certification: { emoji: '\uD83D\uDCCB', label: 'Certification', color: 'text-amber-400' },
  document: { emoji: '\uD83D\uDCC4', label: 'Document', color: 'text-purple-400' },
  assignment: { emoji: '\uD83C\uDFAF', label: 'Assignment', color: 'text-green-400' },
  timesheet: { emoji: '\u23F1', label: 'Timesheet', color: 'text-cyan-400' },
  general: { emoji: '\uD83D\uDCA1', label: 'Action', color: 'text-primary' },
}

const PRIORITY_STYLES: Record<string, { border: string; bg: string; badgeVariant: 'warning' | 'default' | 'secondary' }> = {
  high: { border: 'border-warning/40', bg: 'bg-warning/5', badgeVariant: 'warning' },
  medium: { border: 'border-primary/40', bg: 'bg-primary/5', badgeVariant: 'default' },
  low: { border: 'border-border', bg: 'bg-card', badgeVariant: 'secondary' },
}

export function NextStepCard({ technicianId, fallbackCard }: NextStepCardProps) {
  const navigate = useNavigate()
  const store = useTechPortalStore()
  const { nextStepCard, nextStepLoading, nextStepAnimating } = store
  const [justUpdated, setJustUpdated] = useState(false)
  const [dismissing, setDismissing] = useState(false)
  const previousCardId = useRef<string | null>(null)

  // ── Fetch next step card from API on mount ────────────────
  useEffect(() => {
    store.fetchNextStepCard(technicianId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [technicianId])

  // ── Fall back to seed data if API returned nothing ────────
  useEffect(() => {
    if (!nextStepLoading && !nextStepCard && fallbackCard) {
      store.setNextStepCard(fallbackCard)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nextStepLoading, nextStepCard, fallbackCard])

  // ── Flash animation when card changes ─────────────────────
  useEffect(() => {
    if (nextStepCard && nextStepCard.id !== previousCardId.current) {
      if (previousCardId.current !== null) {
        // Card actually changed (not initial load)
        setJustUpdated(true)
        const timer = setTimeout(() => setJustUpdated(false), 2000)
        return () => clearTimeout(timer)
      }
      previousCardId.current = nextStepCard.id
    }
  }, [nextStepCard])

  // ── WebSocket subscription for real-time updates ──────────
  const handleWSMessage = useCallback(
    (event: WSEvent<NextStepCardUpdateEvent>) => {
      const eventType = event.event_type
      if (
        eventType === 'next_step.updated' ||
        eventType === 'portal.next_step_updated' ||
        eventType === 'recommendation.created' ||
        eventType === 'recommendation.status_changed'
      ) {
        // If it's a next_step.updated or portal.next_step_updated event with direct payload
        if ((eventType === 'next_step.updated' || eventType === 'portal.next_step_updated') && event.data) {
          store.handleNextStepWSUpdate(event.data)
          return
        }

        // For recommendation events, check if it's a next_step type
        const rec = event.recommendation || (event.data as unknown as Record<string, unknown>)
        if (rec && (rec as Record<string, unknown>).type === 'next_step') {
          const r = rec as Record<string, unknown>
          const card: NextStepCardType = {
            id: (r.id as string) || `ns-ws-${Date.now()}`,
            action: (r.title as string) || (r.action as string) || (r.summary as string) || 'Review recommendation',
            reasoning: (r.explanation as string) || (r.reasoning as string) || (r.description as string) || '',
            priority: ((r.priority as string) || 'medium') as 'high' | 'medium' | 'low',
            type: ((r.next_step_type as string) || (r.sub_type as string) || 'general') as NextStepCardType['type'],
            link: (r.link as string) || undefined,
            recommendation_id: r.id as string,
            generated_at: (r.created_at as string) || new Date().toISOString(),
          }
          store.setNextStepCard(card)
        }
      }

      // Handle recommendation dismissed/rejected — clear card if it matches
      if (
        (eventType === 'recommendation.executed' || eventType === 'recommendation.rejected') &&
        event.recommendation
      ) {
        const currentCard = store.nextStepCard
        if (currentCard?.recommendation_id === event.recommendation.id) {
          store.setNextStepCard(null)
          // Re-fetch to get next best card
          store.fetchNextStepCard(technicianId)
        }
      }
    },
    [store, technicianId],
  )

  // Subscribe to both notifications and recommendations topics
  const { connected } = useWebSocket({
    topic: 'notifications',
    onMessage: handleWSMessage,
    enabled: true,
    id: `next-step-notifications-${technicianId}`,
  })

  useWebSocket({
    topic: 'recommendations',
    onMessage: handleWSMessage,
    enabled: true,
    id: `next-step-recommendations-${technicianId}`,
  })

  // ── Dismiss handler ───────────────────────────────────────
  const handleDismiss = useCallback(() => {
    if (!nextStepCard) return
    setDismissing(true)
    setTimeout(() => {
      store.dismissNextStep(nextStepCard.id)
      setDismissing(false)
    }, 300)
  }, [nextStepCard, store])

  // ── Take Action handler ───────────────────────────────────
  const handleTakeAction = useCallback(() => {
    if (!nextStepCard) return
    if (nextStepCard.link) {
      navigate(nextStepCard.link)
    } else {
      // Navigate based on type
      const typeRoutes: Record<string, string> = {
        certification: '/tech/certifications',
        training: '/tech/training',
        document: '/tech/documents',
        assignment: '/tech/assignments',
        timesheet: '/tech/timesheets',
        general: '/tech',
      }
      navigate(typeRoutes[nextStepCard.type] || '/tech')
    }
  }, [nextStepCard, navigate])

  // ── Loading skeleton ──────────────────────────────────────
  if (nextStepLoading && !nextStepCard) {
    return <NextStepSkeleton />
  }

  // ── Nothing to show ───────────────────────────────────────
  if (!nextStepCard || nextStepCard.dismissed) return null

  const typeConfig = TYPE_CONFIG[nextStepCard.type] || TYPE_CONFIG.general
  const priorityStyle = PRIORITY_STYLES[nextStepCard.priority] || PRIORITY_STYLES.medium

  // Calculate days until deadline
  const daysUntilDeadline = nextStepCard.deadline
    ? Math.ceil((new Date(nextStepCard.deadline).getTime() - Date.now()) / 86400000)
    : null

  return (
    <Card
      className={[
        priorityStyle.border,
        priorityStyle.bg,
        'transition-all duration-300',
        justUpdated ? 'ring-2 ring-primary/50 animate-pulse' : '',
        nextStepAnimating ? 'opacity-50 scale-[0.98]' : 'opacity-100 scale-100',
        dismissing ? 'opacity-0 translate-x-4' : '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <CardContent className="py-4 px-4 sm:px-6">
        <div className="flex items-start gap-3">
          {/* Type emoji */}
          <div className="text-2xl sm:text-3xl flex-shrink-0 mt-0.5" role="img" aria-label={typeConfig.label}>
            {typeConfig.emoji}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            {/* Header row */}
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Your Next Step
              </span>
              {nextStepCard.priority === 'high' && (
                <Badge variant="warning" className="text-xs">
                  Urgent
                </Badge>
              )}
              {daysUntilDeadline !== null && daysUntilDeadline <= 14 && daysUntilDeadline > 0 && (
                <Badge variant={daysUntilDeadline <= 7 ? 'destructive' : 'warning'} className="text-xs">
                  {daysUntilDeadline}d left
                </Badge>
              )}
              {connected && (
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-success animate-pulse" title="Live updates active" />
              )}
            </div>

            {/* Action text */}
            <h3 className="font-semibold text-foreground text-sm sm:text-base leading-tight">
              {nextStepCard.action}
            </h3>

            {/* Reasoning */}
            <p className="text-xs sm:text-sm text-muted-foreground mt-1.5 leading-relaxed line-clamp-3">
              {nextStepCard.reasoning}
            </p>

            {/* Generated timestamp */}
            {nextStepCard.generated_at && (
              <p className="text-xs text-muted-foreground/60 mt-2">
                Suggested {formatRelativeTime(nextStepCard.generated_at)}
              </p>
            )}
          </div>

          {/* Action buttons — stacked on mobile, inline on desktop */}
          <div className="flex flex-col gap-1.5 flex-shrink-0">
            <Button
              size="sm"
              className="touch-manipulation text-xs sm:text-sm whitespace-nowrap"
              onClick={handleTakeAction}
            >
              Take Action
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="touch-manipulation text-xs text-muted-foreground hover:text-foreground"
              onClick={handleDismiss}
            >
              Dismiss
            </Button>
          </div>
        </div>

        {/* Just-updated indicator bar */}
        {justUpdated && (
          <div className="mt-3 h-0.5 bg-primary/30 rounded-full overflow-hidden">
            <div className="h-full bg-primary animate-[shrink_2s_ease-out_forwards] w-full" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Loading skeleton ──────────────────────────────────────────
function NextStepSkeleton() {
  return (
    <Card className="border-border bg-card">
      <CardContent className="py-4 px-4 sm:px-6">
        <div className="flex items-start gap-3 animate-pulse">
          <div className="h-8 w-8 rounded-lg bg-muted" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-24 bg-muted rounded" />
            <div className="h-4 w-3/4 bg-muted rounded" />
            <div className="h-3 w-full bg-muted rounded" />
          </div>
          <div className="h-8 w-24 bg-muted rounded" />
        </div>
      </CardContent>
    </Card>
  )
}

// ── Relative time formatter ─────────────────────────────────
function formatRelativeTime(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffMs = now - then
  const diffMin = Math.floor(diffMs / 60000)
  const diffHr = Math.floor(diffMs / 3600000)
  const diffDay = Math.floor(diffMs / 86400000)

  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHr < 24) return `${diffHr}h ago`
  if (diffDay < 7) return `${diffDay}d ago`
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// ── Re-export for barrel import ─────────────────────────────
export default NextStepCard
