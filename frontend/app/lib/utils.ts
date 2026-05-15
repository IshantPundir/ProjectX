import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Compact relative-time formatter — "today", "Nd ago", "Nmo ago".
 * Used by the Roles list and Tracker landing card. The buckets are
 * deliberately coarse — the Tracker UI doesn't surface anything sub-day
 * resolution, and this matches the existing /jobs presentation.
 */
export function postedAgo(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  const days = Math.floor((now - then) / (1000 * 60 * 60 * 24))
  if (days === 0) return 'today'
  if (days === 1) return '1d ago'
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months === 1) return '1mo ago'
  return `${months}mo ago`
}
