# Verdict-Driven Fit Score — Design

- **Date:** 2026-05-28
- **Status:** Draft (pending review)
- **Area:** `backend/nexus/app/modules/reporting/` + `frontend/app/components/dashboard/reports/`
- **Extends:** `2026-05-25-report-scoring-engine-design.md`, `2026-05-26-recruiter-report-ui-design.md`
- **Author:** Ishant + Claude

---

## 1. Problem

Two completed-interview reports for the same job show a contradiction a recruiter cannot
parse:

| Session | Overall | Verdict | Why |
|---|---|---|---|
| `bc7ba6d3` | **4.0** | **Not Recommended** | engine `knockout_close` on REST APIs ("I've never built…") |
| `c7173674` | **3.9** | **Borderline** | a must-have (programming language) was only `partial` → "couldn't confirm" |

The higher number is the worse outcome. The root cause is **not** the verdict logic — it is
that the **overall score measures the wrong thing**.

Today (`scoring/aggregate.py`):

- `overall_score` = weighted mean over **assessed** signals only (`none` excluded), using
  `STATE_POINTS` keyed on coverage **state** alone.
  → It measures *"average quality of the signals we happened to assess."*
- `verdict` (`resolve_verdict`) is decided by **knockout gates first** (`knockout_close` →
  reject; failed must-have → reject; unconfirmed must-have → borderline), and only falls
  through to the score band if every must-have passed.
  → It measures *"did they clear the must-haves."*

These are different quantities, computed independently, so they can — and here do —
disagree. A candidate who closes the interview on a must-have gap after showing two strong
signals scores **higher** than a candidate who engaged broadly but never confirmed a
must-have.

A second, related gap: the LLM re-check (`scoring/recheck.py`) already grades each signal's
**evidence texture** (`concrete` / `thin` / `null`) and the prompt explicitly says *"DO NOT
reward length or confidence. A long, fluent answer with no concrete mechanism is weak."* But
the score is computed from **state only** (`score_state(state)`), so a buzzword-fluent answer
rated `sufficient`/`thin` scores the **same 70** as a `sufficient`/`concrete` answer that
demonstrated real depth. The bluff is detected and then discarded.

---

## 2. Goals & Non-Goals

### Goals

1. **Kill the inversion.** A reject must never out-number a borderline. Achieved by making
   the score *mean role-fit* so it can drive the verdict directly.
2. **Make the number verdict-driving, not verdict-contradicting.** `verdict = threshold(score)`,
   with knockout/coverage as categorical guarantees folded into the score's definition.
3. **Catch bluffing.** A `sufficient`/`thin` answer (correct vocabulary, no demonstrated depth)
   must score materially below a `sufficient`/`concrete` one. The LLM's existing texture
   judgment must reach the number.
4. **Stay comparable across candidates for the same job.** The headline number must be a valid
   ranking key for 100s of candidates — deterministic given the persisted grades, stable across
   model versions, criterion-referenced (vs the rubric/JD bar), never norm-referenced (vs the
   applicant pool).
5. **Preserve auditability.** Keep a fully reproducible deterministic core ("same grades → same
   number") as the audit anchor under any LLM judgment. Supports the EEOC scoring-audit-trail
   anchor.

### Non-Goals

- **No norm-referencing.** Scores are never relative to who else applied (non-deterministic +
  EEOC fairness risk).
- **No free-floating LLM headline number.** Absolute LLM scores drift across calls/model
  versions and are an invalid ranking key.
- **The report does not overturn the engine's `knockout_close`.** The engine ending the
  interview on a gating gap remains a categorical reject. (Documented future work — see §11.)
- **Communication scoring is unchanged** (separate axis, not in overall).
- **Screening is not merged into scoring.** See §3.

---

## 3. Foundational principle: Screening ≠ Scoring

A deliberate, load-bearing separation:

- The **interview engine** (`interview_engine/`) *screens* — it collects signals under a ~1.2s
  real-time budget, optimizing for natural conversation. Its per-turn coverage grades and its
  `knockout_close` are **provisional** judgments made under time pressure.
- The **report module** (`reporting/`) is the **sole final authority** — no latency pressure,
  full transcript at once, a stronger model, full reasoning. It may override the engine's
  provisional coverage.

The precise statement is **not** "the engine never judges" (its `coverage_summary` and
`knockout_close` are judgments). It is: *"the engine makes no binding decision; the report is
the final judge and may override the engine's provisional coverage."*

Today the report **trusts** the engine on two things: signals marked `none` are never
re-examined, and `knockout_close` is taken as authoritative. This design keeps that trust
boundary (re-deriving `none` signals from raw transcript is out of scope — §11), but every
*reached* substantive signal is re-checked and can be downgraded.

---

## 4. The model

Two numbers, each honest about what it is:

| Name | What it is | Properties | Where it shows |
|---|---|---|---|
| **Session Score** | Deterministic function of per-signal `(state, texture, weight, knockout, coverage)` | Reproducible given persisted grades; comparable; the **audit anchor & sort key** | Audit detail + reports hub sort key |
| **Overall Score** | Session Score **+ bounded holistic adjustment** (re-capped) | The headline number; **drives the verdict** | Hero gauge + verdict |

"Deterministic" means the **aggregation** is reproducible given the grades. The grades
themselves (`state`, `texture`) are produced by the LLM re-check — but they are persisted, so
the Session Score is 100% reproducible from the stored scorecards. The LLM's intelligence
enters where it is both **smart and comparable** (grounded per-signal grading against fixed
rubric anchors) plus a **small, bounded, justified** holistic adjustment for cross-signal
gestalt the formula cannot see.

```
                 ┌─ Layer 1: engine coverage_summary → per-signal state (none default)
                 │
 per signal ─────┼─ Layer 2: LLM re-check → (state, texture grade, evidence quotes)   [grounded]
                 │       └─ score_signal(state, texture) → 0..100 points
                 │
 aggregate ──────┼─ fit-aware weighted mean + categorical caps  ──────────► SESSION SCORE
                 │       (must-have fail/unconfirmed + coverage baked in)
                 │
 holistic ───────┼─ Layer 2.5 (NEW): bounded ±N adjustment + justification ─► OVERALL SCORE
                 │       (re-capped — cannot break a categorical guarantee)
                 │
 verdict ────────┴─ verdict = threshold(OVERALL) + categorical backstops
                         │
 narrative ──────────────┴─ Layer 3: prose (consistent with the fixed numbers)
```

---

## 5. Backend changes

All under `app/modules/reporting/`.

### 5.1 Per-signal score: `state × texture` (the bluff fix)

**`scoring/types.py`** — `ScoredSignal` gains `texture: GradeTexture` (`concrete | thin | null`).

**`scoring/constants.py`** — replace the state-only `STATE_POINTS` with a `(state, texture)`
matrix. Starting values (policy numbers, calibrate empirically):

| state \ texture | concrete | thin | null |
|---|---|---|---|
| `exceeded`   | 100 | 80 | — |
| `sufficient` | 75  | 50 | — |
| `partial`    | 40  | 25 | 12 |
| `failed`     | 0   | 0  | 0 |
| `none`       | None (excluded from denominator) | | |

Key property: `sufficient/thin = 50` lands in the borderline corridor — a bluffed "pass"
becomes "couldn't really confirm," exactly the intent. `sufficient/concrete = 75` clears the
advance bar.

**`scoring/aggregate.py`** — `score_state(state)` → `score_signal(state, texture)`.

**`service.py::build_report`** — carry `recheck_results[sv].grade` into the `ScoredSignal`
texture. Texture defaults to `concrete` (no penalty) when a signal was **not** re-checked —
i.e. factual gates (`_is_factual_gate_signal`, experience/compliance checks) and any
engine-only state — preserving today's behavior that a brief correct answer to "how many
years?" is a complete answer.

### 5.2 Fit-aware aggregation → Session Score

Redefine the overall to **mean role-fit**, deterministically. In `scoring/aggregate.py`:

```
base   = weighted_mean(assessed signals, score_signal(state, texture))   # none excluded
session_score =
    REJECT_CEILING       if knockout_close OR any must-have `failed`          # ≤ 35 → reject band
    min(base, BORDERLINE_CEILING)  if any must-have unconfirmed (none/partial)
                                    OR coverage < MIN_COVERAGE_FOR_ADVANCE    # ≤ 60 → ≤ borderline
    base                 otherwise
```

New constants: `REJECT_CEILING = 35`, `BORDERLINE_CEILING = 60`. Existing
`ADVANCE_THRESHOLD = 65`, `REJECT_THRESHOLD = 40`, `MIN_COVERAGE_FOR_ADVANCE = 0.6` retained.

**Why this is NOT the "clamp" we rejected:** the rejected hack computed a number meaning *X*
("assessed quality") then clipped it to a band derived from a **separate** verdict. Here there
is **one** definition — *fit for this role* — computed deterministically, in which a missing
must-have genuinely lowers fit. The caps **are** the fit definition, applied to the
deterministic base. The verdict is then derived **from** the fit number, not stapled beside it.

### 5.3 Layer 2.5 — bounded holistic adjustment (NEW)

A single LLM call after aggregation. New module `scoring/holistic.py` mirroring
`scoring/recheck.py` (raw OpenAI client, `responses.parse`, structured output, grounded,
graceful refusal → adjustment 0).

- **Input:** session score, all per-signal scorecards (state/texture/evidence), knockout +
  coverage facts, the candidate-side transcript, rubrics, JD metadata.
- **Output (`HolisticAdjustmentOut`):** `delta: int` (∈ `[-HOLISTIC_ADJ_MAX, +HOLISTIC_ADJ_MAX]`,
  `HOLISTIC_ADJ_MAX = 5`), `justification: str`, `evidence_quotes: list[str]` (grounded).
- **Mandate:** capture cross-signal gestalt the per-signal sum misses — e.g. *a pervasive
  pattern of surface-level answers across all signals* (compounding bluff), or genuine depth
  that several "partial"s understate.
- **Re-cap:** `overall = apply_caps(session_score + delta)`. The adjustment can move the number
  within and across **score bands** (gestalt earning its keep) but **cannot break a categorical
  guarantee** (a must-have-fail/knockout cap re-applies after the delta). Asymmetry note: the
  per-signal texture already does the bulk of bluff penalization; this delta is for
  cross-cutting patterns only.

### 5.4 Verdict = threshold(Overall) + backstops

Rewrite `resolve_verdict`:

```
verdict = advance     if overall >= ADVANCE_THRESHOLD
          reject      if overall <  REJECT_THRESHOLD
          borderline  otherwise
# categorical backstops (redundant with caps, kept as guarantees / defense-in-depth):
if knockout_close OR any must-have failed:        verdict = reject
elif any must-have unconfirmed OR low coverage:   verdict = at most borderline   # never advance
```

Because the caps in §5.2 already force the score into the right band, the backstops should
never fire in correct operation — they are insurance against a formula/LLM bug and they keep
the Borderline product invariant ("never auto-advanced/auto-rejected") and the EEOC
human-sign-off anchor categorically enforced.

### 5.5 Worked example (the two real sessions)

Under this model, using the persisted grades:

- **`bc7ba6d3`**: `knockout_close` on REST → `REJECT_CEILING` → **~35, reject**. (Programming
  must-have was `none`; REST `failed` with `thin`/`null` texture further lowers the base.)
- **`c7173674`**: no knockout_close, no failed must-have; programming must-have `partial`
  (unconfirmed) → capped ≤ `BORDERLINE_CEILING`; base over many `partial`s with thin texture
  → **~38, borderline**.

Result: **35 (reject) < 38 (borderline)** — the inversion is gone, and the numbers now rank
correctly. These two sessions become the golden regression test (§9).

### 5.6 Prompt changes (`prompts/v3/report_scorer/`, version bump)

- **`signal_recheck.txt`** — sharpen the texture rubric for bluff vs. depth, and add explicit
  anti-bias guidance:
  - `concrete` requires specific, owned mechanism / numbers / failure modes / tradeoffs, **and**
    holding up under the engine's follow-up probe. `thin` = correct vocabulary, deflects or
    repeats when probed.
  - **DO NOT equate verbosity with competence in either direction.** A one-sentence concrete
    answer is `concrete`; a paragraph of buzzwords is `thin`. Grade *specificity + behavior
    under probing*, never length.
  - **DO NOT penalize brevity or ESL phrasing** (interview is `en-IN`; many strong candidates
    are concise/ESL). Evidence-based skepticism, bounded by charity.
- **`holistic.txt`** (NEW) — the §5.3 adjustment prompt. Hard bound ±5, must cite evidence, must
  not attempt to override knockouts, must record any charity applied.
- Bump `report_scorer_prompt_version` (config-driven, `AIConfig`). No new model config needed
  beyond reusing `report_scorer_model` / `report_scorer_effort`.

### 5.7 Persistence

- `session_reports.overall_score` (existing Integer column) stores the **Overall** (headline,
  verdict-driving) number — this is what the hub reads today.
- `session_reports.scoring_manifest` (JSONB, already flexible) records the audit trail:
  `session_score`, `holistic_delta`, `holistic_justification`, `caps_applied`, plus existing
  model/prompt/version fields.
- `signal_scorecards` already store `score` + `grade`; the score values now reflect
  `state × texture`. `ScoreOut` gains optional `session_score: int | None` and
  `holistic_delta: int | None` for the headline breakdown.
- **No DB schema change.** Per the O1 decision, **Overall is the single main score used
  everywhere** (headline, verdict, and hub ranking). The hub already sorts the existing
  `overall_score` column, so no migration is needed. Session Score and the holistic delta live
  in the manifest for audit only.

### 5.8 Comparability & determinism guarantees

- Within a job, all candidates share signals/weights/knockouts/rubric → identical structure →
  the deterministic Session Score is directly comparable by construction.
- The only non-deterministic term is the ±5 holistic delta. **Decision (O1): Overall is the
  single main score used everywhere** — headline, verdict driver, and reports-hub ranking key.
  The hub sorts the existing `overall_score` column (no migration). The ±5 delta therefore
  carries a small ranking wobble; this is accepted because the per-signal grades (the bulk of
  the score) are deterministic and the delta is bounded, justified, and logged. The Session
  Score remains the reproducible audit anchor in the manifest.
- Every consequential guarantee (knockout, coverage, verdict band) is deterministic. Only the
  cosmetic-tier ±5 carries LLM non-determinism, and it is bounded + justified + logged.

---

## 6. Frontend changes

All under `frontend/app/components/dashboard/reports/` + `app/(dashboard)/reports/page.tsx` +
`lib/api/reports.ts`.

1. **`lib/api/reports.ts`** — extend `ScoreOut` with `session_score?: number | null` and
   `holistic_delta?: number | null`.
2. **`ScoresCard.tsx`** — hero "Overall" gauge stays (already colored by verdict tone). Add a
   sub-line under it: `Session score 3.8 · holistic +0.4` with the holistic justification in a
   tooltip. This makes the headline's provenance legible (the "show your work" the EEOC story
   wants) without a second hero number.
3. **`SignalAuditTable.tsx`** — add a **Score** column and a **depth** indicator. It already
   shows `grade`; now show the numeric contribution so a recruiter sees *why* a `sufficient`
   contributed only 5.0 (thin). Add a "thin evidence" / possible-bluff chip where
   `grade === 'thin'`. This is where bluff transparency lives.
4. **`reports/page.tsx` (hub)** — add a **sort-by-score** control (desc) alongside the existing
   completed-at sort. This is the concrete payoff of comparability: ranking 100s of candidates
   for one job. Sort key = Session Score (§5.8).
5. **`report-format.ts`** — reconcile `scoreBandTone` cuts (currently `75 / 55`) with the
   backend verdict thresholds (`ADVANCE 65 / REJECT 40`) so gauge coloring and verdict never
   disagree. Single source of truth = backend thresholds; export them or mirror with a comment
   pointing at `constants.py`.
6. **`ReportMethodologyFooter.tsx`** — one line noting the score is criterion-referenced (vs the
   role's rubric), evidence-grounded, and that a bounded holistic adjustment of `±delta` was
   applied with the recorded rationale.

No new `px/` primitives expected; reuse `Badge`, `Tooltip`. Desktop-first; no dark mode.

---

## 7. Anti-bias / charity safeguards (cross-cutting)

Bluff-detection's dangerous inverse is penalizing terse experts or ESL candidates. Mitigations,
all already-supported primitives:

- Texture grade keys on **specificity + behavior under probing**, never word count (§5.6).
- `methodology.charity_flags` (exists) records each charity application ("terse but concrete",
  "ESL phrasing — graded on intent").
- The holistic delta must justify any downward move against quoted evidence; vibes are not
  grounds.
- Test coverage explicitly includes terse-concrete and verbose-thin cases (§9).

This sits under the EEOC AI-bias anchor (scoring audit trail) and the `en-IN` audio reality.

---

## 8. Determinism, auditability & EEOC posture

- **Reproducible core:** Session Score = pure function of persisted `(state, texture, weight,
  knockout, coverage)`. Re-running the aggregation yields the identical number.
- **Bounded non-determinism:** only the ±5 holistic delta varies across runs; it is logged with
  justification + model/prompt manifest, and cannot alter a categorical outcome.
- **Trade accepted (per product owner):** the LLM-graded inputs improve quality at the cost of
  the *grades* themselves not being byte-reproducible across model versions. Contained by (a)
  persisting grades, (b) pinning model + prompt version in the manifest, (c) keeping the
  consequential verdict guarantees deterministic.

---

## 9. Testing

`tests/` (reporting subtree). Coverage gate per CLAUDE.md for scoring-threshold changes.

- **Unit — `score_signal` matrix:** every `(state, texture)` cell maps to the expected points;
  `none → None`.
- **Unit — fit aggregation caps:** failed must-have / knockout_close → ≤ REJECT_CEILING;
  unconfirmed must-have / low coverage → ≤ BORDERLINE_CEILING; clean → base.
- **Unit — `verdict = threshold` + backstops:** band edges (39/40/64/65); backstop redundancy
  (caps already place score in-band).
- **Golden / regression — the inversion:** feed the persisted grades for `bc7ba6d3` and
  `c7173674`; assert reject-score **<** borderline-score and both verdicts unchanged in meaning.
- **Bluff:** synthetic `sufficient/thin` signal scores materially below `sufficient/concrete`.
- **Anti-verbosity:** terse-concrete scores high; verbose-thin scores low (negative control).
- **Holistic bound:** `|delta| ≤ 5`; delta cannot lift a knockout cap above the reject band;
  refusal → delta 0.
- **Determinism:** identical grades → identical Session Score across repeated aggregation.
- **Frontend:** composition test of `ScoresCard` (session sub-line renders), `SignalAuditTable`
  (score column + thin chip), hub sort-by-score ordering. Mock at the API boundary.

---

## 10. Rollout

- Pure code + constants + prompt-version bump; **no DB migration** (O1 = Overall is the sole
  main score; hub sorts the existing `overall_score` column).
- Existing reports keep their stored values until re-scored via
  `POST /api/reports/session/{id}/regenerate` (`force=True`, super-admin). Re-generation under
  the new model is expected and safe (idempotency gate handles it).
- Restart `nexus-worker` (Dramatiq actor) + `nexus` after deploy; engine untouched.
- Human-review gate (CLAUDE.md): this changes candidate scoring/classification thresholds —
  requires explicit sign-off before merge.

---

## 11. Open questions / future

- **O1 — hub sort key — RESOLVED:** Overall is the single main score used everywhere (headline,
  verdict, hub ranking). No `session_score` column; hub sorts existing `overall_score`.
- **O2 — holistic adjustment magnitude — RESOLVED:** ±5 (±0.5 / 10) starting bound, tunable
  after we observe real reports.
- **O3 — report overturning `knockout_close`:** currently a categorical reject the report
  cannot override. Revisit if false-knockouts prove common (would make the report a fuller
  "final judge").
- **O4 — re-deriving `none` signals from raw transcript:** out of scope now (the report trusts
  the engine's attribution for untouched signals); a future "full re-judge" mode.
- **O5 — communication bluff axis:** communication is a separate weak/adequate/strong scale; a
  texture dimension there is out of scope.
