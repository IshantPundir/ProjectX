import type { Confidence, Severity, StatusBadge, Verdict } from '@/lib/api/reports'

export type Tone = 'ok' | 'caution' | 'danger' | 'neutral' | 'human' | 'accent'

/** Ink (text/stroke) color var per tone. Pastels (`-fill`) never carry text. */
export const TONE_INK: Record<Tone, string> = {
  ok: 'var(--px-ok)',
  caution: 'var(--px-caution)',
  danger: 'var(--px-danger)',
  neutral: 'var(--px-fg-4)',
  human: 'var(--px-human)',
  accent: 'var(--px-accent)',
}

/** Saturated fill var per tone (solid chip backgrounds / gauge rings). */
export const TONE_FILL: Record<Tone, string> = {
  ok: 'var(--px-ok-fill)',
  caution: 'var(--px-caution-fill)',
  danger: 'var(--px-danger-fill)',
  neutral: 'var(--px-surface-3)',
  human: 'var(--px-human-fill)',
  accent: 'var(--px-accent)',
}

/** Soft tint var per tone (card backgrounds). */
export const TONE_BG: Record<Tone, string> = {
  ok: 'var(--px-ok-bg)',
  caution: 'var(--px-caution-bg)',
  danger: 'var(--px-danger-bg)',
  neutral: 'var(--px-surface-2)',
  human: 'var(--px-human-bg)',
  accent: 'var(--px-accent-tint)',
}

/**
 * @deprecated Backend scores are already 0–10. Use `formatTen` instead.
 * Kept only for components that haven't been migrated yet.
 * 0–100 integer → "X.X" out of ten; null stays null (never a zero).
 */
export function scoreToTen(score: number | null): string | null {
  if (score === null || score === undefined) return null
  return (score / 10).toFixed(1)
}

/** Format an already-0–10 score as "X.X"; null stays null (never a zero). */
export function formatTen(score: number | null): string | null {
  if (score === null || score === undefined) return null
  return score.toFixed(1)
}

/** ms → "mm:ss". */
export function formatTimestamp(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export interface VerdictMeta { label: string; tone: Tone }
const VERDICT_META: Record<Verdict, VerdictMeta> = {
  advance: { label: 'Recommended', tone: 'ok' },
  borderline: { label: 'Borderline', tone: 'human' },
  reject: { label: 'Not Recommended', tone: 'danger' },
}
export function verdictMeta(v: Verdict): VerdictMeta {
  return VERDICT_META[v]
}

/** Tier tone from a 0–10 score, aligned to backend verdict thresholds
 *  (ADVANCE_THRESHOLD 6.5 / REJECT_THRESHOLD 4.0 on a 0–10 scale). */
export function scoreBandTone(score: number | null): Tone {
  if (score === null || score === undefined) return 'neutral'
  if (score >= 6.5) return 'ok'
  if (score >= 4.0) return 'caution'
  return 'danger'
}

const _TONES: readonly Tone[] = ['ok', 'caution', 'danger', 'neutral', 'human', 'accent']

/** Validate a backend-provided tone string; unknown → neutral. */
export function tierTone(tone: string): Tone {
  return (_TONES as readonly string[]).includes(tone) ? (tone as Tone) : 'neutral'
}

export interface BadgeMeta { label: string; tone: Tone }

const SEVERITY_META: Record<Severity, BadgeMeta> = {
  deal_breaker: { label: 'Deal-breaker', tone: 'danger' },
  major: { label: 'Major', tone: 'caution' },
  moderate: { label: 'Moderate', tone: 'neutral' },
}
export function severityMeta(s: Severity): BadgeMeta { return SEVERITY_META[s] }

const STATUS_BADGE_META: Record<StatusBadge, BadgeMeta> = {
  passed: { label: 'Passed', tone: 'ok' },
  partial: { label: 'Partial', tone: 'caution' },
  failed_required: { label: 'Failed — required skill', tone: 'danger' },
  not_demonstrated: { label: 'Not demonstrated', tone: 'danger' },
  not_attempted: { label: 'Not attempted', tone: 'neutral' },
  not_fully_assessed: { label: 'Not fully assessed', tone: 'neutral' },
}
export function statusBadgeMeta(b: StatusBadge): BadgeMeta { return STATUS_BADGE_META[b] }

export function confidenceLabel(c: Confidence): string {
  return { high: 'High', medium: 'Medium', low: 'Low' }[c]
}
