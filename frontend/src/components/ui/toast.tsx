/**
 * Toast notification system with user attribution.
 *
 * Shows who triggered each change (e.g., "Sarah approved recommendation")
 * Supports multiple toast types: success, error, warning, info, sync
 * Stacks multiple toasts with auto-dismiss
 */
import { create } from "zustand"
import { useEffect, useCallback } from "react"
import { X, Check, AlertTriangle, Info, RefreshCw, User } from "lucide-react"
import { cn } from "@/lib/utils"

// Toast types
export type ToastType = "success" | "error" | "warning" | "info" | "sync"

export interface Toast {
  id: string
  type: ToastType
  title: string
  description?: string
  /** Who triggered the action (for multi-user attribution) */
  actor?: {
    name: string
    role: string
    userId: string
  }
  /** Auto-dismiss delay in ms. 0 = persistent. Default 5000. */
  duration?: number
  /** Timestamp for ordering */
  createdAt: number
  /** Whether this toast is currently animating out */
  dismissing?: boolean
}

interface ToastStore {
  toasts: Toast[]
  addToast: (toast: Omit<Toast, "id" | "createdAt">) => string
  removeToast: (id: string) => void
  dismissToast: (id: string) => void
  clearAll: () => void
}

let toastCounter = 0

export const useToastStore = create<ToastStore>((set, get) => ({
  toasts: [],

  addToast: (toast) => {
    const id = `toast-${++toastCounter}-${Date.now()}`
    const newToast: Toast = {
      ...toast,
      id,
      createdAt: Date.now(),
      duration: toast.duration ?? 5000,
    }

    set((s) => ({
      toasts: [...s.toasts, newToast].slice(-8), // Max 8 toasts
    }))

    // Auto-dismiss
    if (newToast.duration && newToast.duration > 0) {
      setTimeout(() => get().dismissToast(id), newToast.duration)
    }

    return id
  },

  dismissToast: (id) => {
    set((s) => ({
      toasts: s.toasts.map((t) =>
        t.id === id ? { ...t, dismissing: true } : t
      ),
    }))
    // Remove after animation
    setTimeout(() => get().removeToast(id), 300)
  },

  removeToast: (id) => {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
  },

  clearAll: () => set({ toasts: [] }),
}))

// Convenience function for use outside React components
export function toast(
  type: ToastType,
  title: string,
  options?: Partial<Omit<Toast, "id" | "createdAt" | "type" | "title">>
): string {
  return useToastStore.getState().addToast({ type, title, ...options })
}

// Toast icon mapping
function ToastIcon({ type }: { type: ToastType }) {
  switch (type) {
    case "success":
      return <Check className="h-4 w-4 text-emerald-400" />
    case "error":
      return <AlertTriangle className="h-4 w-4 text-red-400" />
    case "warning":
      return <AlertTriangle className="h-4 w-4 text-amber-400" />
    case "info":
      return <Info className="h-4 w-4 text-blue-400" />
    case "sync":
      return <RefreshCw className="h-4 w-4 text-violet-400" />
  }
}

// Single toast item component
function ToastItem({ toast: t, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  return (
    <div
      className={cn(
        "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border bg-popover p-4 shadow-lg transition-all duration-300",
        t.dismissing
          ? "translate-x-full opacity-0"
          : "translate-x-0 opacity-100",
        t.type === "error" && "border-red-500/30",
        t.type === "success" && "border-emerald-500/30",
        t.type === "warning" && "border-amber-500/30",
        t.type === "sync" && "border-violet-500/30",
      )}
    >
      <div className="mt-0.5">
        <ToastIcon type={t.type} />
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-foreground">{t.title}</p>
        {t.description && (
          <p className="mt-1 text-xs text-muted-foreground">{t.description}</p>
        )}
        {t.actor && (
          <div className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
            <User className="h-3 w-3" />
            <span>
              {t.actor.name}
              <span className="text-muted-foreground/60 ml-1">
                ({t.actor.role})
              </span>
            </span>
          </div>
        )}
      </div>

      <button
        onClick={() => onDismiss(t.id)}
        className="mt-0.5 rounded-md p-1 text-muted-foreground hover:text-foreground transition-colors"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  )
}

/**
 * Toast container — renders all active toasts.
 * Place this once in your app layout (e.g., AppLayout).
 */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const dismissToast = useToastStore((s) => s.dismissToast)

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col-reverse gap-2 pointer-events-none">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={dismissToast} />
      ))}
    </div>
  )
}
