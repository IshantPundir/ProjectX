# Recruiter Report UI (A2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the recruiter-facing per-session candidate evaluation report UI in `frontend/app`, rendering the existing `/api/reports/*` data as a defensible dashboard (banded verdict, 0–10 gauges, signal spider, evidence-beside-every-score, a logged human decision, and a playback stub for the future recording feature).

**Architecture:** A dedicated full-page route keyed by session id (`/reports/session/[sessionId]`). A thin client page derives one of six states (`loading | noReport | forbidden | pending | failed | ready`) from a polling TanStack Query hook and switches on it. The `ready` view composes presentational components (gauges, spider, scorecards, decision panel, Q&A/evidence, playback stub) under `components/dashboard/reports/`. All charts are in-house SVG (no chart library). Interactive components are presentational (callbacks + props); the page/`ReportView` wires the mutation hooks. Every evidence quote carries `timestamp_ms`/`question_id` so its timestamp chip becomes a video seek control when sub-project B (recording) lands — zero data reshaping later.

**Tech Stack:** Next.js 16 App Router, TypeScript strict, TanStack Query v5, React Hook Form + Zod, in-house `components/px/` primitives on `@base-ui-components/react`, Tailwind v4 + Iris design tokens (`var(--px-*)`), Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-26-recruiter-report-ui-design.md`. **Approved visual mockup (reference only):** `.superpowers/brainstorm/<session>/content/molded-v2.html`.

**Backend contract (already shipped, do not modify):**
- `GET /api/reports/session/{session_id}` → `200` ReportRead (status `ready`|`failed`) · `202 {status}` (`pending`|`generating`) · `404` none · `403` no `reports.view`.
- `GET /api/reports/{report_id}` → `200` ReportRead · `404`.
- `POST /api/reports/session/{session_id}/regenerate` → `202` (super-admin only).
- `POST /api/reports/{report_id}/decision` body `{decision: "advance"|"reject"|"hold", rationale: string}` → `200` ReportRead.

Backend enum literals (must match TS exactly — `app/modules/reporting/scoring/types.py` + `schemas.py`):
- `Verdict = advance | borderline | reject`
- `Confidence = high | medium | low`
- `Opportunity = full | partial | none`
- `SignalState = excellent | meets_bar | below_bar | not_assessed`
- `KnockoutStatus = passed | failed | insufficient`
- `QuestionScorecard.level = below_bar | meets_bar | excellent | not_assessed`
- signal `type = experience | competency | behavioral | credential`
- `dimension_scores` keys: `technical`, `behavioral`, `communication`; each `{name, score: int|null, coverage: float, confidence, note: string|null}`

---

## File structure

**New:**
- `lib/api/reports.ts` — types + API namespace + `ReportEnvelope`.
- `lib/hooks/use-report.ts` — `useReport` (polling), `useRecordDecision`, `useRegenerateReport`, `deriveReportState`.
- `components/dashboard/reports/report-format.ts` — pure formatters + tone/color maps.
- `components/dashboard/reports/report.css` — keyframes + reduced-motion guard.
- `components/dashboard/reports/ScoreGauge.tsx`
- `components/dashboard/reports/VerdictBand.tsx` (exports `VerdictBand`, `VerdictChip`)
- `components/dashboard/reports/EvidenceQuote.tsx`
- `components/dashboard/reports/SignalSpiderChart.tsx`
- `components/dashboard/reports/SignalScorecards.tsx`
- `components/dashboard/reports/AiRecommendationCard.tsx`
- `components/dashboard/reports/HumanDecisionPanel.tsx`
- `components/dashboard/reports/QaEvidencePanel.tsx`
- `components/dashboard/reports/SessionPlaybackStub.tsx` (exports `SessionPlaybackStub`, `VerbalContentOnlyBadge`)
- `components/dashboard/reports/ReportSummary.tsx`
- `components/dashboard/reports/ReportMethodologyFooter.tsx`
- `components/dashboard/reports/ReportTopBar.tsx`
- `components/dashboard/reports/ReportStates.tsx` (`ReportEmptyState`, `ReportPendingState`, `ReportFailedState`, `ReportForbiddenState`)
- `components/dashboard/reports/ReportView.tsx`
- `app/(dashboard)/reports/session/[sessionId]/page.tsx`
- `app/(dashboard)/reports/session/[sessionId]/loading.tsx`
- `app/(dashboard)/reports/session/[sessionId]/error.tsx`
- Tests under `tests/` mirroring the above.

**Modified:**
- `app/(dashboard)/candidates/[candidateId]/CandidateSessionsTab.tsx` — "View report" link on completed sessions.

---

### Task 1: API namespace + types (`lib/api/reports.ts`)

**Files:**
- Create: `frontend/app/lib/api/reports.ts`
- Test: `frontend/app/tests/api/reports.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/tests/api/reports.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { reportsApi } from '@/lib/api/reports'
import { ApiError } from '@/lib/api/client'

const READY = {
  verdict: 'reject', verdict_reason: 'failed must-have', overall_score: 36,
  overall_coverage: 0.7, overall_confidence: 'medium', dimension_scores: {},
  knockout_results: [], signal_scorecards: [], question_scorecards: [],
  summary: { headline: 'h', strengths: [], gaps: [], rationale: '' },
  status: 'ready', id: 'r1', session_id: 's1', version: 1,
}

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response)
}

afterEach(() => vi.unstubAllGlobals())

describe('reportsApi.getBySession', () => {
  it('returns ready envelope on 200', async () => {
    vi.stubGlobal('fetch', mockFetch(200, READY))
    const env = await reportsApi.getBySession('tok', 's1')
    expect(env.state).toBe('ready')
    if (env.state === 'ready') expect(env.report.verdict).toBe('reject')
  })

  it('returns pending envelope on 202', async () => {
    vi.stubGlobal('fetch', mockFetch(202, { status: 'generating' }))
    const env = await reportsApi.getBySession('tok', 's1')
    expect(env).toEqual({ state: 'pending', status: 'generating' })
  })

  it('returns noReport envelope on 404 (does not throw)', async () => {
    vi.stubGlobal('fetch', mockFetch(404, { detail: 'Report not found' }))
    const env = await reportsApi.getBySession('tok', 's1')
    expect(env).toEqual({ state: 'noReport' })
  })

  it('throws ApiError on 403', async () => {
    vi.stubGlobal('fetch', mockFetch(403, { detail: 'Missing reports.view' }))
    await expect(reportsApi.getBySession('tok', 's1')).rejects.toBeInstanceOf(ApiError)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/api/reports.test.ts`
Expected: FAIL — `Cannot find module '@/lib/api/reports'`.

- [ ] **Step 3: Write the implementation**

```ts
// frontend/app/lib/api/reports.ts
import { ApiError, apiFetch } from './client'

// --- Enums (mirror app/modules/reporting/scoring/types.py) ---
export type Verdict = 'advance' | 'borderline' | 'reject'
export type Confidence = 'high' | 'medium' | 'low'
export type Opportunity = 'full' | 'partial' | 'none'
export type SignalState = 'excellent' | 'meets_bar' | 'below_bar' | 'not_assessed'
export type KnockoutStatus = 'passed' | 'failed' | 'insufficient'
export type QuestionLevel = 'below_bar' | 'meets_bar' | 'excellent' | 'not_assessed'
export type HumanDecisionValue = 'advance' | 'reject' | 'hold'

// --- Response shapes (mirror reporting/schemas.py::ReportRead) ---
export interface EvidenceOut {
  quote: string
  timestamp_ms: number
  question_id: string
  grounded: boolean
}

export interface SignalScorecard {
  value: string
  type: string
  weight: number
  knockout: boolean
  state: SignalState
  score: number | null
  opportunity: Opportunity | null
  evidence: EvidenceOut[]
  covered_by: string[]
}

export interface DimensionScoreOut {
  name: string
  score: number | null
  coverage: number
  confidence: Confidence
  note: string | null
}

export interface KnockoutResultOut {
  signal: string
  status: KnockoutStatus
  reason: string
  evidence: EvidenceOut[]
}

export interface QuestionScorecard {
  question_id: string
  question_text: string
  level: QuestionLevel
  evidence: EvidenceOut[]
  red_flags_hit: string[]
  probes_fired: number
  opportunity: Opportunity | null
}

export interface SummaryOut {
  headline: string
  strengths: string[]
  gaps: string[]
  rationale: string
}

export interface ScoringManifest {
  scorer_model: string | null
  reasoning_effort: string | null
  verbosity: string | null
  prompt_version: string | null
  prompt_cache_key: string | null
  scorer_code_version: string | null
  bank_id: string | null
  signal_snapshot_id: string | null
  n_samples: number | null
  cache_hit_rate: number | null
  evidence_grounding_summary: Record<string, unknown> | null
  generated_at: string | null
  correlation_id: string | null
}

export interface HumanDecision {
  decided_by: string
  decision: HumanDecisionValue
  rationale: string
  decided_at: string
}

export interface ReportRead {
  verdict: Verdict
  verdict_reason: string
  overall_score: number | null
  overall_coverage: number
  overall_confidence: Confidence
  dimension_scores: Record<string, DimensionScoreOut>
  knockout_results: KnockoutResultOut[]
  signal_scorecards: SignalScorecard[]
  question_scorecards: QuestionScorecard[]
  summary: SummaryOut
  id: string | null
  session_id: string | null
  status: 'pending' | 'generating' | 'ready' | 'failed'
  engine_version: string | null
  version: number
  scoring_manifest: ScoringManifest | null
  human_decision: HumanDecision | null
  generated_at: string | null
}

export interface HumanDecisionIn {
  decision: HumanDecisionValue
  rationale: string
}

// --- Envelope: the polling-friendly union ---
export type ReportEnvelope =
  | { state: 'ready'; report: ReportRead }
  | { state: 'pending'; status: 'pending' | 'generating' }
  | { state: 'noReport' }

export const reportsApi = {
  /**
   * GET /api/reports/session/{sessionId}.
   * 200 → ready; 202 → pending; 404 → noReport (caught, not thrown);
   * 403 → throws ApiError so the caller can render an access-denied state.
   */
  getBySession: async (
    token: string,
    sessionId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<ReportEnvelope> => {
    try {
      const body = await apiFetch<ReportRead | { status: 'pending' | 'generating' }>(
        `/api/reports/session/${sessionId}`,
        { token, signal: opts?.signal },
      )
      if ('verdict' in body) return { state: 'ready', report: body }
      return { state: 'pending', status: body.status }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return { state: 'noReport' }
      throw err
    }
  },

  regenerate: (token: string, sessionId: string): Promise<{ status: string }> =>
    apiFetch<{ status: string }>(
      `/api/reports/session/${sessionId}/regenerate`,
      { token, method: 'POST', body: JSON.stringify({}) },
    ),

  recordDecision: (
    token: string,
    reportId: string,
    body: HumanDecisionIn,
  ): Promise<ReportRead> =>
    apiFetch<ReportRead>(`/api/reports/${reportId}/decision`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/api/reports.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/api/reports.ts frontend/app/tests/api/reports.test.ts
git commit -m "feat(reports): typed /api/reports namespace + ReportEnvelope"
```

---

### Task 2: Report hooks + state derivation (`lib/hooks/use-report.ts`)

**Files:**
- Create: `frontend/app/lib/hooks/use-report.ts`
- Test: `frontend/app/tests/lib/derive-report-state.test.ts`

`deriveReportState` is a pure function (easy to test exhaustively); the hooks wrap it. We test the pure function directly and keep the hook thin.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/tests/lib/derive-report-state.test.ts
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api/client'
import { deriveReportState } from '@/lib/hooks/use-report'
import type { ReportRead } from '@/lib/api/reports'

const ready = (status: ReportRead['status']): ReportRead =>
  ({ verdict: 'reject', status } as ReportRead)

describe('deriveReportState', () => {
  it('loading while query is loading and no data', () => {
    expect(deriveReportState({ isLoading: true, data: undefined, error: null }).kind).toBe('loading')
  })
  it('forbidden on 403 error', () => {
    expect(deriveReportState({ isLoading: false, data: undefined, error: new ApiError('x', 403) }).kind).toBe('forbidden')
  })
  it('noReport on noReport envelope', () => {
    expect(deriveReportState({ isLoading: false, data: { state: 'noReport' }, error: null }).kind).toBe('noReport')
  })
  it('pending on pending envelope', () => {
    expect(deriveReportState({ isLoading: false, data: { state: 'pending', status: 'generating' }, error: null }).kind).toBe('pending')
  })
  it('failed when ready envelope has status failed', () => {
    expect(deriveReportState({ isLoading: false, data: { state: 'ready', report: ready('failed') }, error: null }).kind).toBe('failed')
  })
  it('ready when ready envelope has status ready', () => {
    const s = deriveReportState({ isLoading: false, data: { state: 'ready', report: ready('ready') }, error: null })
    expect(s.kind).toBe('ready')
    if (s.kind === 'ready') expect(s.report.verdict).toBe('reject')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/lib/derive-report-state.test.ts`
Expected: FAIL — `Cannot find module '@/lib/hooks/use-report'`.

- [ ] **Step 3: Write the implementation**

```ts
// frontend/app/lib/hooks/use-report.ts
'use client'

import { useRef } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError } from '@/lib/api/client'
import {
  reportsApi,
  type HumanDecisionIn,
  type ReportEnvelope,
  type ReportRead,
} from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export type ReportState =
  | { kind: 'loading' }
  | { kind: 'forbidden' }
  | { kind: 'noReport' }
  | { kind: 'pending' }
  | { kind: 'failed'; report: ReportRead }
  | { kind: 'ready'; report: ReportRead }

/** Pure mapping from query result → the state the page switches on. */
export function deriveReportState(q: {
  isLoading: boolean
  data: ReportEnvelope | undefined
  error: unknown
}): ReportState {
  if (q.error instanceof ApiError && q.error.status === 403) return { kind: 'forbidden' }
  if (q.data) {
    if (q.data.state === 'noReport') return { kind: 'noReport' }
    if (q.data.state === 'pending') return { kind: 'pending' }
    const r = q.data.report
    return r.status === 'failed' ? { kind: 'failed', report: r } : { kind: 'ready', report: r }
  }
  if (q.isLoading) return { kind: 'loading' }
  // Non-403 error with no data — surface as loading-failed via the route error boundary.
  if (q.error) throw q.error
  return { kind: 'loading' }
}

const GRACE_MS = 30_000 // poll through the 404 window right after a regenerate

export function useReport(sessionId: string) {
  // When a regenerate was just requested, keep polling even while the GET
  // still returns noReport (the actor hasn't created the row yet).
  const generatingUntilRef = useRef<number>(0)

  const query: UseQueryResult<ReportEnvelope> = useQuery<ReportEnvelope>({
    queryKey: ['report', sessionId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.getBySession(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    retry: (count, err) => {
      if (err instanceof ApiError && (err.status === 403 || err.status === 404)) return false
      return count < 2
    },
    refetchInterval: (q) => {
      const data = q.state.data
      if (data?.state === 'pending') return 4000
      if (data?.state === 'noReport' && Date.now() < generatingUntilRef.current) return 4000
      return false
    },
  })

  const state = deriveReportState({
    isLoading: query.isLoading,
    data: query.data,
    error: query.error,
  })

  return { state, query, markGenerating: () => { generatingUntilRef.current = Date.now() + GRACE_MS } }
}

export function useRecordDecision(sessionId: string) {
  const qc = useQueryClient()
  return useMutation<ReportRead, Error, { reportId: string; body: HumanDecisionIn }>({
    mutationFn: async ({ reportId, body }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.recordDecision(token, reportId, body)
    },
    onSuccess: (report) => {
      qc.setQueryData<ReportEnvelope>(['report', sessionId], { state: 'ready', report })
    },
  })
}

export function useRegenerateReport(sessionId: string) {
  const qc = useQueryClient()
  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return reportsApi.regenerate(token, sessionId)
    },
    onSuccess: () => {
      // Optimistically flip to pending so polling starts immediately.
      qc.setQueryData<ReportEnvelope>(['report', sessionId], { state: 'pending', status: 'generating' })
    },
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/lib/derive-report-state.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/hooks/use-report.ts frontend/app/tests/lib/derive-report-state.test.ts
git commit -m "feat(reports): useReport polling hook + deriveReportState"
```

---

### Task 3: Pure formatters + tone maps (`report-format.ts`)

**Files:**
- Create: `frontend/app/components/dashboard/reports/report-format.ts`
- Test: `frontend/app/tests/components/reports/report-format.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/tests/components/reports/report-format.test.ts
import { describe, expect, it } from 'vitest'
import {
  scoreToTen, formatTimestamp, verdictMeta, scoreBandTone,
  signalStateTone, knockoutStatusTone, confidenceLabel, TONE_INK,
} from '@/components/dashboard/reports/report-format'

describe('report-format', () => {
  it('scoreToTen: 36 -> "3.6", null -> null', () => {
    expect(scoreToTen(36)).toBe('3.6')
    expect(scoreToTen(100)).toBe('10.0')
    expect(scoreToTen(null)).toBeNull()
  })
  it('formatTimestamp: ms -> mm:ss', () => {
    expect(formatTimestamp(90000)).toBe('01:30')
    expect(formatTimestamp(0)).toBe('00:00')
    expect(formatTimestamp(252000)).toBe('04:12')
  })
  it('verdictMeta maps each verdict to a tone + label', () => {
    expect(verdictMeta('advance').tone).toBe('ok')
    expect(verdictMeta('borderline').tone).toBe('human')
    expect(verdictMeta('reject').tone).toBe('danger')
    expect(verdictMeta('borderline').label).toBe('Borderline')
  })
  it('scoreBandTone: >=75 ok, 55-74 caution, <55 danger, null neutral', () => {
    expect(scoreBandTone(80)).toBe('ok')
    expect(scoreBandTone(60)).toBe('caution')
    expect(scoreBandTone(30)).toBe('danger')
    expect(scoreBandTone(null)).toBe('neutral')
  })
  it('signalStateTone + knockoutStatusTone', () => {
    expect(signalStateTone('excellent')).toBe('ok')
    expect(signalStateTone('below_bar')).toBe('danger')
    expect(signalStateTone('not_assessed')).toBe('neutral')
    expect(knockoutStatusTone('passed')).toBe('ok')
    expect(knockoutStatusTone('failed')).toBe('danger')
    expect(knockoutStatusTone('insufficient')).toBe('caution')
  })
  it('confidenceLabel + TONE_INK has a var for every tone', () => {
    expect(confidenceLabel('high')).toBe('High')
    expect(TONE_INK.ok).toMatch(/var\(--px-/)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/report-format.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```ts
// frontend/app/components/dashboard/reports/report-format.ts
import type {
  Confidence, KnockoutStatus, SignalState, Verdict,
} from '@/lib/api/reports'

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

/** 0–100 integer → "X.X" out of ten; null stays null (never a zero). */
export function scoreToTen(score: number | null): string | null {
  if (score === null || score === undefined) return null
  return (score / 10).toFixed(1)
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
  advance: { label: 'Advance', tone: 'ok' },
  borderline: { label: 'Borderline', tone: 'human' },
  reject: { label: 'Reject', tone: 'danger' },
}
export function verdictMeta(v: Verdict): VerdictMeta {
  return VERDICT_META[v]
}

/** Tier tone from a 0–100 score (advance≥75 / borderline 55–74 / reject<55). */
export function scoreBandTone(score: number | null): Tone {
  if (score === null || score === undefined) return 'neutral'
  if (score >= 75) return 'ok'
  if (score >= 55) return 'caution'
  return 'danger'
}

export function signalStateTone(state: SignalState): Tone {
  switch (state) {
    case 'excellent':
    case 'meets_bar':
      return 'ok'
    case 'below_bar':
      return 'danger'
    case 'not_assessed':
      return 'neutral'
  }
}

export function knockoutStatusTone(status: KnockoutStatus): Tone {
  switch (status) {
    case 'passed':
      return 'ok'
    case 'failed':
      return 'danger'
    case 'insufficient':
      return 'caution'
  }
}

const SIGNAL_STATE_LABEL: Record<SignalState, string> = {
  excellent: 'Excellent',
  meets_bar: 'Meets bar',
  below_bar: 'Below bar',
  not_assessed: 'Not assessed',
}
export function signalStateLabel(state: SignalState): string {
  return SIGNAL_STATE_LABEL[state]
}

const KO_STATUS_LABEL: Record<KnockoutStatus, string> = {
  passed: 'Passed',
  failed: 'Failed',
  insufficient: 'Insufficient',
}
export function knockoutStatusLabel(status: KnockoutStatus): string {
  return KO_STATUS_LABEL[status]
}

export function confidenceLabel(c: Confidence): string {
  return { high: 'High', medium: 'Medium', low: 'Low' }[c]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/report-format.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/report-format.ts frontend/app/tests/components/reports/report-format.test.ts
git commit -m "feat(reports): pure formatters + Iris tone maps"
```

---

### Task 4: Animation CSS + `ScoreGauge`

**Files:**
- Create: `frontend/app/components/dashboard/reports/report.css`
- Create: `frontend/app/components/dashboard/reports/ScoreGauge.tsx`
- Test: `frontend/app/tests/components/reports/ScoreGauge.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/ScoreGauge.test.tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreGauge } from '@/components/dashboard/reports/ScoreGauge'

describe('ScoreGauge', () => {
  it('renders normalized 0-10 value (36 -> 3.6)', () => {
    render(<ScoreGauge score={36} label="Overall" />)
    expect(screen.getByText('3.6')).toBeInTheDocument()
  })
  it('renders "n/a" for a null score (never a zero)', () => {
    render(<ScoreGauge score={null} label="Behavioral" />)
    expect(screen.getByText('n/a')).toBeInTheDocument()
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
  })
  it('exposes an accessible label with the value + context', () => {
    render(<ScoreGauge score={70} label="Technical" />)
    expect(screen.getByRole('img', { name: /Technical score 7\.0 out of 10/i })).toBeInTheDocument()
  })
  it('null gauge label says not assessed', () => {
    render(<ScoreGauge score={null} label="Behavioral" />)
    expect(screen.getByRole('img', { name: /Behavioral not assessed/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/ScoreGauge.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the CSS**

```css
/* frontend/app/components/dashboard/reports/report.css */
@keyframes px-gauge-fill {
  to { stroke-dashoffset: var(--px-gauge-final-offset); }
}
.px-gauge-ring {
  animation: px-gauge-fill 1.1s cubic-bezier(0.2, 0.8, 0.2, 1) forwards;
}
@keyframes px-radar-pop {
  from { opacity: 0; transform: scale(0.5); }
  to   { opacity: 1; transform: scale(1); }
}
.px-radar-data {
  transform-box: fill-box;
  transform-origin: center;
  animation: px-radar-pop 0.9s 0.3s ease-out forwards;
}
@media (prefers-reduced-motion: reduce) {
  .px-gauge-ring { animation: none; stroke-dashoffset: var(--px-gauge-final-offset); }
  .px-radar-data { animation: none; opacity: 1; transform: none; }
}
```

- [ ] **Step 4: Write the `ScoreGauge` implementation**

The number renders its final value immediately (deterministic, no flash); only the **ring** animates (CSS `stroke-dashoffset` sweep), and the radar pop-in (Task 7) covers the other motion. Reduced-motion disables both via the `@media` block in `report.css`. No `useEffect`/`rAF`/`matchMedia` in JS — keeps the component pure and the tests synchronous.

```tsx
// frontend/app/components/dashboard/reports/ScoreGauge.tsx
import type { CSSProperties } from 'react'

import { scoreToTen, scoreBandTone, TONE_FILL, type Tone } from './report-format'
import './report.css'

interface ScoreGaugeProps {
  /** 0–100 domain (the report's native scale). null → "not assessed". */
  score: number | null
  label: string
  /** Diameter in px. Default 58 (dimension gauges); pass ~118 for Overall. */
  size?: number
  /** Override the tier-derived ring tone (e.g. color Overall by verdict). */
  toneOverride?: Tone
  /** Secondary caption under the gauge (e.g. "cov 0.66 · medium"). */
  caption?: string
}

const R = 42
const C = 2 * Math.PI * R // ≈ 263.9

export function ScoreGauge({ score, label, size = 58, toneOverride, caption }: ScoreGaugeProps) {
  const assessed = score !== null && score !== undefined
  const ten = scoreToTen(score)
  const tone = toneOverride ?? scoreBandTone(score)
  const finalOffset = assessed ? C * (1 - (score as number) / 100) : C
  const stroke = size >= 90 ? 9 : 10
  const numFont = size >= 90 ? 22 : 26 // viewBox units (100×100)
  const aria = assessed ? `${label} score ${ten} out of 10` : `${label} not assessed`
  const ringStyle = { '--px-gauge-final-offset': String(finalOffset) } as CSSProperties

  return (
    <div className="flex flex-col items-center text-center">
      <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={aria}>
        <circle
          cx="50" cy="50" r={R} fill="none"
          stroke="var(--px-surface-3)" strokeWidth={stroke}
          {...(!assessed ? { strokeDasharray: '3 4' } : {})}
        />
        {assessed && (
          <circle
            className="px-gauge-ring"
            cx="50" cy="50" r={R} fill="none"
            stroke={TONE_FILL[tone]} strokeWidth={stroke} strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={C}
            transform="rotate(-90 50 50)"
            style={ringStyle}
          />
        )}
        {assessed ? (
          <text x="50" y={size >= 90 ? 48 : 58} textAnchor="middle"
            style={{ fontSize: numFont, fontWeight: 800, fill: 'var(--px-fg)' }}>{ten}</text>
        ) : (
          <text x="50" y="56" textAnchor="middle"
            style={{ fontSize: 15, fontWeight: 700, fill: 'var(--px-fg-4)' }}>n/a</text>
        )}
        {assessed && size >= 90 && (
          <text x="50" y="64" textAnchor="middle" style={{ fontSize: 7, fill: 'var(--px-fg-4)' }}>/ 10</text>
        )}
      </svg>
      <div className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{label}</div>
      {caption && <div className="text-[9px]" style={{ color: 'var(--px-fg-4)' }}>{caption}</div>}
    </div>
  )
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/ScoreGauge.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/reports/report.css frontend/app/components/dashboard/reports/ScoreGauge.tsx frontend/app/tests/components/reports/ScoreGauge.test.tsx
git commit -m "feat(reports): animated 0-10 ScoreGauge (SVG, reduced-motion safe)"
```

---

### Task 5: `VerdictBand` + `VerdictChip`

**Files:**
- Create: `frontend/app/components/dashboard/reports/VerdictBand.tsx`
- Test: `frontend/app/tests/components/reports/VerdictBand.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/VerdictBand.test.tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VerdictBand, VerdictChip } from '@/components/dashboard/reports/VerdictBand'

describe('VerdictBand', () => {
  it('renders the verdict label', () => {
    render(<VerdictBand verdict="reject" />)
    expect(screen.getByText('Reject')).toBeInTheDocument()
  })
  it('borderline uses the human (lavender) ink token', () => {
    render(<VerdictBand verdict="borderline" />)
    const el = screen.getByText('Borderline')
    expect(el).toHaveStyle({ color: 'var(--px-human)' })
  })
})

describe('VerdictChip', () => {
  it('renders a compact uppercase chip', () => {
    render(<VerdictChip verdict="advance" />)
    expect(screen.getByText('ADVANCE')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/VerdictBand.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/VerdictBand.tsx
import type { Verdict } from '@/lib/api/reports'
import { TONE_FILL, TONE_INK, verdictMeta } from './report-format'

/** Large banded verdict word — the report headline. */
export function VerdictBand({ verdict }: { verdict: Verdict }) {
  const meta = verdictMeta(verdict)
  return (
    <div
      className="text-[22px] font-extrabold tracking-tight"
      style={{ color: TONE_INK[meta.tone] }}
    >
      {meta.label}
    </div>
  )
}

/** Compact solid chip for the top bar / dense rows. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const meta = verdictMeta(verdict)
  // High-contrast text on the saturated fill. danger/caution fills are
  // saturated enough for white; ok/human fills are pastel → use ink.
  const onFillWhite = meta.tone === 'danger'
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-0.5 text-[11px] font-bold tracking-wide"
      style={{
        background: TONE_FILL[meta.tone],
        color: onFillWhite ? '#fff' : TONE_INK[meta.tone],
      }}
    >
      {meta.label.toUpperCase()}
    </span>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/VerdictBand.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/VerdictBand.tsx frontend/app/tests/components/reports/VerdictBand.test.tsx
git commit -m "feat(reports): VerdictBand + VerdictChip"
```

---

### Task 6: `EvidenceQuote` (forward-compat seek control)

**Files:**
- Create: `frontend/app/components/dashboard/reports/EvidenceQuote.tsx`
- Test: `frontend/app/tests/components/reports/EvidenceQuote.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/EvidenceQuote.test.tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EvidenceQuote } from '@/components/dashboard/reports/EvidenceQuote'

const ev = { quote: 'I have sixteen years', timestamp_ms: 90000, question_id: 'years_experience', grounded: true }

describe('EvidenceQuote', () => {
  it('renders the quote, mm:ss timestamp, and question id', () => {
    render(<EvidenceQuote evidence={ev} />)
    expect(screen.getByText(/I have sixteen years/)).toBeInTheDocument()
    expect(screen.getByText('01:30')).toBeInTheDocument()
    expect(screen.getByText(/years_experience/)).toBeInTheDocument()
  })
  it('marks ungrounded evidence with a warning', () => {
    render(<EvidenceQuote evidence={{ ...ev, grounded: false }} />)
    expect(screen.getByLabelText(/unverified/i)).toBeInTheDocument()
  })
  it('the timestamp chip is inert today (no seek handler) and labelled as future playback', () => {
    render(<EvidenceQuote evidence={ev} />)
    const chip = screen.getByText('01:30').closest('[data-seek-stub]')
    expect(chip).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/EvidenceQuote.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/EvidenceQuote.tsx
import type { EvidenceOut } from '@/lib/api/reports'
import { formatTimestamp } from './report-format'

interface EvidenceQuoteProps {
  evidence: EvidenceOut
  /** Tone of the left rule + chip (defaults to accent violet). */
  toneVar?: string
}

/**
 * One grounded evidence quote. The timestamp chip is the forward-compat
 * seek control for sub-project B (recording): it already carries
 * `timestamp_ms` + `question_id`; today it is inert (`data-seek-stub`),
 * later it becomes a button that seeks the session player. Keep the prop
 * shape stable so that is a drop-in.
 */
export function EvidenceQuote({ evidence, toneVar = 'var(--px-accent)' }: EvidenceQuoteProps) {
  return (
    <div
      className="mt-2 rounded-r-lg py-1.5 pl-2.5 pr-2"
      style={{ borderLeft: `3px solid ${toneVar}`, background: 'var(--px-accent-tint)' }}
    >
      <p className="text-[11.5px] italic" style={{ color: 'var(--px-fg)' }}>
        “{evidence.quote}”
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[9.5px]" style={{ color: 'var(--px-fg-3)' }}>
        <span
          data-seek-stub
          title="Jump to this moment — playback arrives with session recording"
          className="inline-flex items-center gap-1 rounded border px-1.5 py-px font-semibold"
          style={{ borderColor: 'var(--px-hairline-strong)', background: 'var(--px-surface)', color: toneVar }}
        >
          ▶ {formatTimestamp(evidence.timestamp_ms)}
        </span>
        <span style={{ fontFamily: 'var(--font-mono, monospace)' }}>Q {evidence.question_id}</span>
        {evidence.grounded ? (
          <span aria-label="verified in transcript">grounded ✓</span>
        ) : (
          <span aria-label="unverified — quote not found in transcript" style={{ color: 'var(--px-caution)' }}>
            ⚠ unverified
          </span>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/EvidenceQuote.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/EvidenceQuote.tsx frontend/app/tests/components/reports/EvidenceQuote.test.tsx
git commit -m "feat(reports): EvidenceQuote with forward-compat seek chip"
```

---

### Task 7: `SignalSpiderChart`

**Files:**
- Create: `frontend/app/components/dashboard/reports/SignalSpiderChart.tsx`
- Test: `frontend/app/tests/components/reports/SignalSpiderChart.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/SignalSpiderChart.test.tsx
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { SignalSpiderChart, radarPolygonPoints } from '@/components/dashboard/reports/SignalSpiderChart'
import type { SignalScorecard } from '@/lib/api/reports'

const sig = (value: string, state: SignalScorecard['state'], score: number | null): SignalScorecard => ({
  value, type: 'competency', weight: 2, knockout: false, state, score, opportunity: 'full', evidence: [], covered_by: [],
})

describe('radarPolygonPoints', () => {
  it('maps n values to n "x,y" pairs scaled by value/10', () => {
    const pts = radarPolygonPoints([10, 0], 100, 100, 80).split(' ')
    expect(pts).toHaveLength(2)
    // first axis points straight up at full radius (value 10): y = 100 - 80.
    // Coordinates are toFixed(1)-formatted, so expect "100.0,20.0".
    expect(pts[0]).toBe('100.0,20.0')
  })
})

describe('SignalSpiderChart', () => {
  it('returns null with fewer than 3 assessed signals', () => {
    const { container } = render(<SignalSpiderChart signals={[sig('A', 'meets_bar', 70), sig('B', 'below_bar', 30)]} />)
    expect(container.firstChild).toBeNull()
  })
  it('plots only assessed signals (excludes not_assessed)', () => {
    const signals = [
      sig('A', 'meets_bar', 70), sig('B', 'excellent', 100),
      sig('C', 'below_bar', 30), sig('D', 'not_assessed', null),
    ]
    const { container } = render(<SignalSpiderChart signals={signals} />)
    // 3 assessed → polygon present with 3 vertices
    const poly = container.querySelector('polygon.px-radar-data') as SVGPolygonElement | null
    expect(poly).not.toBeNull()
    expect(poly!.getAttribute('points')!.trim().split(' ')).toHaveLength(3)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/SignalSpiderChart.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/SignalSpiderChart.tsx
'use client'

import type { SignalScorecard } from '@/lib/api/reports'
import './report.css'

const CX = 100
const CY = 100
const RADIUS = 80

/** angle for axis i of n, starting straight up (-90°), clockwise. */
function axisAngle(i: number, n: number): number {
  return (-90 + (360 / n) * i) * (Math.PI / 180)
}

/** values are 0–10; returns "x,y x,y ..." scaled by value/10. */
export function radarPolygonPoints(values: number[], cx: number, cy: number, radius: number): string {
  const n = values.length
  return values
    .map((v, i) => {
      const a = axisAngle(i, n)
      const r = radius * (Math.max(0, Math.min(10, v)) / 10)
      return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`
    })
    .join(' ')
}

function axisEndpoints(n: number): { x: number; y: number }[] {
  return Array.from({ length: n }, (_, i) => {
    const a = axisAngle(i, n)
    return { x: CX + RADIUS * Math.cos(a), y: CY + RADIUS * Math.sin(a) }
  })
}

export function SignalSpiderChart({ signals }: { signals: SignalScorecard[] }) {
  const assessed = signals.filter((s) => s.state !== 'not_assessed' && s.score !== null)
  if (assessed.length < 3) return null

  const n = assessed.length
  const ends = axisEndpoints(n)
  const outer = ends.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const inner = ends
    .map((p) => `${(CX + 0.5 * (p.x - CX)).toFixed(1)},${(CY + 0.5 * (p.y - CY)).toFixed(1)}`)
    .join(' ')
  const data = radarPolygonPoints(assessed.map((s) => (s.score as number) / 10), CX, CY, RADIUS)

  return (
    <svg viewBox="0 0 200 200" width="200" height="200" role="img" aria-label="Signal score profile">
      <polygon points={outer} fill="none" stroke="var(--px-hairline)" strokeWidth="0.8" />
      <polygon points={inner} fill="none" stroke="var(--px-hairline)" strokeWidth="0.8" />
      {ends.map((p, i) => (
        <line key={i} x1={CX} y1={CY} x2={p.x} y2={p.y} stroke="var(--px-hairline-strong)" strokeWidth="0.5" />
      ))}
      <polygon
        className="px-radar-data"
        points={data}
        fill="var(--px-accent-tint)"
        stroke="var(--px-accent)"
        strokeWidth="1.6"
      />
      {assessed.map((s, i) => {
        const a = axisAngle(i, n)
        const rr = RADIUS * ((s.score as number) / 100) // score is 0–100; full radius at 100
        return <circle key={s.value} cx={CX + rr * Math.cos(a)} cy={CY + rr * Math.sin(a)} r="2" fill="var(--px-accent)" />
      })}
      {ends.map((p, i) => {
        const a = axisAngle(i, n)
        const lx = CX + (RADIUS + 12) * Math.cos(a)
        const ly = CY + (RADIUS + 12) * Math.sin(a)
        const anchor = Math.abs(Math.cos(a)) < 0.3 ? 'middle' : Math.cos(a) > 0 ? 'start' : 'end'
        return (
          <text key={`l${i}`} x={lx} y={ly} textAnchor={anchor as 'start' | 'middle' | 'end'}
            style={{ fontSize: 6.5, fill: 'var(--px-fg-3)' }}>
            {assessed[i].value.length > 14 ? `${assessed[i].value.slice(0, 13)}…` : assessed[i].value}
          </text>
        )
      })}
    </svg>
  )
}
```

> Radius math check: the vertex dot uses `rr = RADIUS*(score/100)` — score 100 → 80 (outer ring), 50 → 40 (mid). Consistent with `radarPolygonPoints`, which takes a 0–10 value and scales by `/10` (50/100 → 5 → 5/10·80 = 40). ✓

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/SignalSpiderChart.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/SignalSpiderChart.tsx frontend/app/tests/components/reports/SignalSpiderChart.test.tsx
git commit -m "feat(reports): SignalSpiderChart (assessed-only radar)"
```

---

### Task 8: `SignalScorecards` (knockouts + signals, evidence inline)

**Files:**
- Create: `frontend/app/components/dashboard/reports/SignalScorecards.tsx`
- Test: `frontend/app/tests/components/reports/SignalScorecards.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/SignalScorecards.test.tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SignalScorecards } from '@/components/dashboard/reports/SignalScorecards'
import type { KnockoutResultOut, SignalScorecard } from '@/lib/api/reports'

const ko: KnockoutResultOut = {
  signal: '4+ years experience', status: 'failed',
  reason: 'answer did not establish required tenure',
  evidence: [{ quote: 'more than sixteen years', timestamp_ms: 90000, question_id: 'years', grounded: true }],
}
const passSig: SignalScorecard = {
  value: 'REST API design', type: 'competency', weight: 2, knockout: false,
  state: 'meets_bar', score: 70, opportunity: 'full', evidence: [], covered_by: ['q1'],
}
const naSig: SignalScorecard = {
  value: 'System design', type: 'competency', weight: 2, knockout: false,
  state: 'not_assessed', score: null, opportunity: 'none', evidence: [], covered_by: [],
}

describe('SignalScorecards', () => {
  it('shows every knockout reason AND its evidence quote inline', () => {
    render(<SignalScorecards knockouts={[ko]} signals={[]} />)
    expect(screen.getByText(/did not establish required tenure/)).toBeInTheDocument()
    expect(screen.getByText(/more than sixteen years/)).toBeInTheDocument() // the "catch the miscalibration" payoff
  })
  it('renders not_assessed signals as an explicit state, not a zero', () => {
    render(<SignalScorecards knockouts={[]} signals={[naSig]} />)
    expect(screen.getByText('Not assessed')).toBeInTheDocument()
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
  })
  it('renders a passing signal with its 0-10 score', () => {
    render(<SignalScorecards knockouts={[]} signals={[passSig]} />)
    expect(screen.getByText('7.0')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/SignalScorecards.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/SignalScorecards.tsx
import type { KnockoutResultOut, SignalScorecard } from '@/lib/api/reports'
import { EvidenceQuote } from './EvidenceQuote'
import {
  knockoutStatusLabel, knockoutStatusTone, scoreToTen, signalStateLabel,
  signalStateTone, TONE_BG, TONE_FILL, TONE_INK,
} from './report-format'

interface Props {
  knockouts: KnockoutResultOut[]
  signals: SignalScorecard[]
}

/**
 * Knockouts first (they gate the verdict), then the weighted signals.
 * EVERY knockout shows its reason + evidence inline — that is the
 * mechanism that lets a recruiter catch a miscalibrated rubric (there is
 * deliberately no heuristic guessing which one is wrong).
 */
export function SignalScorecards({ knockouts, signals }: Props) {
  const nonKnockoutSignals = signals.filter((s) => !s.knockout)
  return (
    <div>
      {knockouts.map((k) => {
        const tone = knockoutStatusTone(k.status)
        return (
          <div
            key={k.signal}
            className="mb-2 rounded-lg p-2.5"
            style={{ border: `1px solid ${TONE_INK[tone]}33`, background: TONE_BG[tone] }}
          >
            <div className="flex items-center gap-2">
              <span className="rounded px-1.5 py-px text-[9px] font-semibold" style={{ background: 'var(--px-fg)', color: '#fff' }}>
                KNOCKOUT
              </span>
              <span className="rounded px-1.5 py-px text-[9px] font-semibold"
                style={{ background: TONE_FILL[tone], color: tone === 'danger' ? '#fff' : TONE_INK[tone] }}>
                {knockoutStatusLabel(k.status).toUpperCase()}
              </span>
              <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{k.signal}</span>
            </div>
            <div className="mt-1.5 text-[10.5px]" style={{ color: TONE_INK[tone] }}>Reason: {k.reason}</div>
            {k.evidence.map((e, i) => (
              <EvidenceQuote key={i} evidence={e} toneVar={TONE_INK[tone]} />
            ))}
          </div>
        )
      })}

      {nonKnockoutSignals.map((s) => {
        const tone = signalStateTone(s.state)
        const ten = scoreToTen(s.score)
        return (
          <div key={s.value} className="mb-1.5 rounded-lg border p-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 flex-none rounded-full" style={{ background: TONE_FILL[tone] }} aria-hidden="true" />
              <span className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{s.value}</span>
              <span className="rounded px-1.5 py-px text-[9px] font-semibold"
                style={{ background: TONE_FILL[tone], color: tone === 'danger' ? '#fff' : TONE_INK[tone] }}>
                {signalStateLabel(s.state)}
              </span>
              {s.state === 'not_assessed' ? (
                <span className="ml-auto text-[9px]" style={{ color: 'var(--px-fg-4)' }}>
                  opportunity {s.opportunity ?? 'none'}
                </span>
              ) : (
                <>
                  <div className="ml-auto h-1.5 w-16 overflow-hidden rounded-full" style={{ background: 'var(--px-surface-3)' }}>
                    <span className="block h-full rounded-full" style={{ width: `${s.score ?? 0}%`, background: TONE_FILL[tone] }} />
                  </div>
                  <span className="w-8 text-right text-[10px]" style={{ color: 'var(--px-fg-3)' }}>{ten}</span>
                </>
              )}
            </div>
            {s.evidence.map((e, i) => (
              <EvidenceQuote key={i} evidence={e} />
            ))}
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/SignalScorecards.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/SignalScorecards.tsx frontend/app/tests/components/reports/SignalScorecards.test.tsx
git commit -m "feat(reports): SignalScorecards with evidence beside every knockout"
```

---

### Task 9: `AiRecommendationCard`

**Files:**
- Create: `frontend/app/components/dashboard/reports/AiRecommendationCard.tsx`
- Test: `frontend/app/tests/components/reports/AiRecommendationCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/AiRecommendationCard.test.tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AiRecommendationCard } from '@/components/dashboard/reports/AiRecommendationCard'
import type { ReportRead } from '@/lib/api/reports'

const report = {
  verdict: 'reject', verdict_reason: 'failed must-have: Python proficiency',
  overall_score: 36, overall_coverage: 0.7, overall_confidence: 'medium',
  dimension_scores: {
    technical: { name: 'Technical', score: 37, coverage: 0.66, confidence: 'medium', note: null },
    behavioral: { name: 'Behavioral', score: null, coverage: 0, confidence: 'low', note: 'no signal' },
    communication: { name: 'Communication', score: 30, coverage: 1, confidence: 'medium', note: 'content-only' },
  },
} as unknown as ReportRead

describe('AiRecommendationCard', () => {
  it('renders the verdict band, overall 0-10, coverage and confidence', async () => {
    render(<AiRecommendationCard report={report} />)
    expect(screen.getByText('Reject')).toBeInTheDocument()
    expect(await screen.findByText('3.6')).toBeInTheDocument() // overall gauge
    expect(screen.getByText('0.70')).toBeInTheDocument()       // coverage
    expect(screen.getByText('Medium')).toBeInTheDocument()     // confidence
  })
  it('renders Behavioral dimension as n/a (null score)', () => {
    render(<AiRecommendationCard report={report} />)
    expect(screen.getByRole('img', { name: /Behavioral not assessed/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/AiRecommendationCard.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/AiRecommendationCard.tsx
import type { DimensionScoreOut, ReportRead } from '@/lib/api/reports'
import { ScoreGauge } from './ScoreGauge'
import { VerdictBand } from './VerdictBand'
import { confidenceLabel, verdictMeta } from './report-format'

const DIMENSION_ORDER: { key: string; label: string }[] = [
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Communication' },
]

function dimCaption(d: DimensionScoreOut | undefined): string | undefined {
  if (!d) return undefined
  if (d.score === null) return 'not assessed'
  return `cov ${d.coverage.toFixed(2)} · ${confidenceLabel(d.confidence).toLowerCase()}`
}

export function AiRecommendationCard({ report }: { report: ReportRead }) {
  const tone = verdictMeta(report.verdict).tone
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="AI recommendation">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>
        AI recommendation
      </h2>
      <VerdictBand verdict={report.verdict} />
      <p className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>{report.verdict_reason}</p>

      <div className="my-3 flex justify-center">
        <ScoreGauge score={report.overall_score} label="Overall" size={118} toneOverride={tone} />
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        {DIMENSION_ORDER.map(({ key, label }) => {
          const d = report.dimension_scores[key]
          return <ScoreGauge key={key} score={d?.score ?? null} label={label} size={58} caption={dimCaption(d)} />
        })}
      </div>

      <div className="mt-3 flex gap-2 border-t pt-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Coverage</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{report.overall_coverage.toFixed(2)}</div>
        </div>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Confidence</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{confidenceLabel(report.overall_confidence)}</div>
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/AiRecommendationCard.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/AiRecommendationCard.tsx frontend/app/tests/components/reports/AiRecommendationCard.test.tsx
git commit -m "feat(reports): AiRecommendationCard (verdict + all-score gauges)"
```

---

### Task 10: `HumanDecisionPanel`

**Files:**
- Create: `frontend/app/components/dashboard/reports/HumanDecisionPanel.tsx`
- Test: `frontend/app/tests/components/reports/HumanDecisionPanel.test.tsx`

Presentational: it owns the form UI + borderline lock, and calls `onSubmit(decision, rationale)`. The page wires `useRecordDecision`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/HumanDecisionPanel.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { HumanDecisionPanel } from '@/components/dashboard/reports/HumanDecisionPanel'

describe('HumanDecisionPanel', () => {
  it('disables submit until a decision is chosen AND a rationale is entered', () => {
    render(<HumanDecisionPanel verdict="reject" decision={null} onSubmit={vi.fn()} isSubmitting={false} />)
    const submit = screen.getByRole('button', { name: /record decision/i })
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /^reject$/i }))
    expect(submit).toBeDisabled() // rationale still empty
    fireEvent.change(screen.getByLabelText(/rationale/i), { target: { value: 'agrees with the evidence' } })
    expect(submit).toBeEnabled()
  })

  it('borderline shows the locked, required-review notice (never one-click)', () => {
    render(<HumanDecisionPanel verdict="borderline" decision={null} onSubmit={vi.fn()} isSubmitting={false} />)
    expect(screen.getByText(/requires a human decision/i)).toBeInTheDocument()
    // no quick-action button exists; the only path is the rationale-gated form
    expect(screen.getByRole('button', { name: /record decision/i })).toBeDisabled()
  })

  it('calls onSubmit with the chosen decision + rationale', () => {
    const onSubmit = vi.fn()
    render(<HumanDecisionPanel verdict="advance" decision={null} onSubmit={onSubmit} isSubmitting={false} />)
    fireEvent.click(screen.getByRole('button', { name: /^advance$/i }))
    fireEvent.change(screen.getByLabelText(/rationale/i), { target: { value: 'strong signals' } })
    fireEvent.click(screen.getByRole('button', { name: /record decision/i }))
    expect(onSubmit).toHaveBeenCalledWith('advance', 'strong signals')
  })

  it('renders the recorded decision + a Change decision affordance when already decided', () => {
    render(
      <HumanDecisionPanel
        verdict="reject"
        decision={{ decided_by: 'u1', decision: 'reject', rationale: 'weak', decided_at: '2026-05-26T00:00:00Z' }}
        onSubmit={vi.fn()}
        isSubmitting={false}
      />,
    )
    expect(screen.getByText(/decision recorded/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /change decision/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/HumanDecisionPanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/HumanDecisionPanel.tsx
'use client'

import { useState } from 'react'

import { Button } from '@/components/px'
import type { HumanDecision, HumanDecisionValue, Verdict } from '@/lib/api/reports'

interface Props {
  verdict: Verdict
  decision: HumanDecision | null
  onSubmit: (decision: HumanDecisionValue, rationale: string) => void
  isSubmitting: boolean
}

const CHOICES: { value: HumanDecisionValue; label: string }[] = [
  { value: 'advance', label: 'Advance' },
  { value: 'reject', label: 'Reject' },
  { value: 'hold', label: 'Hold' },
]

export function HumanDecisionPanel({ verdict, decision, onSubmit, isSubmitting }: Props) {
  const [editing, setEditing] = useState(false)
  const [choice, setChoice] = useState<HumanDecisionValue | null>(null)
  const [rationale, setRationale] = useState('')

  const showForm = editing || decision === null
  const canSubmit = choice !== null && rationale.trim().length > 0 && !isSubmitting

  if (!showForm && decision) {
    return (
      <section className="rounded-xl border p-3.5" style={{ borderColor: 'var(--px-accent-line)', background: 'var(--px-accent-tint)' }} aria-label="Human decision">
        <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-accent)' }}>Your decision</h2>
        <p className="text-[12px]" style={{ color: 'var(--px-fg)' }}>
          <b>Decision recorded:</b> {decision.decision.toUpperCase()}
        </p>
        <p className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>{decision.rationale}</p>
        <p className="mt-1 text-[9.5px]" style={{ color: 'var(--px-fg-4)' }}>
          {new Date(decision.decided_at).toLocaleString()}
        </p>
        <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => { setEditing(true); setChoice(decision.decision); setRationale(decision.rationale) }}>
          Change decision
        </Button>
      </section>
    )
  }

  return (
    <section className="rounded-xl border p-3.5" style={{ borderColor: 'var(--px-accent-line)', background: 'var(--px-accent-tint)' }} aria-label="Human decision">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-accent)' }}>
        Your decision — required, logged
      </h2>

      {verdict === 'borderline' && (
        <div className="mb-2 rounded-md px-2.5 py-2 text-[10.5px]" style={{ background: 'var(--px-human-bg)', color: 'var(--px-human)' }}>
          This candidate is <b>Borderline</b> and requires a human decision. It cannot be auto-resolved — record your call with a written rationale below.
        </div>
      )}

      <p className="mb-2 text-[10.5px]" style={{ color: 'var(--px-fg-2)' }}>
        AI recommends <b>{verdict}</b>. You decide.
      </p>

      <div className="flex gap-1.5" role="group" aria-label="Decision">
        {CHOICES.map((c) => (
          <button
            key={c.value}
            type="button"
            aria-pressed={choice === c.value}
            onClick={() => setChoice(c.value)}
            className="rounded-md border px-3 py-1 text-[11px] font-semibold"
            style={{
              borderColor: choice === c.value ? 'var(--px-accent)' : 'var(--px-hairline-strong)',
              background: choice === c.value ? 'var(--px-accent)' : 'var(--px-surface)',
              color: choice === c.value ? 'var(--px-accent-ink)' : 'var(--px-fg-2)',
            }}
          >
            {c.label}
          </button>
        ))}
      </div>

      <label htmlFor="decision-rationale" className="mt-2.5 block text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
        Rationale (required)
      </label>
      <textarea
        id="decision-rationale"
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        rows={3}
        className="mt-1 w-full rounded-md border p-2 text-[11.5px]"
        style={{ borderColor: 'var(--px-hairline-strong)', background: 'var(--px-surface)', color: 'var(--px-fg)' }}
        placeholder="Why this decision, with reference to the evidence above."
      />

      <Button
        type="button"
        variant="primary"
        size="sm"
        className="mt-2"
        disabled={!canSubmit}
        loading={isSubmitting}
        onClick={() => { if (choice) onSubmit(choice, rationale.trim()) }}
      >
        Record decision
      </Button>
    </section>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/HumanDecisionPanel.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/HumanDecisionPanel.tsx frontend/app/tests/components/reports/HumanDecisionPanel.test.tsx
git commit -m "feat(reports): HumanDecisionPanel with borderline lock"
```

---

### Task 11: `QaEvidencePanel`

**Files:**
- Create: `frontend/app/components/dashboard/reports/QaEvidencePanel.tsx`
- Test: `frontend/app/tests/components/reports/QaEvidencePanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/QaEvidencePanel.test.tsx
import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QaEvidencePanel } from '@/components/dashboard/reports/QaEvidencePanel'
import type { QuestionScorecard } from '@/lib/api/reports'

const qs: QuestionScorecard[] = [
  {
    question_id: 'q_python', question_text: 'Tell me about your Python depth.',
    level: 'below_bar', red_flags_hit: ['no concrete depth'], probes_fired: 1, opportunity: 'full',
    evidence: [{ quote: 'ChatGPT writes most of it', timestamp_ms: 252000, question_id: 'q_python', grounded: true }],
  },
]

describe('QaEvidencePanel', () => {
  it('lists each question with its level and evidence quote', () => {
    render(<QaEvidencePanel questionScorecards={qs} />)
    expect(screen.getByText(/Tell me about your Python depth/)).toBeInTheDocument()
    expect(screen.getByText(/ChatGPT writes most of it/)).toBeInTheDocument()
  })
  it('has a Q&A and an Evidence tab', () => {
    render(<QaEvidencePanel questionScorecards={qs} />)
    expect(screen.getByRole('tab', { name: /q&a/i })).toBeInTheDocument()
    const evTab = screen.getByRole('tab', { name: /evidence/i })
    fireEvent.click(evTab)
    expect(screen.getByText('04:12')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/QaEvidencePanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/app/components/dashboard/reports/QaEvidencePanel.tsx
'use client'

import { useState } from 'react'

import type { QuestionScorecard } from '@/lib/api/reports'
import { EvidenceQuote } from './EvidenceQuote'

const LEVEL_LABEL: Record<QuestionScorecard['level'], string> = {
  excellent: 'Excellent', meets_bar: 'Meets bar', below_bar: 'Below bar', not_assessed: 'Not assessed',
}

/**
 * The A2 stand-in for a full transcript (no recruiter transcript endpoint
 * exists yet). Built entirely from question_scorecards: the agent's
 * question text + the grounded evidence quotes. Gains a real "Transcript"
 * tab when the backend exposes sessions.transcript (deferred).
 */
export function QaEvidencePanel({ questionScorecards }: { questionScorecards: QuestionScorecard[] }) {
  const [tab, setTab] = useState<'qa' | 'evidence'>('qa')
  const allEvidence = questionScorecards.flatMap((q) => q.evidence)

  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Questions and evidence">
      <div role="tablist" aria-label="Q&A and evidence" className="mb-2.5 flex gap-1 rounded-lg p-0.5" style={{ background: 'var(--px-bg-2)' }}>
        <button role="tab" aria-selected={tab === 'qa'} onClick={() => setTab('qa')}
          className="flex-1 rounded-md py-1 text-[10.5px] font-medium"
          style={{ background: tab === 'qa' ? 'var(--px-surface)' : 'transparent', color: tab === 'qa' ? 'var(--px-fg)' : 'var(--px-fg-3)' }}>
          Q&amp;A
        </button>
        <button role="tab" aria-selected={tab === 'evidence'} onClick={() => setTab('evidence')}
          className="flex-1 rounded-md py-1 text-[10.5px] font-medium"
          style={{ background: tab === 'evidence' ? 'var(--px-surface)' : 'transparent', color: tab === 'evidence' ? 'var(--px-fg)' : 'var(--px-fg-3)' }}>
          Evidence ({allEvidence.length})
        </button>
      </div>

      {tab === 'qa' ? (
        <div className="space-y-3">
          {questionScorecards.map((q) => (
            <div key={q.question_id}>
              <div className="flex items-start justify-between gap-2">
                <p className="text-[11.5px] font-medium" style={{ color: 'var(--px-fg)' }}>{q.question_text}</p>
                <span className="shrink-0 text-[9px]" style={{ color: 'var(--px-fg-4)' }}>{LEVEL_LABEL[q.level]}</span>
              </div>
              {q.evidence.map((e, i) => <EvidenceQuote key={i} evidence={e} />)}
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-1.5">
          {allEvidence.length === 0
            ? <p className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>No grounded evidence captured.</p>
            : allEvidence.map((e, i) => <EvidenceQuote key={i} evidence={e} />)}
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/QaEvidencePanel.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/QaEvidencePanel.tsx frontend/app/tests/components/reports/QaEvidencePanel.test.tsx
git commit -m "feat(reports): QaEvidencePanel (Q&A + evidence from report)"
```

---

### Task 12: Presentational set (playback stub, summary, methodology, top bar)

**Files:**
- Create: `frontend/app/components/dashboard/reports/SessionPlaybackStub.tsx`
- Create: `frontend/app/components/dashboard/reports/ReportSummary.tsx`
- Create: `frontend/app/components/dashboard/reports/ReportMethodologyFooter.tsx`
- Create: `frontend/app/components/dashboard/reports/ReportTopBar.tsx`
- Test: `frontend/app/tests/components/reports/report-presentational.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/report-presentational.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SessionPlaybackStub, VerbalContentOnlyBadge } from '@/components/dashboard/reports/SessionPlaybackStub'
import { ReportSummary } from '@/components/dashboard/reports/ReportSummary'
import { ReportMethodologyFooter } from '@/components/dashboard/reports/ReportMethodologyFooter'
import { ReportTopBar } from '@/components/dashboard/reports/ReportTopBar'

describe('report presentational components', () => {
  it('SessionPlaybackStub names the future recording feature', () => {
    render(<SessionPlaybackStub />)
    expect(screen.getByText(/session playback/i)).toBeInTheDocument()
    expect(screen.getByText(/recording/i)).toBeInTheDocument()
  })
  it('VerbalContentOnlyBadge states no facial/affect scoring', () => {
    render(<VerbalContentOnlyBadge />)
    expect(screen.getByText(/no facial/i)).toBeInTheDocument()
  })
  it('ReportSummary renders strengths and gaps', () => {
    render(<ReportSummary summary={{ headline: 'Weak Python', strengths: ['REST APIs'], gaps: ['No depth'], rationale: 'r' }} />)
    expect(screen.getByText('Weak Python')).toBeInTheDocument()
    expect(screen.getByText('REST APIs')).toBeInTheDocument()
    expect(screen.getByText('No depth')).toBeInTheDocument()
  })
  it('ReportMethodologyFooter shows the verbal-content-only line + model', () => {
    render(<ReportMethodologyFooter manifest={{ scorer_model: 'gpt-5.4', reasoning_effort: 'medium', prompt_version: 'v1', generated_at: '2026-05-26T00:00:00Z', correlation_id: 'abc', verbosity: null, prompt_cache_key: null, scorer_code_version: null, bank_id: null, signal_snapshot_id: null, n_samples: 3, cache_hit_rate: null, evidence_grounding_summary: null }} />)
    expect(screen.getByText(/verbal-content-only/i)).toBeInTheDocument()
    expect(screen.getByText(/gpt-5\.4/)).toBeInTheDocument()
  })
  it('ReportTopBar shows the regenerate control only when canRegenerate', () => {
    const onRegen = vi.fn()
    const { rerender } = render(<ReportTopBar candidateName="Anand" candidateId="c1" title="Senior Python Engineer" subtitle="AI Screening" verdict="reject" canRegenerate={false} onRegenerate={onRegen} />)
    expect(screen.queryByRole('button', { name: /regenerate/i })).not.toBeInTheDocument()
    rerender(<ReportTopBar candidateName="Anand" candidateId="c1" title="Senior Python Engineer" subtitle="AI Screening" verdict="reject" canRegenerate onRegenerate={onRegen} />)
    fireEvent.click(screen.getByRole('button', { name: /regenerate/i }))
    expect(onRegen).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/report-presentational.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write the implementations**

```tsx
// frontend/app/components/dashboard/reports/SessionPlaybackStub.tsx
/**
 * Reserves the media slot the session recording (sub-project B) will fill.
 * Self-contained so B can replace its internals with a real player without
 * touching the surrounding layout.
 */
export function SessionPlaybackStub() {
  return (
    <div className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
      <div
        className="relative flex flex-col items-center justify-center rounded-lg"
        style={{ aspectRatio: '16 / 7', background: 'linear-gradient(160deg,#22323b,#0C2A38)', border: '1px dashed rgba(255,255,255,0.18)' }}
      >
        <span className="absolute right-2.5 top-2.5 rounded px-1.5 py-0.5 text-[9px]" style={{ background: 'rgba(255,255,255,0.12)', color: '#cdd9df' }}>
          Sub-project B
        </span>
        <span className="text-[30px]" aria-hidden="true">🎬</span>
        <span className="mt-1.5 text-[12px] font-semibold" style={{ color: '#c4d2d9' }}>Session playback</span>
        <span className="mt-0.5 text-[10.5px]" style={{ color: '#7d929c' }}>Recording &amp; video playback arrive with the recording feature</span>
      </div>
      <VerbalContentOnlyBadge />
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

```tsx
// frontend/app/components/dashboard/reports/ReportSummary.tsx
import type { SummaryOut } from '@/lib/api/reports'

export function ReportSummary({ summary }: { summary: SummaryOut }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Summary</h2>
      <p className="mb-2 text-[12.5px] font-semibold" style={{ color: 'var(--px-fg)' }}>{summary.headline}</p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase" style={{ color: 'var(--px-ok)' }}>Strengths</div>
          <ul className="list-disc pl-4 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>
            {summary.strengths.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase" style={{ color: 'var(--px-danger)' }}>Gaps</div>
          <ul className="list-disc pl-4 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>
            {summary.gaps.map((g, i) => <li key={i}>{g}</li>)}
          </ul>
        </div>
      </div>
      {summary.rationale && (
        <p className="mt-2 whitespace-pre-wrap text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{summary.rationale}</p>
      )}
    </section>
  )
}
```

```tsx
// frontend/app/components/dashboard/reports/ReportMethodologyFooter.tsx
import type { ScoringManifest } from '@/lib/api/reports'

export function ReportMethodologyFooter({ manifest }: { manifest: ScoringManifest | null }) {
  const items: string[] = []
  if (manifest?.scorer_model) items.push(`scorer ${manifest.scorer_model}${manifest.reasoning_effort ? ` · ${manifest.reasoning_effort}` : ''}`)
  if (manifest?.prompt_version) items.push(`prompt ${manifest.prompt_version}`)
  items.push('verbal-content-only')
  if (manifest?.generated_at) items.push(`generated ${new Date(manifest.generated_at).toLocaleDateString()}`)
  if (manifest?.correlation_id) items.push(`corr ${manifest.correlation_id.slice(0, 8)}`)

  return (
    <footer className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t px-1 pt-3 text-[9.5px]" style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-4)' }}>
      <span className="font-bold" style={{ color: 'var(--px-fg-3)' }}>Methodology</span>
      {items.map((it, i) => <span key={i}>{it}</span>)}
    </footer>
  )
}
```

```tsx
// frontend/app/components/dashboard/reports/ReportTopBar.tsx
'use client'

import Link from 'next/link'

import { Button } from '@/components/px'
import type { Verdict } from '@/lib/api/reports'
import { VerdictChip } from './VerdictBand'

interface Props {
  candidateName: string
  candidateId: string
  title: string
  subtitle: string
  verdict: Verdict
  canRegenerate: boolean
  onRegenerate: () => void
}

export function ReportTopBar({ candidateName, candidateId, title, subtitle, verdict, canRegenerate, onRegenerate }: Props) {
  return (
    <div className="mb-4 flex items-center gap-3">
      <Link href={`/candidates/${candidateId}?tab=sessions`} className="text-[12px] hover:underline" style={{ color: 'var(--px-fg-3)' }}>
        ← {candidateName}
      </Link>
      <div className="min-w-0 flex-1">
        <h1 className="px-serif m-0 truncate text-[24px] font-normal" style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}>
          Evaluation — {title}
        </h1>
        <p className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>{subtitle}</p>
      </div>
      <VerdictChip verdict={verdict} />
      {canRegenerate && (
        <Button type="button" variant="outline" size="sm" onClick={onRegenerate}>Regenerate</Button>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/report-presentational.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/SessionPlaybackStub.tsx frontend/app/components/dashboard/reports/ReportSummary.tsx frontend/app/components/dashboard/reports/ReportMethodologyFooter.tsx frontend/app/components/dashboard/reports/ReportTopBar.tsx frontend/app/tests/components/reports/report-presentational.test.tsx
git commit -m "feat(reports): playback stub, summary, methodology footer, top bar"
```

---

### Task 13: State components + `ReportView`

**Files:**
- Create: `frontend/app/components/dashboard/reports/ReportStates.tsx`
- Create: `frontend/app/components/dashboard/reports/ReportView.tsx`
- Test: `frontend/app/tests/components/reports/ReportView.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/ReportView.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportView } from '@/components/dashboard/reports/ReportView'
import { ReportEmptyState } from '@/components/dashboard/reports/ReportStates'
import type { ReportRead } from '@/lib/api/reports'

const base = {
  verdict: 'reject', verdict_reason: 'failed', overall_score: 36, overall_coverage: 0.7,
  overall_confidence: 'medium',
  dimension_scores: { technical: { name: 'Technical', score: 37, coverage: 0.6, confidence: 'medium', note: null } },
  knockout_results: [], signal_scorecards: [], question_scorecards: [],
  summary: { headline: 'h', strengths: [], gaps: [], rationale: '' },
  id: 'r1', session_id: 's1', version: 1, engine_version: 'v2', scoring_manifest: null,
  human_decision: null, generated_at: null,
} as unknown as ReportRead

const noop = vi.fn()

describe('ReportView', () => {
  it('renders the verdict band for a ready report', () => {
    render(<ReportView report={{ ...base, status: 'ready' }} candidateName="A" candidateId="c1" canRegenerate={false} onRegenerate={noop} onDecision={noop} isSubmitting={false} />)
    expect(screen.getByText('Reject')).toBeInTheDocument()
  })
})

describe('ReportEmptyState', () => {
  it('shows Generate report only for super-admin', () => {
    const { rerender } = render(<ReportEmptyState canGenerate={false} onGenerate={noop} />)
    expect(screen.queryByRole('button', { name: /generate report/i })).not.toBeInTheDocument()
    rerender(<ReportEmptyState canGenerate onGenerate={noop} />)
    expect(screen.getByRole('button', { name: /generate report/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/ReportView.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `ReportStates.tsx`**

```tsx
// frontend/app/components/dashboard/reports/ReportStates.tsx
'use client'

import { Button } from '@/components/px'

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-[640px] px-8 py-16 text-center">{children}</div>
}

export function ReportEmptyState({ canGenerate, onGenerate }: { canGenerate: boolean; onGenerate: () => void }) {
  return (
    <Centered>
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-fg)' }}>No evaluation yet</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        This session has no report. A report is generated after an AI-screening session completes.
      </p>
      {canGenerate && (
        <Button type="button" variant="primary" size="sm" className="mt-5" onClick={onGenerate}>Generate report</Button>
      )}
    </Centered>
  )
}

export function ReportPendingState() {
  return (
    <Centered>
      <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2" style={{ borderColor: 'var(--px-accent)', borderTopColor: 'transparent' }} />
      <h2 className="px-serif mt-4 text-2xl" style={{ color: 'var(--px-fg)' }}>Scoring this interview…</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        The evaluation is being generated. This page updates automatically.
      </p>
    </Centered>
  )
}

export function ReportFailedState({ canRegenerate, onRegenerate }: { canRegenerate: boolean; onRegenerate: () => void }) {
  return (
    <Centered>
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-danger)' }}>Report generation failed</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Something went wrong while scoring this interview.
      </p>
      {canRegenerate && (
        <Button type="button" variant="primary" size="sm" className="mt-5" onClick={onRegenerate}>Regenerate</Button>
      )}
    </Centered>
  )
}

export function ReportForbiddenState() {
  return (
    <Centered>
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-fg)' }}>Access denied</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        You don’t have permission to view this report.
      </p>
    </Centered>
  )
}
```

- [ ] **Step 4: Write `ReportView.tsx`**

```tsx
// frontend/app/components/dashboard/reports/ReportView.tsx
'use client'

import type { HumanDecisionValue, ReportRead } from '@/lib/api/reports'
import { AiRecommendationCard } from './AiRecommendationCard'
import { HumanDecisionPanel } from './HumanDecisionPanel'
import { QaEvidencePanel } from './QaEvidencePanel'
import { ReportMethodologyFooter } from './ReportMethodologyFooter'
import { ReportSummary } from './ReportSummary'
import { ReportTopBar } from './ReportTopBar'
import { SessionPlaybackStub } from './SessionPlaybackStub'
import { SignalScorecards } from './SignalScorecards'
import { SignalSpiderChart } from './SignalSpiderChart'

interface Props {
  report: ReportRead
  candidateName: string
  candidateId: string
  title?: string
  subtitle?: string
  canRegenerate: boolean
  onRegenerate: () => void
  onDecision: (decision: HumanDecisionValue, rationale: string) => void
  isSubmitting: boolean
}

export function ReportView({
  report, candidateName, candidateId, title = 'Interview', subtitle = '',
  canRegenerate, onRegenerate, onDecision, isSubmitting,
}: Props) {
  const spider = <SignalSpiderChart signals={report.signal_scorecards} />
  return (
    <div className="mx-auto max-w-[1400px] px-6 pb-10 pt-5">
      <ReportTopBar
        candidateName={candidateName} candidateId={candidateId}
        title={title} subtitle={subtitle} verdict={report.verdict}
        canRegenerate={canRegenerate} onRegenerate={onRegenerate}
      />
      <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-[1.85fr_1fr]">
        {/* MAIN */}
        <div className="space-y-3.5">
          <SessionPlaybackStub />
          {spider && (
            <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
              <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Signal profile — 0–10</h2>
              <div className="flex justify-center">{spider}</div>
            </section>
          )}
          <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
            <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Knockouts &amp; signals — evidence inline</h2>
            <SignalScorecards knockouts={report.knockout_results} signals={report.signal_scorecards} />
          </section>
          <ReportSummary summary={report.summary} />
        </div>
        {/* SIDE */}
        <div className="space-y-3.5">
          <AiRecommendationCard report={report} />
          <HumanDecisionPanel verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />
          <QaEvidencePanel questionScorecards={report.question_scorecards} />
        </div>
      </div>
      <ReportMethodologyFooter manifest={report.scoring_manifest} />
    </div>
  )
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/ReportView.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/reports/ReportStates.tsx frontend/app/components/dashboard/reports/ReportView.tsx frontend/app/tests/components/reports/ReportView.test.tsx
git commit -m "feat(reports): ReportView composition + empty/pending/failed/forbidden states"
```

---

### Task 14: Route page + `loading.tsx` + `error.tsx`

**Files:**
- Create: `frontend/app/app/(dashboard)/reports/session/[sessionId]/page.tsx`
- Create: `frontend/app/app/(dashboard)/reports/session/[sessionId]/loading.tsx`
- Create: `frontend/app/app/(dashboard)/reports/session/[sessionId]/error.tsx`
- Test: `frontend/app/tests/components/reports/ReportPage.test.tsx`

The page resolves candidate context for the back-link from the report's `assignment` lineage is not directly available; A2 reads the candidate name/title from query params passed by the entry link (so no extra fetch). The link in Task 15 supplies `?candidateId=&candidateName=&title=&subtitle=`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/ReportPage.test.tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '@/tests/_utils/render'

vi.mock('@/lib/auth/tokens', () => ({ getFreshSupabaseToken: vi.fn().mockResolvedValue('tok') }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ sessionId: 's1' }),
  useSearchParams: () => new URLSearchParams('candidateId=c1&candidateName=Anand&title=Senior%20Python%20Engineer'),
}))
vi.mock('@/lib/hooks/use-me', () => ({ useMe: () => ({ data: { is_super_admin: false } }) }))

import ReportPage from '@/app/(dashboard)/reports/session/[sessionId]/page'

const READY = {
  verdict: 'reject', verdict_reason: 'failed', overall_score: 36, overall_coverage: 0.7,
  overall_confidence: 'medium', dimension_scores: {}, knockout_results: [], signal_scorecards: [],
  question_scorecards: [], summary: { headline: 'h', strengths: [], gaps: [], rationale: '' },
  status: 'ready', id: 'r1', session_id: 's1', version: 1, scoring_manifest: null, human_decision: null,
}

afterEach(() => vi.unstubAllGlobals())

describe('ReportPage', () => {
  it('renders the ready report', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => READY } as Response))
    renderWithProviders(<ReportPage />)
    expect(await screen.findByText('Reject')).toBeInTheDocument()
  })

  it('renders the empty state on 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({ detail: 'none' }) } as Response))
    renderWithProviders(<ReportPage />)
    await waitFor(() => expect(screen.getByText(/no evaluation yet/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/ReportPage.test.tsx`
Expected: FAIL — page module not found.

- [ ] **Step 3: Write the page**

```tsx
// frontend/app/app/(dashboard)/reports/session/[sessionId]/page.tsx
'use client'

import { useParams, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'

import { ReportView } from '@/components/dashboard/reports/ReportView'
import {
  ReportEmptyState, ReportFailedState, ReportForbiddenState, ReportPendingState,
} from '@/components/dashboard/reports/ReportStates'
import type { HumanDecisionValue } from '@/lib/api/reports'
import { useMe } from '@/lib/hooks/use-me'
import { useRecordDecision, useRegenerateReport, useReport } from '@/lib/hooks/use-report'

export default function ReportPage() {
  const params = useParams<{ sessionId: string }>()
  const sessionId = params.sessionId
  const sp = useSearchParams()
  const candidateId = sp.get('candidateId') ?? ''
  const candidateName = sp.get('candidateName') ?? 'Candidate'
  const title = sp.get('title') ?? 'Interview'
  const subtitle = sp.get('subtitle') ?? ''

  const { data: me } = useMe()
  const isSuperAdmin = !!me?.is_super_admin

  const { state, markGenerating } = useReport(sessionId)
  const regenerate = useRegenerateReport(sessionId)
  const decision = useRecordDecision(sessionId)

  const handleRegenerate = () => {
    markGenerating()
    regenerate.mutate(undefined, {
      onSuccess: () => toast.success('Report generation started'),
      onError: (e) => toast.error(e.message || 'Could not start generation'),
    })
  }

  const handleDecision = (reportId: string) => (d: HumanDecisionValue, rationale: string) => {
    decision.mutate(
      { reportId, body: { decision: d, rationale } },
      {
        onSuccess: () => toast.success('Decision recorded'),
        onError: (e) => toast.error(e.message || 'Could not record decision'),
      },
    )
  }

  switch (state.kind) {
    case 'loading':
    case 'pending':
      return <ReportPendingState />
    case 'forbidden':
      return <ReportForbiddenState />
    case 'noReport':
      return <ReportEmptyState canGenerate={isSuperAdmin} onGenerate={handleRegenerate} />
    case 'failed':
      return <ReportFailedState canRegenerate={isSuperAdmin} onRegenerate={handleRegenerate} />
    case 'ready':
      return (
        <ReportView
          report={state.report}
          candidateName={candidateName}
          candidateId={candidateId}
          title={title}
          subtitle={subtitle}
          canRegenerate={isSuperAdmin}
          onRegenerate={handleRegenerate}
          onDecision={handleDecision(state.report.id ?? '')}
          isSubmitting={decision.isPending}
        />
      )
  }
}
```

> Note: `state.kind === 'loading'` shows the pending spinner (same visual) so there is never a blank screen during the first fetch.

- [ ] **Step 4: Write `loading.tsx` and `error.tsx`**

```tsx
// frontend/app/app/(dashboard)/reports/session/[sessionId]/loading.tsx
export default function ReportLoading() {
  return (
    <div className="mx-auto max-w-[1400px] px-6 pb-10 pt-5">
      <div className="mb-4 h-8 w-72 animate-pulse rounded" style={{ background: 'var(--px-surface-2)' }} />
      <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-[1.85fr_1fr]">
        <div className="space-y-3.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="animate-pulse rounded-xl border" style={{ height: i === 0 ? 220 : 160, background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }} />
          ))}
        </div>
        <div className="space-y-3.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="animate-pulse rounded-xl border" style={{ height: 180, background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }} />
          ))}
        </div>
      </div>
    </div>
  )
}
```

```tsx
// frontend/app/app/(dashboard)/reports/session/[sessionId]/error.tsx
'use client'

export default function ReportError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-[800px] px-8 pt-12 text-center">
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-fg)' }}>Couldn’t load this report</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        {error.message || 'Something went wrong.'}
      </p>
      <button type="button" onClick={reset} className="px-btn primary sm mt-6 inline-block">Try again</button>
    </div>
  )
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/ReportPage.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add "frontend/app/app/(dashboard)/reports/session" frontend/app/tests/components/reports/ReportPage.test.tsx
git commit -m "feat(reports): report route page + loading/error segments"
```

---

### Task 15: Entry point — "View report" link in `CandidateSessionsTab`

**Files:**
- Modify: `frontend/app/app/(dashboard)/candidates/[candidateId]/CandidateSessionsTab.tsx`
- Test: `frontend/app/tests/components/reports/SessionRowReportLink.test.tsx`

The `SessionRow` component needs the candidate id + job title to build the link. `AssignmentSessionsBlock` already has the `assignment` (with `job_title`, `current_stage_name`) and the candidate id comes from the page param — thread both into `SessionRow`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/SessionRowReportLink.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// CandidateSessionsTab pulls in hooks; test the SessionRow link logic by
// rendering the exported helper component with a completed + a non-terminal session.
import { SessionRow } from '@/app/(dashboard)/candidates/[candidateId]/CandidateSessionsTab'
import type { SessionDetail } from '@/lib/api/scheduler'

vi.mock('@/lib/hooks/use-resend-invite', () => ({ useResendInvite: () => ({ mutate: vi.fn(), isPending: false }) }))
vi.mock('@/lib/hooks/use-revoke-invite', () => ({ useRevokeInvite: () => ({ mutate: vi.fn(), isPending: false }) }))

const make = (state: SessionDetail['state']): SessionDetail => ({
  id: 'sess1', assignment_id: 'a1', stage_id: 'st1', stage_name: 'AI Screening', state,
  state_changed_at: '', otp_required: false, consent_recorded_at: null, scheduled_for: null,
  started_at: null, completed_at: null, created_at: new Date().toISOString(),
})

function rowInTable(session: SessionDetail) {
  return render(
    <table><tbody>
      <SessionRow session={session} candidateId="c1" jobTitle="Senior Python Engineer" />
    </tbody></table>,
  )
}

describe('SessionRow report link', () => {
  it('shows a View report link for completed sessions', () => {
    rowInTable(make('completed'))
    const link = screen.getByRole('link', { name: /view report/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('/reports/session/sess1'))
    expect(link).toHaveAttribute('href', expect.stringContaining('candidateId=c1'))
  })
  it('does not show a View report link before completion', () => {
    rowInTable(make('active'))
    expect(screen.queryByRole('link', { name: /view report/i })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/reports/SessionRowReportLink.test.tsx`
Expected: FAIL — `SessionRow` is not exported / does not accept `candidateId`/`jobTitle`.

- [ ] **Step 3: Modify `CandidateSessionsTab.tsx`**

Thread `candidateId` from the page param into the tree, pass `candidateId` + `jobTitle` into `SessionRow`, export `SessionRow`, and add the link for completed sessions.

3a. Update the top-level component to capture `candidateId` and pass it down:

```tsx
// In CandidateSessionsTab: pass candidateId into each block
return (
  <div className="space-y-6">
    {assignments.map((assignment) => (
      <AssignmentSessionsBlock key={assignment.id} assignment={assignment} candidateId={candidateId} />
    ))}
  </div>
)
```

3b. Update `AssignmentSessionsBlockProps` + the block to accept and forward `candidateId`:

```tsx
interface AssignmentSessionsBlockProps {
  assignment: AssignmentResponse
  candidateId: string
}

function AssignmentSessionsBlock({ assignment, candidateId }: AssignmentSessionsBlockProps) {
  // ...unchanged...
  // in the tbody map:
  {sessions.map((session) => (
    <SessionRow
      key={session.id}
      session={session}
      candidateId={candidateId}
      jobTitle={assignment.job_title || 'Interview'}
    />
  ))}
```

3c. Add the import for `Link`, update `SessionRowProps`, export the component, and add the link before the resend/revoke actions:

```tsx
import Link from 'next/link'
// ...existing imports...

interface SessionRowProps {
  session: SessionDetail
  candidateId: string
  jobTitle: string
}

export function SessionRow({ session, candidateId, jobTitle }: SessionRowProps) {
  const resend = useResendInvite()
  const revoke = useRevokeInvite()
  const actionable = isPreActive(session.state)
  const pending = resend.isPending || revoke.isPending

  // ...handleResend / handleRevoke unchanged...

  const reportHref =
    `/reports/session/${session.id}` +
    `?candidateId=${encodeURIComponent(candidateId)}` +
    `&candidateName=${encodeURIComponent('')}` + // name filled by report top bar from param when available
    `&title=${encodeURIComponent(jobTitle)}` +
    `&subtitle=${encodeURIComponent(session.stage_name || '')}`

  return (
    <tr className="hover:bg-zinc-50">
      {/* ...stage / status / created cells unchanged... */}
      <td className="px-4 py-2 text-right text-sm">
        <div className="inline-flex items-center gap-2">
          {session.state === 'completed' && (
            <Link
              href={reportHref}
              className="text-xs font-medium hover:underline"
              style={{ color: 'var(--px-accent)' }}
            >
              View report
            </Link>
          )}
          {actionable ? (
            <div className="inline-flex gap-2">
              {/* ...resend + revoke buttons unchanged... */}
            </div>
          ) : session.state === 'completed' ? null : (
            <span className="text-xs text-zinc-400">—</span>
          )}
        </div>
      </td>
    </tr>
  )
}
```

> Note: keep the existing default export `CandidateSessionsTab` unchanged in name; just add the named `SessionRow` export. The `candidateName` param is passed empty here (the report top bar falls back to "Candidate"); the candidate detail page already shows the name, and the report's back-link returns to the candidate. If you want the name in the report header, thread `candidate.name` through `CandidateDetailPage → CandidateSessionsTab` in a follow-up — not required for A2.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/reports/SessionRowReportLink.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Full gate — lint, type-check, tests**

Run:
```bash
cd frontend/app
npm run lint
npm run type-check
npm run test
```
Expected: all pass with zero errors. Fix any type/lint issues (most likely: unused imports, the `['--px-...' as string]` style cast, or the dead `r` line flagged in Task 7).

- [ ] **Step 6: Commit**

```bash
git add "frontend/app/app/(dashboard)/candidates/[candidateId]/CandidateSessionsTab.tsx" frontend/app/tests/components/reports/SessionRowReportLink.test.tsx
git commit -m "feat(reports): View report link from completed sessions"
```

---

## Self-review notes (gaps closed during planning)

- **Spec §6 states** — every state (`loading/noReport/forbidden/pending/failed/ready`) has a component (Task 13) and is wired in the page switch (Task 14). `failed` is branched before gauges (the page never renders `ReportView` for a failed report).
- **Spec §3 components** — all present: ScoreGauge (T4), VerdictBand (T5), EvidenceQuote (T6), SignalSpiderChart (T7), SignalScorecards (T8), AiRecommendationCard (T9), HumanDecisionPanel (T10), QaEvidencePanel (T11), playback stub + trust badge + summary + methodology + top bar (T12).
- **Spec §4 data** — reports API (T1) + hooks with regenerate-through-404 polling (T2).
- **Spec §5 decision flow** — borderline lock + always-required rationale + change-decision (T10); records to report only, no stage move.
- **Spec §7 forward-compat** — EvidenceQuote seek chip (`data-seek-stub`) + SessionPlaybackStub reserve B's slot; every quote carries `timestamp_ms`/`question_id`.
- **Spec §8 a11y/perf** — reduced-motion guard in report.css; gauges expose `aria-label`; no chart lib.
- **Type consistency** — TS enums in T1 match `scoring/types.py` exactly; `ReportEnvelope` and `ReportState` names are used consistently T1→T2→T14; `SessionRow` signature change is applied in both the call site and the export (T15).
- **Deferred (named in spec, not built):** full transcript tab, video playback, decision-triggers-stage-move, aggregate analytics, PDF.
```
