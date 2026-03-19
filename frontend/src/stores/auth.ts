/**
 * Backward-compatible re-export from the unified authStore.
 *
 * All consumers should eventually import from '@/stores/authStore' directly,
 * but this file keeps existing imports working without changes.
 */
export { useAuthStore } from "./authStore";
export type { AuthState, Role } from "./authStore";
