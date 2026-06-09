# Rubric-Aware, Question-Anchored Report Scoring

**Date:** 2026-06-09
**Status:** Design — pending implementation plan
**Module:** `backend/nexus/app/modules/reporting/` (+ small `interview_engine/` change, + `frontend/app` report viewer)
**Builds on:** `2026-06-08-report-contract-rewrite-design.md` (gen-3 `SessionEvidence` consumption, provenance-aware scoring), `2026-05-28-verdict-driven-fit-score-design.md` (verdict-driven fit score)

---

## 1. Problem

The Question Bank is the **standardization spine**: hundreds of candidates interview against one JD, and the per-question evaluator fields — `rubric` (excellent / meets_bar / below_bar), `positive_evidence` ("Listen for"), `red_flags`, `evaluation_hint`, `difficulty`, and the `follow_ups` probe ladder — are what make those candidates objectively comparable. The interview engine's brain consumes the **full** card live. The reporting module does not.

A forensic pass over the live session `f2fd4b03-9eb5-4384-acec-e6143552b4e4` ("Test – EMM Engineer") established:

### 1.1 The engine→report plumbing is sound (no change needed to the handoff)
`record_session_evidence` writes `sessions.session_evidence_json` in one atomic, state-gated (`active→completed`), idempotent, tenant-filtered UPDATE; commits durably; then best-effort-enqueues the report actor. The actor reloads via `SessionEvidence.model_validate(...)` — strongly typed, lossless round-trip (test-asserted). This layer is production-grade and is **not** changed by this spec.

### 1.2 The report ignores most of the grading template
Verified against code + the live report:

- **`positive_evidence` and `red_flags` reach no scoring layer.** They are loaded (`reporting/actors.py`), stored in `rubric_snapshot` for audit, then dropped. Only `rubric` reaches the Layer-2 re-check (`recheck.py` builds `question_context = "Q: …\nrubric: …"`).
- **`difficulty` is loaded (`actors.py:202`) then dropped before the LLM** — the grader never learns a question was `hard` vs `easy`.
- **Grading is signal-centric, but rubrics are per-question.** A signal appears on several questions; `_eliciting_question` grades it against the rubric of whichever question owned the signal's *first supporting note*. Combined with **best-texture-across-all-notes** roll-up, a strong cross-credit masks a thin dedicated answer.
  - **Live proof:** the `iOS device management at scale` signal has a dedicated **hard** technical_scenario (the Wi-Fi/cert breakage question). The candidate's answers to *that* question were uniformly **thin** (notes seq 44–48: *"we can check the Wi-Fi policy… and look into it"*) — exactly what its `red_flags` predict (*"Does not know Wi-Fi or certificate profile handling"*). But iOS was also cross-credited from a strong change-management story, so the report scored it **solid (80)** and never recorded the tripped red flag.
- **`questions[].probes_used` (probe-dependence) is unused** — only the per-note `via_probe` tag is consumed, not the aggregate "needed all 3 probes to get there."
- **`transcript[].pre_turn_gap_ms` (think-time) is unused**, despite its contract docstring stating *"the report derives flatline / copilot patterns from it."*

### 1.3 Provenance manifest is half-empty
The live report's `scoring_manifest` has `bank_id=null`, `signal_snapshot_id=null`, `scorer_code_version=null`. Nothing records **which template version graded the candidate** — fatal for comparing 100s of candidates when a bank can be regenerated mid-campaign. `QuestionRecord.time_spent_s` is `0.0` on every question (the engine never populates it).

---

## 2. Goals / Non-Goals

**Goals**
1. Make per-question grading consume the **full** bank card (rubric + listen-for + red-flags + evaluation_hint + difficulty + probe-dependence).
2. Re-anchor grading: **the question is the unit of grading; the signal stays the unit of the verdict.** Grade each *asked* question against its own card using the notes it elicited; roll question-grades up to `primary_signal`.
3. Surface to the recruiter, per question: which Listen-for points were hit, which Red-flags tripped, the question difficulty, and probe-dependence.
4. Record template provenance (`bank_id`, `signal_snapshot_id`, `scorer_code_version`) on every report; populate `time_spent_s`.
5. Add a deterministic think-time **human-review flag** (never a score input).

**Non-Goals**
- No change to the engine→report persistence/handoff.
- No change to verdict math, dimension weighting, knockout gates, fit ceilings, holistic ±5, or thresholds (`constants.py`). The roll-up changes how a *signal's level* is derived, not how levels become a verdict.
- No new scoring input from think-time, duration overrun, `tier`, or `estimated_minutes` (deferred / context-only).

---

## 3. Step 0 — Provenance plumbing (standalone, ship first)

Independent of the scoring rework, low risk:

1. **Manifest template provenance.** `score_session_report` already loads the bank (`StageQuestionBank`) and the signal snapshot. Thread `bank_id`, `signal_snapshot_id`, and a module-level `SCORER_CODE_VERSION` constant through `build_report(...)` into `ScoringManifest`. (`ScoringManifest` already declares these fields — they are simply never populated.)
2. **`time_spent_s`.** Populate `QuestionRecord.time_spent_s` in the engine's `interview_engine/driver.py::finalize` (and/or `brain/resolver.py::build_question_records`) from the per-thread turn spans the driver already tracks. Engine-side change; covered by an engine unit test.

Ships and is verified before Step 1 begins.

---

## 4. Step 1 — Question-anchored grading

### 4.1 Architecture

Replace the per-signal Layer-2 re-check with a per-**question** grader, then roll up to signals. The deterministic auditable core ("same logs → same number") is preserved as the base; the LLM refines.

```
SessionEvidence ──► EvidenceView
                      │
   per ASKED question │  notes WHERE from_question_id == q.id
                      ▼
        scoring/question_grade.py
          ├─ deterministic base   (texture roll-up over the question's own notes)
          └─ LLM grade            (full card → level + listen_for_hits + red_flags_tripped
                                    + evidence_quotes + needs_verification, probe-aware)
                      │
                      ▼
        scoring/rollup.py  (question grades ──► per-signal level)
          ├─ dedicated question anchors the base
          ├─ cross-credit lifts at most +1 tier
          └─ dedicated-not-reached ⇒ cross-credit authoritative
                      │
                      ▼
   aggregate.py  (UNCHANGED: dimensions, coverage, ceilings, verdict, holistic, communication)
                      │
        think_time.py (pure) ──► methodology charity flags (never a score input)
```

New files: `scoring/question_grade.py`, `scoring/rollup.py`, `scoring/think_time.py`, `prompts/v4/report_scorer/question_grade.txt`.
Retired: `scoring/recheck.py` + `prompts/v4/report_scorer/signal_recheck.txt` (factual-gate + charity-flag logic migrates into `question_grade`).
`service.py::build_report` is rewired to orchestrate question-grade → rollup instead of per-signal recheck.

LLM call budget is unchanged: one grade per *asked* question (7 in the live session) vs. today's one re-check per *evidenced signal* (7).

### 4.2 Per-question grader — inputs & output

**Inputs** (per asked question): the full bank card —
`text`, `rubric{excellent,meets_bar,below_bar}`, `positive_evidence[]`, `red_flags[]`, `evaluation_hint`, `question_kind`, `difficulty`, `probes_used` (count), `probes_available` (count) — plus the elicited notes (`from_question_id == q.id`, each a verbatim quote tagged stance/texture/via_probe) and the deterministic base level.

**Output** (`QuestionGradeOut`, structured):
```
level:            strong | solid | thin | absent      # graded against the rubric anchors
listen_for_hits:  list[str]                            # which positive_evidence points were covered (grounded)
red_flags_tripped:list[str]                            # which red_flags fired (grounded)
evidence_quotes:  list[str]                            # candidate's own words (grounded against the notes)
needs_verification: bool
verification_note: str | None                          # factual-gate charity flag (missing required facts)
overridden:       bool                                 # grade != deterministic base
override_reason:  str | None
```

**Grading rules (in `prompts/v4/report_scorer/question_grade.txt`):**
- Grade the answer against the rubric's three anchors; use the full range (do not collapse to `solid`); do not anchor on the engine base.
- Map `positive_evidence` → `listen_for_hits` (only when genuinely covered, grounded in a quote) and `red_flags` → `red_flags_tripped` (only when genuinely fired).
- **Factual gates** (`experience_check`, `compliance_binary`): a brief precise fact is a complete answer — never penalize brevity; some-but-not-all required facts → grade the tier it reaches + `needs_verification=true` naming the missing facts. (Preserves current behavior; see `FACTUAL_QUESTION_KINDS`.)
- **Probe-dependence:** evidence that only reached `concrete`/`strong` after most/all probes were fired (`probes_used` near `probes_available`) caps the tier one step (e.g. concrete-only-after-3-probes → `solid`, not `strong`). Volunteered depth is worth more than extracted depth.
- **Difficulty** is provided as calibration context (the rubric is already difficulty-tuned); it informs the grade and is displayed, it is not a separate multiplier.
- Stay grounded — never invent a quote. Reveal nothing a candidate could see.

### 4.3 Roll-up: question grades → signal level (`rollup.py`)

For each signal in the primary set:

1. **Locate its dedicated question(s)** = questions whose `primary_signal == signal`.
   **Tie-break** (≥2 share a primary, e.g. live pos-0 + pos-7 both `3–5 years EMM/MDM`): prefer `outcome == asked` over `not_reached`; among asked, prefer the lowest `position`.
2. **Base level** = that dedicated question's graded level.
   - If the dedicated question was `not_reached` (or none exists): **cross-credit is authoritative** — grade against the question that actually elicited the signal's supporting notes (the existing `_eliciting_question` fallback), or `not_reached` if none.
3. **Cross-credit lift (±1):** the level ladder is `absent < thin < solid < strong`. Inspect this signal's notes whose `from_question_id` is a *different* question. If that cross-credited evidence reaches a tier **above** the base, lift the base by **exactly one** rung (`absent→thin`, `thin→solid`, `solid→strong`) — never two (e.g. never `thin→strong`). A dedicated answer that was a genuine disclaim (`absent` via an un-retracted contradiction) is **not** lifted by cross-credit — an explicit "I haven't done X" outweighs an incidental mention elsewhere; the lift applies only to a base reached by weak-but-present evidence.
4. Record `cross_credit_applied: bool` and a one-line `level_basis` (e.g. `"dedicated question: thin; +1 cross-credit → solid"`).

`level_for_signal` in `aggregate.py` (today's texture-only roll-up) is superseded by `rollup.py` for primary signals; the deterministic texture helper is reused inside the per-question base. Everything downstream of the per-signal `level` — `score_dimension`, `score_overall`, `compute_coverage`, `signal_ceiling`/`must_have_cap`, `resolve_verdict`, `apply_holistic`, `grade_communication` — is **unchanged**.

**Worked example (live iOS signal):** dedicated Wi-Fi question grades **thin** and records the tripped red-flag *"Does not know Wi-Fi/cert handling"*. Strong cross-credit from the change-mgmt story lifts +1 → iOS = **solid (80)** — the same number as today, now honestly derived and fully explained on the card (dedicated answer was thin; signal still earns cross-credit). The verdict stays `borderline` (driven by the unconfirmed `3–5 years EMM/MDM` must-have, unchanged).

### 4.4 Think-time flag (`think_time.py`, pure, deterministic)

Compute the per-session distribution of candidate-turn `pre_turn_gap_ms`. Flag a turn when its gap is a **conservative outlier** (`gap_ms > max(THINK_TIME_FLOOR_MS, session_p90 * THINK_TIME_OUTLIER_FACTOR)`) **and** the answer's texture is `concrete`/`strong` (the "long pause → suddenly fluent" pattern). Emit a methodology/charity flag, e.g. *"3 answers followed unusually long pauses before a fluent reply; confirm responses were unassisted."* Constants live in `constants.py` and are conservative to avoid false alarms. **This never enters the score, dimension, coverage, or verdict** — it appends to `methodology.charity_flags` only, consistent with "borderline = human" and proctoring "for review, not a decision." A session below the minimum-turns threshold produces no flag.

### 4.5 Schema changes (no migration)

`question_scorecards` and `signal_scorecards` are JSONB columns — additive fields need no migration.

- **`QuestionOut`** (`reporting/schemas.py`) gains: `level: str`, `difficulty: str | None`, `listen_for_hits: list[str]`, `red_flags_tripped: list[str]`, `probes_used: int`, `probes_available: int`. Existing fields unchanged.
- **`SignalAssessmentOut`** gains: `cross_credit_applied: bool`, `level_basis: str`.
- **`MethodologyOut`** — think-time flags append to the existing `charity_flags`; no new field required (a dedicated `review_flags` list is optional and deferred).
- New `QuestionGradeOut` schema; `SignalRecheckOut` removed.

### 4.6 Frontend (`frontend/app`)

- `components/dashboard/reports/QuestionByQuestion.tsx` — per card: difficulty chip, ✓ Listen-for-hits, ⚠ Red-flags-tripped, and a probe-dependence indicator (`probes_used / probes_available`).
- `components/dashboard/reports/SignalAuditTable.tsx` — show `level_basis` (cross-credit transparency).
- `lib/api/reports.ts` — extend the `QuestionOut` / `SignalAssessment` types.
- Vitest fixtures (`tests/components/reports/_fixture.ts`) + component tests updated for the new fields.

---

## 5. Data audit — disposition of every engine/bank field

| Field | Source | Disposition in this spec |
|---|---|---|
| `rubric` | bank | per-question grader (kept) |
| `positive_evidence` (Listen-for) | bank | **now consumed** → `listen_for_hits` |
| `red_flags` | bank | **now consumed** → `red_flags_tripped` |
| `evaluation_hint` | bank | **now consumed** (grader context) |
| `difficulty` | bank | **now consumed** (grader context + card) |
| `questions[].probes_used`/`probes_available` | engine | **now consumed** (probe-dependence cap + card) |
| `questions[].time_spent_s` | engine | **Step 0** (populated) |
| `transcript[].pre_turn_gap_ms` | engine | **now consumed** → think-time review flag (never scores) |
| `notes[].{stance,texture,quote,from_question_id,via_probe,retracts_seq}` | engine | consumed (per-question grading + grounding) |
| `signals[].provenance` | engine | consumed (roll-up + coverage) — unchanged |
| `bank_id` / `signal_snapshot_id` | actor | **Step 0** (manifest) |
| `questions[].tier` (core/coverage) | engine | deferred (context-only) |
| `meta.duration_s` vs `time_budget_s` | engine | deferred (context-only) |
| `estimated_minutes` | bank | deferred (context-only) |
| `notes[].span`, `transcript[].words` | engine | unchanged (reel/vision use) |

---

## 6. Verdict-threshold safety & testing

Because §4.3 can shift a signal's level, prove no silent verdict drift:

- **Pure unit tests (TDD) on `rollup.py`:** dedicated-thin + strong-cross-credit → solid; dedicated-thin + no-cross-credit → thin; dedicated-not-reached → cross-credit authoritative; `thin→strong` forbidden; duplicate-primary tie-break (asked > not_reached, lowest position).
- **`think_time.py` test:** a synthetic outlier turn produces a flag **and** leaves the score byte-identical (flag is score-neutral).
- **Probe-dependence test:** concrete-only-after-all-probes caps at `solid`.
- **Regression harness on the real session** (`f2fd4b03`): re-score and assert verdict stays `borderline`, iOS `level_basis == "dedicated: thin; +1 cross-credit → solid"`, iOS red-flag recorded, manifest now carries `bank_id`/`signal_snapshot_id`. Snapshot the level map so future prompt edits show blast radius.
- **Live-endpoint prompt eval** (`@prompt_quality`, opt-in) for `question_grade.txt` against the real OpenAI endpoint — per the project rule to validate prompts by live test, not mocks.
- Scoring/classification change → **explicit human sign-off before merge** (CLAUDE.md HITL gate).

---

## 7. File-by-file change list

**Backend — Step 0**
- `interview_engine/driver.py` (and/or `brain/resolver.py`) — populate `QuestionRecord.time_spent_s`.
- `reporting/actors.py` — pass `bank_id`, `signal_snapshot_id` into `build_report`.
- `reporting/service.py` — accept + thread them; add `SCORER_CODE_VERSION`; populate `ScoringManifest`.

**Backend — Step 1**
- `reporting/scoring/question_grade.py` (new) — per-question deterministic base + LLM grade.
- `reporting/scoring/rollup.py` (new) — question→signal roll-up (anchor + ±1 cross-credit + tie-break).
- `reporting/scoring/think_time.py` (new) — deterministic review flag.
- `reporting/scoring/recheck.py` (removed).
- `reporting/scoring/evidence_adapter.py` — add `notes_by_question` / per-question views as needed.
- `reporting/service.py` — rewire orchestration; build per-question cards from grades.
- `reporting/schemas.py` — `QuestionGradeOut` (new), `QuestionOut`/`SignalAssessmentOut` fields; remove `SignalRecheckOut`.
- `reporting/scoring/constants.py` — think-time + probe-dependence constants.
- `prompts/v4/report_scorer/question_grade.txt` (new); `signal_recheck.txt` (removed).

**Frontend — Step 1**
- `components/dashboard/reports/QuestionByQuestion.tsx`, `SignalAuditTable.tsx`, `lib/api/reports.ts`, report fixtures + tests.

---

## 8. Risks & mitigations

- **Strictness shift changes verdicts at the margin.** Mitigated by the regression harness + the categorical guarantees in `aggregate.py` being untouched (borderline still always human-held).
- **LLM grounding drift** (inventing listen-for hits / red-flags). Mitigated by `ground_quotes`-style grounding on every quoted field + live prompt evals.
- **Think-time false positives.** Mitigated by conservative outlier thresholds, the concrete/strong co-condition, a minimum-turns floor, and the flag being score-neutral (worst case = a recruiter dismisses a note).
- **Probe-dependence double-counting** (the engine may already temper texture for probed answers). Mitigated by treating it as a tier *cap*, not an additional penalty, and validating on the real session.

---

## 9. Rollout

Step 0 ships first (no behavior change to scores; pure provenance/data completeness). Step 1 follows behind the existing `AUTO_SCORE_SESSION_REPORTS` dev toggle, validated on the real session and a live prompt eval, then merged with explicit sign-off. Reports are re-scorable (`force=True` on the actor), so existing reports can be regenerated under the new scorer.
