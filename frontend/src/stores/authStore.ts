/**
 * Unified Auth Store
 *
 * Handles:
 * - JWT token management (localStorage persistence)
 * - Role switching via backend API call
 * - WebSocket lifecycle on role switch (teardown → reconnect)
 * - Store resets to reload role-scoped data
 * - Demo initialization with default ops user
 */
import { create } from "zustand";
import type { User } from "@/types/auth";
import api from "@/lib/api";
import { wsManager } from "@/lib/wsManager";

const TOKEN_KEY = "deployable_token";

export type Role = "ops" | "technician" | "partner";

export interface AuthState {
  // Core auth data
  user: User | null;
  token: string | null;
  role: Role;
  userId: string | null;
  userName: string | null;
  scopedTo: string | null;

  // UI state
  isLoading: boolean;
  isSwitching: boolean;

  // Actions
  setUser: (user: User, token: string) => void;
  switchRole: (
    role: Role,
    userId: string,
    userName: string,
    scopedTo?: string | null,
  ) => Promise<void>;
  initialize: () => void;
  logout: () => void;
  setAuth: (
    token: string,
    role: string,
    userId: string,
    userName: string,
    scopedTo?: string,
  ) => void;

  // Store reset registry — other stores register their reset functions
  _resetCallbacks: Array<() => void>;
  registerStoreReset: (cb: () => void) => () => void;
}

function decodeJwtPayload(token: string): Record<string, any> | null {
  try {
    const base64 = token.split(".")[1];
    const payload = atob(base64.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(payload);
  } catch {
    return null;
  }
}

function roleFromPath(): Role {
  const path = window.location.pathname;
  if (path.startsWith("/tech")) return "technician";
  if (path.startsWith("/partner")) return "partner";
  return "ops";
}

function defaultRouteForRole(role: Role): string {
  switch (role) {
    case "technician":
      return "/tech/portal";
    case "partner":
      return "/partner/portal";
    case "ops":
    default:
      return "/ops/dashboard";
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  // Initial state
  user: null,
  token: null,
  role: "ops",
  userId: null,
  userName: null,
  scopedTo: null,
  isLoading: false,
  isSwitching: false,
  _resetCallbacks: [],

  /**
   * Register a store reset callback. Returns an unregister function.
   * Other stores (dashboard, partner, techPortal, etc.) call this on mount
   * so their data is cleared on role switch.
   */
  registerStoreReset: (cb: () => void) => {
    set((s) => ({ _resetCallbacks: [...s._resetCallbacks, cb] }));
    return () => {
      set((s) => ({
        _resetCallbacks: s._resetCallbacks.filter((fn) => fn !== cb),
      }));
    };
  },

  /**
   * Set user + token directly (used by initialize and direct setAuth).
   */
  setUser: (user: User, token: string) => {
    localStorage.setItem(TOKEN_KEY, token);
    wsManager.setToken(token);
    set({
      user,
      token,
      role: (user.role as Role) || "ops",
      userId: user.user_id,
      userName: user.name,
      scopedTo: user.scoped_to || null,
    });
  },

  /**
   * Backward-compatible setAuth used by existing AppLayout role switcher.
   * Calls the backend for a real JWT, then updates state.
   */
  setAuth: (
    token: string,
    role: string,
    userId: string,
    userName: string,
    scopedTo?: string,
  ) => {
    // Use switchRole internally for the full lifecycle
    get().switchRole(role as Role, userId, userName, scopedTo);
  },

  /**
   * Full role switch lifecycle:
   * 1. Call backend to get a new JWT
   * 2. Tear down existing WebSocket connections
   * 3. Replace stored JWT
   * 4. Re-establish WebSocket connections with new token
   * 5. Reset all role-scoped stores
   * 6. Navigate to the appropriate route
   */
  switchRole: async (
    role: Role,
    userId: string,
    userName: string,
    scopedTo?: string | null,
  ) => {
    set({ isSwitching: true, isLoading: true });

    let newToken: string;

    try {
      // Step 1: Call backend to generate a new JWT for this role
      const res = await api.post("/auth/demo-token", {
        user_id: userId,
        role,
        name: userName,
        scoped_to: scopedTo || null,
      });
      newToken = res.data.access_token;
    } catch {
      // Fallback: create a minimal client-side token for demo resilience
      // This ensures the UI still works even if the backend is down
      const payload = btoa(
        JSON.stringify({
          sub: userId,
          user_id: userId,
          role,
          name: userName,
          scoped_to: scopedTo || null,
          exp: Math.floor(Date.now() / 1000) + 86400,
        }),
      );
      newToken = `eyJhbGciOiJIUzI1NiJ9.${payload}.demo`;
    }

    // Step 2: Tear down ALL existing WebSocket connections
    wsManager.teardownAll();

    // Step 3: Replace stored JWT
    localStorage.setItem(TOKEN_KEY, newToken);

    // Step 4: Update the WebSocket manager with the new token
    wsManager.setToken(newToken);

    // Build user object
    const user: User = {
      user_id: userId,
      name: userName,
      role: role,
      scoped_to: scopedTo || null,
    };

    // Step 5: Update auth state
    set({
      user,
      token: newToken,
      role,
      userId,
      userName,
      scopedTo: scopedTo || null,
      isLoading: false,
      isSwitching: false,
    });

    // Step 6: Reset all role-scoped stores so data reloads
    const callbacks = get()._resetCallbacks;
    for (const cb of callbacks) {
      try {
        cb();
      } catch {
        // Don't let one failing reset block others
      }
    }

    // Step 7: Re-establish WebSocket connections with new token
    // Small delay to let state propagate before reconnecting
    setTimeout(() => {
      wsManager.reconnectAll();
    }, 100);

    // Step 8: Navigate to the appropriate route for the new role
    const targetRoute = defaultRouteForRole(role);
    // Use window.location for a clean navigation that triggers route-level data fetches
    window.location.href = targetRoute;
  },

  /**
   * Initialize auth state from localStorage on app startup.
   * Decodes existing JWT or falls back to demo ops user.
   */
  initialize: () => {
    const storedToken = localStorage.getItem(TOKEN_KEY);

    if (storedToken) {
      const payload = decodeJwtPayload(storedToken);
      if (payload) {
        const user: User = {
          user_id: payload.sub || payload.user_id || "demo-user",
          name: payload.name || "Demo User",
          role: payload.role || "ops",
          scoped_to: payload.scoped_to || null,
        };

        wsManager.setToken(storedToken);

        set({
          user,
          token: storedToken,
          role: (user.role as Role) || "ops",
          userId: user.user_id,
          userName: user.name,
          scopedTo: user.scoped_to || null,
        });
        return;
      }
      // Invalid token, remove it
      localStorage.removeItem(TOKEN_KEY);
    }

    // No valid token — default to ops for demo
    const defaultRole = roleFromPath();
    const defaultUser: User = {
      user_id: defaultRole === "ops" ? "ops-1" : defaultRole === "technician" ? "tech-1" : "partner-lumen",
      name: defaultRole === "ops" ? "Demo Ops User" : defaultRole === "technician" ? "Demo Technician" : "Demo Partner",
      role: defaultRole,
      scoped_to: defaultRole === "partner" ? "partner_lumen" : null,
    };

    set({
      user: defaultUser,
      role: defaultRole,
      userId: defaultUser.user_id,
      userName: defaultUser.name,
      scopedTo: defaultUser.scoped_to || null,
    });

    // Auto-generate a demo token
    api
      .post("/auth/demo-token", {
        user_id: defaultUser.user_id,
        role: defaultUser.role,
      })
      .then((res) => {
        const token = res.data.access_token;
        localStorage.setItem(TOKEN_KEY, token);
        wsManager.setToken(token);
        set({ token });
      })
      .catch(() => {
        // Backend not available yet, continue with demo headers
      });
  },

  /**
   * Logout: clear all auth state, tear down WebSocket connections.
   */
  logout: () => {
    wsManager.teardownAll();
    localStorage.removeItem(TOKEN_KEY);

    set({
      user: null,
      token: null,
      role: "ops",
      userId: null,
      userName: null,
      scopedTo: null,
    });
  },
}));
