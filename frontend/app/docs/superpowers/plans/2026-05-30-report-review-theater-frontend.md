# Report Review Theater — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the report page's small inline player with an immersive, light-glass **Review Theater** popup — a click-to-open modal whose centerpiece is a 3-layer session timeline (question filmstrip + node track + integrity heatmap lane) that lets a recruiter scan a session in seconds and click-to-seek any moment.

**Architecture:** A new `components/dashboard/reports/theater/` subtree. Pure timeline-model helpers (`timeline-model.ts`) turn the report/recording/proctoring data into positioned markers + density buckets (unit-tested). Presentational components (Filmstrip, NodeTrack, IntegrityLane, ThisMomentPanel, TheaterTopBar, TheaterStage) are driven by a small state hook (`useTheaterState`) that owns the video playhead + active selection. `ReviewTheater` composes them inside the existing `px` `Dialog` (portal + focus-trap + ESC + scroll-lock). The current `SessionPlayback` becomes a poster that opens the theater. All data comes from the existing `useSessionRecording` / `useSessionProctoring` hooks + the `report` prop — the backend contract (`QuestionOut.asked_at_ms`/`thumbnail_url`, flag `thumbnail_url`) is already live on `main`.

**Tech Stack:** Next.js 16 (App Router), TypeScript strict, Tailwind v4 + `--px-*` theme tokens, `@tanstack/react-query`, in-house `px/` primitives, Vitest + @testing-library/react + jsdom.

**Spec:** `backend/nexus/docs/superpowers/specs/2026-05-30-report-review-theater-design.md` (§3 UX, §6 component plan)

**Conventions**
- All paths below are under `frontend/app/`. Run tests: `npm run test -- <path>` (vitest). Type-check: `npm run type-check`. Lint: `npm run lint`.
- Reuse existing helpers — do NOT re-implement: `formatTimestamp`, `statusBadgeMeta`, `verdictMeta`, `scoreBandTone`, `tierTone`, `TONE_INK/FILL/BG`, `Tone` from `components/dashboard/reports/report-format.ts`; `ScoreGauge` from `./ScoreGauge`; `Dialog`/`DialogContent` from `@/components/px`.
- TypeScript strict: no `any`. Light/calm aesthetic only (no dark mode). Tokens from `app/theme.css` (`--px-*`), no raw hex where a token exists.
- Tests follow the composition convention: render with `renderWithProviders` from `tests/_utils/render.tsx`; assert real interactions (clicks → callbacks, active state), mock hook data at the module boundary with `vi.mock`.

---

## File Structure

**Create**
- `components/dashboard/reports/theater/timeline-model.ts` — pure data model: marker/flag/density builders + active-item resolution + `activeSegmentIndex` (moved here).
- `components/dashboard/reports/theater/theater.css` — glass panel + filmstrip/track/lane styles + backdrop override.
- `components/dashboard/reports/theater/Filmstrip.tsx` — question chapter cards (browse layer).
- `components/dashboard/reports/theater/NodeTrack.tsx` — scrubber + playhead + per-question nodes (seek layer).
- `components/dashboard/reports/theater/IntegrityLane.tsx` — proctoring density band + top-flag markers.
- `components/dashboard/reports/theater/SessionTimeline.tsx` — composes the three layers.
- `components/dashboard/reports/theater/ThisMomentPanel.tsx` — default decision summary / per-item detail.
- `components/dashboard/reports/theater/TheaterTopBar.tsx` — identity + gauges + risk/verdict chips + close.
- `components/dashboard/reports/theater/TheaterStage.tsx` — `<video>` + transport + seek API + playhead.
- `components/dashboard/reports/theater/useTheaterState.ts` — playhead + active selection + seek wiring.
- `components/dashboard/reports/theater/ReviewTheater.tsx` — the Dialog shell composing everything + transcript toggle.
- Tests under `tests/components/theater/`: `timeline-model.test.ts`, `Filmstrip.test.tsx`, `NodeTrack.test.tsx`, `IntegrityLane.test.tsx`, `ThisMomentPanel.test.tsx`, `TheaterTopBar.test.tsx`, `ReviewTheater.test.tsx`, `SessionPlaybackPoster.test.tsx`.

**Modify**
- `lib/api/reports.ts` — add `asked_at_ms` + `thumbnail_url` to `QuestionOut`; add `thumbnail_url` to `ProctoringFlaggedInterval`.
- `components/dashboard/reports/SessionPlayback.tsx` — becomes the poster that opens `ReviewTheater` (keeps `activeSegmentIndex` re-export for back-compat; the transcript rail moves into the theater).

---

## Task 1: API contract types

**Files:**
- Modify: `lib/api/reports.ts`, `tests/components/reports/_fixture.ts`
- Test: (type-only; verified by `npm run type-check`)

- [ ] **Step 1: Add the fields**

In `lib/api/reports.ts`, change the `QuestionOut` interface to:

```typescript
export interface QuestionOut {
  seq: number
  question_id: string
  title: string
  status_badge: StatusBadge
  status_tone: string
  question_text: string
  candidate_quote: string
  our_read: string
  /** ms since session start; null for legacy sessions (engine tagged it). */
  asked_at_ms: number | null
  /** Presigned R2 GET for the question's video frame; null until generated. */
  thumbnail_url: string | null
}
```

And change `ProctoringFlaggedInterval` to:

```typescript
export interface ProctoringFlaggedInterval {
  start_ms: number
  end_ms: number
  kind: string
  confidence: number
  /** Presigned R2 GET for the flag's video frame; present only for top flags. */
  thumbnail_url?: string | null
}
```

- [ ] **Step 2: Update the existing report fixture**

`tests/components/reports/_fixture.ts` builds `QuestionOut` literals; the two new required fields will fail type-check there. READ the file, and to EVERY `QuestionOut` object literal add `asked_at_ms: null` and `thumbnail_url: null` (place them after `our_read`). `ProctoringFlaggedInterval`'s `thumbnail_url` is optional, so flag fixtures need no change.

- [ ] **Step 3: Verify type-check passes**

Run: `npm run type-check`
Expected: PASS. If it still reports a `QuestionOut` literal missing the fields, you missed one in `_fixture.ts` (or another fixture) — add the two fields there too and re-run until clean.

- [ ] **Step 4: Commit**

```bash
git add lib/api/reports.ts tests/components/reports/_fixture.ts
git commit -m "feat(reports-api): asked_at_ms + thumbnail_url on QuestionOut, flag thumbnail_url"
```

---

## Task 2: Timeline model (pure helpers)

**Files:**
- Create: `components/dashboard/reports/theater/timeline-model.ts`
- Test: `tests/components/theater/timeline-model.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/timeline-model.test.ts
import { describe, expect, it } from 'vitest'

import type { ProctoringFlaggedInterval, QuestionOut } from '@/lib/api/reports'
import {
  activeQuestionId,
  activeSegmentIndex,
  buildFlagMarkers,
  buildQuestionMarkers,
  densityBuckets,
} from '@/components/dashboard/reports/theater/timeline-model'

function q(partial: Partial<QuestionOut>): QuestionOut {
  return {
    seq: 1, question_id: 'q1', title: 'Q', status_badge: 'passed', status_tone: 'ok',
    question_text: 'Q?', candidate_quote: 'a', our_read: '', asked_at_ms: null,
    thumbnail_url: null, ...partial,
  }
}

describe('buildQuestionMarkers', () => {
  it('positions a question by asked_at_ms / duration', () => {
    const [m] = buildQuestionMarkers([q({ asked_at_ms: 30_000 })], 120_000)
    expect(m.positionPct).toBeCloseTo(25)
    expect(m.tone).toBe('ok')
  })

  it('null asked_at_ms → null position (filmstrip-only)', () => {
    const [m] = buildQuestionMarkers([q({ asked_at_ms: null })], 120_000)
    expect(m.positionPct).toBeNull()
  })

  it('zero/absent duration → null position (no divide-by-zero)', () => {
    const [m] = buildQuestionMarkers([q({ asked_at_ms: 30_000 })], 0)
    expect(m.positionPct).toBeNull()
  })

  it('maps status_badge to tone via statusBadgeMeta', () => {
    const [m] = buildQuestionMarkers([q({ status_badge: 'failed_required' })], 1000)
    expect(m.tone).toBe('danger')
  })
})

describe('buildFlagMarkers', () => {
  const flags: ProctoringFlaggedInterval[] = [
    { kind: 'down_glance', start_ms: 100, end_ms: 200, confidence: 0.6 },
    { kind: 'multiple_faces', start_ms: 900, end_ms: 1000, confidence: 0.9, thumbnail_url: 'u' },
    { kind: 'off_screen_sustained', start_ms: 300, end_ms: 800, confidence: 0.65 },
  ]

  it('selects top-N by severity then confidence and positions them', () => {
    const out = buildFlagMarkers(flags, 1000, 2)
    expect(out.map((f) => f.kind)).toEqual(['multiple_faces', 'off_screen_sustained'])
    expect(out[0].positionPct).toBeCloseTo(90)
    expect(out[0].thumbnailUrl).toBe('u')
  })

  it('empty flags → empty', () => {
    expect(buildFlagMarkers([], 1000, 6)).toEqual([])
  })
})

describe('densityBuckets', () => {
  it('marks buckets covered by a flag interval as hot', () => {
    const flags: ProctoringFlaggedInterval[] = [
      { kind: 'off_screen_sustained', start_ms: 0, end_ms: 1000, confidence: 0.65 },
    ]
    const out = densityBuckets(flags, 4000, 4)
    expect(out).toHaveLength(4)
    expect(out[0]).toBeGreaterThan(0)
    expect(out[3]).toBe(0)
  })

  it('zero duration → all-zero buckets of the requested length', () => {
    expect(densityBuckets([], 0, 4)).toEqual([0, 0, 0, 0])
  })
})

describe('activeQuestionId', () => {
  it('returns the latest question whose asked_at_ms <= currentMs', () => {
    const markers = buildQuestionMarkers(
      [q({ question_id: 'q1', asked_at_ms: 1000 }), q({ question_id: 'q2', asked_at_ms: 5000 })],
      10_000,
    )
    expect(activeQuestionId(markers, 4000)).toBe('q1')
    expect(activeQuestionId(markers, 6000)).toBe('q2')
    expect(activeQuestionId(markers, 0)).toBeNull()
  })

  it('ignores markers with null asked_at_ms', () => {
    const markers = buildQuestionMarkers([q({ question_id: 'q1', asked_at_ms: null })], 10_000)
    expect(activeQuestionId(markers, 9999)).toBeNull()
  })
})

describe('activeSegmentIndex', () => {
  it('returns the last segment whose t_ms <= currentMs', () => {
    const segs = [{ role: 'agent', text: 'a', t_ms: 0 }, { role: 'c', text: 'b', t_ms: 1000 }]
    expect(activeSegmentIndex(segs, 500)).toBe(0)
    expect(activeSegmentIndex(segs, 1500)).toBe(1)
    expect(activeSegmentIndex(segs, -1)).toBe(-1)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/timeline-model.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `timeline-model.ts`**

```typescript
// components/dashboard/reports/theater/timeline-model.ts
import type {
  ProctoringFlaggedInterval,
  QuestionOut,
  RecordingTranscriptSegment,
} from '@/lib/api/reports'
import { scoreBandTone, statusBadgeMeta, type Tone } from '../report-format'

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/timeline-model.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/timeline-model.ts tests/components/theater/timeline-model.test.ts
git commit -m "feat(theater): pure timeline model (markers, flags, density, active item)"
```

---

## Task 3: theater.css

**Files:**
- Create: `components/dashboard/reports/theater/theater.css`
- Test: none (stylesheet; verified visually + by the component render tests that follow).

- [ ] **Step 1: Create the stylesheet**

```css
/* components/dashboard/reports/theater/theater.css
   Light, calm frosted-glass styling for the Review Theater. Tokens from app/theme.css. */

/* Light frosted backdrop when the theater dialog is open (scoped via :has). */
.px-dialog-backdrop:has(.theater-shell) {
  background: rgba(231, 238, 242, 0.55);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

.theater-shell {
  width: min(1160px, 95vw);
  height: min(760px, 92vh);
  max-width: 95vw;
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: linear-gradient(160deg, #f3f8fa, #e9f1f4);
  border-radius: 18px;
}

.theater-glass {
  background: rgba(255, 255, 255, 0.66);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 8px 30px rgba(20, 40, 60, 0.12);
}

.theater-card { cursor: pointer; transition: transform 0.15s ease, box-shadow 0.15s ease; }
.theater-card:hover { transform: translateY(-2px); }
.theater-card[data-active='true'] {
  outline: 2px solid var(--px-caution);
  outline-offset: 1px;
}

.theater-node { transition: width 0.12s ease, height 0.12s ease; }
.theater-node[data-active='true'] { box-shadow: 0 0 0 4px rgba(189, 138, 22, 0.2); }

.theater-strip { scrollbar-width: thin; }
```

- [ ] **Step 2: Verify the app still builds (CSS import wiring comes with the components)**

Run: `npm run lint`
Expected: PASS (no JS touched; the file is imported by components in later tasks).

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/reports/theater/theater.css
git commit -m "feat(theater): frosted-glass theater stylesheet"
```

---

## Task 4: Filmstrip

**Files:**
- Create: `components/dashboard/reports/theater/Filmstrip.tsx`
- Test: `tests/components/theater/Filmstrip.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/Filmstrip.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Filmstrip } from '@/components/dashboard/reports/theater/Filmstrip'
import type { TimelineMarker } from '@/components/dashboard/reports/theater/timeline-model'

const markers: TimelineMarker[] = [
  { seq: 1, questionId: 'q1', title: 'Experience', statusBadge: 'passed', tone: 'ok',
    askedAtMs: 23_000, thumbnailUrl: 'https://x/q1.webp', positionPct: 10 },
  { seq: 2, questionId: 'q2', title: 'AI agent', statusBadge: 'failed_required', tone: 'danger',
    askedAtMs: null, thumbnailUrl: null, positionPct: null },
]

describe('Filmstrip', () => {
  it('renders one card per marker with a thumbnail when present', () => {
    render(<Filmstrip markers={markers} activeQuestionId={null} onSelect={() => {}} />)
    expect(screen.getByText('Experience')).toBeInTheDocument()
    expect(screen.getByText('AI agent')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Experience/i })).toHaveAttribute('src', 'https://x/q1.webp')
  })

  it('calls onSelect with the questionId on click', async () => {
    const onSelect = vi.fn()
    render(<Filmstrip markers={markers} activeQuestionId={null} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /Experience/i }))
    expect(onSelect).toHaveBeenCalledWith('q1')
  })

  it('marks the active card', () => {
    render(<Filmstrip markers={markers} activeQuestionId="q1" onSelect={() => {}} />)
    expect(screen.getByRole('button', { name: /Experience/i })).toHaveAttribute('data-active', 'true')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/Filmstrip.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `Filmstrip.tsx`**

```typescript
// components/dashboard/reports/theater/Filmstrip.tsx
'use client'

import { formatTimestamp, statusBadgeMeta, TONE_BG, TONE_INK } from '../report-format'
import type { TimelineMarker } from './timeline-model'
import './theater.css'

export function Filmstrip({
  markers,
  activeQuestionId,
  onSelect,
}: {
  markers: TimelineMarker[]
  activeQuestionId: string | null
  onSelect: (questionId: string) => void
}) {
  return (
    <div className="theater-strip flex gap-2 overflow-x-auto pb-1" aria-label="Question timeline">
      {markers.map((m) => {
        const badge = statusBadgeMeta(m.statusBadge)
        const active = m.questionId === activeQuestionId
        return (
          <button
            key={m.questionId}
            type="button"
            data-active={active ? 'true' : 'false'}
            onClick={() => onSelect(m.questionId)}
            aria-label={`Q${m.seq} ${m.title} — ${badge.label}`}
            className="theater-card theater-glass flex w-[168px] flex-none flex-col overflow-hidden rounded-xl text-left"
          >
            <div className="relative h-[44px] w-full" style={{ background: TONE_BG[m.tone] }}>
              {m.thumbnailUrl ? (
                <img
                  src={m.thumbnailUrl}
                  alt={`Q${m.seq} ${m.title}`}
                  className="h-full w-full object-cover"
                />
              ) : (
                <span
                  className="absolute inset-0 grid place-items-center text-[16px]"
                  aria-hidden="true"
                  style={{ color: TONE_INK[m.tone] }}
                >
                  {m.tone === 'ok' ? '✓' : m.tone === 'danger' ? '✕' : '~'}
                </span>
              )}
              {m.askedAtMs != null && (
                <span
                  className="absolute bottom-1 right-1 rounded px-1 py-0.5 text-[8.5px] font-bold text-white"
                  style={{ background: 'rgba(20,30,40,0.45)' }}
                >
                  {formatTimestamp(m.askedAtMs)}
                </span>
              )}
            </div>
            <div className="px-2 py-1.5">
              <div className="text-[8px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
                Q{m.seq}
              </div>
              <div
                className="truncate text-[11px] font-semibold"
                style={{ color: 'var(--px-fg)' }}
                title={m.title}
              >
                {m.title}
              </div>
              <div className="mt-0.5 text-[9px] font-bold" style={{ color: TONE_INK[m.tone] }}>
                {badge.label}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/Filmstrip.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/Filmstrip.tsx tests/components/theater/Filmstrip.test.tsx
git commit -m "feat(theater): Filmstrip question cards"
```

---

## Task 5: NodeTrack

**Files:**
- Create: `components/dashboard/reports/theater/NodeTrack.tsx`
- Test: `tests/components/theater/NodeTrack.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/NodeTrack.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { NodeTrack } from '@/components/dashboard/reports/theater/NodeTrack'
import type { TimelineMarker } from '@/components/dashboard/reports/theater/timeline-model'

const markers: TimelineMarker[] = [
  { seq: 1, questionId: 'q1', title: 'A', statusBadge: 'passed', tone: 'ok',
    askedAtMs: 10_000, thumbnailUrl: null, positionPct: 10 },
  { seq: 2, questionId: 'q2', title: 'B', statusBadge: 'partial', tone: 'caution',
    askedAtMs: null, thumbnailUrl: null, positionPct: null },
]

describe('NodeTrack', () => {
  it('renders a node only for markers with a position', () => {
    render(<NodeTrack markers={markers} playheadPct={0} activeQuestionId={null} onSeekMs={() => {}} />)
    expect(screen.getAllByRole('button', { name: /jump to/i })).toHaveLength(1)
  })

  it('seeks to the marker asked_at_ms on node click', async () => {
    const onSeekMs = vi.fn()
    render(<NodeTrack markers={markers} playheadPct={0} activeQuestionId={null} onSeekMs={onSeekMs} />)
    await userEvent.click(screen.getByRole('button', { name: /jump to/i }))
    expect(onSeekMs).toHaveBeenCalledWith(10_000)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/NodeTrack.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `NodeTrack.tsx`**

```typescript
// components/dashboard/reports/theater/NodeTrack.tsx
'use client'

import { formatTimestamp, TONE_FILL } from '../report-format'
import type { TimelineMarker } from './timeline-model'
import './theater.css'

export function NodeTrack({
  markers,
  playheadPct,
  activeQuestionId,
  onSeekMs,
}: {
  markers: TimelineMarker[]
  playheadPct: number
  activeQuestionId: string | null
  onSeekMs: (ms: number) => void
}) {
  return (
    <div className="relative mx-1 mt-2 h-2 rounded" style={{ background: 'rgba(20,40,60,0.1)' }}>
      {/* played portion */}
      <div
        className="absolute left-0 top-0 bottom-0 rounded"
        style={{ width: `${Math.min(100, Math.max(0, playheadPct))}%`, background: 'var(--px-accent)', opacity: 0.6 }}
      />
      {markers.map((m) =>
        m.positionPct == null || m.askedAtMs == null ? null : (
          <button
            key={m.questionId}
            type="button"
            data-active={m.questionId === activeQuestionId ? 'true' : 'false'}
            onClick={() => onSeekMs(m.askedAtMs as number)}
            aria-label={`Q${m.seq} jump to ${formatTimestamp(m.askedAtMs)}`}
            className="theater-node absolute top-1/2 h-[14px] w-[14px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white"
            style={{ left: `${m.positionPct}%`, border: `3px solid ${TONE_FILL[m.tone]}` }}
          />
        ),
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/NodeTrack.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/NodeTrack.tsx tests/components/theater/NodeTrack.test.tsx
git commit -m "feat(theater): NodeTrack scrubber + question nodes"
```

---

## Task 6: IntegrityLane

**Files:**
- Create: `components/dashboard/reports/theater/IntegrityLane.tsx`
- Test: `tests/components/theater/IntegrityLane.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/IntegrityLane.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { IntegrityLane } from '@/components/dashboard/reports/theater/IntegrityLane'
import type { FlagMarker } from '@/components/dashboard/reports/theater/timeline-model'

const flags: FlagMarker[] = [
  { kind: 'off_screen_sustained', startMs: 5000, endMs: 6000, confidence: 0.65,
    thumbnailUrl: null, positionPct: 20 },
]

describe('IntegrityLane', () => {
  it('renders the risk caption and a clickable flag marker', () => {
    render(
      <IntegrityLane buckets={[0.2, 0.8, 0.4, 0]} flags={flags} riskBand="high"
        caption="56% off-screen · 42 down-glances" onSelectFlag={() => {}} />,
    )
    expect(screen.getByText(/high risk/i)).toBeInTheDocument()
    expect(screen.getByText(/56% off-screen/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /off-screen/i })).toBeInTheDocument()
  })

  it('calls onSelectFlag with the flag on marker click', async () => {
    const onSelectFlag = vi.fn()
    render(
      <IntegrityLane buckets={[0.2]} flags={flags} riskBand="high" caption="" onSelectFlag={onSelectFlag} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /off-screen/i }))
    expect(onSelectFlag).toHaveBeenCalledWith(flags[0])
  })

  it('renders nothing for an empty/absent lane', () => {
    const { container } = render(
      <IntegrityLane buckets={[]} flags={[]} riskBand={null} caption="" onSelectFlag={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/IntegrityLane.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `IntegrityLane.tsx`**

```typescript
// components/dashboard/reports/theater/IntegrityLane.tsx
'use client'

import type { RiskBand } from '@/lib/api/reports'
import { formatTimestamp } from '../report-format'
import type { FlagMarker } from './timeline-model'
import './theater.css'

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

export function IntegrityLane({
  buckets,
  flags,
  riskBand,
  caption,
  onSelectFlag,
}: {
  buckets: number[]
  flags: FlagMarker[]
  riskBand: RiskBand | null
  caption: string
  onSelectFlag: (flag: FlagMarker) => void
}) {
  if (buckets.length === 0 && flags.length === 0) return null
  const riskText =
    riskBand === 'high' ? 'high risk' : riskBand === 'medium' ? 'medium risk' : 'integrity'
  return (
    <div className="mx-1 mt-2">
      <div className="relative flex h-[14px] overflow-hidden rounded" style={{ background: 'rgba(20,40,60,0.05)' }}>
        {buckets.map((v, i) => (
          <div key={i} style={{ flex: 1, height: '100%', background: `rgba(229,85,107,${0.12 + v * 0.7})` }} />
        ))}
        {flags.map((f, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onSelectFlag(f)}
            aria-label={`${KIND_LABEL[f.kind] ?? f.kind} at ${formatTimestamp(f.startMs)}`}
            className="absolute top-0 h-full w-[3px] -translate-x-1/2"
            style={{ left: `${f.positionPct}%`, background: 'var(--px-danger)' }}
          />
        ))}
      </div>
      <div className="mt-1 flex items-center justify-between text-[9.5px]">
        <span className="font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
          ⚠ Integrity · {riskText}
        </span>
        {caption && <span className="font-bold" style={{ color: 'var(--px-danger)' }}>{caption}</span>}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/IntegrityLane.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/IntegrityLane.tsx tests/components/theater/IntegrityLane.test.tsx
git commit -m "feat(theater): IntegrityLane density band + flag markers"
```

---

## Task 7: SessionTimeline (composition)

**Files:**
- Create: `components/dashboard/reports/theater/SessionTimeline.tsx`
- Test: (covered by `ReviewTheater.test.tsx` in Task 11; this is a thin presentational composition — no standalone test)

- [ ] **Step 1: Implement `SessionTimeline.tsx`**

```typescript
// components/dashboard/reports/theater/SessionTimeline.tsx
'use client'

import type { RiskBand } from '@/lib/api/reports'
import { Filmstrip } from './Filmstrip'
import { IntegrityLane } from './IntegrityLane'
import { NodeTrack } from './NodeTrack'
import type { FlagMarker, TimelineMarker } from './timeline-model'

export function SessionTimeline({
  markers,
  flags,
  buckets,
  riskBand,
  integrityCaption,
  playheadPct,
  activeQuestionId,
  onSelectQuestion,
  onSeekMs,
  onSelectFlag,
}: {
  markers: TimelineMarker[]
  flags: FlagMarker[]
  buckets: number[]
  riskBand: RiskBand | null
  integrityCaption: string
  playheadPct: number
  activeQuestionId: string | null
  onSelectQuestion: (questionId: string) => void
  onSeekMs: (ms: number) => void
  onSelectFlag: (flag: FlagMarker) => void
}) {
  return (
    <div className="theater-glass rounded-2xl p-3">
      <Filmstrip markers={markers} activeQuestionId={activeQuestionId} onSelect={onSelectQuestion} />
      <NodeTrack
        markers={markers}
        playheadPct={playheadPct}
        activeQuestionId={activeQuestionId}
        onSeekMs={onSeekMs}
      />
      <IntegrityLane
        buckets={buckets}
        flags={flags}
        riskBand={riskBand}
        caption={integrityCaption}
        onSelectFlag={onSelectFlag}
      />
    </div>
  )
}
```

- [ ] **Step 2: Verify type-check**

Run: `npm run type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/reports/theater/SessionTimeline.tsx
git commit -m "feat(theater): SessionTimeline 3-layer composition"
```

---

## Task 8: ThisMomentPanel

**Files:**
- Create: `components/dashboard/reports/theater/ThisMomentPanel.tsx`
- Test: `tests/components/theater/ThisMomentPanel.test.tsx`

The panel has three modes: default (decision summary), a selected question, or a selected flag.

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/ThisMomentPanel.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ThisMomentPanel } from '@/components/dashboard/reports/theater/ThisMomentPanel'
import type { DecisionOut, QuestionOut } from '@/lib/api/reports'

const decision: DecisionOut = {
  headline: 'Closed early on agent experience',
  why_positive: { title: 'Relevant platform experience', body: '~6 yrs' },
  why_negative: { title: 'No agent experience', body: 'never worked with AI agents' },
}

const question: QuestionOut = {
  seq: 3, question_id: 'q3', title: 'AI triage', status_badge: 'partial', status_tone: 'caution',
  question_text: 'How would you design the flow?', candidate_quote: 'extract metadata…',
  our_read: 'workable but no validation', asked_at_ms: 74_000, thumbnail_url: null,
}

describe('ThisMomentPanel', () => {
  it('shows the decision summary by default', () => {
    render(<ThisMomentPanel selection={null} decision={decision} onJump={() => {}} />)
    expect(screen.getByText(/Closed early on agent experience/)).toBeInTheDocument()
    expect(screen.getByText(/No agent experience/)).toBeInTheDocument()
  })

  it('shows the question read when a question is selected', () => {
    render(
      <ThisMomentPanel selection={{ type: 'question', question }} decision={decision} onJump={() => {}} />,
    )
    expect(screen.getByText(/How would you design the flow/)).toBeInTheDocument()
    expect(screen.getByText(/extract metadata/)).toBeInTheDocument()
    expect(screen.getByText(/workable but no validation/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/ThisMomentPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ThisMomentPanel.tsx`**

```typescript
// components/dashboard/reports/theater/ThisMomentPanel.tsx
'use client'

import type { DecisionOut, QuestionOut } from '@/lib/api/reports'
import { formatTimestamp, statusBadgeMeta, TONE_BG, TONE_INK } from '../report-format'
import type { FlagMarker } from './timeline-model'

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

export type MomentSelection =
  | { type: 'question'; question: QuestionOut }
  | { type: 'flag'; flag: FlagMarker }
  | null

export function ThisMomentPanel({
  selection,
  decision,
  onJump,
}: {
  selection: MomentSelection
  decision: DecisionOut
  onJump: (ms: number) => void
}) {
  return (
    <div className="theater-glass flex h-full flex-col rounded-2xl p-4">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-extrabold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--px-accent)' }} />
        This moment
      </div>

      {selection === null && (
        <div className="space-y-3 overflow-y-auto">
          <p className="text-[13px] font-semibold" style={{ color: 'var(--px-fg)' }}>{decision.headline}</p>
          <div>
            <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-ok)' }}>{decision.why_positive.title}</div>
            <p className="mt-0.5 text-[12px]" style={{ color: 'var(--px-fg-3)' }}>{decision.why_positive.body}</p>
          </div>
          <div>
            <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-danger)' }}>{decision.why_negative.title}</div>
            <p className="mt-0.5 text-[12px]" style={{ color: 'var(--px-fg-3)' }}>{decision.why_negative.body}</p>
          </div>
        </div>
      )}

      {selection?.type === 'question' && (
        <div className="flex flex-1 flex-col overflow-y-auto">
          <div className="mb-2 flex items-center gap-2">
            <span
              className="rounded-md px-2 py-0.5 text-[10px] font-extrabold"
              style={{ background: TONE_BG[statusBadgeMeta(selection.question.status_badge).tone], color: TONE_INK[statusBadgeMeta(selection.question.status_badge).tone] }}
            >
              {statusBadgeMeta(selection.question.status_badge).label}
            </span>
            <span className="text-[13px] font-bold">Q{selection.question.seq} · {selection.question.title}</span>
          </div>
          <p className="mb-2 text-[11.5px]" style={{ color: 'var(--px-fg-3)', whiteSpace: 'pre-wrap' }}>{selection.question.question_text}</p>
          {selection.question.candidate_quote && (
            <p className="mb-2 border-l-2 pl-2 text-[12px] italic" style={{ borderColor: 'var(--px-caution)', color: 'var(--px-fg)', whiteSpace: 'pre-wrap' }}>
              {selection.question.candidate_quote}
            </p>
          )}
          {selection.question.our_read && (
            <>
              <div className="text-[9.5px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Our read</div>
              <p className="text-[12px]" style={{ color: 'var(--px-fg-3)', whiteSpace: 'pre-wrap' }}>{selection.question.our_read}</p>
            </>
          )}
          {selection.question.asked_at_ms != null && (
            <button
              type="button"
              onClick={() => onJump(selection.question.asked_at_ms as number)}
              className="mt-auto pt-2 text-left text-[11.5px] font-bold"
              style={{ color: 'var(--px-accent)' }}
            >
              ▶ Jump to {formatTimestamp(selection.question.asked_at_ms)}
            </button>
          )}
        </div>
      )}

      {selection?.type === 'flag' && (
        <div className="flex flex-1 flex-col overflow-y-auto">
          <div className="mb-2 text-[13px] font-bold" style={{ color: 'var(--px-danger)' }}>
            {KIND_LABEL[selection.flag.kind] ?? selection.flag.kind}
          </div>
          {selection.flag.thumbnailUrl && (
            <img src={selection.flag.thumbnailUrl} alt="Flagged moment" className="mb-2 w-full rounded-lg object-cover" />
          )}
          <p className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
            {formatTimestamp(selection.flag.startMs)}–{formatTimestamp(selection.flag.endMs)} · {Math.round(selection.flag.confidence * 100)}% confidence
          </p>
          <button
            type="button"
            onClick={() => onJump(selection.flag.startMs)}
            className="mt-auto pt-2 text-left text-[11.5px] font-bold"
            style={{ color: 'var(--px-accent)' }}
          >
            ▶ Jump to {formatTimestamp(selection.flag.startMs)}
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/ThisMomentPanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/ThisMomentPanel.tsx tests/components/theater/ThisMomentPanel.test.tsx
git commit -m "feat(theater): ThisMomentPanel (decision / question / flag detail)"
```

---

## Task 9: TheaterTopBar

**Files:**
- Create: `components/dashboard/reports/theater/TheaterTopBar.tsx`
- Test: `tests/components/theater/TheaterTopBar.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/TheaterTopBar.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { TheaterTopBar } from '@/components/dashboard/reports/theater/TheaterTopBar'
import type { ReportRead } from '@/lib/api/reports'

const report = {
  verdict: 'reject',
  scores: {
    overall: { score: 35, tier_label: 'Well Below Bar', tone: 'danger', confidence: 'low', coverage: 0.27 },
    technical: { score: 44, tier_label: 'Below Bar', tone: 'caution', confidence: 'low', coverage: 0.3 },
    communication: { score: 70, tier_label: 'Strong', tone: 'ok', confidence: 'medium', coverage: 1 },
  },
} as unknown as ReportRead

describe('TheaterTopBar', () => {
  it('renders the verdict chip + dimension gauges and a close button', () => {
    render(<TheaterTopBar report={report} candidateName="Aarav" subtitle="Jr. FDE" riskBand="high" onClose={() => {}} />)
    expect(screen.getByText(/Not Recommended/i)).toBeInTheDocument()
    expect(screen.getByText('Aarav')).toBeInTheDocument()
    expect(screen.getByText(/high integrity risk/i)).toBeInTheDocument()
    // ScoreGauge renders an accessible label per dimension
    expect(screen.getByRole('img', { name: /Overall/i })).toBeInTheDocument()
  })

  it('calls onClose when the close button is clicked', async () => {
    const onClose = vi.fn()
    render(<TheaterTopBar report={report} candidateName="Aarav" subtitle="" riskBand={null} onClose={onClose} />)
    await userEvent.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/TheaterTopBar.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `TheaterTopBar.tsx`**

```typescript
// components/dashboard/reports/theater/TheaterTopBar.tsx
'use client'

import type { ReportRead, RiskBand } from '@/lib/api/reports'
import { ScoreGauge } from '../ScoreGauge'
import { TONE_BG, TONE_INK, tierTone, verdictMeta } from '../report-format'

export function TheaterTopBar({
  report,
  candidateName,
  subtitle,
  riskBand,
  onClose,
}: {
  report: ReportRead
  candidateName: string
  subtitle: string
  riskBand: RiskBand | null
  onClose: () => void
}) {
  const v = verdictMeta(report.verdict)
  const dims: { key: string; label: string }[] = [
    { key: 'overall', label: 'Overall' },
    { key: 'technical', label: 'Technical' },
    { key: 'communication', label: 'Comms' },
  ]
  return (
    <div className="theater-glass m-3 mb-0 flex items-center gap-4 rounded-2xl px-4 py-2">
      <div className="min-w-0">
        <div className="truncate text-[13.5px] font-bold" style={{ color: 'var(--px-fg)' }}>
          {candidateName || 'Candidate'}
        </div>
        {subtitle && <div className="truncate text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{subtitle}</div>}
      </div>
      <div className="h-8 w-px" style={{ background: 'var(--px-hairline)' }} />
      <div className="flex items-center gap-3">
        {dims.map((d) => {
          const s = report.scores[d.key]
          if (!s) return null
          return <ScoreGauge key={d.key} score={s.score} label={d.label} size={40} caption={undefined}
            toneOverride={d.key === 'overall' ? v.tone : tierTone(s.tone)} />
        })}
      </div>
      <div className="flex-1" />
      {riskBand === 'high' && (
        <span className="whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-bold"
          style={{ background: TONE_BG.danger, color: TONE_INK.danger }}>
          ⚠ High integrity risk
        </span>
      )}
      <span className="whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-bold"
        style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}>
        {v.label}
      </span>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="grid h-7 w-7 place-items-center rounded-lg border text-[13px]"
        style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
      >
        ✕
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/TheaterTopBar.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/TheaterTopBar.tsx tests/components/theater/TheaterTopBar.test.tsx
git commit -m "feat(theater): TheaterTopBar identity + gauges + verdict/risk chips"
```

---

## Task 10: TheaterStage + useTheaterState

**Files:**
- Create: `components/dashboard/reports/theater/TheaterStage.tsx`, `components/dashboard/reports/theater/useTheaterState.ts`
- Test: (covered by `ReviewTheater.test.tsx`; the stage's only logic — playhead from `onTimeUpdate` — is exercised there)

- [ ] **Step 1: Implement `useTheaterState.ts`**

```typescript
// components/dashboard/reports/theater/useTheaterState.ts
'use client'

import { useCallback, useMemo, useRef, useState } from 'react'

import type { QuestionOut } from '@/lib/api/reports'
import type { PlaybackSeekApi } from '../SessionPlayback'
import type { MomentSelection } from './ThisMomentPanel'
import { activeQuestionId, type FlagMarker, type TimelineMarker } from './timeline-model'

export function useTheaterState(params: {
  markers: TimelineMarker[]
  questions: QuestionOut[]
  durationMs: number
}) {
  const { markers, questions, durationMs } = params
  const seekRef = useRef<PlaybackSeekApi | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  // explicit selection overrides the playhead-derived active question until cleared
  const [explicit, setExplicit] = useState<MomentSelection>(null)

  const playheadActiveId = useMemo(() => activeQuestionId(markers, currentMs), [markers, currentMs])

  const selection: MomentSelection = explicit
  const activeId =
    explicit?.type === 'question' ? explicit.question.question_id : playheadActiveId

  const seekMs = useCallback((ms: number) => {
    seekRef.current?.seekToMs(ms)
  }, [])

  const selectQuestion = useCallback((questionId: string) => {
    const q = questions.find((x) => x.question_id === questionId)
    if (!q) return
    setExplicit({ type: 'question', question: q })
    if (q.asked_at_ms != null) seekRef.current?.seekToMs(q.asked_at_ms)
  }, [questions])

  const selectFlag = useCallback((flag: FlagMarker) => {
    setExplicit({ type: 'flag', flag })
    seekRef.current?.seekToMs(flag.startMs)
  }, [])

  const clearSelection = useCallback(() => setExplicit(null), [])

  const playheadPct = durationMs > 0 ? Math.min(100, (currentMs / durationMs) * 100) : 0

  return {
    seekRef,
    currentMs,
    setCurrentMs,
    selection,
    activeId,
    playheadPct,
    seekMs,
    selectQuestion,
    selectFlag,
    clearSelection,
  }
}
```

- [ ] **Step 2: Implement `TheaterStage.tsx`**

```typescript
// components/dashboard/reports/theater/TheaterStage.tsx
'use client'

import { useEffect, useRef, type MutableRefObject } from 'react'

import type { PlaybackSeekApi } from '../SessionPlayback'

export function TheaterStage({
  signedUrl,
  offsetMs,
  seekApiRef,
  onCurrentMs,
}: {
  signedUrl: string | null
  offsetMs: number
  seekApiRef: MutableRefObject<PlaybackSeekApi | null>
  onCurrentMs: (ms: number) => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    seekApiRef.current = {
      seekToMs: (ms: number) => {
        const v = videoRef.current
        if (!v) return
        v.currentTime = Math.max(0, (ms + offsetMs) / 1000)
        void v.play?.()
      },
    }
    return () => {
      seekApiRef.current = null
    }
  }, [seekApiRef, offsetMs])

  if (!signedUrl) {
    return (
      <div className="grid flex-1 place-items-center text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
        Recording unavailable.
      </div>
    )
  }
  return (
    <video
      ref={videoRef}
      src={signedUrl}
      controls
      playsInline
      aria-label="Interview session recording"
      onTimeUpdate={() => {
        const v = videoRef.current
        if (v) onCurrentMs(v.currentTime * 1000 - offsetMs)
      }}
      className="h-full w-full rounded-xl bg-black object-contain"
    />
  )
}
```

- [ ] **Step 3: Verify type-check**

Run: `npm run type-check`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add components/dashboard/reports/theater/TheaterStage.tsx components/dashboard/reports/theater/useTheaterState.ts
git commit -m "feat(theater): TheaterStage video + useTheaterState (playhead + selection)"
```

---

## Task 11: ReviewTheater (the shell)

**Files:**
- Create: `components/dashboard/reports/theater/ReviewTheater.tsx`
- Test: `tests/components/theater/ReviewTheater.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/ReviewTheater.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ReviewTheater } from '@/components/dashboard/reports/theater/ReviewTheater'
import type { ProctoringAnalysis, RecordingPlayback, ReportRead } from '@/lib/api/reports'

vi.mock('@/lib/hooks/use-session-recording', () => ({
  useSessionRecording: () => ({
    data: {
      status: 'ready', signed_url: 'https://x/v.mp4', expires_at: null,
      duration_seconds: 242, offset_ms: 0,
      transcript: [{ role: 'agent', text: 'Hi', t_ms: 0 }],
    } satisfies RecordingPlayback,
    isLoading: false,
  }),
}))

vi.mock('@/lib/hooks/use-session-proctoring', () => ({
  useSessionProctoring: () => ({
    data: {
      status: 'ready', risk_band: 'high',
      detector_summary: { off_screen_pct: 0.56, down_glance_count: 42, reading_sweep_intervals: 0, max_faces: 2, multi_face_intervals: [] },
      gaze_heatmap: null,
      flagged_intervals: [{ kind: 'off_screen_sustained', start_ms: 16200, end_ms: 18400, confidence: 0.65 }],
      gaze_signal_quality: 'good', unscorable_pct: 0.02,
    } satisfies ProctoringAnalysis,
    isLoading: false,
  }),
}))

const report = {
  session_id: 's1', verdict: 'reject',
  decision: { headline: 'Closed early', why_positive: { title: 'P', body: 'p' }, why_negative: { title: 'N', body: 'n' } },
  scores: { overall: { score: 35, tier_label: 'x', tone: 'danger', confidence: 'low', coverage: 0.3 } },
  questions: [
    { seq: 1, question_id: 'q1', title: 'Experience', status_badge: 'passed', status_tone: 'ok',
      question_text: 'Years?', candidate_quote: 'six', our_read: 'ok', asked_at_ms: 23000, thumbnail_url: null },
  ],
} as unknown as ReportRead

describe('ReviewTheater', () => {
  it('renders the stage, timeline and verdict when open', () => {
    render(<ReviewTheater open report={report} candidateName="Aarav" subtitle="Jr. FDE" onClose={() => {}} />)
    expect(screen.getByLabelText(/Interview session recording/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Experience/i })).toBeInTheDocument()
    expect(screen.getByText(/Not Recommended/i)).toBeInTheDocument()
  })

  it('selecting a question shows its read in the panel', async () => {
    render(<ReviewTheater open report={report} candidateName="Aarav" subtitle="" onClose={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /Experience/i }))
    expect(screen.getByText(/Years\?/)).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    render(<ReviewTheater open={false} report={report} candidateName="A" subtitle="" onClose={() => {}} />)
    expect(screen.queryByLabelText(/Interview session recording/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/ReviewTheater.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ReviewTheater.tsx`**

```typescript
// components/dashboard/reports/theater/ReviewTheater.tsx
'use client'

import { useMemo } from 'react'

import { Dialog, DialogContent } from '@/components/px'
import type { ReportRead } from '@/lib/api/reports'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'
import { SessionTimeline } from './SessionTimeline'
import { TheaterStage } from './TheaterStage'
import { TheaterTopBar } from './TheaterTopBar'
import { ThisMomentPanel } from './ThisMomentPanel'
import { buildFlagMarkers, buildQuestionMarkers, densityBuckets } from './timeline-model'
import { useTheaterState } from './useTheaterState'
import './theater.css'

const TOP_FLAGS = 6
const DENSITY_BUCKETS = 48

export function ReviewTheater({
  open,
  report,
  candidateName,
  subtitle,
  onClose,
}: {
  open: boolean
  report: ReportRead
  candidateName: string
  subtitle: string
  onClose: () => void
}) {
  const sessionId = report.session_id ?? ''
  const { data: rec } = useSessionRecording(open ? sessionId : '')
  const { data: proc } = useSessionProctoring(open ? sessionId : '')

  const durationMs = (rec?.duration_seconds ?? 0) * 1000
  const flaggedRaw = proc && proc.status === 'ready' ? proc.flagged_intervals : []
  const riskBand = proc && proc.status === 'ready' ? proc.risk_band : null

  const markers = useMemo(
    () => buildQuestionMarkers(report.questions, durationMs),
    [report.questions, durationMs],
  )
  const flags = useMemo(
    () => buildFlagMarkers(flaggedRaw, durationMs, TOP_FLAGS),
    [flaggedRaw, durationMs],
  )
  const buckets = useMemo(
    () => (flaggedRaw.length ? densityBuckets(flaggedRaw, durationMs, DENSITY_BUCKETS) : []),
    [flaggedRaw, durationMs],
  )

  const st = useTheaterState({ markers, questions: report.questions, durationMs })

  const integrityCaption = useMemo(() => {
    const s = proc && proc.status === 'ready' ? proc.detector_summary : null
    if (!s) return ''
    return `${Math.round(s.off_screen_pct * 100)}% off-screen · ${s.down_glance_count} down-glances`
  }, [proc])

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent showCloseButton={false} widthClass="" className="theater-shell">
        <TheaterTopBar
          report={report}
          candidateName={candidateName}
          subtitle={subtitle}
          riskBand={riskBand}
          onClose={onClose}
        />
        <div className="flex min-h-0 flex-1 gap-3 px-3 pt-3">
          <div className="min-w-0 flex-1">
            <TheaterStage
              signedUrl={rec?.status === 'ready' ? rec.signed_url : null}
              offsetMs={rec?.offset_ms ?? 0}
              seekApiRef={st.seekRef}
              onCurrentMs={st.setCurrentMs}
            />
          </div>
          <div className="w-[260px] flex-none">
            <ThisMomentPanel selection={st.selection} decision={report.decision} onJump={st.seekMs} />
          </div>
        </div>
        <div className="p-3">
          <SessionTimeline
            markers={markers}
            flags={flags}
            buckets={buckets}
            riskBand={riskBand}
            integrityCaption={integrityCaption}
            playheadPct={st.playheadPct}
            activeQuestionId={st.activeId}
            onSelectQuestion={st.selectQuestion}
            onSeekMs={st.seekMs}
            onSelectFlag={st.selectFlag}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/theater/ReviewTheater.test.tsx`
Expected: PASS.

> If jsdom errors on `HTMLMediaElement.prototype.play` (not implemented), add to the test file top: `vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)`. Mirror any existing media stub in `tests/setup.ts` first; only add if the test actually throws.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/ReviewTheater.tsx tests/components/theater/ReviewTheater.test.tsx
git commit -m "feat(theater): ReviewTheater shell (dialog + stage + panel + timeline)"
```

---

## Task 12: Poster — open the theater from the report page

**Files:**
- Modify: `components/dashboard/reports/SessionPlayback.tsx`, `components/dashboard/reports/ReportView.tsx`
- Create: `tests/components/theater/SessionPlaybackPoster.test.tsx`
- Delete: `tests/components/reports/session-playback.test.tsx` (its `activeSegmentIndex` coverage moved to `timeline-model.test.ts`; its inline-player render coverage is replaced by the theater + poster tests)

> Pre-flight: the old `tests/components/reports/session-playback.test.tsx` tests the inline player (video/transcript/processing states) and the old `<SessionPlayback sessionId=...>` signature — all removed by this task. The other two SessionPlayback importers are safe: `report-presentational.test.tsx` only imports `VerbalContentOnlyBadge` (kept), and `ReportView.test.tsx` renders the poster which fires no hooks until clicked. Confirm by running the full report suite in Step 5.

`ReportView` already renders `<SessionPlayback sessionId={...} seekApiRef={...} />`. We rework `SessionPlayback` into a **poster** that opens `ReviewTheater`. The `seekApiRef` prop is no longer used by the page (the theater owns its own video), so we keep the prop optional for back-compat but ignore it. The `activeSegmentIndex` export moves to `timeline-model.ts` (Task 2) — re-export it here so any importer keeps working.

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/SessionPlaybackPoster.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SessionPlayback } from '@/components/dashboard/reports/SessionPlayback'
import type { ReportRead } from '@/lib/api/reports'

vi.mock('@/components/dashboard/reports/theater/ReviewTheater', () => ({
  ReviewTheater: ({ open }: { open: boolean }) =>
    open ? <div data-testid="theater-open" /> : null,
}))

const report = { session_id: 's1', verdict: 'reject', questions: [], scores: {},
  decision: { headline: '', why_positive: { title: '', body: '' }, why_negative: { title: '', body: '' } } } as unknown as ReportRead

describe('SessionPlayback poster', () => {
  it('renders a play button and opens the theater on click', async () => {
    render(<SessionPlayback report={report} candidateName="Aarav" subtitle="Jr. FDE" />)
    expect(screen.queryByTestId('theater-open')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /play|review/i }))
    expect(screen.getByTestId('theater-open')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/theater/SessionPlaybackPoster.test.tsx`
Expected: FAIL — `SessionPlayback` does not accept a `report` prop / no play button.

- [ ] **Step 3: Rework `SessionPlayback.tsx` into the poster**

Replace the ENTIRE contents of `components/dashboard/reports/SessionPlayback.tsx` with:

```typescript
'use client'

import { useState, type MutableRefObject } from 'react'

import type { ReportRead } from '@/lib/api/reports'
import { verdictMeta, TONE_BG, TONE_INK } from './report-format'
import { ReviewTheater } from './theater/ReviewTheater'

// Re-exported for back-compat: the pure helper + seek-api type now live in the theater model.
export { activeSegmentIndex } from './theater/timeline-model'
export interface PlaybackSeekApi {
  seekToMs: (ms: number) => void
}

const CARD = 'rounded-xl border bg-white p-3.5'

/**
 * Report-page session playback ENTRY: a poster with a Play button. Clicking it
 * opens the immersive Review Theater (glass popup + scannable session timeline).
 * `seekApiRef` is accepted for back-compat but unused — the theater owns its
 * own video + seek.
 */
export function SessionPlayback({
  report,
  candidateName,
  subtitle,
  seekApiRef: _seekApiRef,
}: {
  report: ReportRead
  candidateName: string
  subtitle: string
  seekApiRef?: MutableRefObject<PlaybackSeekApi | null>
}) {
  const [open, setOpen] = useState(false)
  const v = verdictMeta(report.verdict)

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Play session recording — open review theater"
        className="relative flex w-full items-center justify-center rounded-lg"
        style={{
          aspectRatio: '16 / 9',
          background: 'radial-gradient(110% 90% at 50% 18%, #fbf4ee, #e7eef3 60%, #dfe9ee)',
          border: '1px solid var(--px-hairline)',
        }}
      >
        <span
          className="grid h-14 w-14 place-items-center rounded-full text-[20px] text-white shadow-lg"
          style={{ background: 'var(--px-accent)' }}
          aria-hidden="true"
        >
          ▶
        </span>
        <span
          className="absolute right-2.5 top-2.5 rounded-full px-2.5 py-1 text-[11px] font-bold"
          style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}
        >
          {v.label}
        </span>
        <span className="absolute bottom-2.5 left-2.5 text-[11px] font-semibold" style={{ color: 'var(--px-fg-3)' }}>
          Review session →
        </span>
      </button>
      <VerbalContentOnlyBadge />
      {open && (
        <ReviewTheater
          open={open}
          report={report}
          candidateName={candidateName}
          subtitle={subtitle}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  )
}

export function VerbalContentOnlyBadge() {
  return (
    <div className="mt-2.5 flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11px]"
      style={{ color: 'var(--px-ai)', background: 'var(--px-ai-bg)', borderColor: 'var(--px-ai-line)' }}>
      🛈&nbsp;Verbal-content-only — scored on what the candidate said. No facial, affect, or appearance analysis.
    </div>
  )
}
```

- [ ] **Step 4: Update the call site in `ReportView.tsx`**

In `components/dashboard/reports/ReportView.tsx`, the `SessionPlayback` element currently is:

```typescript
<SessionPlayback key="p" sessionId={report.session_id} seekApiRef={seekApiRef} />,
```

Replace it with (pass the report + identity; drop the now-unused `sessionId`/`seekApiRef` wiring for the player — `seekApiRef` is still used by `ProctoringIntegrityPanel.onSeek`, so leave the `seekApiRef`/`handleSeek` declarations and the `ProctoringIntegrityPanel` usage untouched):

```typescript
<SessionPlayback key="p" report={report} candidateName={candidateName} subtitle={title} />,
```

> Note: `ProctoringIntegrityPanel` still receives `onSeek={handleSeek}` and `seekApiRef` is still declared — but with the inline player gone, `seekApiRef.current` is never set, so its seek becomes a no-op. That's acceptable for this phase (the panel's seek was the old inline player; integrity review now happens inside the theater). Leave the panel as-is; do not delete `seekApiRef`/`handleSeek` (removing them is out of scope and risks unrelated churn).

- [ ] **Step 5: Delete the obsolete inline-player test, then run the report + theater suites**

```bash
git rm tests/components/reports/session-playback.test.tsx
npm run test -- tests/components/reports tests/components/theater
```
Expected: PASS. (`ReportView.test.tsx` and `report-presentational.test.tsx` still pass — verified above. If `ReportView.test.tsx` fails because the poster throws on the fixture, ensure `makeReport()` returns a `verdict` — it does — and that the poster reads only `report.verdict`/`session_id` at render time, which it does.)

- [ ] **Step 6: Type-check + lint**

Run: `npm run type-check && npm run lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add components/dashboard/reports/SessionPlayback.tsx components/dashboard/reports/ReportView.tsx tests/components/theater/SessionPlaybackPoster.test.tsx
git rm --cached tests/components/reports/session-playback.test.tsx 2>/dev/null || true
git commit -m "feat(theater): SessionPlayback becomes the poster that opens ReviewTheater"
```

---

## Final verification

- [ ] **Run the full theater suite + the existing report tests**

```bash
npm run test -- tests/components/theater
npm run test
```
Expected: all PASS (no regressions in the existing suite).

- [ ] **Type-check + lint + build**

```bash
npm run type-check
npm run lint
npm run build
```
Expected: all clean. `build` confirms the new client components compile under Next 16 and the route stays within the bundle budget.

- [ ] **Manual visual check (dev server)**

```bash
npm run dev
```
Open the report URL, click the **Play** poster → the glass theater opens with the top summary, the video, the "this moment" panel, and the 3-layer timeline. On a legacy session (no `asked_at_ms`), cards render in the filmstrip without node-track positions and without question thumbnails — the integrity lane + flag thumbnails still appear; a fresh interview shows the full timeline.

---

## Notes / known limitations (carried from the spec)

- **Legacy sessions** (transcript created before the engine `question_id` change) have `asked_at_ms = null` → filmstrip cards show, node-track nodes don't, question thumbnails are absent. By design (graceful degradation). A fresh interview run shows the full experience.
- **Transcript toggle** (§3.2 of the spec) is intentionally deferred from this plan to keep scope tight — the timeline + "this moment" panel are the primary surface. Add a `Tabs` toggle inside `ReviewTheater` in a follow-up if desired (the transcript data is already in `rec.transcript`, and `activeSegmentIndex` is available).
- **Offset calibration** (`offset_ms`) is consumed as-is from the recording endpoint (currently 0), consistent with the existing player + proctoring alignment.
