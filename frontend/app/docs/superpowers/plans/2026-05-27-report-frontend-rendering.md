# Report Frontend Rendering — Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the new PDF-shaped report in the recruiter dashboard — rewrite the TS contract, relabel verdicts, build the PDF-content components, delete the old "Knockouts & signals" dump — keeping the existing visual chassis (split layout, `ScoreGauge`, verdict band/chip, px tokens).

**Architecture:** "Re-skin the content, keep the chassis." `lib/api/reports.ts` is rewritten to mirror the backend's new `ReportRead`. `report-format.ts` relabels verdicts and adds severity/status maps. New presentational components render `decision`, `scores`, `quick_summary`, `strengths`/`concerns`, `questions`, `signal_assessments`, `methodology`. `ReportView` recomposes them into the existing 2-column split. The page + `useReport` hook are untouched.

**Tech Stack:** Next.js 16 App Router, TypeScript (strict), Tailwind v4 + px design tokens, Vitest + @testing-library/react + jsdom. In-house `components/px/` primitives. Spec: `frontend/app/docs/superpowers/specs/2026-05-27-report-frontend-rendering-design.md`.

**Run from:** `frontend/app/`. Per-task test: `npm run test -- <pattern>` (Vitest transpiles only what a test imports, so individual tests pass even while a global `tsc` is mid-migration). **Global `npm run type-check` will be RED from Task 1 until Task 10** — that is expected; Task 10 makes it green. Do not chase global type-check before Task 10.

---

## File map

**Create**
- `tests/components/reports/_fixture.ts` — `makeReport(overrides?)` factory returning a valid new-shape `ReportRead`; shared by every component test (DRY).
- `components/dashboard/reports/ScoresCard.tsx` — verdict band + headline + 4 score gauges + coverage/confidence (replaces `AiRecommendationCard`).
- `components/dashboard/reports/WhyContrast.tsx` — two-column positive/negative.
- `components/dashboard/reports/QuickSummary.tsx` — narrative paragraph.
- `components/dashboard/reports/StrengthsConcerns.tsx` — strengths + concerns w/ severity chips.
- `components/dashboard/reports/QuestionByQuestion.tsx` — per-question badge + quote + read.
- `components/dashboard/reports/SignalAuditTable.tsx` — collapsed `<details>` audit table.
- Tests for each new component under `tests/components/reports/`.

**Modify**
- `lib/api/reports.ts` — new contract.
- `components/dashboard/reports/report-format.ts` — verdict relabel, severity/status maps, `tierTone`; remove signal/knockout helpers.
- `components/dashboard/reports/ReportView.tsx` — recompose.
- `components/dashboard/reports/ReportMethodologyFooter.tsx` — render `methodology` + trimmed manifest.
- `tests/components/reports/ReportView.test.tsx`, `tests/components/reports/report-format.test.ts` — rewrite to new shape.

**Delete**
- `components/dashboard/reports/{SignalScorecards,QaEvidencePanel,ReportSummary,SignalSpiderChart,EvidenceQuote,AiRecommendationCard}.tsx` + their tests under `tests/components/reports/`.

---

## Task 1: New TS contract + shared test fixture

**Files:**
- Modify: `lib/api/reports.ts`
- Create: `tests/components/reports/_fixture.ts`
- Test: `tests/components/reports/_fixture.test.ts`

- [ ] **Step 1: Rewrite the types in `lib/api/reports.ts`**

Replace the enums + response-shape block (lines ~3–106, everything from `// --- Enums` down to the end of `interface ReportRead`) with:

```ts
// --- Enums (mirror app/modules/reporting/scoring/types.py) ---
export type Verdict = 'advance' | 'borderline' | 'reject'        // enum unchanged; UI-relabeled
export type Confidence = 'high' | 'medium' | 'low'
export type Severity = 'deal_breaker' | 'major' | 'moderate'
export type StatusBadge =
  | 'passed' | 'partial' | 'failed_required'
  | 'not_demonstrated' | 'not_attempted' | 'not_fully_assessed'
export type HumanDecisionValue = 'advance' | 'reject' | 'hold'

// --- Response shapes (mirror reporting/schemas.py::ReportRead) ---
export interface WhyColumn { title: string; body: string }
export interface DecisionOut { headline: string; why_positive: WhyColumn; why_negative: WhyColumn }
export interface ScoreOut {
  score: number | null
  tier_label: string
  tone: string
  confidence: Confidence
  coverage: number
}
export interface StrengthOut { title: string; detail: string }
export interface ConcernOut { title: string; detail: string; severity: Severity }
export interface QuestionOut {
  seq: number
  question_id: string
  title: string
  status_badge: StatusBadge
  status_tone: string
  question_text: string
  candidate_quote: string
  our_read: string
}
export interface MethodologyOut { note: string; charity_flags: string[] }
export interface SignalAssessmentOut {
  signal: string
  type: string
  weight: number
  knockout: boolean
  priority: string
  engine_state: string
  final_state: string
  grade: string | null
  score: number | null
  evidence: string[]
  overridden: boolean
  override_reason: string | null
}

export interface ScoringManifest {
  scorer_model: string | null
  reasoning_effort: string | null
  prompt_version: string | null
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
  decision: DecisionOut
  scores: Record<string, ScoreOut>
  quick_summary: string
  strengths: StrengthOut[]
  concerns: ConcernOut[]
  questions: QuestionOut[]
  methodology: MethodologyOut
  signal_assessments: SignalAssessmentOut[]
  id: string | null
  session_id: string | null
  status: 'pending' | 'generating' | 'ready' | 'failed'
  engine_version: string | null
  version: number
  scoring_manifest: ScoringManifest | null
  human_decision: HumanDecision | null
  generated_at: string | null
}
```

Leave `HumanDecisionIn`, `ReportIndexItem`, `ReportIndexPage`, `ReportEnvelope`, and the `reportsApi` object below unchanged. (The `getBySession` `'verdict' in body` check still works.)

- [ ] **Step 2: Create the shared fixture factory**

Create `tests/components/reports/_fixture.ts`:

```ts
import type { ReportRead } from '@/lib/api/reports'

/** A complete, valid new-shape ReportRead for component tests. Override per test. */
export function makeReport(overrides: Partial<ReportRead> = {}): ReportRead {
  return {
    verdict: 'borderline',
    verdict_reason: 'Could not confirm a must-have.',
    overall_score: 41,
    overall_coverage: 0.47,
    overall_confidence: 'medium',
    decision: {
      headline: 'Credible baseline, but key requirements unproven.',
      why_positive: { title: 'Foundations are there', body: 'Meets the experience bar.' },
      why_negative: { title: 'But depth was not shown', body: 'Technical answers stayed thin.' },
    },
    scores: {
      overall: { score: 41, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium', coverage: 0.47 },
      technical: { score: 41, tier_label: 'Below Bar', tone: 'caution', confidence: 'medium', coverage: 0.55 },
      behavioral: { score: null, tier_label: 'Not Assessed', tone: 'neutral', confidence: 'low', coverage: 0 },
      communication: { score: 70, tier_label: 'Meets Bar', tone: 'ok', confidence: 'medium', coverage: 1 },
    },
    quick_summary: 'This candidate sits right on the line.',
    strengths: [{ title: 'Meets the experience bar', detail: 'Around six years overall.' }],
    concerns: [
      { title: 'No core skill reached the bar', detail: 'Every technical answer stayed thin.', severity: 'major' },
      { title: 'A required skill is unproven', detail: 'Programming depth not shown.', severity: 'deal_breaker' },
    ],
    questions: [
      {
        seq: 1, question_id: 'q1', title: 'Experience & background',
        status_badge: 'passed', status_tone: 'ok',
        question_text: 'How many years of experience do you have?',
        candidate_quote: 'Around six years.', our_read: 'Comfortably clears the four-year minimum.',
      },
      {
        seq: 2, question_id: 'q2', title: 'API rate limits', status_badge: 'partial', status_tone: 'caution',
        question_text: 'How would you handle API rate limits?',
        candidate_quote: 'Track the call count and handle errors.', our_read: 'Right concerns, thin on strategy.',
      },
    ],
    methodology: {
      note: 'Reached 7 of 8 planned questions; closed normally.',
      charity_flags: ['A long mid-interview silence may be a technical issue — worth confirming.'],
    },
    signal_assessments: [
      {
        signal: '4+ years total professional experience', type: 'experience', weight: 3, knockout: true,
        priority: 'required', engine_state: 'sufficient', final_state: 'sufficient', grade: null, score: 70,
        evidence: ['Around six years.'], overridden: false, override_reason: null,
      },
    ],
    id: 'r1', session_id: 's1', status: 'ready', engine_version: 'v2', version: 1,
    scoring_manifest: {
      scorer_model: 'gpt-5.4', reasoning_effort: 'medium', prompt_version: 'v3',
      evidence_grounding_summary: null, generated_at: '2026-05-27T11:00:00Z', correlation_id: 'abcd1234',
    },
    human_decision: null, generated_at: '2026-05-27T11:00:00Z',
    ...overrides,
  }
}
```

- [ ] **Step 3: Trivial fixture test (proves it's importable + shaped)**

Create `tests/components/reports/_fixture.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { makeReport } from './_fixture'

describe('makeReport fixture', () => {
  it('builds a ready borderline report with the new shape', () => {
    const r = makeReport()
    expect(r.verdict).toBe('borderline')
    expect(r.scores.overall.score).toBe(41)
    expect(r.decision.why_positive.title).toBeTruthy()
    expect(r.questions[0].status_badge).toBe('passed')
    expect(r.concerns.some((c) => c.severity === 'deal_breaker')).toBe(true)
  })
  it('applies overrides', () => {
    expect(makeReport({ verdict: 'reject' }).verdict).toBe('reject')
  })
})
```

- [ ] **Step 4: Run it**

Run: `npm run test -- _fixture`
Expected: 2 passed. (Global `npm run type-check` is now RED across components — expected until Task 10.)

- [ ] **Step 5: Commit**

```bash
git add lib/api/reports.ts tests/components/reports/_fixture.ts tests/components/reports/_fixture.test.ts
git commit -m "feat(reports-fe): rewrite ReportRead contract to new schema + shared test fixture"
```

---

## Task 2: Format layer — verdict relabel + severity/status maps

**Files:**
- Modify: `components/dashboard/reports/report-format.ts`
- Test: `tests/components/reports/report-format.test.ts`

- [ ] **Step 1: Rewrite `report-format.test.ts`**

Replace the file with:

```ts
import { describe, expect, it } from 'vitest'
import {
  scoreToTen, formatTimestamp, verdictMeta, scoreBandTone, tierTone,
  severityMeta, statusBadgeMeta, confidenceLabel, TONE_INK,
} from '@/components/dashboard/reports/report-format'

describe('report-format', () => {
  it('scoreToTen', () => {
    expect(scoreToTen(41)).toBe('4.1')
    expect(scoreToTen(100)).toBe('10.0')
    expect(scoreToTen(null)).toBeNull()
  })
  it('verdictMeta relabels to recruiter-facing words', () => {
    expect(verdictMeta('advance').label).toBe('Recommended')
    expect(verdictMeta('borderline').label).toBe('Borderline')
    expect(verdictMeta('reject').label).toBe('Not Recommended')
    expect(verdictMeta('advance').tone).toBe('ok')
    expect(verdictMeta('reject').tone).toBe('danger')
  })
  it('severityMeta maps severity to label + tone', () => {
    expect(severityMeta('deal_breaker')).toEqual({ label: 'Deal-breaker', tone: 'danger' })
    expect(severityMeta('major').tone).toBe('caution')
    expect(severityMeta('moderate').tone).toBe('neutral')
  })
  it('statusBadgeMeta maps each badge to label + tone', () => {
    expect(statusBadgeMeta('passed')).toEqual({ label: 'Passed', tone: 'ok' })
    expect(statusBadgeMeta('failed_required').tone).toBe('danger')
    expect(statusBadgeMeta('not_fully_assessed').label).toBe('Not fully assessed')
  })
  it('tierTone passes through valid tones, else neutral', () => {
    expect(tierTone('ok')).toBe('ok')
    expect(tierTone('danger')).toBe('danger')
    expect(tierTone('bogus')).toBe('neutral')
  })
  it('scoreBandTone + confidenceLabel still work (kept)', () => {
    expect(scoreBandTone(80)).toBe('ok')
    expect(scoreBandTone(null)).toBe('neutral')
    expect(confidenceLabel('high')).toBe('High')
    expect(formatTimestamp(90000)).toBe('01:30')
    expect(TONE_INK.ok).toMatch(/var\(--px-/)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- report-format`
Expected: FAIL (severityMeta / statusBadgeMeta / tierTone not exported; verdict labels still "Advance/Reject").

- [ ] **Step 3: Update `report-format.ts`**

In `components/dashboard/reports/report-format.ts`:

(a) Update the verdict labels:
```ts
const VERDICT_META: Record<Verdict, VerdictMeta> = {
  advance: { label: 'Recommended', tone: 'ok' },
  borderline: { label: 'Borderline', tone: 'human' },
  reject: { label: 'Not Recommended', tone: 'danger' },
}
```

(b) Add new types + maps + `tierTone` (append near the bottom, before/after existing exports):
```ts
import type { Severity, StatusBadge } from '@/lib/api/reports'

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
```

(c) Update the top import line to drop removed enums and add the new ones:
```ts
import type { Confidence, Severity, StatusBadge, Verdict } from '@/lib/api/reports'
```
(Remove `KnockoutStatus`, `SignalState` from that import.)

(d) **Delete** the now-orphaned helpers: `signalStateTone`, `signalStateLabel`, `knockoutStatusTone`, `knockoutStatusLabel`, and their `SIGNAL_STATE_LABEL` / `KO_STATUS_LABEL` maps. **Keep** `scoreToTen`, `formatTimestamp`, `verdictMeta`, `scoreBandTone`, `confidenceLabel`, `TONE_INK/FILL/BG`, `Tone`.

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- report-format`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/report-format.ts tests/components/reports/report-format.test.ts
git commit -m "feat(reports-fe): relabel verdicts + add severity/status/tier helpers"
```

---

## Task 3: `ScoresCard` (replaces AiRecommendationCard)

**Files:**
- Create: `components/dashboard/reports/ScoresCard.tsx`
- Test: `tests/components/reports/ScoresCard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `tests/components/reports/ScoresCard.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoresCard } from '@/components/dashboard/reports/ScoresCard'
import { makeReport } from './_fixture'

describe('ScoresCard', () => {
  it('shows the relabeled verdict, headline, and four gauges', () => {
    render(<ScoresCard report={makeReport()} />)
    expect(screen.getByText('Borderline')).toBeInTheDocument()
    expect(screen.getByText(/Credible baseline/)).toBeInTheDocument()
    expect(screen.getByText('Overall')).toBeInTheDocument()
    expect(screen.getByText('Technical')).toBeInTheDocument()
    expect(screen.getByText('Behavioral')).toBeInTheDocument()
    expect(screen.getByText('Communication')).toBeInTheDocument()
    // overall 41 -> "4.1"
    expect(screen.getAllByText('4.1').length).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- ScoresCard`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `ScoresCard.tsx`**

```tsx
import type { ReportRead, ScoreOut } from '@/lib/api/reports'
import { ScoreGauge } from './ScoreGauge'
import { VerdictBand } from './VerdictBand'
import { confidenceLabel, tierTone, verdictMeta } from './report-format'

const DIMS: { key: string; label: string }[] = [
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Communication' },
]

function caption(s: ScoreOut | undefined): string | undefined {
  if (!s) return undefined
  if (s.score === null) return 'not assessed'
  return `cov ${s.coverage.toFixed(2)} · ${confidenceLabel(s.confidence).toLowerCase()}`
}

export function ScoresCard({ report }: { report: ReportRead }) {
  const overall = report.scores.overall
  const verdictTone = verdictMeta(report.verdict).tone
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Scores">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>AI recommendation</h2>
      <VerdictBand verdict={report.verdict} />
      <p className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>{report.decision.headline}</p>

      <div className="my-3 flex justify-center">
        <ScoreGauge score={overall?.score ?? null} label="Overall" size={118} toneOverride={verdictTone} />
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        {DIMS.map(({ key, label }) => {
          const d = report.scores[key]
          return <ScoreGauge key={key} score={d?.score ?? null} label={label} size={58}
            toneOverride={d ? tierTone(d.tone) : undefined} caption={caption(d)} />
        })}
      </div>

      <div className="mt-3 flex gap-2 border-t pt-2.5" style={{ borderColor: 'var(--px-hairline)' }}>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Coverage</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{(overall?.coverage ?? 0).toFixed(2)}</div>
        </div>
        <div className="flex-1">
          <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>Confidence</div>
          <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{confidenceLabel(overall?.confidence ?? 'low')}</div>
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- ScoresCard`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/ScoresCard.tsx tests/components/reports/ScoresCard.test.tsx
git commit -m "feat(reports-fe): ScoresCard — verdict + headline + 4 gauges"
```

---

## Task 4: `WhyContrast`

**Files:**
- Create: `components/dashboard/reports/WhyContrast.tsx`
- Test: `tests/components/reports/WhyContrast.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WhyContrast } from '@/components/dashboard/reports/WhyContrast'
import { makeReport } from './_fixture'

describe('WhyContrast', () => {
  it('renders both columns with titles and bodies', () => {
    render(<WhyContrast decision={makeReport().decision} />)
    expect(screen.getByText('Foundations are there')).toBeInTheDocument()
    expect(screen.getByText('Meets the experience bar.')).toBeInTheDocument()
    expect(screen.getByText('But depth was not shown')).toBeInTheDocument()
    expect(screen.getByText('Technical answers stayed thin.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- WhyContrast` → FAIL (module not found).

- [ ] **Step 3: Implement `WhyContrast.tsx`**

```tsx
import type { DecisionOut } from '@/lib/api/reports'

export function WhyContrast({ decision }: { decision: DecisionOut }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Why this verdict">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Why this verdict</h2>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg p-3" style={{ background: 'var(--px-ok-bg)' }}>
          <div className="mb-1 text-[11.5px] font-bold" style={{ color: 'var(--px-ok)' }}>{decision.why_positive.title}</div>
          <p className="text-[11.5px]" style={{ color: 'var(--px-fg-2)' }}>{decision.why_positive.body}</p>
        </div>
        <div className="rounded-lg p-3" style={{ background: 'var(--px-caution-bg)' }}>
          <div className="mb-1 text-[11.5px] font-bold" style={{ color: 'var(--px-caution)' }}>{decision.why_negative.title}</div>
          <p className="text-[11.5px]" style={{ color: 'var(--px-fg-2)' }}>{decision.why_negative.body}</p>
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test -- WhyContrast` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/WhyContrast.tsx tests/components/reports/WhyContrast.test.tsx
git commit -m "feat(reports-fe): WhyContrast two-column"
```

---

## Task 5: `QuickSummary`

**Files:**
- Create: `components/dashboard/reports/QuickSummary.tsx`
- Test: `tests/components/reports/QuickSummary.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuickSummary } from '@/components/dashboard/reports/QuickSummary'

describe('QuickSummary', () => {
  it('renders the narrative text', () => {
    render(<QuickSummary text="This candidate sits right on the line." />)
    expect(screen.getByText('This candidate sits right on the line.')).toBeInTheDocument()
  })
  it('renders nothing when text is empty', () => {
    const { container } = render(<QuickSummary text="" />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- QuickSummary` → FAIL.

- [ ] **Step 3: Implement `QuickSummary.tsx`**

```tsx
export function QuickSummary({ text }: { text: string }) {
  if (!text) return null
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Summary">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Quick summary</h2>
      <p className="whitespace-pre-wrap text-[12px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>{text}</p>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test -- QuickSummary` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/QuickSummary.tsx tests/components/reports/QuickSummary.test.tsx
git commit -m "feat(reports-fe): QuickSummary paragraph"
```

---

## Task 6: `StrengthsConcerns`

**Files:**
- Create: `components/dashboard/reports/StrengthsConcerns.tsx`
- Test: `tests/components/reports/StrengthsConcerns.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StrengthsConcerns } from '@/components/dashboard/reports/StrengthsConcerns'
import { makeReport } from './_fixture'

describe('StrengthsConcerns', () => {
  it('renders strengths, concerns, counts, and a severity chip', () => {
    const r = makeReport()
    render(<StrengthsConcerns strengths={r.strengths} concerns={r.concerns} />)
    expect(screen.getByText('Meets the experience bar')).toBeInTheDocument()
    expect(screen.getByText('No core skill reached the bar')).toBeInTheDocument()
    expect(screen.getByText('Deal-breaker')).toBeInTheDocument()   // severity chip label
    expect(screen.getByText(/Strengths/)).toBeInTheDocument()
    expect(screen.getByText(/Concerns/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- StrengthsConcerns` → FAIL.

- [ ] **Step 3: Implement `StrengthsConcerns.tsx`**

```tsx
import type { ConcernOut, StrengthOut } from '@/lib/api/reports'
import { severityMeta, TONE_BG, TONE_INK } from './report-format'

export function StrengthsConcerns({ strengths, concerns }: { strengths: StrengthOut[]; concerns: ConcernOut[] }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Strengths and concerns">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <div className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-ok)' }}>
            Strengths {strengths.length}
          </div>
          <ul className="space-y-2">
            {strengths.map((s, i) => (
              <li key={i}>
                <div className="text-[11.5px] font-semibold" style={{ color: 'var(--px-fg)' }}>{s.title}</div>
                <p className="text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{s.detail}</p>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-danger)' }}>
            Concerns {concerns.length}
          </div>
          <ul className="space-y-2">
            {concerns.map((c, i) => {
              const sev = severityMeta(c.severity)
              return (
                <li key={i}>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11.5px] font-semibold" style={{ color: 'var(--px-fg)' }}>{c.title}</span>
                    <span className="rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                      style={{ background: TONE_BG[sev.tone], color: TONE_INK[sev.tone] }}>{sev.label}</span>
                  </div>
                  <p className="text-[11px]" style={{ color: 'var(--px-fg-3)' }}>{c.detail}</p>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test -- StrengthsConcerns` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/StrengthsConcerns.tsx tests/components/reports/StrengthsConcerns.test.tsx
git commit -m "feat(reports-fe): StrengthsConcerns with severity chips"
```

---

## Task 7: `QuestionByQuestion`

**Files:**
- Create: `components/dashboard/reports/QuestionByQuestion.tsx`
- Test: `tests/components/reports/QuestionByQuestion.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QuestionByQuestion } from '@/components/dashboard/reports/QuestionByQuestion'
import { makeReport } from './_fixture'

describe('QuestionByQuestion', () => {
  it('renders each question with badge, quote, and our-read', () => {
    render(<QuestionByQuestion questions={makeReport().questions} />)
    expect(screen.getByText('How many years of experience do you have?')).toBeInTheDocument()
    expect(screen.getByText('Passed')).toBeInTheDocument()
    expect(screen.getByText('Partial')).toBeInTheDocument()
    expect(screen.getByText(/Comfortably clears/)).toBeInTheDocument()
    expect(screen.getByText(/Around six years\./)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- QuestionByQuestion` → FAIL.

- [ ] **Step 3: Implement `QuestionByQuestion.tsx`**

```tsx
import type { QuestionOut } from '@/lib/api/reports'
import { statusBadgeMeta, TONE_BG, TONE_INK, tierTone } from './report-format'

export function QuestionByQuestion({ questions }: { questions: QuestionOut[] }) {
  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Question by question">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>Question by question</h2>
      <ol className="space-y-3">
        {questions.map((q) => {
          const meta = statusBadgeMeta(q.status_badge)
          const tone = tierTone(q.status_tone)   // prefer backend tone; fall back via meta
          const badgeTone = tone === 'neutral' ? meta.tone : tone
          return (
            <li key={q.question_id} className="border-t pt-3 first:border-t-0 first:pt-0" style={{ borderColor: 'var(--px-hairline)' }}>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[10px] font-bold" style={{ color: 'var(--px-fg-4)' }}>
                  {q.seq}. {q.title}
                </span>
                <span className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                  style={{ background: TONE_BG[badgeTone], color: TONE_INK[badgeTone] }}>{meta.label}</span>
              </div>
              <p className="text-[11.5px] font-medium" style={{ color: 'var(--px-fg)' }}>{q.question_text}</p>
              {q.candidate_quote && (
                <blockquote className="mt-1 border-l-2 pl-2 text-[11px] italic" style={{ borderColor: 'var(--px-hairline-strong)', color: 'var(--px-fg-3)' }}>
                  “{q.candidate_quote}”
                </blockquote>
              )}
              {q.our_read && (
                <p className="mt-1.5 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>
                  <span className="font-semibold" style={{ color: 'var(--px-fg-3)' }}>Our read: </span>{q.our_read}
                </p>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test -- QuestionByQuestion` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/QuestionByQuestion.tsx tests/components/reports/QuestionByQuestion.test.tsx
git commit -m "feat(reports-fe): QuestionByQuestion — badge, quote, our-read"
```

---

## Task 8: `SignalAuditTable` (collapsed)

**Files:**
- Create: `components/dashboard/reports/SignalAuditTable.tsx`
- Test: `tests/components/reports/SignalAuditTable.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SignalAuditTable } from '@/components/dashboard/reports/SignalAuditTable'
import { makeReport } from './_fixture'

describe('SignalAuditTable', () => {
  it('renders a collapsed details with the signal rows', () => {
    render(<SignalAuditTable assessments={makeReport().signal_assessments} />)
    const summary = screen.getByText(/Audit detail/i)
    expect(summary).toBeInTheDocument()
    // the <details> is collapsed by default (no `open` attribute)
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    expect(details?.hasAttribute('open')).toBe(false)
    // content is in the DOM (jsdom renders collapsed details content)
    expect(screen.getByText('4+ years total professional experience')).toBeInTheDocument()
    expect(screen.getByText(/sufficient/)).toBeInTheDocument()
  })
  it('renders nothing when there are no assessments', () => {
    const { container } = render(<SignalAuditTable assessments={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- SignalAuditTable` → FAIL.

- [ ] **Step 3: Implement `SignalAuditTable.tsx`**

```tsx
import type { SignalAssessmentOut } from '@/lib/api/reports'

export function SignalAuditTable({ assessments }: { assessments: SignalAssessmentOut[] }) {
  if (!assessments.length) return null
  return (
    <details className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }}>
      <summary className="cursor-pointer text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-4)' }}>
        Audit detail — signal by signal ({assessments.length})
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-[10.5px]">
          <thead>
            <tr style={{ color: 'var(--px-fg-4)' }} className="text-left">
              <th className="py-1 pr-2 font-semibold">Signal</th>
              <th className="py-1 pr-2 font-semibold">Must-have</th>
              <th className="py-1 pr-2 font-semibold">Engine → Final</th>
              <th className="py-1 pr-2 font-semibold">Grade</th>
              <th className="py-1 font-semibold">Note</th>
            </tr>
          </thead>
          <tbody>
            {assessments.map((a) => (
              <tr key={a.signal} className="border-t align-top" style={{ borderColor: 'var(--px-hairline)' }}>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-2)' }}>{a.signal}</td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>{a.knockout ? 'yes' : '—'}</td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>
                  {a.engine_state} → {a.final_state}{a.overridden ? ' *' : ''}
                </td>
                <td className="py-1 pr-2" style={{ color: 'var(--px-fg-3)' }}>{a.grade ?? '—'}</td>
                <td className="py-1" style={{ color: 'var(--px-fg-4)' }}>{a.override_reason ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1.5 text-[9px]" style={{ color: 'var(--px-fg-4)' }}>* re-checked and adjusted by the post-session scorer.</p>
      </div>
    </details>
  )
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test -- SignalAuditTable` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/SignalAuditTable.tsx tests/components/reports/SignalAuditTable.test.tsx
git commit -m "feat(reports-fe): collapsed SignalAuditTable"
```

---

## Task 9: `ReportMethodologyFooter` update

**Files:**
- Modify: `components/dashboard/reports/ReportMethodologyFooter.tsx`
- Test: `tests/components/reports/ReportMethodologyFooter.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `tests/components/reports/ReportMethodologyFooter.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportMethodologyFooter } from '@/components/dashboard/reports/ReportMethodologyFooter'
import { makeReport } from './_fixture'

describe('ReportMethodologyFooter', () => {
  it('renders the methodology note, charity flags, and manifest line', () => {
    const r = makeReport()
    render(<ReportMethodologyFooter methodology={r.methodology} manifest={r.scoring_manifest} />)
    expect(screen.getByText(/Reached 7 of 8/)).toBeInTheDocument()
    expect(screen.getByText(/long mid-interview silence/)).toBeInTheDocument()
    expect(screen.getByText(/gpt-5.4/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- ReportMethodologyFooter` → FAIL (signature now takes `methodology`).

- [ ] **Step 3: Rewrite `ReportMethodologyFooter.tsx`**

```tsx
import type { MethodologyOut, ScoringManifest } from '@/lib/api/reports'

export function ReportMethodologyFooter({ methodology, manifest }: { methodology: MethodologyOut; manifest: ScoringManifest | null }) {
  const meta: string[] = []
  if (manifest?.scorer_model) meta.push(`scorer ${manifest.scorer_model}${manifest.reasoning_effort ? ` · ${manifest.reasoning_effort}` : ''}`)
  if (manifest?.prompt_version) meta.push(`prompt ${manifest.prompt_version}`)
  meta.push('verbal-content-only')
  if (manifest?.generated_at) meta.push(`generated ${new Date(manifest.generated_at).toLocaleDateString()}`)
  if (manifest?.correlation_id) meta.push(`corr ${manifest.correlation_id.slice(0, 8)}`)

  return (
    <footer className="mt-4 border-t px-1 pt-3" style={{ borderColor: 'var(--px-hairline)' }}>
      <p className="text-[10.5px]" style={{ color: 'var(--px-fg-3)' }}>
        <span className="font-bold">About this report. </span>{methodology.note}
      </p>
      {methodology.charity_flags.map((f, i) => (
        <p key={i} className="mt-1 text-[10px]" style={{ color: 'var(--px-fg-4)' }}>⚑ {f}</p>
      ))}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[9.5px]" style={{ color: 'var(--px-fg-4)' }}>
        <span className="font-bold" style={{ color: 'var(--px-fg-3)' }}>Methodology</span>
        {meta.map((it, i) => <span key={i}>{it}</span>)}
      </div>
    </footer>
  )
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test -- ReportMethodologyFooter` → PASS.

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/ReportMethodologyFooter.tsx tests/components/reports/ReportMethodologyFooter.test.tsx
git commit -m "feat(reports-fe): methodology footer renders note + charity flags"
```

---

## Task 10: Recompose `ReportView`, delete old components, full gate

**Files:**
- Modify: `components/dashboard/reports/ReportView.tsx`
- Modify: `tests/components/reports/ReportView.test.tsx`
- Delete: `components/dashboard/reports/{SignalScorecards,QaEvidencePanel,ReportSummary,SignalSpiderChart,EvidenceQuote,AiRecommendationCard}.tsx` + matching tests under `tests/components/reports/`.

- [ ] **Step 1: Rewrite `ReportView.test.tsx`**

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReportView } from '@/components/dashboard/reports/ReportView'
import { makeReport } from './_fixture'

const noop = vi.fn()

function renderView(report = makeReport()) {
  return render(
    <ReportView report={report} candidateName="Asha" candidateId="c1"
      title="Jr. FDE" subtitle="AI Screening" canRegenerate={false}
      onRegenerate={noop} onDecision={noop} isSubmitting={false} />,
  )
}

describe('ReportView', () => {
  it('renders all PDF sections from a ready report', () => {
    renderView()
    expect(screen.getAllByText('Borderline').length).toBeGreaterThan(0)   // chip + verdict band
    expect(screen.getByText('Why this verdict')).toBeInTheDocument()
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
    expect(screen.getByText(/Strengths/)).toBeInTheDocument()
    expect(screen.getByText('Question by question')).toBeInTheDocument()
    expect(screen.getByText(/Audit detail/)).toBeInTheDocument()
    expect(screen.getByText(/About this report/)).toBeInTheDocument()
  })
  it('does not crash when optional collections are empty', () => {
    renderView(makeReport({ strengths: [], concerns: [], questions: [], signal_assessments: [] }))
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails** — `npm run test -- ReportView` → FAIL (ReportView still renders old components).

- [ ] **Step 3: Rewrite `ReportView.tsx`**

```tsx
'use client'

import type { HumanDecisionValue, ReportRead } from '@/lib/api/reports'
import { HumanDecisionPanel } from './HumanDecisionPanel'
import { QuestionByQuestion } from './QuestionByQuestion'
import { QuickSummary } from './QuickSummary'
import { ReportMethodologyFooter } from './ReportMethodologyFooter'
import { ReportTopBar } from './ReportTopBar'
import { ScoresCard } from './ScoresCard'
import { SessionPlaybackStub } from './SessionPlaybackStub'
import { SignalAuditTable } from './SignalAuditTable'
import { StrengthsConcerns } from './StrengthsConcerns'
import { WhyContrast } from './WhyContrast'

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
          <WhyContrast decision={report.decision} />
          <QuickSummary text={report.quick_summary} />
          <StrengthsConcerns strengths={report.strengths} concerns={report.concerns} />
          <QuestionByQuestion questions={report.questions} />
          <SignalAuditTable assessments={report.signal_assessments} />
        </div>
        {/* SIDE */}
        <div className="space-y-3.5">
          <ScoresCard report={report} />
          <HumanDecisionPanel verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />
        </div>
      </div>
      <ReportMethodologyFooter methodology={report.methodology} manifest={report.scoring_manifest} />
    </div>
  )
}
```

- [ ] **Step 4: Delete the retired components + their tests**

```bash
git rm components/dashboard/reports/SignalScorecards.tsx \
       components/dashboard/reports/QaEvidencePanel.tsx \
       components/dashboard/reports/ReportSummary.tsx \
       components/dashboard/reports/SignalSpiderChart.tsx \
       components/dashboard/reports/EvidenceQuote.tsx \
       components/dashboard/reports/AiRecommendationCard.tsx
# remove their tests if present:
git rm -f tests/components/reports/SignalScorecards.test.tsx \
          tests/components/reports/SignalSpiderChart.test.tsx \
          tests/components/reports/QaEvidencePanel.test.tsx \
          tests/components/reports/EvidenceQuote.test.tsx \
          tests/components/reports/AiRecommendationCard.test.tsx 2>/dev/null || true
```

Then GREP for any lingering importers and fix/remove them:
```bash
grep -rln "SignalScorecards\|QaEvidencePanel\|ReportSummary\|SignalSpiderChart\|EvidenceQuote\|AiRecommendationCard" components app tests
```
Expected: no matches (if any remain outside the deleted files, update them — likely none).

- [ ] **Step 5: Run the ReportView test**

Run: `npm run test -- ReportView`
Expected: PASS.

- [ ] **Step 6: Full gate (this is where global type-check goes green)**

```bash
npm run type-check   # tsc --noEmit — must be zero errors now
npm run lint         # zero errors
npm run test         # all reporting + other suites green
npm run build        # production build succeeds
```
Fix any errors surfaced (most likely: a missed importer of a deleted symbol, or a residual reference to a removed type like `SignalScorecard`/`QuestionScorecard` in a non-report file — grep and fix). The reports hub page (`app/(dashboard)/reports/page.tsx`) uses only `ReportIndexItem` (unchanged) — verify it still type-checks.

- [ ] **Step 7: Commit**

```bash
git add -A components/dashboard/reports tests/components/reports
git commit -m "feat(reports-fe): recompose ReportView to the new schema; remove the signals dump"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** §2 contract → Task 1; §3 format → Task 2; §4 components → Tasks 3–9 (`ScoresCard` 3, `WhyContrast` 4, `QuickSummary` 5, `StrengthsConcerns` 6, `QuestionByQuestion` 7, `SignalAuditTable` 8, footer 9); §5 ReportView + §4 deletes → Task 10; §6 testing → per-task tests + Task 10 gate.
- **Type-check is intentionally red Tasks 1–9** (the contract changed before consumers). Per-task verification uses `npm run test -- <pattern>` (Vitest transpiles, doesn't whole-project type-check). Task 10 Step 6 is the global green gate.
- **`tierTone`** is used in `ScoresCard` (dimension gauge tone) and `QuestionByQuestion` (badge tone) — defined once in Task 2.
- **`TONE_BG`/`TONE_INK`** already exist in `report-format.ts` (used by `StrengthsConcerns`, `QuestionByQuestion`). Confirm they remain exported when removing the signal/knockout helpers in Task 2.
- **`HumanDecisionPanel`** is unchanged and consumes only `verdict` + `human_decision` (both still present) — no edit needed.
- If `grep` in Task 10 Step 4 finds an importer of a deleted component anywhere unexpected, fix it before the gate rather than forcing the build.
