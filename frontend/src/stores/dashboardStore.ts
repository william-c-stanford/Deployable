import { create } from "zustand";
import type { DashboardData, KPICard, SuggestedAction, ActivityEntry } from "@/types/dashboard";
import api from "@/lib/api";

interface DashboardState {
  kpiCards: KPICard[];
  suggestedActions: SuggestedAction[];
  recentActivity: ActivityEntry[];
  isLoading: boolean;
  isLoadingActions: boolean;
  error: string | null;

  /** IDs of actions that arrived via WebSocket (for "new" badge animation) */
  newActionIds: Set<string>;

  /** IDs of actions that have been dismissed locally (optimistic) */
  dismissedActionIds: Set<string>;

  /** Timestamp of last WS-driven refresh */
  lastWsRefresh: number;

  /** Whether a WS update is being applied (for pulse animation) */
  isWsUpdating: boolean;

  fetchDashboard: () => Promise<void>;
  refreshKPIs: () => Promise<void>;
  fetchSuggestedActions: () => Promise<void>;

  /** Handle a single new suggested action pushed via WebSocket */
  addSuggestedAction: (action: SuggestedAction) => void;

  /** Handle a full refresh of suggested actions from WS (batch) */
  replaceSuggestedActions: (actions: SuggestedAction[]) => void;

  /** Remove a suggested action from WS event */
  removeSuggestedAction: (actionId: string) => void;

  /** Update a suggested action from WS event */
  updateSuggestedAction: (actionId: string, updates: Partial<SuggestedAction>) => void;

  /** Dismiss an action (optimistic + API call) */
  dismissAction: (actionId: string) => Promise<void>;

  /** Mark an action as "acted on" (navigate + dismiss) */
  actOnAction: (actionId: string) => Promise<void>;

  /** Clear the "new" marker from an action */
  clearNewMarker: (actionId: string) => void;

  /** Clear all "new" markers */
  clearAllNewMarkers: () => void;

  /** Signal that WS update is happening (for animation) */
  setWsUpdating: (updating: boolean) => void;
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  kpiCards: [],
  suggestedActions: [],
  recentActivity: [],
  isLoading: false,
  isLoadingActions: false,
  error: null,
  newActionIds: new Set<string>(),
  dismissedActionIds: new Set<string>(),
  lastWsRefresh: 0,
  isWsUpdating: false,

  fetchDashboard: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await api.get<DashboardData>("/dashboard");
      set({
        kpiCards: res.data.kpi_cards,
        suggestedActions: res.data.suggested_actions,
        recentActivity: res.data.recent_activity,
        isLoading: false,
        // Clear dismissed set when fetching fresh data
        dismissedActionIds: new Set<string>(),
      });
    } catch (err: any) {
      set({ error: err.message || "Failed to load dashboard", isLoading: false });
    }
  },

  refreshKPIs: async () => {
    try {
      const res = await api.get<KPICard[]>("/dashboard/kpis");
      set({ kpiCards: res.data });
    } catch {
      // Silent fail on refresh
    }
  },

  fetchSuggestedActions: async () => {
    set({ isLoadingActions: true });
    try {
      const res = await api.get<SuggestedAction[]>("/dashboard/suggested-actions");
      set({
        suggestedActions: res.data,
        isLoadingActions: false,
        dismissedActionIds: new Set<string>(),
      });
    } catch {
      // Fall back to full dashboard fetch
      try {
        const res = await api.get<DashboardData>("/dashboard");
        set({
          suggestedActions: res.data.suggested_actions,
          isLoadingActions: false,
        });
      } catch {
        set({ isLoadingActions: false });
      }
    }
  },

  addSuggestedAction: (action) => {
    set((state) => {
      // Don't add duplicates
      const exists = state.suggestedActions.some((a) => a.id === action.id);
      if (exists) return state;

      const newIds = new Set(state.newActionIds);
      newIds.add(action.id);

      // Insert in priority order (higher priority first)
      const updated = [...state.suggestedActions, action].sort(
        (a, b) => (b.priority || 0) - (a.priority || 0)
      );

      return {
        suggestedActions: updated,
        newActionIds: newIds,
        lastWsRefresh: Date.now(),
      };
    });
  },

  replaceSuggestedActions: (actions) => {
    set((state) => {
      const existingIds = new Set(state.suggestedActions.map((a) => a.id));
      const newIds = new Set<string>();

      for (const action of actions) {
        if (!existingIds.has(action.id)) {
          newIds.add(action.id);
        }
      }

      return {
        suggestedActions: actions,
        newActionIds: newIds,
        dismissedActionIds: new Set<string>(),
        lastWsRefresh: Date.now(),
      };
    });
  },

  removeSuggestedAction: (actionId) => {
    set((state) => {
      const newIds = new Set(state.newActionIds);
      newIds.delete(actionId);
      return {
        suggestedActions: state.suggestedActions.filter((a) => a.id !== actionId),
        newActionIds: newIds,
      };
    });
  },

  updateSuggestedAction: (actionId, updates) => {
    set((state) => ({
      suggestedActions: state.suggestedActions.map((a) =>
        a.id === actionId ? { ...a, ...updates } : a
      ),
    }));
  },

  dismissAction: async (actionId) => {
    // Optimistic dismiss
    set((state) => {
      const dismissed = new Set(state.dismissedActionIds);
      dismissed.add(actionId);
      const newIds = new Set(state.newActionIds);
      newIds.delete(actionId);
      return { dismissedActionIds: dismissed, newActionIds: newIds };
    });

    try {
      await api.post(`/dashboard/suggested-actions/${actionId}/dismiss`);
    } catch {
      // Revert optimistic dismiss on failure
      set((state) => {
        const dismissed = new Set(state.dismissedActionIds);
        dismissed.delete(actionId);
        return { dismissedActionIds: dismissed };
      });
    }
  },

  actOnAction: async (actionId) => {
    // Mark as acted on (same as dismiss from API perspective)
    set((state) => {
      const dismissed = new Set(state.dismissedActionIds);
      dismissed.add(actionId);
      const newIds = new Set(state.newActionIds);
      newIds.delete(actionId);
      return { dismissedActionIds: dismissed, newActionIds: newIds };
    });

    try {
      await api.post(`/dashboard/suggested-actions/${actionId}/act`);
    } catch {
      // Silent fail — the action was already navigated to
    }
  },

  clearNewMarker: (actionId) => {
    set((state) => {
      const newIds = new Set(state.newActionIds);
      newIds.delete(actionId);
      return { newActionIds: newIds };
    });
  },

  clearAllNewMarkers: () => {
    set({ newActionIds: new Set<string>() });
  },

  setWsUpdating: (updating) => {
    set({ isWsUpdating: updating });
  },
}));
