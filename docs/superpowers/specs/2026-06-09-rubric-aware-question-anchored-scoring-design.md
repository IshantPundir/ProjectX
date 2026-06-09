# Rubric-Aware, Question-Anchored Report Scoring

**Date:** 2026-06-09
**Status:** Design — pending implementation plan
**Module:** `backend/nexus/app/modules/reporting/` (+ `frontend/app` report viewer)
**Builds on:** `2026-06-08-report-contract-rewrite-design.md` (gen-3 `SessionEvidence` consumption, provenance-aware scoring), `2026-05-28-verdict-driven-fit-score-design.md` (verdict-driven fit score)

---

## 1. Problem

The Question Bank is the **standardization spine**: hundreds of candidates interview against one JD, and the per-question evaluator fields — `rubric` (excellent / meets_bar / below_bar), `positive_evidence` ("Listen for"), `red_flags`, `evaluation_hint`, `difficulty`, and the `follow_ups` probe ladder — are what make those candidates objectively comparable. The interview engine's brain consumes the **full** card live. The reporting module does not.

A forensic pass over the live session `f2fd4b03-9eb5-4384-acec-e6143552b4e4` ("Test – EMM Engineer") established:

### 1.1 The engine→report plumbing is sound (the handoff is not changed)
`record_session_evidence` writes `sessions.session_evidence_json` in one atomic, state-gated (`active→completed`), idempotent, tenant-filtered UPDATE; commits durably; then best-effort-enqueues the report actor. The actor reloads via `SessionEvidence.model_validate(...)` — strongly typed, lossless round-trip (test-asserted). This layer is production-grade and is **not** changed by this spec.

### 1.2 The report ignores most of the grading template (verified against code + the live report)
- **`positive_evidence` and `red_flags` reach no scoring layer.** They are loaded (`reporting/actors.py:197-198`), stored in `rubric_snapshot` for audit, then dropped. Only `rubric` reaches the Layer-2 re-check (`recheck.py` builds `question_context = "Q: …\nrubric: …"`).
- **`difficulty` is loaded (`actors.py:202`) then dropped before the LLM** — the grader never learns a question was `hard` vs `easy`.
- **Grading is signal-centric, but rubrics are per-question.** A signal appears on several questions; `_eliciting_question` grades it against the rubric of whichever question owned the signal's *first supporting note*. Combined with **best-texture-across-all-notes** roll-up (`aggregate.level_for_signal`), a strong cross-credit masks a thin dedicated answer.
  - **Live proof:** the `iOS device management at scale` signal has a dedicated **hard** technical_scenario (the Wi-Fi/cert breakage question). The candidate's answers to *that* question were uniformly **thin** (notes seq 44–48: *"we can check the Wi-Fi policy… and look into it"*) — exactly what its `red_flags` predict (*"Does not know Wi-Fi or certificate profile handling"*). But iOS was also cross-credited from a strong change-management story, so the report scored it **solid (80)** and never recorded the tripped red flag.
- **`questions[].probes_used` (probe-dependence) is unused** — only the per-note `via_probe` tag is consumed, not the aggregate "needed all 3 probes to get there." `probes_used` **is** populated by the engine (verified: `[0,1,2]`, `[1,0,2]`, …).

### 1.3 Provenance manifest is half-empty
The live report's `scoring_manifest` has `bank_id=null`, `signal_snapshot_id=null`, `scorer_code_version=null` (the fields exist on `ScoringManifest` but are never set). Nothing records **which template version graded the candidate** — fatal for comparing 100s of candidates when a bank can be regenerated mid-campaign.

### 1.4 Deferred dependency: engine timing data is contract-present but unpopulated
Verified on the live evidence: `transcript[].pre_turn_gap_ms = 0` on all 167 turns; transcript span clocks are inconsistent (agent turns zero-width relative-ms, candidate turns absolute epoch-ms); `transcript[].words = []`; `questions[].time_spent_s = 0.0`. Two report features that would consume this — a **think-time review flag** and **per-question time** — therefore have no real data today and are **out of scope** here. They are documented as a deferred dependency in §7 and gated on engine timing work. Everything in this spec stands on **populated, real** data.

---

## 2. Goals / Non-Goals

**Goals**
1. Make per-question grading consume the **full** bank card: `rubric` + `positive_evidence` + `red_flags` + `evaluation_hint` + `difficulty` + probe-dependence (`probes_used`/`probes_available`).
2. Re-anchor grading: **the question is the unit of grading; the signal stays the unit of the verdict.** Grade each *asked* question against its own card using the notes it elicited; roll question-grades up to `primary_signal`.
3. Surface to the recruiter, per question: which Listen-for points were hit, which Red-flags tripped, difficulty, and probe-dependence; and per signal: the cross-credit basis for its level.
4. Record template provenance (`bank_id`, `signal_snapshot_id`, `scorer_code_version`) on every report.

**Non-Goals**
- No change to the engine→report persistence/handoff.
- No change to verdict math, dimension weighting, knockout gates, fit ceilings, holistic ±5, or thresholds (`constants.py`). The roll-up changes how a *signal's level* is derived, not how levels become a verdict.
- No think-time flag, per-question time, duration-overrun, `tier`, or `estimated_minutes` scoring (deferred / context-only — see §7).
- No engine-side changes in this spec.

---

## 3. Step 0 — Manifest provenance (report-side, ship first)

Pure report-side, no behavior change to scores, low risk:

`score_session_report` already loads the bank (`StageQuestionBank`) and the signal snapshot. Thread `bank_id` and `signal_snapshot_id` through `build_report(...)` into `ScoringManifest`, and add a module-level `SCORER_CODE_VERSION` constant. (`ScoringManifest` already declares all three fields — they are simply never populated.) Ships and is verified before Step 1.

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
```

New files: `scoring/question_grade.py`, `scoring/rollup.py`, `prompts/v4/report_scorer/question_grade.txt`.
Retired: `scoring/recheck.py` + `prompts/v4/report_scorer/signal_recheck.txt` (factual-gate + charity-flag logic migrates into `question_grade`).
`service.py::build_report` is rewired to orchestrate question-grade → rollup instead of per-signal recheck.

LLM call budget is unchanged: one grade per *asked* question (7 in the live session) vs. today's one re-check per *evidenced signal* (7).

### 4.2 Per-question grader — inputs & output

**Inputs** (per asked question): the full bank card —
`text`, `rubric{excellent,meets_bar,below_bar}`, `positive_evidence[]`, `red_flags[]`, `evaluation_hint`, `question_kind`, `difficulty`, `probes_used` (count), `probes_available` (count) — plus the elicited notes (`from_question_id == q.id`, each a verbatim quote tagged stance/texture/via_probe) and the deterministic base level.

**Output** (`QuestionGradeOut`, structured):
```
level:             strong | solid | thin | absent      # graded against the rubric anchors
listen_for_hits:   list[str]                            # which positive_evidence points were covered (grounded)
red_flags_tripped: list[str]                            # which red_flags fired (grounded)
evidence_quotes:   list[str]                            # candidate's own words (grounded against the notes)
needs_verification: bool
verification_note: str | None                           # factual-gate charity flag (missing required facts)
overridden:        bool                                 # grade != deterministic base
override_reason:   str | None
```

**Grading rules (in `prompts/v4/report_scorer/question_grade.txt`):**
- Grade the answer against the rubric's three anchors; use the full range (do not collapse to `solid`); do not anchor on the engine base.
- Map `positive_evidence` → `listen_for_hits` (only when genuinely covered, grounded in a quote) and `red_flags` → `red_flags_tripped` (only when genuinely fired).
- **Factual gates** (`experience_check`, `compliance_binary`): a brief precise fact is a complete answer — never penalize brevity; some-but-not-all required facts → grade the tier it reaches + `needs_verification=true` naming the missing facts. (Preserves current behavior; see `FACTUAL_QUESTION_KINDS`.)
- **Probe-dependence:** evidence that only reached `concrete`/`strong` after most/all probes were fired (`probes_used` near `probes_available`) caps the tier one step (e.g. concrete-only-after-3-probes → `solid`, not `strong`). Volunteered depth is worth more than extracted depth. This is a cap, not an additional penalty.
- **Difficulty** is provided as calibration context (the rubric is already difficulty-tuned); it informs the grade and is displayed, it is not a separate multiplier.
- Stay grounded — never invent a quote. Reveal nothing a candidate could see.

### 4.3 Roll-up: question grades → signal level (`rollup.py`)

For each signal in the primary set:

1. **Locate its dedicated question(s)** = questions whose `primary_signal == signal`.
   **Tie-break** (≥2 share a primary, e.g. live pos-0 + pos-7 both `3–5 years EMM/MDM`): prefer `outcome == asked` over `not_reached`; among asked, prefer the lowest `position`.
2. **Base level** = that dedicated question's graded level.
   - If the dedicated question was `not_reached` (or none exists): **cross-credit is authoritative** — grade against the question that actually elicited the signal's supporting notes (the `_eliciting_question` fallback, moved into `rollup.py`), or `not_reached` if none.
3. **Cross-credit lift (±1):** the level ladder is `absent < thin < solid < strong`. Inspect this signal's notes whose `from_question_id` is a *different* question. If that cross-credited evidence reaches a tier **above** the base, lift the base by **exactly one** rung (`absent→thin`, `thin→solid`, `solid→strong`) — never two (e.g. never `thin→strong`). A dedicated answer that was a genuine disclaim (`absent` via an un-retracted contradiction) is **not** lifted — an explicit "I haven't done X" outweighs an incidental mention elsewhere; the lift applies only to a base reached by weak-but-present evidence.
4. Record `cross_credit_applied: bool` and a one-line `level_basis` (e.g. `"dedicated: thin; +1 cross-credit → solid"`).

`aggregate.level_for_signal` (today's texture-only roll-up) is superseded by `rollup.py` for primary signals; the deterministic texture helper is reused inside the per-question base. Everything downstream of the per-signal `level` — `score_dimension`, `score_overall`, `compute_coverage`, `signal_ceiling`/`must_have_cap`, `resolve_verdict`, `apply_holistic`, `grade_communication` — is **unchanged**.

**Worked example (live iOS signal):** dedicated Wi-Fi question grades **thin** and records the tripped red-flag *"Does not know Wi-Fi/cert handling"*. Strong cross-credit from the change-mgmt story lifts +1 → iOS = **solid (80)** — the same number as today, now honestly derived and fully explained on the card (dedicated answer was thin; signal still earns cross-credit). The verdict stays `borderline` (driven by the unconfirmed `3–5 years EMM/MDM` must-have, unchanged).

### 4.4 Schema changes (no migration)

`question_scorecards` and `signal_scorecards` are JSONB columns — additive fields need no migration.

- **`QuestionOut`** (`reporting/schemas.py`) gains: `level: str = "not_reached"`, `difficulty: str | None = None`, `listen_for_hits: list[str] = []`, `red_flags_tripped: list[str] = []`, `probes_used: int = 0`, `probes_available: int = 0`. Existing fields unchanged.
- **`SignalAssessmentOut`** gains: `cross_credit_applied: bool = False`, `level_basis: str = ""`.
- New `QuestionGradeOut` schema; `SignalRecheckOut` removed.

### 4.5 Frontend (`frontend/app`)

- `components/dashboard/reports/QuestionByQuestion.tsx` — per card: difficulty chip, ✓ Listen-for-hits, ⚠ Red-flags-tripped, and a probe-dependence indicator (`probes_used / probes_available`).
- `components/dashboard/reports/SignalAuditTable.tsx` — show `level_basis` (cross-credit transparency).
- `lib/api/reports.ts` — extend the `QuestionOut` / `SignalAssessment` types.
- Vitest fixtures (`tests/components/reports/_fixture.ts`) + component tests updated for the new fields.

---

## 5. Data audit — disposition of every engine/bank field

| Field | Source | Populated today? | Disposition |
|---|---|---|---|
| `rubric` | bank | yes | per-question grader (kept) |
| `positive_evidence` (Listen-for) | bank | yes | **now consumed** → `listen_for_hits` |
| `red_flags` | bank | yes | **now consumed** → `red_flags_tripped` |
| `evaluation_hint` | bank | yes | **now consumed** (grader context) |
| `difficulty` | bank | yes | **now consumed** (grader context + card) |
| `questions[].probes_used`/`probes_available` | engine | yes | **now consumed** (probe-dependence cap + card) |
| `notes[].{stance,texture,quote,from_question_id,via_probe,retracts_seq}` | engine | yes | consumed (per-question grading + grounding) |
| `signals[].provenance` | engine | yes | consumed (roll-up + coverage) — unchanged |
| `bank_id` / `signal_snapshot_id` | actor | yes | **Step 0** (manifest) |
| `transcript[].pre_turn_gap_ms` (think-time) | engine | **NO (all 0)** | **deferred** → §7 (engine timing) |
| `questions[].time_spent_s` | engine | **NO (all 0.0)** | **deferred** → §7 (engine timing) |
| `transcript[].words` (word timing) | engine | **NO (empty)** | not needed here; reel/vision concern |
| `questions[].tier` (core/coverage) | engine | yes | deferred (context-only) |
| `meta.duration_s` vs `time_budget_s` | engine | yes | deferred (context-only) |
| `estimated_minutes` | bank | yes | deferred (context-only) |

---

## 6. Verdict-threshold safety & testing

Because §4.3 can shift a signal's level, prove no silent verdict drift:

- **Pure unit tests (TDD) on `rollup.py`:** dedicated-thin + strong-cross-credit → solid; dedicated-thin + no-cross-credit → thin; dedicated-not-reached → cross-credit authoritative; `thin→strong` forbidden; disclaim `absent` not lifted; duplicate-primary tie-break (asked > not_reached, lowest position).
- **Probe-dependence test:** concrete-only-after-all-probes caps at `solid`.
- **Regression harness on the real session** (`f2fd4b03`): re-score and assert verdict stays `borderline`, iOS `level_basis` shows "dedicated: thin; +1 cross-credit → solid", iOS red-flag recorded, manifest now carries `bank_id`/`signal_snapshot_id`. Snapshot the level map so future prompt edits show blast radius.
- **Live-endpoint prompt eval** (`@prompt_quality`, opt-in) for `question_grade.txt` against the real OpenAI endpoint — per the project rule to validate prompts by live test, not mocks.
- Scoring/classification change → **explicit human sign-off before merge** (CLAUDE.md HITL gate).

---

## 7. Deferred dependency: engine timing population

`pre_turn_gap_ms`, consistent per-question/turn span timing, `words`, and `time_spent_s` are declared in the `SessionEvidence` contract but are **not populated** by the engine today (verified on the live session). Two report features wait on this:

- **Think-time review flag** — a deterministic, score-neutral methodology flag that detects anomalous "long pause → suddenly fluent" turns (a copilot/assistance review cue, never a score input). Requires real `pre_turn_gap_ms`.
- **Per-question time** (`time_spent_s` on the card) — requires a single, consistent session clock for both speakers.

These are **engine-side work** (the "engine isn't perfect, fix it later" track), out of scope here. When that work lands, both features are additive to the report (the `QuestionOut`/methodology shapes already accommodate them) and get their own spec. Tracked as an explicit action item so it is not silently dropped.

---

## 8. File-by-file change list

**Step 0 (report-side)**
- `reporting/actors.py` — pass `bank_id`, `signal_snapshot_id` into `build_report`.
- `reporting/service.py` — accept + thread them; add `SCORER_CODE_VERSION`; populate `ScoringManifest`.

**Step 1 (backend)**
- `reporting/scoring/question_grade.py` (new) — per-question deterministic base + LLM grade.
- `reporting/scoring/rollup.py` (new) — question→signal roll-up (anchor + ≤1-tier cross-credit + tie-break).
- `reporting/scoring/recheck.py` (removed).
- `reporting/scoring/evidence_adapter.py` — add `notes_by_question` / per-question views as needed.
- `reporting/service.py` — rewire orchestration; build per-question cards from grades.
- `reporting/schemas.py` — `QuestionGradeOut` (new), `QuestionOut`/`SignalAssessmentOut` fields; remove `SignalRecheckOut`.
- `reporting/scoring/constants.py` — probe-dependence constant(s).
- `prompts/v4/report_scorer/question_grade.txt` (new); `signal_recheck.txt` (removed).

**Step 1 (frontend)**
- `components/dashboard/reports/QuestionByQuestion.tsx`, `SignalAuditTable.tsx`, `lib/api/reports.ts`, report fixtures + tests.

---

## 9. Risks & mitigations

- **Strictness shift changes verdicts at the margin.** Mitigated by the regression harness + the categorical guarantees in `aggregate.py` being untouched (borderline still always human-held).
- **LLM grounding drift** (inventing listen-for hits / red-flags). Mitigated by `ground_quotes` grounding on every quoted field + live prompt evals.
- **Probe-dependence double-counting** (the engine may already temper texture for probed answers). Mitigated by treating it as a tier *cap*, not an additional penalty, and validating on the real session.

---

## 10. Rollout

Step 0 ships first (no behavior change to scores; pure provenance completeness). Step 1 follows behind the existing `AUTO_SCORE_SESSION_REPORTS` dev toggle, validated on the real session and a live prompt eval, then merged with explicit sign-off. Reports are re-scorable (`force=True` on the actor), so existing reports can be regenerated under the new scorer.
