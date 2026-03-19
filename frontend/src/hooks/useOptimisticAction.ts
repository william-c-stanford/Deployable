/**
 * useOptimisticAction Hook
 *
 * Provides a clean pattern for optimistic updates in any component.
 * Wraps an API call with:
 * 1. Immediate optimistic state application
 * 2. Server confirmation tracking
 * 3. Automatic rollback on failure
 * 4. Toast notifications for success/failure
 */
import { useCallback, useRef } from "react"
import { optimisticAction } from "@/stores/syncStore"
import { toast } from "@/components/ui/toast"

interface OptimisticActionOptions<T> {
  /** Entity type for version tracking */
  entityType: string
  /** Entity ID */
  entityId: string
  /** Function to get current state */
  getCurrentState: () => T
  /** Function to apply optimistic state immediately */
  applyOptimistic: (state: T) => void
  /** Server action to execute */
  serverAction: () => Promise<any>
  /** Callback on success */
  onSuccess?: (response: any) => void
  /** Success toast message */
  successMessage?: string
  /** Whether to show success toast (default: true) */
  showSuccessToast?: boolean
}

export function useOptimisticAction() {
  const pendingRef = useRef<Set<string>>(new Set())

  const execute = useCallback(async <T>(options: OptimisticActionOptions<T>) => {
    const {
      entityType,
      entityId,
      getCurrentState,
      applyOptimistic,
      serverAction,
      onSuccess,
      successMessage,
      showSuccessToast = true,
    } = options

    // Prevent duplicate submissions
    const key = `${entityType}:${entityId}`
    if (pendingRef.current.has(key)) {
      toast("warning", "Action already in progress", { duration: 2000 })
      return { success: false }
    }

    pendingRef.current.add(key)

    const previousState = getCurrentState()
    const optimisticState = options.getCurrentState() // Re-read for freshness

    // Apply optimistic update immediately
    applyOptimistic(optimisticState)

    try {
      const result = await optimisticAction(
        entityType,
        entityId,
        previousState,
        optimisticState,
        serverAction,
        (response) => {
          onSuccess?.(response)
          if (showSuccessToast && successMessage) {
            toast("success", successMessage, { duration: 3000 })
          }
        },
        (prevState) => {
          // Rollback
          applyOptimistic(prevState)
        },
      )

      return result
    } finally {
      pendingRef.current.delete(key)
    }
  }, [])

  return { execute }
}

/**
 * Simpler variant: just wraps an async action with optimistic state tracking
 * and automatic toast notifications.
 */
export function useSimpleOptimistic() {
  const execute = useCallback(
    async <T>({
      label,
      action,
      onSuccess,
      onError,
    }: {
      label: string
      action: () => Promise<T>
      onSuccess?: (result: T) => void
      onError?: (err: any) => void
    }): Promise<T | null> => {
      try {
        const result = await action()
        toast("success", label, { duration: 3000 })
        onSuccess?.(result)
        return result
      } catch (err: any) {
        toast("error", `Failed: ${label}`, {
          description: err?.response?.data?.detail || err?.message || "Unknown error",
          duration: 5000,
        })
        onError?.(err)
        return null
      }
    },
    [],
  )

  return { execute }
}
