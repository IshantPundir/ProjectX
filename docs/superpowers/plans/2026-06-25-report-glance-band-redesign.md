# Report Glance-Band Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift all scoring visuals into a glanceable full-width "Glance Band" under the report hero, built from a new threshold-banded bar primitive, so a recruiter grasps a candidate in 10–15 seconds; reduce the right rail to Proctoring + Decision.

**Architecture:** Pure frontend pass over the existing `ReportRead` payload. One new atomic component `ScoreBar` (threshold-banded horizontal bar) replaces the body gauges and the radar chart. One new section component `GlanceBand` composes `ScoreBar` into verdict + overall + dimensions + grouped competencies. `ReportView` is rewired; `ScoresCard` + `CompetencyRadar` retire. Band thresholds are exported from `report-format.ts` as the single source of truth.

**Tech Stack:** Next.js 16 + React 19, TypeScript strict, Tailwind v4 (`--px-*` design tokens via `app/theme.css`), in-house `px/` primitives, hand-rolled SVG/CSS (no chart lib), Vitest + Testing Library.

## Global Constraints

- Surface is `frontend/app` (recruiter dashboard) ONLY. No `livekit-*`, no `@/components/{ui,agents-ui,ai-elements}/` imports.
- TypeScript strict — no `any`; use `unknown` + narrowing. All types explicit.
- No raw hex / no `text-[#...]`. Use `--px-*` tokens (via `TONE_*` maps in `report-format.ts`) and Tailwind named utilities.
- No custom CSS unless a utility genuinely doesn't exist — band zones / fill keyframe go in the existing `report.css`.
- Respect `prefers-reduced-motion`: values readable immediately; animation is enhancement only.
- a11y: icon/graphic elements carry `role="img"` + descriptive `aria-label`; never convey state by color alone (pair with glyph + visible value).
- No backend / schema / API changes. No new npm deps. No dark mode.
- Desktop-first (1280px target). Bundle budget < 250 KB gz/route.
- Tests: composition convention (parent+child, mock at API boundary). Run `npm run test`, `npm run lint`, `npm run type-check` — all must pass.
- Verdict band thresholds on the 0–10 scale: `REJECT_BAND = 4.0`, `ADVANCE_BAND = 6.5` (hiring bar = 6.5). Copy verbatim.

---

## File Structure

**New:**
- `components/dashboard/reports/ScoreBar.tsx` — threshold-banded bar primitive (`hero` / `row` / `compact` variants).
- `components/dashboard/reports/GlanceBand.tsx` — top dashboard section composing `ScoreBar`.
- `tests/components/reports/ScoreBar.test.tsx`
- `tests/components/reports/GlanceBand.test.tsx`

**Modified:**
- `components/dashboard/reports/report-format.ts` — export `REJECT_BAND`, `ADVANCE_BAND`, `SCORE_MAX`, `bandZones()`; refactor `scoreBandTone` to use them.
- `components/dashboard/reports/report.css` — band zone classes, hiring-bar marker, bar-grow keyframe + reduced-motion entry.
- `components/dashboard/reports/ReportView.tsx` — insert `<GlanceBand>`, drop `ScoresCard` from the rail, re-number reveal staggers.
- `tests/components/reports/ReportView.test.tsx` — assert GlanceBand present, ScoresCard absent from rail, Proctoring + Decision present.

**Deleted:**
- `components/dashboard/reports/ScoresCard.tsx` + `tests/components/reports/ScoresCard.test.tsx`
- `components/dashboard/reports/CompetencyRadar.tsx` + `tests/components/reports/CompetencyRadar.test.tsx`

**Untouched (verify still compile):** `ScoreGauge.tsx` (used by `theater/ScoreRail.tsx`), `VerdictBand.tsx` (used by `ReportTopBar.tsx`, `reports/page.tsx`), `PublicRecordingsView.tsx` (inherits `ReportView`).

---

### Task 1: Export band thresholds from `report-format.ts`

Single source of truth for the verdict bands, consumed by both `scoreBandTone` and `ScoreBar`.

**Files:**
- Modify: `components/dashboard/reports/report-format.ts`
- Test: `tests/components/reports/report-format.test.ts` (exists — add cases)

**Interfaces:**
- Produces:
  - `export const REJECT_BAND = 4.0`
  - `export const ADVANCE_BAND = 6.5`
  - `export const SCORE_MAX = 10`
  - `export function bandZones(): { rejectPct: number; advancePct: number }` → `{ rejectPct: 40, advancePct: 65 }`
  - `scoreBandTone(score: number | null): Tone` (unchanged signature, now uses the constants)

- [ ] **Step 1: Write the failing test**

Append to `tests/components/reports/report-format.test.ts`:

```ts
import { ADVANCE_BAND, REJECT_BAND, SCORE_MAX, bandZones, scoreBandTone } from '@/components/dashboard/reports/report-format'

describe('band thresholds', () => {
  it('exposes the 0-10 verdict band constants', () => {
    expect(REJECT_BAND).toBe(4.0)
    expect(ADVANCE_BAND).toBe(6.5)
    expect(SCORE_MAX).toBe(10)
  })

  it('bandZones() returns boundary positions as percentages of the track', () => {
    expect(bandZones()).toEqual({ rejectPct: 40, advancePct: 65 })
  })

  it('scoreBandTone stays aligned to the exported bands', () => {
    expect(scoreBandTone(6.5)).toBe('ok')      // >= ADVANCE_BAND
    expect(scoreBandTone(6.49)).toBe('caution') // borderline
    expect(scoreBandTone(4.0)).toBe('caution')  // >= REJECT_BAND
    expect(scoreBandTone(3.99)).toBe('danger')
    expect(scoreBandTone(null)).toBe('neutral')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- report-format`
Expected: FAIL — `REJECT_BAND`/`bandZones` not exported.

- [ ] **Step 3: Write minimal implementation**

In `components/dashboard/reports/report-format.ts`, replace the `scoreBandTone` block (currently lines ~69–76) with:

```ts
/** Verdict band thresholds on the report's native 0–10 scale (single source of
 *  truth — backend ADVANCE_THRESHOLD 6.5 / REJECT_THRESHOLD 4.0). */
export const REJECT_BAND = 4.0
export const ADVANCE_BAND = 6.5
export const SCORE_MAX = 10

/** Band boundary positions as a percentage (0–100) of a 0–10 track. */
export function bandZones(): { rejectPct: number; advancePct: number } {
  return {
    rejectPct: (REJECT_BAND / SCORE_MAX) * 100,
    advancePct: (ADVANCE_BAND / SCORE_MAX) * 100,
  }
}

/** Tier tone from a 0–10 score, aligned to the exported verdict bands. */
export function scoreBandTone(score: number | null): Tone {
  if (score === null || score === undefined) return 'neutral'
  if (score >= ADVANCE_BAND) return 'ok'
  if (score >= REJECT_BAND) return 'caution'
  return 'danger'
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/app && npm run test -- report-format`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/report-format.ts frontend/app/tests/components/reports/report-format.test.ts
git commit -m "feat(report): export verdict band thresholds as single source of truth

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `ScoreBar` primitive + CSS

The threshold-banded horizontal bar. Replaces body gauges + radar.

**Files:**
- Create: `components/dashboard/reports/ScoreBar.tsx`
- Modify: `components/dashboard/reports/report.css`
- Test: `tests/components/reports/ScoreBar.test.tsx`

**Interfaces:**
- Consumes (Task 1): `ADVANCE_BAND`, `bandZones`, `formatTen`, `scoreBandTone`, `TONE_FILL`, `Tone` from `./report-format`.
- Produces:
  - `export type ScoreBarVariant = 'hero' | 'row' | 'compact'`
  - `export function ScoreBar(props: { score: number | null; label: string; variant?: ScoreBarVariant; toneOverride?: Tone; mustHave?: boolean; notReached?: boolean; showBands?: boolean; caption?: string }): React.ReactElement`

- [ ] **Step 1: Write the failing test**

Create `tests/components/reports/ScoreBar.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreBar } from '@/components/dashboard/reports/ScoreBar'

describe('ScoreBar', () => {
  it('renders value, cleared-bar a11y label, and ✓ glyph when score clears 6.5', () => {
    render(<ScoreBar score={7.2} label="Technical" />)
    expect(screen.getByText('7.2')).toBeInTheDocument()
    const bar = screen.getByRole('img', { name: /Technical score 7.2 out of 10, above hiring bar/ })
    expect(bar).toBeInTheDocument()
    expect(bar.textContent).toContain('✓')
  })

  it('shows ⚠ and below-bar label when score is under 6.5', () => {
    render(<ScoreBar score={4.9} label="Behavioral" />)
    const bar = screen.getByRole('img', { name: /Behavioral score 4.9 out of 10, below hiring bar/ })
    expect(bar.textContent).toContain('⚠')
  })

  it('sets the fill width to score/10 as a percentage', () => {
    const { container } = render(<ScoreBar score={6.0} label="Comms" />)
    const fill = container.querySelector('.px-scorebar-fill') as HTMLElement
    expect(fill).toBeTruthy()
    expect(fill.style.getPropertyValue('--px-bar-fill')).toBe('60%')
  })

  it('renders threshold band zones for a row bar', () => {
    const { container } = render(<ScoreBar score={5.0} label="X" variant="row" />)
    expect(container.querySelector('.px-band-reject')).toBeTruthy()
    expect(container.querySelector('.px-band-borderline')).toBeTruthy()
    expect(container.querySelector('.px-band-advance')).toBeTruthy()
    expect(container.querySelector('.px-bar-marker')).toBeTruthy()
  })

  it('compact variant omits the full band zones', () => {
    const { container } = render(<ScoreBar score={5.0} label="X" variant="compact" />)
    expect(container.querySelector('.px-band-reject')).toBeNull()
  })

  it('renders a must-have ★ marker when mustHave is set', () => {
    const { container } = render(<ScoreBar score={8} label="Domain" mustHave />)
    expect(container.textContent).toContain('★')
  })

  it('renders a not-assessed state for null score', () => {
    render(<ScoreBar score={null} label="Ownership" />)
    expect(screen.getByRole('img', { name: /Ownership not assessed/ })).toBeInTheDocument()
    expect(screen.getByText('n/a')).toBeInTheDocument()
  })

  it('renders a not-reached state when notReached is set', () => {
    render(<ScoreBar score={null} label="Stakeholder" notReached />)
    expect(screen.getByRole('img', { name: /Stakeholder not reached/ })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- ScoreBar`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

Create `components/dashboard/reports/ScoreBar.tsx`:

```tsx
import type { CSSProperties } from 'react'

import { ADVANCE_BAND, bandZones, formatTen, scoreBandTone, TONE_FILL, type Tone } from './report-format'
import './report.css'

export type ScoreBarVariant = 'hero' | 'row' | 'compact'

interface ScoreBarProps {
  /** 0–10 score; null → "not assessed". */
  score: number | null
  label: string
  variant?: ScoreBarVariant
  /** Override the tier-derived fill tone (e.g. color Overall by verdict). */
  toneOverride?: Tone
  /** Render a ★ must-have marker before the label. */
  mustHave?: boolean
  /** Signal was never reached in the interview → muted dashed track. */
  notReached?: boolean
  /** Render the faint reject/borderline/advance zones (default: true unless compact). */
  showBands?: boolean
  /** Secondary line under the bar. */
  caption?: string
}

export function ScoreBar({
  score, label, variant = 'row', toneOverride,
  mustHave = false, notReached = false, showBands, caption,
}: ScoreBarProps): React.ReactElement {
  const assessed = score !== null && score !== undefined && !notReached
  const ten = formatTen(score)
  const tone = toneOverride ?? scoreBandTone(score)
  const cleared = assessed && (score as number) >= ADVANCE_BAND
  const { rejectPct, advancePct } = bandZones()
  const fillPct = assessed ? Math.max(0, Math.min(100, ((score as number) / 10) * 100)) : 0
  const bands = (showBands ?? variant !== 'compact') && assessed

  const stateLabel = !assessed
    ? notReached ? 'not reached' : 'not assessed'
    : cleared ? 'above hiring bar' : 'below hiring bar'
  const aria = assessed
    ? `${label} score ${ten} out of 10, ${stateLabel}`
    : `${label} ${stateLabel}`

  const trackH = variant === 'hero' ? 'h-3.5' : variant === 'compact' ? 'h-2' : 'h-2.5'
  const labelSize = variant === 'hero' ? 'text-[13px]' : 'text-[12px]'
  const valueSize = variant === 'hero' ? 'text-[20px]' : 'text-[13px]'

  return (
    <div role="img" aria-label={aria} className="px-scorebar w-full">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className={`${labelSize} truncate font-semibold`} style={{ color: 'var(--px-fg-2)' }}>
          {mustHave && <span aria-hidden className="mr-1" style={{ color: 'var(--px-accent)' }}>★</span>}
          {label}
        </span>
        <span className={`${valueSize} whitespace-nowrap font-bold tabular-nums`}
          style={{ color: assessed ? 'var(--px-fg)' : 'var(--px-fg-4)' }}>
          {assessed ? ten : 'n/a'}
          {assessed && (
            <span aria-hidden className="ml-1" style={{ color: cleared ? 'var(--px-ok)' : 'var(--px-caution)' }}>
              {cleared ? '✓' : '⚠'}
            </span>
          )}
        </span>
      </div>

      <div className={`relative ${trackH} w-full overflow-hidden rounded-full`} style={{ background: 'var(--px-surface-3)' }}>
        {bands && (
          <>
            <div className="px-band-reject absolute inset-y-0 left-0" style={{ width: `${rejectPct}%` }} aria-hidden />
            <div className="px-band-borderline absolute inset-y-0" style={{ left: `${rejectPct}%`, width: `${advancePct - rejectPct}%` }} aria-hidden />
            <div className="px-band-advance absolute inset-y-0" style={{ left: `${advancePct}%`, right: 0 }} aria-hidden />
          </>
        )}
        {assessed ? (
          <div className="px-scorebar-fill absolute inset-y-0 left-0 rounded-full"
            style={{ '--px-bar-fill': `${fillPct}%`, background: TONE_FILL[tone] } as CSSProperties} aria-hidden />
        ) : (
          <div className="absolute inset-0 rounded-full" style={{ border: '1px dashed var(--px-fg-4)', opacity: 0.5 }} aria-hidden />
        )}
        {bands && (
          <div className="px-bar-marker absolute inset-y-0" style={{ left: `${advancePct}%` }} aria-hidden />
        )}
      </div>

      {caption && <div className="mt-0.5 text-[10px]" style={{ color: 'var(--px-fg-4)' }}>{caption}</div>}
    </div>
  )
}
```

- [ ] **Step 4: Add the CSS**

Append to `components/dashboard/reports/report.css` (before the `@media (prefers-reduced-motion: reduce)` block at line ~210):

```css
/* --- ScoreBar (threshold-banded horizontal bar) --- */
.px-band-reject     { background: color-mix(in srgb, var(--px-danger) 12%, transparent); }
.px-band-borderline { background: color-mix(in srgb, var(--px-caution) 12%, transparent); }
.px-band-advance    { background: color-mix(in srgb, var(--px-ok) 12%, transparent); }
.px-bar-marker {
  width: 2px;
  background: var(--px-fg-3);
  opacity: 0.55;
  transform: translateX(-1px);
}
@keyframes px-bar-grow { from { width: 0; } to { width: var(--px-bar-fill); } }
.px-scorebar-fill {
  width: var(--px-bar-fill);
  animation: px-bar-grow 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) forwards;
}
```

Then add to the existing `@media (prefers-reduced-motion: reduce)` block (inside the braces):

```css
  .px-scorebar-fill { animation: none; width: var(--px-bar-fill); }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend/app && npm run test -- ScoreBar`
Expected: PASS (8 tests).

- [ ] **Step 6: Type-check + lint**

Run: `cd frontend/app && npm run type-check && npm run lint`
Expected: zero errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/reports/ScoreBar.tsx frontend/app/components/dashboard/reports/report.css frontend/app/tests/components/reports/ScoreBar.test.tsx
git commit -m "feat(report): add threshold-banded ScoreBar primitive

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `GlanceBand` section

Composes `ScoreBar` into the at-a-glance dashboard: verdict + overall + dimensions + grouped competencies.

**Files:**
- Create: `components/dashboard/reports/GlanceBand.tsx`
- Test: `tests/components/reports/GlanceBand.test.tsx`

**Interfaces:**
- Consumes (Task 2): `ScoreBar`. Plus `confidenceLabel`, `verdictMeta`, `TONE_INK` from `./report-format`; `ReportRead`, `SignalAssessmentOut` types from `@/lib/api/reports`.
- Produces: `export function GlanceBand({ report }: { report: ReportRead }): React.ReactElement`

- [ ] **Step 1: Write the failing test**

Create `tests/components/reports/GlanceBand.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { GlanceBand } from '@/components/dashboard/reports/GlanceBand'
import { makeReport, makeSignalAssessment } from './_fixture'

describe('GlanceBand', () => {
  it('renders the verdict label, headline, and overall score', () => {
    render(<GlanceBand report={makeReport()} />)
    expect(screen.getByText('Borderline')).toBeInTheDocument()
    expect(screen.getByText(/Credible baseline/)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall score 4.1 out of 10/ })).toBeInTheDocument()
  })

  it('renders assessed dimensions only (behavioral is null in fixture)', () => {
    render(<GlanceBand report={makeReport()} />)
    expect(screen.getByRole('img', { name: /Technical score 4.1/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Communication score 7.0/ })).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /Behavioral score/ })).not.toBeInTheDocument()
  })

  it('groups must-have (knockout) signals under Must-have competencies', () => {
    const report = makeReport({
      signal_assessments: [
        makeSignalAssessment({ signal: 'Domain knowledge', knockout: true, weight: 3, score: 7.6 }),
        makeSignalAssessment({ signal: 'Problem-solving', knockout: false, weight: 2, score: 6.4 }),
      ],
    })
    render(<GlanceBand report={report} />)
    expect(screen.getByText('Must-have competencies')).toBeInTheDocument()
    expect(screen.getByText('Other competencies')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Domain knowledge score 7.6/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Problem-solving score 6.4/ })).toBeInTheDocument()
  })

  it('shows a not-reached competency as muted', () => {
    const report = makeReport({
      signal_assessments: [
        makeSignalAssessment({ signal: 'Ownership', knockout: true, provenance: 'not_reached', score: null }),
      ],
    })
    render(<GlanceBand report={report} />)
    expect(screen.getByRole('img', { name: /Ownership not reached/ })).toBeInTheDocument()
  })

  it('omits the competencies tier entirely when there are no signal assessments', () => {
    render(<GlanceBand report={makeReport({ signal_assessments: [] })} />)
    expect(screen.queryByText('Must-have competencies')).not.toBeInTheDocument()
    expect(screen.queryByText('Other competencies')).not.toBeInTheDocument()
  })

  it('renders coverage and confidence chips', () => {
    render(<GlanceBand report={makeReport()} />)
    expect(screen.getByText('Coverage')).toBeInTheDocument()
    expect(screen.getByText('Confidence')).toBeInTheDocument()
    expect(screen.getByText('0.47')).toBeInTheDocument()
    expect(screen.getByText('Medium')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- GlanceBand`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

Create `components/dashboard/reports/GlanceBand.tsx`:

```tsx
import type { ReportRead, SignalAssessmentOut } from '@/lib/api/reports'
import { ScoreBar } from './ScoreBar'
import { confidenceLabel, TONE_INK, verdictMeta } from './report-format'
import './report.css'

const DIMS: { key: string; label: string }[] = [
  { key: 'technical', label: 'Technical' },
  { key: 'behavioral', label: 'Behavioral' },
  { key: 'communication', label: 'Communication' },
]

/** Highest weight first; stable tiebreak by name. */
function byWeightDesc(a: SignalAssessmentOut, b: SignalAssessmentOut): number {
  if (b.weight !== a.weight) return b.weight - a.weight
  return a.signal.localeCompare(b.signal)
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>{label}</div>
      <div className="text-[12px] font-semibold" style={{ color: 'var(--px-fg)' }}>{value}</div>
    </div>
  )
}

export function GlanceBand({ report }: { report: ReportRead }): React.ReactElement {
  const overall = report.scores.overall
  const meta = verdictMeta(report.verdict)
  const dims = DIMS.filter(({ key }) => report.scores[key]?.score != null)

  const mustHaves = report.signal_assessments.filter((a) => a.knockout).sort(byWeightDesc)
  const others = report.signal_assessments.filter((a) => !a.knockout).sort(byWeightDesc)
  const hasSignals = report.signal_assessments.length > 0

  return (
    <section
      aria-label="Candidate at a glance"
      className="px-card rounded-2xl border bg-white p-5"
      style={{ borderColor: 'var(--px-hairline)' }}
    >
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(260px,1fr)_1.1fr_1.3fr]">
        {/* Tier A — Verdict + Overall */}
        <div>
          <div className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
            AI recommendation
          </div>
          <div className="mt-1 text-[24px] font-extrabold tracking-tight" style={{ color: TONE_INK[meta.tone] }}>
            {meta.label}
          </div>
          <p className="mb-3 mt-1.5 text-[12.5px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>
            {report.decision.headline}
          </p>
          <ScoreBar score={overall?.score ?? null} label="Overall" variant="hero" toneOverride={meta.tone} />
          <div className="mt-3 flex gap-4">
            <Chip label="Coverage" value={(overall?.coverage ?? 0).toFixed(2)} />
            <Chip label="Confidence" value={confidenceLabel(overall?.confidence ?? 'low')} />
          </div>
        </div>

        {/* Tier B — Dimensions */}
        <div>
          <div className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
            Dimensions
          </div>
          <div className="flex flex-col gap-3">
            {dims.map(({ key, label }) => (
              <ScoreBar key={key} score={report.scores[key]?.score ?? null} label={label} variant="row" />
            ))}
          </div>
        </div>

        {/* Tier C — Competencies */}
        {hasSignals && (
          <div>
            {mustHaves.length > 0 && (
              <>
                <div className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
                  Must-have competencies
                </div>
                <div className="flex flex-col gap-3">
                  {mustHaves.map((a) => (
                    <ScoreBar key={a.signal} score={a.score} label={a.signal} variant="row"
                      mustHave notReached={a.provenance === 'not_reached'} />
                  ))}
                </div>
              </>
            )}
            {others.length > 0 && (
              <>
                <div className="mb-2 mt-4 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>
                  Other competencies
                </div>
                <div className="grid grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2">
                  {others.map((a) => (
                    <ScoreBar key={a.signal} score={a.score} label={a.signal} variant="compact"
                      notReached={a.provenance === 'not_reached'} />
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/app && npm run test -- GlanceBand`
Expected: PASS (6 tests).

- [ ] **Step 5: Type-check + lint**

Run: `cd frontend/app && npm run type-check && npm run lint`
Expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/components/dashboard/reports/GlanceBand.tsx frontend/app/tests/components/reports/GlanceBand.test.tsx
git commit -m "feat(report): add GlanceBand at-a-glance dashboard section

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire `GlanceBand` into `ReportView`, retire `ScoresCard` + `CompetencyRadar`

**Files:**
- Modify: `components/dashboard/reports/ReportView.tsx`
- Modify: `tests/components/reports/ReportView.test.tsx`
- Delete: `components/dashboard/reports/ScoresCard.tsx`, `components/dashboard/reports/CompetencyRadar.tsx`
- Delete: `tests/components/reports/ScoresCard.test.tsx`, `tests/components/reports/CompetencyRadar.test.tsx`

**Interfaces:**
- Consumes (Task 3): `GlanceBand`.

- [ ] **Step 1: Update the ReportView test (failing)**

Replace the body of `tests/components/reports/ReportView.test.tsx` test cases that reference ScoresCard. Specifically:

Replace the first test (`renders the at-a-glance band...`) with:

```tsx
  it('renders the glance band, left-column content, and right-rail panels', () => {
    renderView()
    // Glance band
    expect(screen.getByRole('region', { name: /Candidate at a glance/ })).toBeInTheDocument()
    expect(screen.getByText('Dimensions')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Overall score 4.1 out of 10/ })).toBeInTheDocument()
    // Left column
    expect(screen.getByText('Why this verdict')).toBeInTheDocument()
    expect(screen.getByText('Quick summary')).toBeInTheDocument()
    expect(screen.getByText('Question by question')).toBeInTheDocument()
    expect(screen.getByText(/Audit detail/)).toBeInTheDocument()
    // Methodology footer
    expect(screen.getByText(/About this report/)).toBeInTheDocument()
  })
```

Replace the `scores in ScoresCard...` test with:

```tsx
  it('scores render without double-division (0-10 native)', () => {
    // Fixture overall.score=4.1 (0-10 native). ScoreBar uses formatTen (no ÷10),
    // so it should display "4.1" — not "0.4" (double-divided).
    renderView()
    expect(screen.getAllByText('4.1').length).toBeGreaterThan(0)
  })
```

Update the mock comment at the top (line 7-8) from `<AtAGlanceBand>` to `<GlanceBand>`:

```tsx
// ReportView embeds <GlanceBand> + <ProctoringIntegrityPanel> which call query
// hooks. Mock them so this stays a focused layout test (no network required).
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- ReportView`
Expected: FAIL — `getByRole('region', { name: /Candidate at a glance/ })` not found (GlanceBand not yet wired).

- [ ] **Step 3: Wire GlanceBand into ReportView**

In `components/dashboard/reports/ReportView.tsx`:

(a) Replace the import line `import { ScoresCard } from './ScoresCard'` with:

```tsx
import { GlanceBand } from './GlanceBand'
```

(b) Insert the GlanceBand block immediately after the header block closes (after the legacy-fallback `)}` on line ~99, before the `{/* ── Two-column body ── */}` comment):

```tsx
      {/* ── At-a-glance band ── */}
      <div className="mb-5 px-reveal" style={{ '--px-stagger': 1 } as CSSProperties}>
        <GlanceBand report={report} />
      </div>
```

(c) In the RIGHT sticky rail array, remove the `ScoresCard` entry. The array becomes:

```tsx
            {[
              <ProctoringIntegrityPanel key="proctoring" sessionId={report.session_id ?? sessionId} onSeek={(ms) => openTheater(ms)} />,
              <HumanDecisionPanel key="decision" verdict={report.verdict} decision={report.human_decision} onSubmit={onDecision} isSubmitting={isSubmitting} />,
            ].map((node, i) => (
              <div key={node.key} className="px-reveal" style={{ '--px-stagger': i + 7 } as CSSProperties}>{node}</div>
            ))}
```

- [ ] **Step 4: Run ReportView test to verify it passes**

Run: `cd frontend/app && npm run test -- ReportView`
Expected: PASS.

- [ ] **Step 5: Delete retired components + their tests**

```bash
cd frontend/app
git rm components/dashboard/reports/ScoresCard.tsx components/dashboard/reports/CompetencyRadar.tsx tests/components/reports/ScoresCard.test.tsx tests/components/reports/CompetencyRadar.test.tsx
```

- [ ] **Step 6: Full verification — tests, type-check, lint, build**

Run: `cd frontend/app && npm run test && npm run type-check && npm run lint`
Expected: all pass, zero errors. (No remaining references to `ScoresCard` or `CompetencyRadar` — `grep -rn "ScoresCard\|CompetencyRadar" components tests` should return nothing.)

Then confirm the production build compiles:

Run: `cd frontend/app && npm run build`
Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/reports/ReportView.tsx frontend/app/tests/components/reports/ReportView.test.tsx
git commit -m "feat(report): mount GlanceBand, reduce rail to proctoring+decision

Retires ScoresCard + CompetencyRadar (radar chart). All scoring visuals
now live in the full-width GlanceBand under the hero.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Live visual verification

Confirm the redesign in the running app at the report URL from the request.

- [ ] **Step 1: Ensure the dev server is running**

Run (if not already up): `cd frontend/app && npm run dev` (port 3000). Note: Next dev can serve stale CSS after git churn — if styles look wrong, restart with a clean `.next`.

- [ ] **Step 2: Open the report and verify the glance picture**

Navigate to `http://localhost:3000/reports/session/b45aad8e-8630-4a63-a60f-be109268cb54?candidateId=7f038156-243a-414e-ab4a-c7cb2331145a&title=Chartered%20Accountant&subtitle=Bot%20Screening`.

Verify by eye (and optionally via the browser MCP / a screenshot):
- The Glance Band sits directly under the hero, full width.
- Verdict + overall bar + 1-line why on the left; three dimension bars; must-have vs other competency bars.
- Each bar shows faint reject/borderline/advance zones + the hiring-bar marker + value + ✓/⚠.
- The right rail shows ONLY Proctoring + Decision (no scores card, no radar).
- Bars animate their fill on load; with `prefers-reduced-motion` the fill is shown immediately.

- [ ] **Step 3: No commit** (verification only). If issues are found, fix in the relevant task's files and re-run that task's tests before re-verifying.

---

## Self-Review

**1. Spec coverage:**
- ScoreBar primitive (variants, bands, marker, null/not-reached, a11y) → Task 2 ✓
- GlanceBand (verdict+overall+why+chips, dimensions, grouped competencies) → Task 3 ✓
- Thresholds single source of truth → Task 1 ✓
- ReportView rewire + rail reduced to proctoring+decision → Task 4 ✓
- Retire ScoresCard + CompetencyRadar; keep ScoreGauge/VerdictBand → Task 4 ✓
- PublicRecordingsView inherits ReportView (no change) → covered by build + noted ✓
- Testing for each new component → Tasks 2/3/4 ✓
- Live verification at the given URL → Task 5 ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**3. Type consistency:** `bandZones()` return shape `{ rejectPct, advancePct }` used identically in Tasks 1 & 2. `ScoreBar` prop names (`score`, `label`, `variant`, `toneOverride`, `mustHave`, `notReached`, `showBands`, `caption`) consistent across Tasks 2 & 3. `GlanceBand({ report })` signature consistent across Tasks 3 & 4. `byWeightDesc` matches the field names on `SignalAssessmentOut` (`weight`, `signal`). ✓
