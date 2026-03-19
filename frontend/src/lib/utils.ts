import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"
import { format } from "date-fns"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(date: string | Date): string {
  if (!date) return ""
  return format(new Date(date), "MMM d, yyyy")
}

export function getStatusColor(status: string): string {
  const colors: Record<string, string> = {
    active: "text-green-500",
    Active: "text-green-500",
    completed: "text-blue-500",
    Completed: "text-blue-500",
    pending: "text-yellow-500",
    Pending: "text-yellow-500",
    cancelled: "text-red-500",
    Cancelled: "text-red-500",
    "in-progress": "text-blue-400",
    "In Progress": "text-blue-400",
    draft: "text-gray-500",
    Draft: "text-gray-500",
  }
  return colors[status] || "text-muted-foreground"
}

export function getProgressColor(progress: number): string {
  if (progress >= 80) return "text-green-500"
  if (progress >= 50) return "text-yellow-500"
  if (progress >= 25) return "text-orange-500"
  return "text-red-500"
}
