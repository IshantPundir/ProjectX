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
  /** 0–100 start position on the track. */
  positionPct: number
  /** 0–100 span width on the track (end − start), so the flag renders as a band
   *  covering the violation's full duration, not a single tick. */
  widthPct: number
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

/**
 * Rail view of the question markers (the vertical question pills): drops
 * never-attempted questions and orders them as they were actually asked — by
 * `asked_at_ms` ascending, with unknown timings sorted last and ties broken by
 * `seq`. Display-only; the scrubber + active tracking still use the full set.
 */
export function buildRailMarkers(markers: TimelineMarker[]): TimelineMarker[] {
  return markers
    .filter((m) => m.statusBadge !== 'not_attempted')
    .slice()
    .sort((a, b) => {
      const aa = a.askedAtMs ?? Number.POSITIVE_INFINITY
      const bb = b.askedAtMs ?? Number.POSITIVE_INFINITY
      if (aa !== bb) return aa - bb
      return a.seq - b.seq
    })
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
  return ranked.slice(0, Math.max(0, topN)).map((f) => {
    const startPct = pct(f.start_ms, durationMs) ?? 0
    const endPct = pct(f.end_ms, durationMs) ?? startPct
    return {
      kind: f.kind,
      startMs: f.start_ms,
      endMs: f.end_ms,
      confidence: f.confidence,
      thumbnailUrl: f.thumbnail_url ?? null,
      positionPct: startPct,
      widthPct: Math.max(0, endPct - startPct),
    }
  })
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

/**
 * The thumbnail URL of the question whose `asked_at_ms` sits nearest the
 * recording midpoint — used as the <video> poster so the still frame is a real
 * mid-interview moment instead of the (typically blurry) opening frame.
 *
 * Only questions with BOTH an `asked_at_ms` and a `thumbnail_url` qualify.
 * Returns null when none qualify or the duration is missing/zero.
 */
export function pickPosterUrl(questions: QuestionOut[], durationMs: number): string | null {
  if (!durationMs || durationMs <= 0 || !Number.isFinite(durationMs)) return null
  const midMs = durationMs / 2
  let best: { url: string; dist: number } | null = null
  for (const q of questions) {
    if (q.asked_at_ms == null || q.thumbnail_url == null) continue
    const dist = Math.abs(q.asked_at_ms - midMs)
    if (best === null || dist < best.dist) best = { url: q.thumbnail_url, dist }
  }
  return best?.url ?? null
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
