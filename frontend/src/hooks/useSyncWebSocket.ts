/**
 * Multi-User Sync WebSocket Hook
 *
 * Connects the sync store to WebSocket events for real-time
 * multi-user state synchronization. Processes incoming sync events
 * and dispatches to appropriate domain stores.
 */
import { useCallback, useEffect, useRef } from "react"
import { useWebSocket } from "./useWebSocket"
import { useSyncStore, type SyncEvent } from "@/stores/syncStore"
import { useAuthStore } from "@/stores/authStore"
import { toast } from "@/components/ui/toast"

// Store update dispatchers — maps entity types to store update functions
type StoreUpdater = (event: SyncEvent) => void
const storeDispatchers: Map<string, StoreUpdater[]> = new Map()

/**
 * Register a store updater for a specific entity type.
 * Called once per store that wants to react to sync events.
 */
export function registerSyncDispatcher(entityType: string, updater: StoreUpdater): () => void {
  if (!storeDispatchers.has(entityType)) {
    storeDispatchers.set(entityType, [])
  }
  storeDispatchers.get(entityType)!.push(updater)

  return () => {
    const dispatchers = storeDispatchers.get(entityType)
    if (dispatchers) {
      const idx = dispatchers.indexOf(updater)
      if (idx >= 0) dispatchers.splice(idx, 1)
    }
  }
}

/**
 * Hook that subscribes to the "sync" WebSocket topic and processes
 * multi-user state sync events.
 *
 * Place this once in your app layout component.
 */
export function useSyncWebSocket() {
  const processSyncEvent = useSyncStore((s) => s.processSyncEvent)
  const setConnected = useSyncStore((s) => s.setConnected)
  const currentUserId = useAuthStore((s) => s.userId)
  const processedEvents = useRef<Set<string>>(new Set())

  const handleMessage = useCallback(
    (data: any) => {
      // Skip events from self (we already applied optimistically)
      if (data.actor?.userId === currentUserId || data.actor?.user_id === currentUserId) {
        return
      }

      // Deduplicate events by correlation_id
      const eventKey = data.correlation_id || `${data.event_type}-${data.entity_id}-${data.timestamp}`
      if (processedEvents.current.has(eventKey)) return
      processedEvents.current.add(eventKey)

      // Cleanup old events (keep last 500)
      if (processedEvents.current.size > 500) {
        const entries = Array.from(processedEvents.current)
        processedEvents.current = new Set(entries.slice(-250))
      }

      // Normalize the event
      const syncEvent: SyncEvent = {
        event_type: data.event_type || data.type || "",
        topic: data.topic || "all",
        entity_type: data.entity_type || "",
        entity_id: data.entity_id || "",
        actor: {
          userId: data.actor?.userId || data.actor?.user_id || data.actor_id || "system",
          name: data.actor?.name || data.actor_name || "System",
          role: data.actor?.role || "system",
        },
        version: data.version || 1,
        data: data.data || data,
        timestamp: data.timestamp || new Date().toISOString(),
        correlation_id: data.correlation_id,
      }

      // Process through sync store (version tracking + conflict resolution)
      processSyncEvent(syncEvent)

      // Dispatch to domain stores
      const dispatchers = storeDispatchers.get(syncEvent.entity_type) || []
      for (const dispatch of dispatchers) {
        try {
          dispatch(syncEvent)
        } catch (err) {
          console.error(`[sync] Error dispatching to ${syncEvent.entity_type} store:`, err)
        }
      }

      // Also dispatch to "all" listeners
      const allDispatchers = storeDispatchers.get("*") || []
      for (const dispatch of allDispatchers) {
        try {
          dispatch(syncEvent)
        } catch (err) {
          console.error("[sync] Error dispatching to wildcard store:", err)
        }
      }
    },
    [currentUserId, processSyncEvent],
  )

  const { connected } = useWebSocket({
    topic: "all",
    onMessage: handleMessage,
    id: "multi-user-sync",
  })

  useEffect(() => {
    setConnected(connected)
  }, [connected, setConnected])

  return { connected }
}

/**
 * Hook for components that want to react to sync events for specific entities.
 * Registers a dispatcher on mount and cleans up on unmount.
 */
export function useSyncListener(
  entityType: string,
  handler: (event: SyncEvent) => void,
) {
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  useEffect(() => {
    const unregister = registerSyncDispatcher(entityType, (event) => {
      handlerRef.current(event)
    })
    return unregister
  }, [entityType])
}
