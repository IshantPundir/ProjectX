import type {
  ProctoringFlaggedInterval,
  QuestionOut,
  RecordingTranscriptSegment,
} from '@/lib/api/reports'
import { statusBadgeMeta, type Tone } from '../report-format'

export interface TimelineMarker {
  seq: number
  questionId: string
  title: string
  statusBadge: QuestionOut['status_badge']
  tone: Tone
  askedAtMs: number | null
  thumbnailUrl: string | null
  /** 0–100 position on the track, or null when unknown (filmstrip still shows it). */
  positionPct: number | null
}

export interface FlagMarker {
  kind: string
  startMs: number
  endMs: number
  confidence: number
  thumbnailUrl: string | null
  positionPct: number
}

// Mirrors the backend's select_flag_targets severity ordering.
const FLAG_SEVERITY: Record<string, number> = {
  multiple_faces: 3,
  off_screen_sustained: 2,
  reading_sweep: 1,
  down_glance: 0,
}

function pct(ms: number, durationMs: number): number | null {
  if (!durationMs || durationMs <= 0) return null
  return Math.min(100, Math.max(0, (ms / durationMs) * 100))
}

export function buildQuestionMarkers(
  questions: QuestionOut[],
  durationMs: number,
): TimelineMarker[] {
  return questions.map((q) => ({
    seq: q.seq,
    questionId: q.question_id,
    title: q.title,
    statusBadge: q.status_badge,
    tone: statusBadgeMeta(q.status_badge).tone,
    askedAtMs: q.asked_at_ms,
    thumbnailUrl: q.thumbnail_url,
    positionPct: q.asked_at_ms == null ? null : pct(q.asked_at_ms, durationMs),
  }))
}

export function buildFlagMarkers(
  flagged: ProctoringFlaggedInterval[],
  durationMs: number,
  topN: number,
): FlagMarker[] {
  const ranked = [...flagged].sort((a, b) => {
    const sev = (FLAG_SEVERITY[b.kind] ?? 0) - (FLAG_SEVERITY[a.kind] ?? 0)
    if (sev !== 0) return sev
    const conf = (b.confidence ?? 0) - (a.confidence ?? 0)
    if (conf !== 0) return conf
    return (a.start_ms ?? 0) - (b.start_ms ?? 0)
  })
  return ranked.slice(0, Math.max(0, topN)).map((f) => ({
    kind: f.kind,
    startMs: f.start_ms,
    endMs: f.end_ms,
    confidence: f.confidence,
    thumbnailUrl: f.thumbnail_url ?? null,
    positionPct: pct(f.start_ms, durationMs) ?? 0,
  }))
}

export function densityBuckets(
  flagged: ProctoringFlaggedInterval[],
  durationMs: number,
  buckets: number,
): number[] {
  const out = new Array<number>(buckets).fill(0)
  if (!durationMs || durationMs <= 0) return out
  const span = durationMs / buckets
  for (const f of flagged) {
    const from = Math.max(0, Math.floor(f.start_ms / span))
    const to = Math.min(buckets - 1, Math.floor((f.end_ms - 1) / span))
    for (let i = from; i <= to; i++) out[i] += 1
  }
  const max = Math.max(1, ...out)
  return out.map((v) => v / max)
}

export function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}

/** Perceptual brightening of a normalized density so a single hit stays visible
 * on dark glass. g < 1 lifts small values; 0 and 1 are fixed points. */
export function gamma(v: number, g = 0.45): number {
  return Math.pow(clamp01(v), g)
}

/** densityBuckets restricted to a set of flag kinds (one proctoring sub-lane). */
export function densityBucketsForKinds(
  flagged: ProctoringFlaggedInterval[],
  durationMs: number,
  buckets: number,
  kinds: string[],
): number[] {
  const set = new Set(kinds)
  return densityBuckets(
    flagged.filter((f) => set.has(f.kind)),
    durationMs,
    buckets,
  )
}

/** The latest question whose asked_at_ms <= currentMs (markers with null are ignored). */
export function activeQuestionId(markers: TimelineMarker[], currentMs: number): string | null {
  let id: string | null = null
  let best = -1
  for (const m of markers) {
    if (m.askedAtMs == null) continue
    if (m.askedAtMs <= currentMs && m.askedAtMs > best) {
      best = m.askedAtMs
      id = m.questionId
    }
  }
  return id
}

/** Index of the last transcript segment whose t_ms <= currentMs (-1 before the first). */
export function activeSegmentIndex(
  segments: RecordingTranscriptSegment[],
  currentMs: number,
): number {
  let idx = -1
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].t_ms <= currentMs) idx = i
    else break
  }
  return idx
}
