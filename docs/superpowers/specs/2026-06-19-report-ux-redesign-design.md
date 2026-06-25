# Report UX Redesign — Design

**Date:** 2026-06-19
**Status:** Approved (brainstorm) → ready for implementation plan
**Surfaces:** `backend/nexus/app/modules/reporting/` (schema + scoring + PDF) · `frontend/app/components/dashboard/reports/` (web report) · public recordings share envelope
**Scope decision:** ONE spec, **web-first then PDF**. The data-model + scoring changes land first; the web report becomes the canonical design; the PDF mirrors the same visual language.

---

## 1. Problem

The post-session report is the core product deliverable, but today everything except the four dimension scores is **prose**. A recruiter reviewing 100s of reports/day cannot extract a candidate's shape in 10–15 seconds — they must read walls of text. Specific failures:

- No at-a-glance visualization of per-competency strength.
- Per-question grading is a coarse 4-level badge (strong/solid/thin/absent), not a number.
- The PDF truncates question text (renders a clipped `title`, not the full question).
- The PDF header is thin: small photo, role only, no email / session time / skills.
- Scores are on a 0–100 scale (`81`, `83`) — humans read 0–10 (`8.1`) faster.

**Goal:** a report a recruiter *gets* in 10–15 seconds — exact numbers rendered as beautiful, legible charts (competency radar, score rings, star-rated questions, pills) — with prose demoted to on-demand detail kept for audit. We are setting the UI/UX bar for the product here.

This is NOT a re-grade of how scoring works (the verdict pipeline is unchanged); it is a **presentation + data-shape** redesign plus two additive scoring outputs (per-signal number, per-question 0–10).

---

## 2. Decisions (locked in brainstorm)

| Area | Decision |
|---|---|
| Score scale | **0–10, one decimal** everywhere recruiter-facing (Overall/Technical/Behavioral/Communication + per-signal radar). |
| Scale implementation | Internal scoring math + DB stay **0–100** (calibrated, auditable core untouched). Convert to 0–10 at the **read-model boundary** via one helper. |
| Centerpiece | **Competency radar** (per-signal 0–10) + 3 equal score **rings** + 1-line lede + a full-width **Top-strengths / Watch-outs pills** strip. |
| Per-signal score | **Deterministic** from existing `LEVEL_POINTS ÷ 10` (strong 10 / solid 8 / thin 4 / absent 1; `not_reached` excluded from radar). |
| Per-question score | **LLM-emitted 0–10**, rubric-anchored; rendered as **5 stars at half-precision** (`score/2`) + "X.X / 5". |
| Header | Immersive (deep gradient + glow + dot-grid), **164px** photo, name + email, role + stage, session date/time + duration, **skill pills**, verdict stamp. |
| Header CTAs | **Two** buttons: **Candidate Reel** (glowing gradient — the USP, primary) + **Full session** (glass, secondary). Reel button hidden on `reject` verdicts (Reel only exists for advance/borderline). |
| Verdict placement | ONLY in the header stamp — never duplicated in the body/band. |
| Page flow | **Band + sticky right rail** (Layout II): at-a-glance band full-width, then detail left / sticky rail right (score detail, proctoring, human decision, playback). |

---

## 3. Backend — data model & scoring

### 3.1 Score scale: 0–100 internal, 0–10 at the boundary

The scoring engine (`reporting/scoring/aggregate.py`, `constants.py`) is **unchanged**: dimension/overall scores stay integer 0–100; thresholds (`ADVANCE_THRESHOLD=65`, `REJECT_THRESHOLD=40`), ceilings (`REJECT_CEILING=35`, `BORDERLINE_CEILING=60`), `HOLISTIC_ADJ_MAX=5`, `LEVEL_POINTS`, and tier bands stay 0–100. This is deliberate: that logic is under "candidate scoring/classification thresholds — human review required," and floats at the 65/40 boundaries would risk verdict drift. The 0–10 scale is a **display unit**, applied once.

**DB stays 0–100.** `session_reports.overall_score` and `dimension_scores` JSONB persist 0–100 (no migration of stored scores; reports regenerate anyway).

**Conversion at the read-model.** Add a single helper:

```python
# reporting/scoring/scale.py  (new)
def to_ten(score_100: int | float | None) -> float | None:
    """0–100 integer score → 0–10 one-decimal display value. None passes through."""
    return None if score_100 is None else round(score_100 / 10, 1)
```

Applied in exactly two read paths:
- `serialization.report_read_from_row` → every `ScoreOut.score`, `ScoreOut.session_score`, `ScoreOut.holistic_delta`, `ReportRead.overall_score`, and each `SignalAssessmentOut.score`.
- the reports-hub index builder → `ReportIndexItem.overall_score`.

`HOLISTIC_ADJ_MAX=5` (0–100) becomes `±0.5` once divided — the comment in `constants.py` already notes this; no logic change.

**Schema type change.** `ScoreOut.score`, `ScoreOut.session_score`, `ScoreOut.holistic_delta`, `ReportRead.overall_score`, `ReportIndexItem.overall_score`, `SignalAssessmentOut.score` change from `int | None` → `float | None`. `tier_label`/`tone`/`confidence` are unchanged (tier is computed internally on 0–100).

### 3.2 Per-signal 0–10 score (radar axis)

`SignalAssessmentOut` already has `level` + `score` (currently `int | None`, often unset). Make `score` authoritative and 0–10:

- Source = `level_score(level)` (existing `LEVEL_POINTS`) `÷ 10`: **strong 10.0 · solid 8.0 · thin 4.0 · absent 1.0 · not_reached 1.0**.
- The radar consumes **assessed primary signals** only: `provenance != "not_reached"`. `not_reached` signals are excluded from the radar polygon (optionally listed faded below). `absent` (asked-and-failed) IS plotted, at 1.0 — that's a real, low data point.
- Radar axis cap: **8** signals, sorted by `weight` desc then name; if a role has >8 primary signals, the lowest-weight extras fall to the signal-audit table only (and we `log()`/note the cap, never silently drop).

No new LLM call — this is a pure mapping over data the scorer already produces.

### 3.3 Per-question 0–10 score (stars)

Extend the per-question grade (`scoring/question_grade.py`, `QuestionGradeOut`):

- Add `score: int` to `QuestionGradeOut` — an **LLM-emitted 0–10**, rubric-anchored. Anchors (in the prompt): `10` = fully meets the rubric / all listen-fors hit; `7–8` = solid, minor gaps; `4–6` = partial/thin; `2–3` = weak attempt; `1` = attempted but failed / red-flag; `0` = not demonstrated. `not_reached` (never asked / truncated) → no score (stars hidden).
- The LLM already grades each question against its full bank card here; emitting a calibrated 0–10 alongside `level` is a small prompt addition. On refusal/fallback, derive from `base_level` via the same `LEVEL_POINTS ÷ 10` mapping so a value always exists.
- Update `prompts/v4/report_scorer/question_grade` to (a) require the 0–10 `score`, (b) state the anchors. Bump `report_scorer_prompt_version`.
- `QuestionOut` gains `score: int | None` (0–10) and keeps `level` + `status_badge`. Frontend renders stars = `score/2` (0–5, half-step) and "X.X / 5". `not_reached` → no stars + "Not assessed" chip.

### 3.4 Header metadata block (server-sourced)

Today the web report receives `candidateName`/`title`/`subtitle` via **query string** — fragile and absent from the PDF. Add a server-sourced header block so web + PDF + public-share all share one source.

Add `ReportHeader` to `schemas.py` and `header: ReportHeader` to `ReportRead`:

```python
class ReportHeader(BaseModel):
    candidate_name: str
    candidate_email: str | None = None
    job_title: str
    stage_label: str
    session_started_at: str | None = None   # ISO; from sessions.agent_started_at
    duration_seconds: int | None = None      # recording_duration_seconds or agent start→complete
    skills: list[str] = Field(default_factory=list)  # demonstrated (level ∈ {solid,strong}), top-by-weight
    reference_photo_url: str | None = None    # presigned R2 GET, attached at read time (already exists on ReportRead)
```

Sources (all already reachable from the session row the report is built for): candidate name/email via `candidate_id`; `job_title` via the job; `stage_label` via the stage; `session_started_at`/`duration_seconds` via the session (`agent_started_at`, `agent_completed_at`, `recording_duration_seconds`); `skills` derived from `signal_assessments` (level ∈ {solid, strong}, sorted by weight, cap ~6). `reference_photo_url` presigning is unchanged (moves under `header`, or stays top-level and is duplicated — implementer's choice; prefer under `header`).

**Population:** the header is assembled where the report read-model is built. Candidate name/email + job/stage are *not* on the `session_reports` row today, so the read path must join the session→candidate/job/stage (a bounded extra query in the report GET handler and in the PDF/public-share builders). Persisting a denormalized header snapshot onto `session_reports` is an option for a later pass; for now assemble at read.

PII note: `candidate_email` travels in an authenticated recruiter response and the recruiter-curated PDF — consistent with today's report (which already shows candidate identity). It is NOT logged (logging discipline unchanged: `candidate_id` only).

### 3.5 What does NOT change

- The verdict pipeline, fit ceilings, holistic delta, `resolve_verdict`, Borderline-always-human invariant.
- `SessionEvidence` ingestion, the 3-layer hybrid, narrative generation.
- `session_reports` table shape (no migration). Scores persist 0–100.

---

## 4. Frontend — web report (`frontend/app`)

Layout II (band + sticky rail). New + restyled components under `components/dashboard/reports/`.

### 4.1 New components
- **`ImmersiveHeader`** — deep gradient + radial glows + faint dot-grid; 164px photo (or monogram); name + email; role · stage · date · duration inline row; **skill pills**; verdict stamp; two CTAs — `ReelButton` (glowing gradient, the USP; hidden when `verdict==='reject'` or no reel) + `FullSessionButton` (glass). Both open the existing `ReviewTheater` / `ReelTheater`.
- **`AtAGlanceBand`** — full-width: left `CompetencyRadar`; right 3 equal-size, evenly-spaced `ScoreRing`s (Overall/Technical/Comms — Behavioral folds into the radar + audit) + 1-line lede; below, a full-width pills strip: **Top strengths** (green) / **Watch-outs** (amber). No verdict here.
- **`CompetencyRadar`** — SVG radar over assessed primary signals (3–8 axes), 0–10. Hand-rolled SVG (matches the in-house `px/` no-charting-lib convention; consistent with `OrgGraphCanvas`). Graceful with 3 axes (still a triangle); if <3 assessed signals, fall back to the ranked `ScoreMeter` bars instead of a degenerate radar.
- **`StarRating`** — 0–5 with half-star precision from a 0–10 input (`value/2`); gold gradient fill; accessible label "X.X out of 5".
- **`ScoreRing`** — reused ring (0–10, one decimal, tone-colored).

### 4.2 Restyled / kept
- **Question-by-question** → hero-star card (`QuestionByQuestion`): seq dot, **full question text** (no clamp), `StarRating` + "X.X/5" top-right, difficulty + status chips, candidate quote, "our read", listen-for hits (green) / red-flags (red).
- **Why-this-verdict**, **Strengths & Concerns**, **Signal audit** — restyled to the new token language; signal audit now shows the per-signal 0–10 + a mini bar.
- **Sticky right rail**: `ScoresCard` (detail), `ProctoringIntegrityPanel`, `HumanDecisionPanel`, `PlaybackPanel`.
- Remove the `scoreToTen` divide in `report-format.ts` (values arrive 0–10); keep tone/tier helpers.

### 4.3 Data wiring
- `lib/api/reports.ts` types updated: `score` fields `number` (0–10), `QuestionOut.score`, `SignalAssessmentOut.score`, new `header`.
- Report page reads identity from `report.header` (stop threading `candidateName`/`title` via query string; keep query params as optional fallback for old links).

---

## 5. PDF (`reporting/pdf/`) — mirrors the web

Update `templates/report.html.j2` + `context.py` to the new language (print-safe SVG works in Chromium):

- **Immersive header**: taller, larger photo, name **+ email**, role · stage · **date · duration**, **skill pills**, verdict stamp. (Reel is interactive-only — the PDF keeps the existing "See full session recording" link; optionally a "Watch the candidate reel" link to the same share page.)
- **At-a-glance band**: radar (SVG) + 3 rings (0–10) + Top-strengths/Watch-out pills.
- **Question cards**: render **full `question_text`** (fixes the truncation — today it prints the clipped `title`), with a **star row** (SVG, half-precision) + "X.X/5".
- All gauges/rings show **0–10**.
- `context.py`: consume `report.header`, `SignalAssessmentOut.score` (radar), `QuestionOut.score` (stars); `_bar_color` thresholds rescale to 0–10 (≥8 green / ≥6 amber / else red).

The PDF stays a recruiter-curated subset (no internal forensics), per the existing share design.

---

## 6. Testing

**Backend**
- `to_ten` conversion (incl. None, rounding e.g. 81→8.1, 65→6.5).
- Per-signal mapping: each level → expected 0–10; `not_reached` excluded from the radar set; >8 signals → cap + remainder to audit.
- `QuestionGradeOut.score` present + range-validated; refusal fallback derives from `base_level`.
- Header assembly: fields populated from session/candidate/job/stage; `skills` = solid/strong by weight; email never logged.
- Verdict outputs **unchanged** vs current fixtures (scale change is display-only) — regression test asserting identical verdicts before/after.
- PDF context: full question text present; radar/star inputs shaped correctly.

**Frontend**
- `CompetencyRadar` (3 / 6 / 8 axes; <3 → bar fallback), `StarRating` (half-star from 0–10), `ScoreRing` (0–10).
- `ImmersiveHeader` renders email/skills/CTAs; Reel button hidden on reject / when no reel.
- `AtAGlanceBand` shows no verdict; pills derived correctly.
- Composition test: report page renders from a `header`-bearing payload (mock at API boundary), negative-control by removing `header`.

---

## 7. Out of scope / follow-ups

- No change to the verdict/scoring logic or `SessionEvidence`.
- No `session_reports` schema migration (scores persist 0–100; header assembled at read — denormalized snapshot is a later option).
- Reports hub table redesign beyond the 0–10 score column.
- Half-star precision requires the LLM to populate 0–10 reliably; if it proves noisy in live tests, fall back to deriving the 0–10 from `level` (whole/√ mapping) — a prompt/derivation switch, not a schema change.

---

## 8. Files touched (anticipated)

**Backend** — `reporting/schemas.py` (ScoreOut/QuestionOut/SignalAssessmentOut float, new `ReportHeader`, `QuestionGradeOut.score`), `reporting/serialization.py` (0–10 conversion + header assembly), `reporting/scoring/scale.py` (new), `reporting/scoring/question_grade.py` (+score), `reporting/scoring/rollup.py` or signal-scorecard builder (per-signal 0–10), `reporting/router.py` (hub index conversion + header join), `reporting/public_share.py` (header on envelope), `reporting/pdf/context.py` + `templates/report.html.j2`, `prompts/v4/report_scorer/question_grade*`, `ai/config.py` (prompt version bump).

**Frontend** — `components/dashboard/reports/`: new `ImmersiveHeader`, `AtAGlanceBand`, `CompetencyRadar`, `StarRating`, (reuse `ScoreRing`); restyle `ReportView`, `QuestionByQuestion`, `ScoresCard`, `SignalAuditTable`, `StrengthsConcerns`, `WhyContrast`, `report.css`, `report-format.ts`; `lib/api/reports.ts` types; `app/(dashboard)/reports/session/[sessionId]/page.tsx` (header from payload).
