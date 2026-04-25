import type { SignalItem } from '@/lib/api/jobs'

export function needsReview(s: SignalItem): boolean {
  return s.source === 'ai_inferred' && s.weight < 2
}
