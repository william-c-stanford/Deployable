/**
 * Domain Sync Dispatchers
 *
 * Connects multi-user sync events to domain-specific Zustand stores.
 * When a sync event arrives via WebSocket, it updates the appropriate store
 * so all connected users see changes in real-time.
 */
import { useEffect } from "react"
import { registerSyncDispatcher } from "@/hooks/useSyncWebSocket"
import type { SyncEvent } from "@/stores/syncStore"
import { useDashboardStore } from "@/stores/dashboardStore"
import { useTechnicianStore } from "@/stores/technicianStore"
import { useProjectStore } from "@/stores/projectStore"
import { useAgentInboxStore } from "@/stores/agentInboxStore"

/**
 * Register all domain sync dispatchers.
 * Call this once from the app layout.
 */
export function useDomainSync() {
  useEffect(() => {
    const unregisters: (() => void)[] = []

    // --- Recommendation sync ---
    unregisters.push(
      registerSyncDispatcher("recommendation", (event: SyncEvent) => {
        const store = useAgentInboxStore.getState()

        switch (event.event_type) {
          case "recommendation.created":
            // Add new recommendation to inbox
            if (event.data && typeof store.fetchRecommendations === "function") {
              store.fetchRecommendations()
            }
            break

          case "recommendation.approved":
          case "recommendation.rejected":
          case "recommendation.dismissed":
            // Update recommendation status in store
            if (event.data?.id && typeof store.fetchRecommendations === "function") {
              store.fetchRecommendations()
            }
            break
        }
      }),
    )

    // --- Technician sync ---
    unregisters.push(
      registerSyncDispatcher("technician", (event: SyncEvent) => {
        const { event_type, data } = event

        if (
          event_type === "technician.status_changed" ||
          event_type === "technician.availability_changed"
        ) {
          // Re-fetch technician list to reflect changes
          const store = useTechnicianStore.getState()
          if (typeof store.fetchTechnicians === "function") {
            store.fetchTechnicians()
          }
        }
      }),
    )

    // --- Dashboard sync ---
    unregisters.push(
      registerSyncDispatcher("dashboard", (event: SyncEvent) => {
        // Refresh dashboard KPIs when any significant change occurs
        const store = useDashboardStore.getState()
        if (typeof store.refreshKPIs === "function") {
          store.refreshKPIs()
        }
      }),
    )

    // --- Preference Rule sync ---
    unregisters.push(
      registerSyncDispatcher("preference_rule", (event: SyncEvent) => {
        const store = useAgentInboxStore.getState()

        // When a preference rule changes, refetch both rules and recommendations
        // The backend re-scores all pending recs and pushes recommendation.list_refresh
        // but we also refetch rules to keep the rules tab in sync
        if (typeof store.fetchRules === "function") {
          store.fetchRules()
        }
        if (typeof store.fetchRecommendations === "function") {
          store.fetchRecommendations()
        }
      }),
    )

    // --- Assignment sync ---
    unregisters.push(
      registerSyncDispatcher("assignment", (event: SyncEvent) => {
        // Refresh projects to show updated assignments
        const store = useProjectStore.getState()
        if (event.data?.project_id) {
          // Could selectively refresh, but for now refetch all
          if (typeof (store as any).fetchProjects === "function") {
            ;(store as any).fetchProjects()
          }
        }
      }),
    )

    // --- Wildcard: refresh dashboard on any entity change ---
    unregisters.push(
      registerSyncDispatcher("*", (event: SyncEvent) => {
        // Debounced dashboard refresh on any entity change
        if (event.event_type === "sync.refresh_requested") {
          // Full refresh requested by another client
          useDashboardStore.getState().fetchDashboard()
          useTechnicianStore.getState().fetchTechnicians?.()
        }
      }),
    )

    return () => {
      for (const unregister of unregisters) {
        unregister()
      }
    }
  }, [])
}
