# Skill Coverage vs. Time Budget — Deterministic Coverage Planner

**Date:** 2026-06-13
**Branch:** `feat/followups-governed-dimensions`
**Status:** Design — approved, pending implementation
**Module:** `app/modules/question_bank/` (+ migration, `frontend/app` badge)
**Related:** `2026-06-13-active-snapshot-and-invariant-gate-design.md`,
`2026-06-12-ai-screening-skills-test-design.md`,
`2026-06-12-question-bank-v3-measurement-instrument-design.md`

---

## 1. Problem

The AI-screening question bank is the backbone of the product: it is the template the
live interview engine asks from **and** the rubric the reporting module scores against.
A skill the bank does not cover is a blind spot in the fit verdict.

The live-validated bank (see active-snapshot-and-invariant-gate spec) is strong, but it
left one **weight-2 must-have skill** (database fundamentals) untested: the 20-minute
budget filled with higher-weight skills, and the deterministic gate's coverage check only
flags **weight-3** skills. This is not a one-JD prompt bug — it is a structural
**coverage-vs-budget** problem that recurs across any JD whose important-skill count
exceeds the question slots a 15–20 minute screen affords.

### 1.1 The pivotal constraint: scored coverage ≠ live coverage

There are two distinct notions of "coverage" in this system, carried by two different
fields:

| | Vehicle | What it buys | Budget at 15–20 min |
|---|---|---|---|
| **Live coverage** | `signal_values` (≤3 per question) | The brain *probes* the skill in-session; accumulates evidence notes | Cheap — bundling works |
| **Scored coverage** | `primary_signal` (1 per question) | The skill gets a **per-signal grade**, rolls up to the dimension scores + fit verdict, and can register as a **gap** | ~5–6 slots |

Verified in code:
- The report's graded denominator is `{q.primary_signal for q in evidence.questions}`
  (`reporting/scoring/evidence_adapter.py::EvidenceView.primary_set`).
- A skill that is only in `signal_values` (bundled, not primary) takes the
  `demonstrated_secondaries` / `cross_credited` path
  (`evidence_adapter.py::demonstrated_secondaries`) — this is **upside-only**: it can
  *add* credit if the candidate shows the skill, but if the candidate is weak or silent
  on it, **it never registers as a gap** and never drags the fit verdict.

**Implication:** "screen everything required and decide fit" means each must-have skill
has to be **gradable as a gap** — you must detect *weakness*, not merely credit strength.
That only happens for `primary_signal` skills. The binding budget is therefore
**scored slots (~5–6)**, not raw questions. Bundling via `signal_values` raises breadth
and candidate UX but **cannot** make an additional must-have skill scorable.

### 1.2 Current gaps (code)

- `invariants.py::check_bank_invariants` — coverage check fires only for
  `weight == 3` skill signals, and checks **`signal_values`** membership (live), not
  **`primary_signal`** membership (scored). Weight-2 must-haves are invisible.
- `invariants.py::_trim_to_budget` — drops the *last non-mandatory question*
  positionally. **Not coverage-aware**: it can drop the sole cover of an important skill.
- `prompts/v3/question_bank_ai_screening.txt` — frames generation as ~one skill per
  scenario ("for each high-weight skill signal, author a `technical_scenario` … STOP when
  the high-weight skill signals are covered"). It does not teach **density** (folding
  related skills into one scenario via `signal_values`).

---

## 2. Decisions (locked with the user)

1. **Scored grain = competency (consolidate).** The report scores at the competency
   grain; the report contract stays **unchanged**. A "scored competency" must be a real
   snapshot signal (because `primary_signal ∈ signal_values ⊆ snapshot values` — the
   validator enforces this). The planner therefore does **not** invent composite names;
   it **decides, among the skill signals that exist, which own a scored `primary_signal`
   slot vs. which ride along as bundled secondaries**.
2. **Must-cover predicate:** `purpose == "skill" AND (priority == "required" OR weight >= 2)`.
   (`priority ∈ {required, preferred}`; `weight ∈ {1,2,3}`, default 2.) The union is the
   most complete line — fewest skills slip — which makes honest over-subscription
   reporting load-bearing.
3. **A must-cover skill is never demoted to a bundled secondary** to make room. Only the
   weight-1 / preferred tail bundles or drops freely. If the must-cover set genuinely
   exceeds the slot budget, that is true **over-subscription**: cover as many as fit
   (priority/weight-ranked), report the rest as secondary-only + recommend extending the
   stage. **Never a silent gap.**
4. **Approach 2 — deterministic planner (pre) + generalized gate (post).** Code solves the
   knapsack on the scored set up front and hands an explicit plan to the LLM; the same
   generalized gate verifies + coverage-aware-trims + re-passes after generation.
5. **Over-subscription surface = first-class.** A typed `coverage_feasibility` field on the
   bank + a recruiter-facing badge in `frontend/app`.

### 2.1 Principle preserved

"Gate in code, LLM does the semantic part." Code owns the **scored set** (countable: which
signals are primary, how many slots fit) — deterministic and trustworthy. The LLM owns
**bundling coherence** (which sub-skills genuinely co-exercise in one realistic scenario)
and **scenario text** — semantic judgment code must not fake. Forcing the bundling
assignment into code (rejected Approach 3) would produce incoherent bundles and a vaguer,
worse candidate experience.

### 2.2 Honest guarantee statement

Code **cannot fabricate** a quality scenario, so primary coverage is **not** 100%
code-enforced the way `≤1 project_deepdive` is. The reliability chain is:

> **pre-gen plan** (the LLM is told exactly which primaries to produce) → **gate verifies**
> → **one targeted critic re-pass** corrects a miss → anything *still* missing is **loudly
> reported, never silent**.

This is enterprise-correct: we do not overclaim a guarantee the medium cannot make. The
countable HARD invariants (`≤1 deepdive`, `≤1 behavioral`, forbidden kinds, fit-budget)
remain code-guaranteed via `hard_repair`.

---

## 3. Design

### 3.1 `coverage_planner.py` (new — pure: no DB, no LLM)

Mirrors `invariants.py`: a pure function over the **skill-filtered** snapshot signals
(`_signals_for_generation(snapshot_signals, stage_type="ai_screening")`, eligibility
excluded — so code and the LLM see the same set) plus the stage duration. Fully
unit-testable.

**Slot-budget formula:**
```
slot_budget = max(1, floor(stage_duration_minutes / MIN_PER_SCORED_SLOT))
```
`MIN_PER_SCORED_SLOT` is a configurable `AIConfig` constant
(`question_bank_min_per_scored_slot_minutes`, default `3.0` min — a scenario lead + its
escalation ladder; the one `project_deepdive` consumes a slot too). At 20 min → ~6 scored
slots, matching the validated Workato bank. The constant is a *planning* estimate; the
post-gen budget invariant + trim handle actual `estimated_minutes`.

**Output — `CoveragePlan` (frozen dataclass):**
```python
@dataclass(frozen=True)
class CoveragePlan:
    slot_budget:        int
    must_cover_count:   int
    required_primaries: list[str]   # must-cover skills that GET a scored slot — gate-enforced as primary_signal
    bundle_eligible:    list[str]   # skills the LLM SHOULD fold into a related question's signal_values (semantic)
    secondary_only:     list[str]   # must-covers that overflowed budget → live+cross-credit only, NOT gap-scored
    dropped:            list[str]   # weight-1 / preferred optionals with no room at all
    feasible:           bool        # secondary_only == []
    recommended_minutes: int        # ceil(must_cover_count * MIN_PER_SCORED_SLOT) when infeasible, else stage duration
    report:             str         # human-readable feasibility note → coverage_notes
```

**Algorithm:**
1. Partition skill signals (the eligibility-filtered set):
   - `must_cover` = `priority == "required" OR weight >= 2`
   - `optional_tail` = `priority == "preferred" AND weight == 1`
2. Rank `must_cover` by `(priority == "required", weight, knockout)` descending; tie-break
   on snapshot order (stable) for determinism.
3. If `len(must_cover) <= slot_budget` (**feasible**):
   - all `must_cover` → `required_primaries`
   - `optional_tail` → `bundle_eligible` (best-effort: the LLM may spend a spare scored
     slot on one or fold it into a related scenario; either is fine — optionals are not
     gap-scored guarantees)
   - `secondary_only = []`, `dropped = []`, `feasible = True`,
     `recommended_minutes = stage_duration_minutes`
4. If `len(must_cover) > slot_budget` (**over-subscription**):
   - top `slot_budget` (by rank) → `required_primaries`
   - overflow `must_cover` → `secondary_only` (also added to `bundle_eligible` so the LLM
     still folds them in where coherent — they get live + cross-credit, just not gap-scored)
   - `optional_tail` → `dropped` (no room at all)
   - `feasible = False`,
     `recommended_minutes = ceil(len(must_cover) * MIN_PER_SCORED_SLOT)`
5. `report` is a human-readable summary of the above for `coverage_notes`.

**Deliberate boundary:** the planner never assigns *which* secondary bundles into *which*
primary and never promotes a secondary to primary (the rubric/text were authored for the
original primary — promoting would mis-score). Code decides the scored set; the LLM decides
coherent bundling + writes scenarios.

### 3.2 Pre-gen injection (`actors.py::_build_user_message`)

The existing soft weight-tier "BUDGET FOR THIS STAGE" block (`actors.py:229–249`) is
replaced (for `ai_screening`) by an explicit, deterministic plan rendered from
`CoveragePlan`:

> This ~20-minute screen fits about **6** scored questions. Produce **exactly one scored
> question per REQUIRED PRIMARY**, each as that question's `primary_signal`:
> `[required_primaries]`. Where these related skills genuinely co-exercise in one realistic
> task, fold them into a scenario's `signal_values` instead of spending a separate scored
> slot: `[bundle_eligible]`. These could not fit as scored questions and will only be
> lightly credited — bundle them where coherent but do **not** expand the bank:
> `[secondary_only]`.

This is the load-bearing change: the bank lands feasible on the **first** generation pass.
The plan is *data* in the user message; the *principle* of density lives in the system
recipe (§3.5). Non-`ai_screening` stages keep the existing budget block unchanged.

`_generate_one_bank` computes the plan once in Phase A (it already has
`snapshot_signals` + `stage_duration` as primitives) and threads it through to
`_stream_bank_questions` → `_build_user_message`, and to the gate (§3.3) and persistence
(§3.4).

### 3.3 Generalized gate (`invariants.py`)

`check_bank_invariants` gains a `plan: CoveragePlan | None` parameter and **changes the
coverage semantics from `signal_values` membership → `primary_signal` membership** (the
core correctness fix):

- **Remove** `uncovered_high_weight_skill` (weight-3, `signal_values`, detect-only).
- **Add** `uncovered_required_primary`: for each `plan.required_primaries` not in
  `{q.primary_signal for q in questions}` → **hard violation**, `hard_repairable=False`
  (code can't author a scenario → drives the targeted critic re-pass, exactly like the
  current weight-3 path drove it). Description names the skill so the re-pass is targeted.
- All existing countable invariants (`≤1 project_deepdive`, `≤1 behavioral`,
  forbidden kinds, over-budget) stay **code-guaranteed** via `hard_repair`.
- `stage_type != "ai_screening"` still returns `[]` (unchanged).

**Coverage-aware trim** — `hard_repair` / `_trim_to_budget` gain the
`required_primaries: set[str]` set. When over budget, the trim drops the lowest-priority
question **whose `primary_signal` is NOT a required_primary** (optional padding, or a
redundant 2nd question on an already-covered competency) first, and **never drops the sole
primary cover of a required_primary**. Because the planner already reconciled the
must-cover set against `slot_budget` up front, budget and coverage do not collide here —
the trim only sheds optional extras. If only required-primary sole-covers remain over
budget, the trim stops (does not drop a must-cover) — that situation should not arise given
the planner's reconciliation, but the trim is defensive.

`hard_repair` and `check_bank_invariants` both take `plan` (passed through from the actor);
when `plan is None` (non-ai_screening, or a defensive call) the coverage checks no-op and
the trim falls back to the current positional behavior.

### 3.4 Persistence (`actors.py` Phase C)

- `plan.report` is appended to `coverage_notes` alongside the existing `gate:` codes (audit
  trail carries the feasibility verdict + what the gate did).
- The typed `coverage_feasibility` JSONB is written:
  `{feasible, slot_budget, must_cover_count, secondary_only, dropped, recommended_minutes}`.

### 3.5 Recipe (`prompts/v3/question_bank_ai_screening.txt`) — principle, not examples

Per `feedback_prompt_principles_not_examples`, teach the rule + *why*; no JD-specific
examples:

- Reframe authoring step 1 from "for each high-weight skill signal, author a
  `technical_scenario`" (1:1) → "one scenario per **scored primary**; where two required
  skills genuinely co-exercise in one realistic task, fold the secondary into
  `signal_values` instead of spending a separate scored slot."
- Add the density principle + rationale: `signal_values` (≤3) is the density vehicle;
  `primary_signal` is what is scored as a potential gap; **bundle only where genuinely
  coherent — a forced bundle of unrelated skills dilutes depth and reads as two crammed
  questions, hurting the candidate experience.**
- Keep all existing distinctness + bank-level-singleton rules.

### 3.6 First-class feasibility surface

- **Migration 0058** (`0058_bank_coverage_feasibility`): `ALTER TABLE
  stage_question_banks ADD COLUMN coverage_feasibility JSONB NULL`. Rollback drops the
  column. No RLS change (existing tenant-scoped table; the column inherits the table's
  policies). Legacy banks read `NULL` → no badge.
- **ORM** (`question_bank/models.py`): `coverage_feasibility: Mapped[dict | None] =
  mapped_column(JSONB, nullable=True)`.
- **Schema** (`question_bank/schemas.py`): a `CoverageFeasibility` Pydantic model and
  `coverage_feasibility: CoverageFeasibility | None = None` on `BankResponse` (and thus
  `BankWithQuestionsResponse`). The router read paths map the JSONB → the model.
- **Frontend** (`frontend/app`): a `CoverageFeasibility` type + an amber warning rendered
  in `BankHeader` **only when `feasible === false`**:
  *"⚠ 2 must-have skills don't fit a 20-min screen — extend this stage to ~28 min."* with
  the `secondary_only` list shown on expand. `npm run type-check` + `npm run build` green.

---

## 4. Files touched

**Backend**
- `app/modules/question_bank/coverage_planner.py` — **new** (pure planner + `CoveragePlan`).
- `app/modules/question_bank/invariants.py` — generalized coverage check (primary_signal)
  + coverage-aware trim; remove `uncovered_high_weight_skill`.
- `app/modules/question_bank/actors.py` — compute plan in Phase A; inject in
  `_build_user_message`; pass `plan` to gate/trim; persist `coverage_feasibility`.
- `app/modules/question_bank/models.py` — `coverage_feasibility` column.
- `app/modules/question_bank/schemas.py` — `CoverageFeasibility` + `BankResponse` field.
- `app/modules/question_bank/router.py` — map JSONB → schema on read paths.
- `app/ai/config.py` — `question_bank_min_per_scored_slot_minutes` (default 3.0).
- `prompts/v3/question_bank_ai_screening.txt` — density principle.
- `migrations/versions/0058_bank_coverage_feasibility.py` — **new** (+ rollback).

**Frontend (`frontend/app`)**
- `CoverageFeasibility` type + `BankHeader.tsx` amber badge (infeasible only).

**Tests**
- `tests/question_bank/test_coverage_planner.py` — **new**.
- `tests/question_bank/test_invariants.py` — extend (primary_signal coverage,
  coverage-aware trim).
- actor wiring test — plan injected, gate→re-pass→hard_repair, `coverage_feasibility`
  persisted.
- `tests/question_bank/prompt_evals/` — extend (`-m prompt_quality`, user runs): over-
  subscribed JD → every `required_primary` is a `primary_signal` or in `secondary_only`;
  density present; distinctness preserved.
- Frontend badge test (renders on infeasible, hidden on feasible).

---

## 5. Non-goals

- No change to the report contract / scorer (`reporting/`): scoring stays at the
  competency grain via the existing `primary_signal` denominator.
- No change to signal extraction (`jd/`): the planner reconciles the skill signals that
  exist; tightening extraction consolidation is a separate, optional lever.
- No two-tier (core/stretch) bank, no adaptive item pool (deferred in the v3 spec).
- No `phone_screen` / other stage changes — the planner + coverage check are
  `ai_screening`-only (every other stage returns the unchanged path).

---

## 6. Risks / edge cases

- **Legacy snapshots** without `purpose` / `priority`: `purpose` defaults to `"skill"`,
  `priority` may be absent → treat a missing `priority` as `"preferred"` and a missing
  `weight` as `2` (so a legacy skill with no metadata is must-cover by the weight default
  — conservative, no silent drop). Planner unit tests cover this.
- **Zero must-cover skills** (all preferred/weight-1, or empty after eligibility filter):
  `required_primaries == []`, gate no-ops on coverage, generation proceeds normally.
- **`MIN_PER_SCORED_SLOT` mismatch with actual minutes:** the constant only sizes
  `slot_budget` for planning; the post-gen over-budget invariant + coverage-aware trim
  reconcile against real `estimated_minutes`. Tunable via env, no code change.
- **Removed `uncovered_high_weight_skill`:** grep-confirm no other caller/test references
  the code string before deleting.
- **Determinism:** ranking ties break on stable snapshot order so the plan is reproducible
  for the same snapshot (no `Math.random`/ordering nondeterminism).
</content>
</invoke>
