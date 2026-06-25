# Report "Glance Band" Redesign — Design

**Date:** 2026-06-25
**Surface:** `frontend/app` — recruiter dashboard, report viewer
**Scope:** Frontend-only. No backend, schema, or API changes.
**Status:** Approved (design), pending implementation plan.

---

## Problem

A recruiter reviews hundreds of candidate interview reports per day. The current
report viewer is detailed and verbose — excellent for audit and evidence, but it
forces the recruiter to *read* to understand a candidate. The scoring visuals
(overall gauge, three dimension gauges, a competency **radar/spider** chart) are
crammed into a narrow right-hand sticky rail, below the fold of attention.

**Goal:** a recruiter should grasp the full picture of a candidate within
**10–15 seconds of opening the report**, with no reading required — by surfacing
beautiful, glanceable charts at the very top. Depth stays one scroll away.

The radar/spider chart is also a known weak choice for this job: it optimizes for
*shape recognition across entities*, not *precise magnitude reading of one
candidate*. Design-intelligence guidance (ui-ux-pro-max chart domain) grades
radar "B" for accessibility and explicitly recommends **grouped/horizontal bars**
when "values need precise comparison" — which is exactly the recruiter's task.

---

## Solution overview

1. Introduce one new atomic viz primitive — a **threshold-banded horizontal bar**
   (`ScoreBar`) — that renders a score against the product's own verdict
   thresholds, so "did this candidate clear the hiring bar?" is readable at a
   glance, not just the number.
2. Lift **all** scoring visuals out of the right rail into a new full-width
   **`GlanceBand`** section directly under the hero header. It shows the verdict,
   overall score, all three dimensions, and every competency signal — built
   entirely from `ScoreBar`.
3. Reduce the right sticky rail to **Proctoring + Decision only**.
4. Retire the radar chart and the rail scores card.

No backend changes — this is a pure presentation pass over the existing
`ReportRead` payload.

---

## Decisions locked during brainstorming

- **Bar style:** threshold-banded bars — faint reject / borderline / advance
  zones behind each bar plus a hiring-bar marker. (Chosen over plain value bars
  and over bars-with-target-ticks.)
- **Top-band scope:** verdict + overall + all dimension bars + all competency
  bars + a one-line "why". The recruiter can read nothing else and still get the
  full picture.
- **Competency density:** group into **Must-have competencies** (knockout signals,
  full bars) vs **Other competencies** (tighter compact bars). Mirrors how the
  verdict is actually computed.

---

## Thresholds — single source of truth

The verdict band thresholds already exist in `report-format.ts::scoreBandTone`:

- `score >= 6.5` → advance (ok / green)
- `4.0 <= score < 6.5` → borderline (caution / amber)
- `score < 4.0` → reject (danger / red)

These constants (`REJECT_BAND = 4.0`, `ADVANCE_BAND = 6.5`, on the 0–10 scale)
will be **exported from `report-format.ts`** along with a small `bandZones()`
helper, so both `scoreBandTone` and the new `ScoreBar` consume one definition.
The hiring-bar marker sits at `ADVANCE_BAND` (6.5).

---

## Component 1 — `ScoreBar` (new atomic primitive)

`components/dashboard/reports/ScoreBar.tsx`

Replaces `ScoreGauge` **in the report body** and the radar chart. Pure SVG/CSS,
token-driven (no raw hex; uses `--px-*` tokens via `TONE_*` maps in
`report-format.ts`).

**Props**

| Prop | Type | Meaning |
|---|---|---|
| `score` | `number \| null` | 0–10; `null` → "not assessed" |
| `label` | `string` | competency / dimension name |
| `variant` | `'hero' \| 'row' \| 'compact'` | size + density (see below) |
| `toneOverride?` | `Tone` | e.g. color Overall by verdict |
| `mustHave?` | `boolean` | render a ★ must-have marker |
| `notReached?` | `boolean` | muted/dashed track + "not reached" |
| `showBands?` | `boolean` | render the reject/borderline/advance zones (default true) |
| `caption?` | `string` | secondary line (e.g. coverage/confidence), optional |

**Variants**

- `hero` — tall bar for Overall; full bands; prominent hiring-bar marker; large
  value; verdict label adjacent (composed by `GlanceBand`, not inside the bar).
- `row` — standard full-width row for dimensions and must-have competencies; band
  zones + hiring-bar tick + value + pass/warn glyph.
- `compact` — single-row, thin hiring-bar tick (not full bands) + inline value;
  used for "other" competencies in a 2-up grid.

**Visual anatomy (row variant)**

```
★ Attention to detail  ███████▌                4.9 ⚠
                       ░░░░░░░▒▒▒▒▒▒▒▒░░░░░░░░░░
                              ▲ 6.5 hiring bar
   (fill tone = tier; faint zones = reject/borderline/advance)
```

- **Track zones:** reject `0–4.0`, borderline `4.0–6.5`, advance `6.5–10`
  rendered as faint background segments (low-contrast, per `gridline-subtle`).
- **Fill:** width = `score/10`, color = `toneOverride ?? scoreBandTone(score)`.
- **Hiring-bar marker:** a tick/triangle at the 6.5 position.
- **Value + glyph:** `4.9` plus ✓ (cleared bar) / ⚠ (below bar). Glyph is
  redundant with color (never color-only — satisfies `color-not-only`).
- **`null` / not-assessed:** dashed muted track, no fill, "not assessed" text.

**Accessibility**

- `role="img"` with `aria-label` like
  `"Attention to detail score 4.9 out of 10, below hiring bar"`.
- All values rendered as visible text (not hover-only).
- Tier conveyed by glyph + value, not color alone.

---

## Component 2 — `GlanceBand` (new top section)

`components/dashboard/reports/GlanceBand.tsx`

Full-width card under the hero. Consumes `ReportRead`. Three tiers:

**Tier A — Verdict + Overall**
- Verdict label via `verdictMeta(report.verdict)` (tone + "Recommended /
  Borderline / Not Recommended").
- `ScoreBar variant="hero"` for `scores.overall` with `toneOverride` = verdict
  tone and the hiring-bar marker.
- One-line **`decision.headline`** beside/under the overall.
- Coverage + Confidence as two small chips (`overall.coverage`,
  `confidenceLabel(overall.confidence)`). If present, the existing session-score /
  holistic-delta footnote is preserved as a small caption.

**Tier B — Dimensions**
- `Technical / Behavioral / Communication` as three `ScoreBar variant="row"`,
  stacked one below another. Only assessed dimensions render (same filter as the
  current `DIMS` logic: `report.scores[key]?.score != null`).

**Tier C — Competencies** (from `report.signal_assessments`)
- **Must-have competencies:** `a.knockout === true`, sorted by `weight` desc →
  `ScoreBar variant="row"` with `mustHave`. `provenance === 'not_reached'` →
  `notReached` muted state.
- **Other competencies:** the rest, sorted by `weight` desc → `ScoreBar
  variant="compact"` in a 2-up grid (1-up on narrow).
- Section headers: "Must-have competencies" / "Other competencies". Hide a group
  if it has no members. If there are zero signal assessments, the whole Tier C is
  omitted.

**Responsive (desktop-first, 1280px target)**
- At `xl`: the three tiers lay out as a row of regions
  (Verdict+Overall | Dimensions | Competencies) to keep total height short
  (target ≤ ~one-third viewport so the two-column body peeks above the fold).
- Below `xl`: tiers stack vertically. Dimension and must-have bars are always
  full-width rows; the "other" 2-up grid collapses to 1-up.

**Motion**
- Bars reveal with a staggered fill animation using the existing `.px-reveal`
  stagger pattern plus a CSS width/opacity keyframe. Respects
  `prefers-reduced-motion` — values are readable immediately, animation is the
  enhancement, not the content.

---

## Layout change — `ReportView.tsx`

```
ImmersiveHeader            (full width — unchanged)
GlanceBand                 (full width — NEW)
┌─ grid xl:[1.7fr_1fr] ────────────────────────┐
│ LEFT                  │ RIGHT rail (sticky)   │
│  QuickSummary         │   ProctoringPanel     │
│  WhyContrast          │   HumanDecisionPanel  │
│  StrengthsConcerns    │                       │
│  QuestionByQuestion   │                       │
│  SignalAuditTable     │                       │
└──────────────────────────────────────────────┘
ReportMethodologyFooter
```

- Insert `<GlanceBand report={report} />` immediately after the header block,
  before the two-column grid (own `.px-reveal` stagger slot).
- Remove `ScoresCard` from the rail. Rail now renders only
  `ProctoringIntegrityPanel` + `HumanDecisionPanel`.
- The `.px-reveal` stagger indices for the rail shift down by one; re-number so
  the cascade stays smooth.

---

## File changes

**New**
- `components/dashboard/reports/ScoreBar.tsx`
- `components/dashboard/reports/GlanceBand.tsx`
- CSS additions in `components/dashboard/reports/report.css` (band zones,
  hiring-bar marker, bar reveal keyframe).

**Modified**
- `components/dashboard/reports/ReportView.tsx` — insert GlanceBand, strip
  ScoresCard from rail, re-number reveal staggers.
- `components/dashboard/reports/report-format.ts` — export `REJECT_BAND`,
  `ADVANCE_BAND`, and a `bandZones()` helper; refactor `scoreBandTone` to use
  them.

**Retired (delete component + its test)**
- `components/dashboard/reports/ScoresCard.tsx` + `tests/.../ScoresCard.test.tsx`
- `components/dashboard/reports/CompetencyRadar.tsx` +
  `tests/.../CompetencyRadar.test.tsx`

**Kept (do not touch)**
- `ScoreGauge.tsx` — still used by `theater/ScoreRail.tsx`. Only its body usage
  goes away. `ScoreGauge.test.tsx` stays.
- `VerdictBand.tsx` — still used by `ReportTopBar.tsx` and `reports/page.tsx`.
- `PublicRecordingsView.tsx` — renders `ReportView`, so it inherits the redesign
  automatically. Sanity-check only; no redesign.

---

## Testing

Vitest + Testing Library, composition convention (parent+child, mock at the API
boundary; verify negative-control by reintroducing the bug).

- **`ScoreBar.test.tsx`** (new): fill width math; band zones rendered; hiring-bar
  marker at the 6.5 position; `null` / not-assessed state; not-reached muted
  state; must-have marker; tier tone from score; `toneOverride`; a11y label text;
  values present as text.
- **`GlanceBand.test.tsx`** (new): renders verdict + overall + 3 dimensions;
  filters unassessed dimensions; groups knockout vs non-knockout signals;
  sorts by weight; muted not-reached; omits empty groups; omits Tier C when no
  signal assessments; renders coverage/confidence chips.
- **`ReportView.test.tsx`** (update): GlanceBand present; ScoresCard **absent**
  from the rail; Proctoring + Decision still present in the rail.
- Delete `ScoresCard.test.tsx` and `CompetencyRadar.test.tsx`.

---

## Out of scope (YAGNI)

- No backend / schema / API changes.
- No dark mode (per frontend rules — not in MVP scope).
- Theater playback untouched (`ScoreGauge` / `ScoreRail` unchanged).
- No redesign of `PublicRecordingsView` beyond inheriting `ReportView`.
- No new chart library — hand-rolled SVG/CSS, consistent with the in-house
  `px/` primitive philosophy.

---

## Risks / notes

- **Band-height budget:** with many must-have signals the full bars could grow
  tall. Sorting by weight + the compact "other" group + the row-of-regions `xl`
  layout keep it bounded; if a report has an unusually large must-have set the
  band scrolls with the page (acceptable — must-haves decide the verdict and
  should not be hidden).
- **Threshold drift:** thresholds are now exported from one place; if backend
  verdict thresholds ever change, update `REJECT_BAND` / `ADVANCE_BAND` only.
- **`bundle budget`** (< 250 KB gz/route): no new deps, hand-rolled SVG — net
  neutral or smaller (radar geometry code removed).
