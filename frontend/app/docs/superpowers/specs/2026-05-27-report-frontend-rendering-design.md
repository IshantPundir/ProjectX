# Report Frontend Rendering — Design Spec (Plan B)

**Date:** 2026-05-27
**Surface:** `frontend/app` (recruiter dashboard) — `components/dashboard/reports/` + `lib/api/reports.ts`
**Depends on:** backend report-generator redesign (merged) — `backend/nexus/docs/superpowers/specs/2026-05-27-report-generator-redesign-design.md`. The backend now returns the new PDF-shaped `ReportRead`; this surface still renders the OLD shape and mis-renders against the new API.

---

## 1. Goal & scope

Render the new PDF-shaped report in the recruiter dashboard, matching the reference PDFs' **content** (decision, scores, why-contrast, summary, strengths/concerns with severity, question-by-question with status + cleaned quote + "our read", methodology) while **keeping the existing visual language** (the 2-column split, `ScoreGauge`, verdict band/chip, px design tokens). "Re-skin the content, keep the chassis."

**Non-goals:** new visual design system; session-recording player (`SessionPlaybackStub` stays a placeholder); the reports hub/index page (unchanged); candidate-session surface (untouched).

---

## 2. New contract (`lib/api/reports.ts`)

Rewrite `ReportRead` and its sub-interfaces to mirror the backend's new `schemas.py`. The polling envelope in `reportsApi.getBySession` is unchanged (`'verdict' in body` still distinguishes ready from pending).

```ts
export type Verdict = 'advance' | 'borderline' | 'reject'        // unchanged enum; relabeled in UI
export type Confidence = 'high' | 'medium' | 'low'
export type Severity = 'deal_breaker' | 'major' | 'moderate'
export type StatusBadge =
  | 'passed' | 'partial' | 'failed_required'
  | 'not_demonstrated' | 'not_attempted' | 'not_fully_assessed'
export type HumanDecisionValue = 'advance' | 'reject' | 'hold'

export interface WhyColumn { title: string; body: string }
export interface DecisionOut { headline: string; why_positive: WhyColumn; why_negative: WhyColumn }
export interface ScoreOut {
  score: number | null; tier_label: string; tone: string; confidence: Confidence; coverage: number
}
export interface StrengthOut { title: string; detail: string }
export interface ConcernOut { title: string; detail: string; severity: Severity }
export interface QuestionOut {
  seq: number; question_id: string; title: string
  status_badge: StatusBadge; status_tone: string
  question_text: string; candidate_quote: string; our_read: string
}
export interface MethodologyOut { note: string; charity_flags: string[] }
export interface SignalAssessmentOut {
  signal: string; type: string; weight: number; knockout: boolean; priority: string
  engine_state: string; final_state: string
  grade: string | null; score: number | null
  evidence: string[]; overridden: boolean; override_reason: string | null
}
export interface ScoringManifest {       // trimmed: verbosity/n_samples removed backend-side
  scorer_model: string | null; reasoning_effort: string | null; prompt_version: string | null
  evidence_grounding_summary: Record<string, unknown> | null
  generated_at: string | null; correlation_id: string | null
  // (other optional fields tolerated; extra keys ignored)
}
export interface HumanDecision { decided_by: string; decision: HumanDecisionValue; rationale: string; decided_at: string }

export interface ReportRead {
  verdict: Verdict
  verdict_reason: string
  overall_score: number | null
  overall_coverage: number
  overall_confidence: Confidence
  decision: DecisionOut
  scores: Record<string, ScoreOut>                 // keys: overall | technical | behavioral | communication
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

**Deleted interfaces/types:** `EvidenceOut`, `SignalScorecard`, `DimensionScoreOut`, `KnockoutResultOut`, `QuestionScorecard`, `SummaryOut`, `Opportunity`, `SignalState`, `KnockoutStatus`, `QuestionLevel`. `ReportIndexItem`/`ReportIndexPage`/`HumanDecisionIn` and the `reportsApi` methods stay as-is.

---

## 3. Format layer (`components/dashboard/reports/report-format.ts`)

- **Relabel** `VERDICT_META`: `advance → "Recommended" (ok)`, `borderline → "Borderline" (human)`, `reject → "Not Recommended" (danger)`. `VerdictBand`/`VerdictChip` consume this unchanged.
- **Add** `SEVERITY_META: Record<Severity, {label, tone}>` — `deal_breaker → {"Deal-breaker", danger}`, `major → {"Major", caution}`, `moderate → {"Moderate", neutral}`.
- **Add** `STATUS_BADGE_META: Record<StatusBadge, {label, tone}>` — `passed→{"Passed",ok}`, `partial→{"Partial",caution}`, `failed_required→{"Failed — required skill",danger}`, `not_demonstrated→{"Not demonstrated",danger}`, `not_attempted→{"Not attempted",neutral}`, `not_fully_assessed→{"Not fully assessed",neutral}`. (Prefer `ScoreOut.tone` / `QuestionOut.status_tone` straight from the backend when present; these maps are the label source + a fallback.)
- **Keep** `scoreToTen`, `TONE_INK/FILL/BG`, `Tone`, `confidenceLabel`, `verdictMeta`. Add `tierTone(tone: string): Tone` (pass-through/validate the backend tone string).
- **Remove** `signalStateTone`, `signalStateLabel`, `knockoutStatusTone`, `knockoutStatusLabel`, `scoreBandTone` (if only used by deleted components — verify; `ScoreGauge` uses `scoreBandTone` as a fallback, so KEEP `scoreBandTone`).

---

## 4. Components (`components/dashboard/reports/`)

**Keep unchanged:** `ScoreGauge`, `ReportTopBar`, `VerdictBand`/`VerdictChip` (labels change via format layer only), `HumanDecisionPanel`, `ReportStates`, `SessionPlaybackStub`, `ReportMethodologyFooter` (see update below).

**New:**
- `WhyContrast.tsx` — two columns from `decision.why_positive` / `decision.why_negative`; each a titled card (positive = ok tint, negative = caution/danger tint).
- `QuickSummary.tsx` — single section rendering `quick_summary` as a paragraph (`whitespace-pre-wrap`, no `dangerouslySetInnerHTML`).
- `StrengthsConcerns.tsx` — two lists: strengths (`{title, detail}`) and concerns (`{title, detail, severity}` with a severity chip via `SEVERITY_META`). Counts in the headings (e.g. "Strengths 5 / Concerns 4") to match the PDF.
- `QuestionByQuestion.tsx` — ordered list of `QuestionOut`: seq number, status badge (`STATUS_BADGE_META` / `status_tone`), question text, candidate quote (quoted block), "Our read" paragraph.
- `SignalAuditTable.tsx` — a collapsed `<details>` ("Audit detail — signal by signal") rendering `signal_assessments`: signal, type/weight, knockout flag, engine→final state, grade, override reason, evidence quotes. Collapsed by default.

**Adapt:** `AiRecommendationCard.tsx` → `ScoresCard.tsx` — reads `report.scores.{overall,technical,behavioral,communication}` (each `ScoreOut`) instead of `overall_score`/`dimension_scores`; shows `VerdictBand` + `decision.headline`; overall gauge (size 118, tone from `scores.overall.tone`) + 3 dimension gauges (size 58, captions `cov X · confidence`); coverage/confidence from `scores.overall`.

**Delete:** `SignalScorecards.tsx`, `QaEvidencePanel.tsx`, `ReportSummary.tsx`, `SignalSpiderChart.tsx`, `EvidenceQuote.tsx` (now unused — verify no other importers).

**Update `ReportMethodologyFooter.tsx`:** prepend `report.methodology.note` and render `methodology.charity_flags[]` as caveat chips, then the manifest line (scorer model, prompt version, generated date, correlation id). Drop the removed manifest fields (`verbosity`, `n_samples`).

---

## 5. `ReportView` composition

```
<ReportTopBar … verdict={report.verdict} />            // chip relabeled
<div grid 1.85fr / 1fr>
  MAIN:
    <SessionPlaybackStub />                            // unchanged placeholder
    <WhyContrast decision={report.decision} />
    <QuickSummary text={report.quick_summary} />
    <StrengthsConcerns strengths={…} concerns={…} />
    <QuestionByQuestion questions={report.questions} />
    <SignalAuditTable assessments={report.signal_assessments} />   // collapsed
  SIDE:
    <ScoresCard report={report} />
    <HumanDecisionPanel verdict decision onSubmit isSubmitting />
</div>
<ReportMethodologyFooter methodology={report.methodology} manifest={report.scoring_manifest} />
```

`ReportView`'s props are unchanged; the page (`app/(dashboard)/reports/session/[sessionId]/page.tsx`) and the `useReport` hook are untouched.

---

## 6. Testing

Follow the established composition-test convention (`tests/components/reports/`): parent+child rendered together, mock at the API boundary, negative-control by reintroducing a bug.

- `ReportView.test.tsx` — rewrite the fixture to the new shape; assert every section renders (verdict label "Borderline"/"Not Recommended", a why-column title, the summary text, a concern's severity chip, a question's status badge + "Our read", the collapsed audit `<details>`). Negative control: a fixture missing `decision` should not crash (guarded by safe rendering).
- `report-format.test.ts` — verdict relabel, `SEVERITY_META`, `STATUS_BADGE_META`, `scoreToTen`.
- Component units for `StrengthsConcerns` (severity chip), `QuestionByQuestion` (badge mapping), `ScoresCard` (4 gauges + headline).
- Delete tests for removed components (`SignalScorecards.test.tsx`, `QaEvidencePanel.test.tsx`, `SignalSpiderChart.test.tsx`, `EvidenceQuote.test.tsx`).
- Gate: `npm run build`, `npm run type-check`, `npm run lint`, `npm run test` all green (the TS rewrite is the main risk surface).

---

## 7. File map

**Create:** `WhyContrast.tsx`, `QuickSummary.tsx`, `StrengthsConcerns.tsx`, `QuestionByQuestion.tsx`, `SignalAuditTable.tsx`, `ScoresCard.tsx` + their tests.
**Modify:** `lib/api/reports.ts`, `report-format.ts`, `ReportView.tsx`, `ReportMethodologyFooter.tsx`, `tests/components/reports/ReportView.test.tsx`, `tests/components/reports/report-format.test.ts`.
**Delete:** `SignalScorecards.tsx`, `QaEvidencePanel.tsx`, `ReportSummary.tsx`, `SignalSpiderChart.tsx`, `EvidenceQuote.tsx`, `AiRecommendationCard.tsx` (renamed to `ScoresCard.tsx`) + their tests.

---

## 8. Open / tunable

- `ScoreOut.tone` / `QuestionOut.status_tone` come straight from the backend; the UI prefers them and uses the format-layer maps as label source + fallback. If a tone string is unrecognized, fall back to `neutral`.
- The reports hub (`app/(dashboard)/reports/page.tsx`) reads only `verdict` + `overall_score` from `ReportIndexItem` (unchanged) — no change needed, but the verdict label there should also read "Recommended/Borderline/Not Recommended" via `verdictMeta` (verify it uses the format layer).
