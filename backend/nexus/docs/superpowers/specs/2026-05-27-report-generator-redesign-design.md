# Report Generator Redesign — Design Spec

**Date:** 2026-05-27
**Module:** `app/modules/reporting/` (backend) + `frontend/app/components/dashboard/reports/` (frontend)
**Status:** Approved design — ready for implementation plan
**Reference job:** `ce6dad9a-8903-4396-8f29-8e36da9bd2a3` (Jr. Forward Deployed Engineer)
**Reference sessions:** `c7173674…b801b`, `bc7ba6d3…aa4a`
**Reference output (the target quality bar):** `tmp/Ishant_Interview_Report_2.pdf`, `tmp/Ishant_Interview_Report_3.pdf`

---

## 1. Why this work exists

Two distinct problems are conflated in the current reports.

### Problem 1 — Reports are *incomplete* (a plumbing bug, not a scoring bug)

Both stored reports show `technical=null, behavioral=null, overall=0, verdict=borderline ("couldn't confirm must-have")`. Only the Communication score survives.

Root cause, confirmed end-to-end:

- The engine writes its audit envelope to `/tmp/engine-events/<id>.json` and **stores that absolute path** in `sessions.raw_result_json.audit_envelope_ref`.
- `docker-compose.yml`: the **engine** service mounts `./engine-events:/tmp/engine-events`; the **worker** service (which runs the report actor) mounts only `.:/app`. Inside the worker, `/tmp/engine-events/` **does not exist**.
- `reporting/actors.py` therefore hits the `envelope_unreadable` branch, silently falls back to `envelope = {"events": []}`, `transcript.segment()` returns `[]`, no answers are graded, every JD signal stays `not_assessed` → no Overall/Technical/Behavioral. Communication (computed straight off `sessions.transcript`) is the only survivor.

This blocks every other improvement and must be fixed first.

### Problem 2 — The scoring architecture is redundant, fragile, and contradicts our own design

The engine already produces, and we throw away, exactly the data needed to build the reports we want:

- Each `turn.decision` carries `grade` (`concrete|thin|null`), `attributed_signals`, `coverage_delta`, full `reasoning`, `candidate_quote`, `active_question_id`, `move`.
- The session row persists a complete **`coverage_summary`**: every signal → `sufficient|partial|failed|none`. Verified clean for both reference sessions.

The current `reporting/service.py` ignores all of this and **re-grades every answer from scratch** with a fresh BARS judge that sees only an isolated `question + single answer` excerpt — *less* context than the engine had live (it never sees the probe thread). This produces a second, weaker opinion that can silently contradict the engine — an auditability hazard — at extra LLM cost, and it contradicts the product architecture: *the engine collects signals; the report scores them.* The `coverage_summary` **is** the collected signal; the report should aggregate it, not re-derive it.

Separately, the report **renders** a raw data dump ("Knockouts & signals — evidence inline" = `SignalScorecards`) instead of the scannable, narrative, auditable structure recruiters need when scanning hundreds of reports a day.

---

## 2. Goals / non-goals

**Goals**
- Reports are **complete** (every dimension + Overall populated) and **accurate**.
- Scores are a **deterministic, reproducible function** of a signal-state map — same logs → same number — so a rejection is auditable and defensible.
- Report **content** matches the reference PDFs (decision, scores+tiers, why-contrast, summary, strengths/concerns with severity, question-by-question with status + cleaned quote + "our read", methodology footer).
- Report **visual language** is unchanged (existing split layout, score gauge, verdict band, methodology footer).
- The "Knockouts & signals — evidence inline" dump is removed from the headline view; signal-level detail survives as an optional audit drawer.

**Non-goals (this arc)**
- Delivery/communication scoring from the audio recording (still transcript-content-only; remains its own dimension).
- PDF export pipeline (the on-screen report is the deliverable; PDF is a later concern).
- Rebranding/visual redesign of report components.
- Changing the interview engine. We only *consume* its outputs.

---

## 3. Architecture — three strictly-separated layers

```
INPUTS                          SPINE (deterministic)        REFINE (LLM)              NARRATIVE (LLM)          OUTPUT
engine coverage_summary  ─┐
per-turn grade+reasoning ─┼─►  engine state per signal  ─►  re-check EVERY        ─►  prose only:           ─►  new schema (reuse
audit envelope (turns)   ─┤     {sufficient|partial|         REACHED signal w/        headline, why,             session_reports
transcript               ─┤      failed|none}                full thread context;     summary, per-Q read,       JSONB columns)
question bank + rubric   ─┤                                  records overrides        strengths/concerns,        → PDF-shaped
signal snapshot          ─┘            │                            │                  severity, charity flags    render
                                       ▼                            ▼                  (NEVER invents a number)
                              pure math: state→points, dimensions, knockout gate, verdict, tiers, coverage/confidence
```

**Invariant:** the numbers come *only* from the deterministic layer. The LLM (a) re-validates each signal's *state* with evidence, and (b) writes prose that is *handed the final numbers as fixed ground truth*. It cannot pick or contradict a score.

---

## 4. Layer 1 — deterministic scoring core (`scoring/aggregate.py`, pure, exhaustively unit-tested)

### 4.1 Signal state vocabulary (scoring)

The engine emits `sufficient | partial | failed | none`. The hybrid re-check (Layer 2) may additionally promote a signal to **`exceeded`** when the candidate clearly went beyond the bar, and may correct any state. Final scoring states:

| State | Meaning | Points |
|---|---|---|
| `exceeded` | clearly beyond the bar (LLM re-check only) | 100 |
| `sufficient` | met the bar | 70 |
| `partial` | engaged but did not reach the bar | 30 |
| `failed` | assessed and below the bar / disclaimed | 0 |
| `none` | never reached | **excluded from denominator** (coverage gap, never a 0) |

`none` signals lower **coverage** and **confidence**; they never count as a 0.

Rationale for the headroom: an all-`sufficient` candidate scores 70 ("met every must-have"). To be *auto-Recommended* we want demonstrated strength somewhere, which the `exceeded` state provides. Thresholds (§4.4) are calibrated so all-`sufficient` lands as a confident pass.

### 4.2 Points → calibration

Calibrated against the reference PDFs. Session 1 technical mix (2 `sufficient`, 6 `partial/thin`):

```
(3·70 + 3·70 + 3·30 + 3·30 + 3·30 + 2·30 + 2·30 + 2·30) / (3+3+3+3+3+2+2+2)
= (420 + 450) / 21 = 41.4 → 4.1 / 10        (PDF showed Technical 4.2 ✓)
```

All points/thresholds live in `scoring/constants.py` as named constants and are explicitly marked **calibration-tunable**; the reference sessions are the calibration fixtures.

### 4.3 Dimensions & Overall

- **Dimension score** = weighted mean of `state→points` over signals whose `type` is in the dimension's type set (`technical = competency|experience|credential`, `behavioral = behavioral`, `communication` = transcript-content judge, unchanged).
- **Overall** = weighted mean across **technical + behavioral** signals only. **Communication is a separate dimension, not folded into Overall** (matches current code and the PDFs' numbers).
- **Coverage** (per dimension and overall) = assessed-weight / total-weight. **Confidence** = `high ≥0.75 · medium ≥0.4 · low` (unchanged helper).
- **Tiers** (0–10 band → label, tunable): `≥7.0 Strong · 5.5–6.9 Meets Bar · 4.0–5.4 Below Bar · <4.0 Well Below Bar`.

### 4.4 Verdict decision tree (deterministic, ordered)

1. Engine fired **`knockout_close`** (terminal CLOSE on a must-have gap) **or** any `knockout=true` signal is `failed` → **Not Recommended** (deal-breaker; `verdict_reason` names the triggering signal).
2. Any `knockout=true` signal is `none` (never confirmed) **or `partial`** (engaged but depth not established) → **Borderline** ("couldn't confirm must-have: …").
3. Overall ≥ `ADVANCE_THRESHOLD` **and** overall coverage ≥ `MIN_COVERAGE_FOR_ADVANCE` → **Recommended**.
4. Overall < `REJECT_THRESHOLD` → **Not Recommended**.
5. otherwise → **Borderline**.

> **Knockout state → verdict (calibration decision, 2026-05-27, implementation review):** a must-have signal maps `failed → Reject` (genuine absence/disclaim only — see the re-check `failed` definition below), but **`partial → Borderline`** and `none → Borderline` ("couldn't confirm → human review"). `knockout_status` therefore returns `failed` only for the `failed` state; `partial` and `none` both return `insufficient`. This honors the "Borderline always needs a human" invariant — a partially-demonstrated must-have is not auto-rejected. (Supersedes the earlier draft where `partial` on a knockout counted as a hard fail.)

- **Honoring `knockout_close` is a key fix.** Session 2 ("I've never built custom connectors" → engine closed on it) correctly becomes Not Recommended — matching the PDF — even though that signal is not flagged `knockout=true` in the snapshot. The triggering signal is identified from the `turn.decision` whose `move` was `knockout_close` (its `attributed_signals` / the last `failed` coverage_delta before CLOSE).
- **Borderline is a hard human-review hold** — never auto-resolved (product invariant). The report states this; `HumanDecisionPanel` handles the decision.
- Initial thresholds (calibration-tunable): `ADVANCE_THRESHOLD = 65`, `REJECT_THRESHOLD = 40`, `MIN_COVERAGE_FOR_ADVANCE = 0.6`. Validated so session 1 (≈41, no knockout) → Borderline, all-`sufficient` (70) → Recommended.

**Internal verdict enum stays `advance | borderline | reject`** (low churn across schema/tests); UI labels them **Recommended / Borderline / Not Recommended** in the format layer.

---

## 5. Layer 2 — hybrid per-signal re-check (LLM, every reached signal)

For each signal whose engine state ≠ `none`:

- **Assemble full context for that signal:** every `turn.decision` where the signal appears in `attributed_signals` or `coverage_delta` (candidate_quote + engine grade + engine reasoning), plus the bank question(s) covering it (text, `rubric`, `positive_evidence`, `red_flags`, `difficulty`).
- **Prompt** (`prompts/v3/report_scorer/signal_recheck.txt`): given the signal, its rubric anchors, and everything the candidate said across the interview that touches it — and the engine's live assessment as a prior — re-confirm. Structured output:
  - `state ∈ {exceeded, sufficient, partial, failed}`
  - `grade ∈ {concrete, thin, null}`
  - `evidence_quotes: list[str]` (verbatim, grounded against the transcript via `grounding.py`)
  - `justification: str`
  - `overridden: bool`, `override_reason: str | null` (set when the model differs from the engine's state)
- The returned `state` replaces the engine's state in the deterministic map; Layer 1 math then runs on the corrected map.
- `none` signals are **not** re-checked (nothing to read); they remain `not_assessed`.
- **Factual gates are not re-checked (calibration decision, 2026-05-27).** A signal whose covering question(s) are all `experience_check` / `compliance_binary` kind keeps the engine's live state — the engine *asked* a counting/binary question and the candidate cleared it; the bank rubric's depth anchors (employer/role/date detail) are not what the engine probed for, so re-checking would unfairly downgrade a brief-but-complete answer. (A separate, out-of-scope follow-up will have the engine itself consult rubrics so it elicits that detail live.) Only substantive technical/behavioral signals are re-checked.
- **`failed` means absence, not shallowness (calibration decision, 2026-05-27).** The re-check prompt reserves `failed` for a genuine absence/disclaim/contradiction ("I've never done that", "I'm not sure", wrong/irrelevant answer). A real-but-shallow answer is `partial`, never `failed`. This keeps the `partial → Borderline` knockout routing meaningful and stops the stronger post-session model from over-rejecting on depth alone.
- Per-signal calls are **parallelizable** and **prefix-cacheable** (the cache key uses a 12-char SHA-256 of the signal value to stay within OpenAI's 64-char `prompt_cache_key` cap). Model: `ai_config.report_scorer_model` (`gpt-5.4`, `effort=medium`) — no latency budget. The old per-answer N-sample self-consistency is retired.
- **Determinism note:** the re-check + communication LLM passes are not bit-reproducible run-to-run, but the report is **generated once on session completion and persisted**; it is only recomputed on an explicit super-admin force-regenerate. The stored row is the auditable record. The verdict is additionally made robust by deriving it from the coverage map (e.g. an unconfirmed must-have → Borderline) rather than a knife-edge score.

Every override (engine_state → final_state, reason) is recorded in `signal_assessments[]` and summarized in the manifest — the defensibility trail.

---

## 6. Layer 3 — narrative layer (LLM, prose only)

A single pass (`prompts/v3/report_scorer/narrative.txt`) is **handed the final scored map + verdict as fixed ground truth** (states, dimension scores, overall, verdict, knockout outcome, coverage), plus the full transcript, the engine's per-turn reasoning, and the question bank. It outputs **only prose**:

- `decision.headline` — 1–2 sentence verdict reason.
- `decision.why_positive` / `decision.why_negative` — the two-column "Foundations are there / But depth was not shown" contrast (`{title, body}`).
- `quick_summary` — the narrative paragraph.
- `strengths[]` — `{title, detail}` (≈3–6).
- `concerns[]` — `{title, detail, severity ∈ deal_breaker|major|moderate}` (≈3–6).
- `questions[].our_read` — the qualitative read per question.
- `questions[].candidate_quote` — a cleaned, readable rendering of the STT-mangled answer (meaning preserved; never fabricated).
- `methodology.note` + `methodology.charity_flags[]` — the "About this report" footer, including charity flags (e.g. the 2-minute silence, the cut-off question) that are surfaced, not used to penalize.

The model is instructed it **must not** restate a different score/verdict than the ground truth it was given; its job is to explain the numbers, faithfully and readably. This guarantees prose ↔ number alignment.

---

## 7. Per-question status badge (deterministic)

Each delivered question gets a status derived from the engine signals + Layer 2 result:

| Badge | Condition |
|---|---|
| `Passed` | factual/experience-check signal `sufficient`/`exceeded` |
| `Partial` / `Partly Covered` | coverage `partial` |
| `Failed — Required Skill` | a `required`/`knockout` signal `failed` (the deal-breaker question) |
| `Not Demonstrated` | triage `no_experience` on a signal-bearing question / signal failed via disclaim |
| `Not Attempted` | no engaged answer (e.g. "Hello?") |
| `Not Fully Assessed` | question cut off / interview closed before completion |

The cut-off Q3 in reference session 1 → `Not Fully Assessed` (not counted for or against), exactly as the PDF.

---

## 8. New report schema

Reuse the existing `session_reports` JSONB columns (**no migration required**) with new internal shapes. `ReportRead` (Pydantic) + the `lib/api/reports.ts` TS types are rewritten to match.

```
verdict            : "advance" | "borderline" | "reject"          (UI-labelled Recommended/Borderline/Not Recommended)
verdict_reason     : str
decision           : { headline, why_positive:{title,body}, why_negative:{title,body} }
scores             : { overall, technical, behavioral, communication }
                       each → { score(0-100|null), tier_label, tone, confidence, coverage }
quick_summary      : str
strengths          : [ { title, detail } ]
concerns           : [ { title, detail, severity } ]
questions          : [ { seq, question_id, title, status_badge, status_tone,
                         question_text, candidate_quote, our_read } ]
methodology        : { note, questions_reached, questions_planned, duration_min, charity_flags[] }
signal_assessments : [ { signal, type, weight, knockout, priority,
                         engine_state, final_state, grade, score,
                         evidence:[{quote,question_id}], overridden, override_reason } ]   ← audit trail
scoring_manifest   : { scorer_model, prompt_versions, generated_at, correlation_id,
                       coverage_map, overrides[], n_signals_rechecked, … }
status, engine_version, version, human_decision, generated_at     (unchanged)
```

Columns reused as-is: `dimension_scores`→`scores`, `summary`→`{decision, quick_summary, strengths, concerns}`, `question_scorecards`→`questions`, `knockout_results`/`signal_scorecards`→`signal_assessments`, `scoring_manifest`, `verdict`, `verdict_reason`, `overall_score`, `overall_coverage`, `overall_confidence`. (If a clean JSONB column mapping proves awkward, add columns in a thin migration `0049`; default plan is no migration.)

---

## 9. Frontend (keep the look, reshape the content)

**Keep:** `ReportTopBar` / verdict band (relabelled), `ScoreGauge`, the split layout, the methodology footer concept, `HumanDecisionPanel`, `ReportStates`, the report hub/index.

**Delete:** `SignalScorecards` (the "Knockouts & signals — evidence inline" section).

**New / changed components** (`components/dashboard/reports/`):
- `DecisionBanner` — verdict + headline + 3-position scale (Recommended/Borderline/Not Recommended).
- `WhyContrast` — two-column positive/negative.
- `ScoresRow` — 4 `ScoreGauge`s + tier labels.
- `QuickSummary` — narrative paragraph.
- `StrengthsConcerns` — replaces `ReportSummary`'s strengths/gaps; adds severity tags (`deal_breaker|major|moderate`).
- `QuestionByQuestion` — replaces `QaEvidencePanel`; per question: status badge + question text + cleaned quote + "Our Read".
- `MethodologyFooter` — the "About this report" prose.
- `SignalSpiderChart` — kept as an optional "Signal profile" viz, repointed to `signal_assessments`.
- Signal-level audit detail → optional collapsible "Audit detail" (not headline).

`report-format.ts` gains: verdict→{Recommended/Borderline/Not Recommended} labels, tier-label/tone helpers, severity→tone, status-badge→tone.

---

## 10. Envelope-path fix (prerequisite — §1 Problem 1)

Two-part, defense-in-depth:

1. **Actor resolves by `session_id` against its own config**, not the stored absolute path: `Path(settings.engine_event_log_dir) / f"{session_id}.json"`, with the stored `audit_envelope_ref` as a fallback. (Optionally store a *relative* ref going forward.)
2. **Add the mount to the worker service** in `docker-compose.yml`: `./engine-events:/tmp/engine-events` (mirrors the engine service) so the path exists in the worker.

S3 sink remains the production story; this fixes local/dev + any same-host deploy.

**Acceptance:** regenerate both reference sessions → non-empty `coverage_map`, populated Technical/Behavioral/Overall.

---

## 11. Prompts (`prompts/v3/report_scorer/`)

- **New:** `signal_recheck.txt` (Layer 2, per-signal re-validation).
- **New:** `narrative.txt` (Layer 3, prose-only; explicitly told the scores/verdict are fixed ground truth).
- **Kept:** `communication.txt` (transcript-content communication judge).
- **Retired:** `system.txt` (the per-answer BARS judge) and its `grade_answer` / `grade_answer_consistent` path.

Per backend rule: validate prompt behavior against the real OpenAI endpoint (`@prompt_quality`, opt-in) using the two reference sessions as fixtures, not mocks alone.

---

## 12. Auditability & defensibility

Every report carries, in `signal_assessments[]` + `scoring_manifest`:
- the full engine coverage map (input),
- per-signal engine_state → final_state, the grade, the points, grounded evidence,
- every override + reason,
- model + prompt versions, correlation id, generated-at.

The deterministic core means the same logs always reproduce the same number; the narrative is constrained to explain (not invent) those numbers. Together this is the "why was I rejected" answer.

---

## 13. Testing

**Backend**
- `scoring/aggregate.py` — exhaustive unit tests on state→points, dimensions, knockout gate (incl. `knockout_close`), verdict tree, tiers, coverage/confidence. (Extends `test_aggregate.py`.)
- Per-question status derivation — unit tests covering all six badges.
- Layer 2 re-check — mocked-LLM unit tests (override recording, grounding) + `@prompt_quality` real-API check on the two reference sessions.
- Layer 3 narrative — mocked-LLM structural tests (prose-only, no number invention) + `@prompt_quality` check.
- Actor — envelope resolution by session_id; non-empty report on the reference envelopes.
- `test_service.py`, `test_schemas.py`, `test_router.py` updated to the new schema.

**Frontend**
- New/changed components get composition tests (parent+child, mock at API boundary, negative-control by reintroducing the bug) per `frontend/app` convention.
- `report-format.test.ts` updated for the new label/tone helpers.

**Calibration acceptance**
- Regenerated session 1 ≈ Borderline, Overall ≈ 4–4.5, the cut-off Q3 = Not Fully Assessed.
- Regenerated session 2 = Not Recommended, deal-breaker = the failed API/connector signal, the 2-min silence surfaced as a charity flag, not a penalty.
- Both read like the reference PDFs in content and structure.

---

## 14. Build sequence

1. **Envelope plumbing fix** — unblocks everything; verify a non-empty report on both reference sessions.
2. **Deterministic core** — rework `aggregate.py` to consume the engine coverage map + `knockout_close`; new constants; full unit tests. (No LLM yet — wire engine states straight through to prove the math.)
3. **Hybrid re-check** — per-signal Layer 2 + override recording.
4. **Narrative layer** — Layer 3 + new prompts.
5. **Schema + frontend** — new `ReportRead`/TS types; new components; delete `SignalScorecards`.
6. **Backfill + calibration** — regenerate both reference sessions, eyeball against the PDFs, tune constants.

---

## 15. Open / tunable items (decided defaults, flagged for validation)

- State→points and verdict thresholds are calibrated to two reference sessions — validate as more sessions accrue; all are named constants.
- `exceeded` state is assigned only by Layer 2; if it proves noisy, collapse to `sufficient`.
- If reusing JSONB columns is awkward in practice, a thin `0049` migration adds explicit columns (default plan: no migration).
- Communication-from-recording remains a separate future sub-project.
