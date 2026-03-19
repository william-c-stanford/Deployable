/**
 * Multi-User State Sync Store
 *
 * Handles:
 * - Optimistic updates with rollback on conflict
 * - Conflict resolution via server version tracking
 * - Toast attribution showing which user triggered each change
 * - WebSocket-driven state synchronization across connected clients
 */
import { create } from "zustand"
import { toast } from "@/components/ui/toast"

// ---- Types ----

export interface SyncActor {
  userId: string
  name: string
  role: string
}

export interface OptimisticUpdate {
  id: string
  entityType: string
  entityId: string
  /** The previous state before optimistic application */
  previousState: any
  /** The optimistic new state */
  optimisticState: any
  /** Server version at time of update */
  baseVersion: number
  /** Timestamp */
  timestamp: number
  /** Whether this update has been confirmed by server */
  confirmed: boolean
  /** Whether this update conflicted */
  conflicted: boolean
}

export interface EntityVersion {
  entityType: string
  entityId: string
  version: number
  lastModifiedBy: SyncActor | null
  lastModifiedAt: string
}

export interface SyncEvent {
  event_type: string
  topic: string
  entity_type: string
  entity_id: string
  actor: SyncActor
  version: number
  data: any
  timestamp: string
  correlation_id?: string
}

export type ConflictResolution = "server_wins" | "client_wins" | "merge"

interface SyncState {
  /** Pending optimistic updates not yet confirmed */
  pendingUpdates: Map<string, OptimisticUpdate>
  /** Tracked entity versions */
  entityVersions: Map<string, EntityVersion>
  /** Connection state */
  isConnected: boolean
  /** Last sync timestamp */
  lastSyncAt: string | null
  /** Conflict resolution strategy */
  conflictStrategy: ConflictResolution

  // Actions
  /** Track an optimistic update before server confirmation */
  trackOptimisticUpdate: (
    entityType: string,
    entityId: string,
    previousState: any,
    optimisticState: any,
  ) => string
  /** Confirm an optimistic update succeeded */
  confirmUpdate: (updateId: string, serverVersion: number) => void
  /** Handle a conflict — rollback or merge */
  handleConflict: (
    updateId: string,
    serverState: any,
    serverVersion: number,
    serverActor: SyncActor,
  ) => any
  /** Process an incoming sync event from WebSocket */
  processSyncEvent: (event: SyncEvent) => void
  /** Get current version for an entity */
  getEntityVersion: (entityType: string, entityId: string) => number
  /** Update entity version */
  setEntityVersion: (
    entityType: string,
    entityId: string,
    version: number,
    actor?: SyncActor,
  ) => void
  /** Set connection state */
  setConnected: (connected: boolean) => void
  /** Clear all pending updates */
  clearPending: () => void
}

function entityKey(type: string, id: string): string {
  return `${type}:${id}`
}

let updateCounter = 0

export const useSyncStore = create<SyncState>((set, get) => ({
  pendingUpdates: new Map(),
  entityVersions: new Map(),
  isConnected: false,
  lastSyncAt: null,
  conflictStrategy: "server_wins",

  trackOptimisticUpdate: (entityType, entityId, previousState, optimisticState) => {
    const id = `opt-${++updateCounter}-${Date.now()}`
    const key = entityKey(entityType, entityId)
    const currentVersion = get().entityVersions.get(key)?.version ?? 0

    const update: OptimisticUpdate = {
      id,
      entityType,
      entityId,
      previousState,
      optimisticState,
      baseVersion: currentVersion,
      timestamp: Date.now(),
      confirmed: false,
      conflicted: false,
    }

    set((s) => {
      const next = new Map(s.pendingUpdates)
      next.set(id, update)
      return { pendingUpdates: next }
    })

    // Auto-timeout: if not confirmed in 15s, mark as stale
    setTimeout(() => {
      const current = get().pendingUpdates.get(id)
      if (current && !current.confirmed && !current.conflicted) {
        // Silently remove stale optimistic updates
        set((s) => {
          const next = new Map(s.pendingUpdates)
          next.delete(id)
          return { pendingUpdates: next }
        })
      }
    }, 15000)

    return id
  },

  confirmUpdate: (updateId, serverVersion) => {
    const update = get().pendingUpdates.get(updateId)
    if (!update) return

    // Update entity version
    const key = entityKey(update.entityType, update.entityId)
    set((s) => {
      const nextPending = new Map(s.pendingUpdates)
      nextPending.delete(updateId)

      const nextVersions = new Map(s.entityVersions)
      nextVersions.set(key, {
        entityType: update.entityType,
        entityId: update.entityId,
        version: serverVersion,
        lastModifiedBy: null,
        lastModifiedAt: new Date().toISOString(),
      })

      return { pendingUpdates: nextPending, entityVersions: nextVersions }
    })
  },

  handleConflict: (updateId, serverState, serverVersion, serverActor) => {
    const update = get().pendingUpdates.get(updateId)
    if (!update) return serverState

    const strategy = get().conflictStrategy

    // Mark as conflicted
    set((s) => {
      const next = new Map(s.pendingUpdates)
      next.set(updateId, { ...update, conflicted: true })
      return { pendingUpdates: next }
    })

    // Show conflict toast with attribution
    toast("warning", "Change conflict detected", {
      description: `${serverActor.name} also modified this ${update.entityType}. Using server version.`,
      actor: serverActor,
      duration: 6000,
    })

    let resolvedState: any
    switch (strategy) {
      case "server_wins":
        resolvedState = serverState
        break
      case "client_wins":
        resolvedState = update.optimisticState
        break
      case "merge":
        // Shallow merge: server wins for conflicting keys, client keeps unique keys
        resolvedState = { ...update.optimisticState, ...serverState }
        break
      default:
        resolvedState = serverState
    }

    // Cleanup
    setTimeout(() => {
      set((s) => {
        const next = new Map(s.pendingUpdates)
        next.delete(updateId)
        return { pendingUpdates: next }
      })
    }, 500)

    // Update entity version
    const key = entityKey(update.entityType, update.entityId)
    set((s) => {
      const nextVersions = new Map(s.entityVersions)
      nextVersions.set(key, {
        entityType: update.entityType,
        entityId: update.entityId,
        version: serverVersion,
        lastModifiedBy: serverActor,
        lastModifiedAt: new Date().toISOString(),
      })
      return { entityVersions: nextVersions }
    })

    return resolvedState
  },

  processSyncEvent: (event) => {
    const key = entityKey(event.entity_type, event.entity_id)
    const currentVersion = get().entityVersions.get(key)?.version ?? 0

    // Skip if we already have a newer version
    if (event.version <= currentVersion) return

    // Check for conflicts with pending optimistic updates
    const pendingForEntity = Array.from(get().pendingUpdates.values()).filter(
      (u) => u.entityType === event.entity_type && u.entityId === event.entity_id
    )

    if (pendingForEntity.length > 0) {
      // There's a pending optimistic update for this entity — conflict!
      for (const pending of pendingForEntity) {
        get().handleConflict(
          pending.id,
          event.data,
          event.version,
          event.actor,
        )
      }
    } else {
      // No conflict — just update version and show attribution toast
      set((s) => {
        const nextVersions = new Map(s.entityVersions)
        nextVersions.set(key, {
          entityType: event.entity_type,
          entityId: event.entity_id,
          version: event.version,
          lastModifiedBy: event.actor,
          lastModifiedAt: event.timestamp,
        })
        return { entityVersions: nextVersions, lastSyncAt: event.timestamp }
      })

      // Show attribution toast for external changes
      const eventLabel = formatEventLabel(event.event_type)
      if (eventLabel) {
        toast("sync", eventLabel, {
          description: formatEventDescription(event),
          actor: event.actor,
          duration: 4000,
        })
      }
    }
  },

  getEntityVersion: (entityType, entityId) => {
    const key = entityKey(entityType, entityId)
    return get().entityVersions.get(key)?.version ?? 0
  },

  setEntityVersion: (entityType, entityId, version, actor) => {
    const key = entityKey(entityType, entityId)
    set((s) => {
      const nextVersions = new Map(s.entityVersions)
      nextVersions.set(key, {
        entityType,
        entityId,
        version,
        lastModifiedBy: actor ?? null,
        lastModifiedAt: new Date().toISOString(),
      })
      return { entityVersions: nextVersions }
    })
  },

  setConnected: (connected) => set({ isConnected: connected }),

  clearPending: () => set({ pendingUpdates: new Map() }),
}))

// ---- Helpers ----

function formatEventLabel(eventType: string): string | null {
  const labels: Record<string, string> = {
    "recommendation.approved": "Recommendation approved",
    "recommendation.rejected": "Recommendation rejected",
    "recommendation.dismissed": "Recommendation dismissed",
    "recommendation.created": "New recommendation",
    "assignment.created": "Assignment created",
    "assignment.started": "Assignment started",
    "assignment.ended": "Assignment ended",
    "assignment.cancelled": "Assignment cancelled",
    "technician.status_changed": "Technician status updated",
    "technician.availability_changed": "Availability updated",
    "training.proficiency_advanced": "Proficiency advanced",
    "training.completed": "Training completed",
    "training.hours_logged": "Training hours logged",
    "confirmation.confirmed": "Confirmation received",
    "confirmation.declined": "Confirmation declined",
    "confirmation.escalated": "Escalation created",
    "timesheet.submitted": "Timesheet submitted",
    "timesheet.approved": "Timesheet approved",
    "timesheet.flagged": "Timesheet flagged",
    "timesheet.resolved": "Dispute resolved",
    "project.created": "Project created",
    "project.status_changed": "Project status changed",
    "preference.rule_created": "Preference rule added",
    "preference.rule_updated": "Preference rule updated",
    "preference.rule_deleted": "Preference rule removed",
    "cert.added": "Certification added",
    "cert.expired": "Certification expired",
    "cert.renewed": "Certification renewed",
    "doc.verified": "Document verified",
    "badge.granted": "Badge granted",
    "badge.revoked": "Badge revoked",
  }
  return labels[eventType] ?? null
}

function formatEventDescription(event: SyncEvent): string {
  const { entity_type, data } = event
  if (data?.name) return `${entity_type}: ${data.name}`
  if (data?.technician_name) return data.technician_name
  if (data?.project_name) return data.project_name
  if (data?.description) return data.description
  return `${entity_type} #${event.entity_id.slice(0, 8)}`
}

// ---- Optimistic update helper hook pattern ----

/**
 * Creates an optimistic update wrapper for any async action.
 *
 * Usage:
 *   const result = await optimisticAction(
 *     "recommendation", recId,
 *     currentRec,
 *     { ...currentRec, status: "Approved" },
 *     () => api.post(`/recommendations/${recId}/approve`),
 *     (response) => applyToStore(response.data),
 *   )
 */
export async function optimisticAction<T>(
  entityType: string,
  entityId: string,
  previousState: T,
  optimisticState: T,
  serverAction: () => Promise<any>,
  onSuccess?: (response: any) => void,
  onRollback?: (previousState: T) => void,
): Promise<{ success: boolean; data?: any; error?: any }> {
  const store = useSyncStore.getState()
  const updateId = store.trackOptimisticUpdate(
    entityType,
    entityId,
    previousState,
    optimisticState,
  )

  try {
    const response = await serverAction()
    const serverVersion = response?.data?.version ?? (store.getEntityVersion(entityType, entityId) + 1)
    store.confirmUpdate(updateId, serverVersion)
    onSuccess?.(response)
    return { success: true, data: response?.data }
  } catch (error: any) {
    // Rollback optimistic update
    onRollback?.(previousState)

    // Remove the pending update
    useSyncStore.setState((s) => {
      const next = new Map(s.pendingUpdates)
      next.delete(updateId)
      return { pendingUpdates: next }
    })

    toast("error", "Action failed", {
      description: error?.response?.data?.detail || error?.message || "Please try again",
      duration: 5000,
    })

    return { success: false, error }
  }
}
