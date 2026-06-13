# Skill Coverage vs. Time Budget — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee every must-have skill of a JD gets a *scored* (`primary_signal`) slot within the AI-screen time budget — via a deterministic coverage planner (knapsack in code) + a generalized invariant gate, with honest over-subscription reporting surfaced to recruiters.

**Architecture:** A pure `coverage_planner.py` partitions the skill signals into must-cover (required|weight≥2) vs an optional tail and solves the scored-slot knapsack against the stage duration, emitting a `CoveragePlan`. The plan is injected into the generation prompt (the LLM lands feasible on the first pass), then the generalized `invariants.py` gate verifies coverage on `primary_signal` (not `signal_values`), coverage-aware-trims, and drives the existing targeted critic re-pass. A typed `coverage_feasibility` JSONB column + a recruiter badge surface over-subscription. Report/engine contracts are unchanged.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, Alembic, Dramatiq, instructor/OpenAI, pytest; Next.js (`frontend/app`).

**Spec:** `docs/superpowers/specs/2026-06-13-skill-coverage-budget-design.md`

**Conventions:**
- Backend tests: `docker compose run --rm nexus pytest <path> -q`
- After backend changes that the worker runs, the worker has no hot-reload — not needed for tests, but note for live smoke.
- Commit after each task. Branch: `feat/followups-governed-dimensions` (already checked out).

---

## File Structure

**New**
- `app/modules/question_bank/coverage_planner.py` — pure planner + `CoveragePlan` dataclass.
- `tests/question_bank/test_coverage_planner.py` — planner unit tests.
- `migrations/versions/0058_bank_coverage_feasibility.py` — `coverage_feasibility` JSONB column.

**Modified**
- `app/ai/config.py` — `question_bank_min_per_scored_slot_minutes` setting.
- `app/modules/question_bank/invariants.py` — coverage check keyed on `primary_signal` via `plan`; coverage-aware trim.
- `tests/question_bank/test_invariants.py` — update for the new signature + coverage semantics.
- `app/modules/question_bank/actors.py` — compute plan (Phase A); inject in `_build_user_message`; pass `plan` to gate/trim; persist `coverage_feasibility` + report.
- `app/modules/question_bank/models.py` — `coverage_feasibility` column.
- `app/modules/question_bank/schemas.py` — `CoverageFeasibility` model + `BankResponse` field.
- `app/modules/question_bank/router.py` — map JSONB → schema in `_bank_to_response`.
- `prompts/v3/question_bank_ai_screening.txt` — density principle.
- `tests/question_bank/prompt_evals/` — over-subscription / density evals (opt-in).
- `frontend/app/lib/api/question-banks.ts` — `CoverageFeasibility` type + field.
- `frontend/app/components/dashboard/question-bank/BankHeader.tsx` — amber over-subscription badge.

---

## Task 1: AIConfig — minutes-per-scored-slot constant

**Files:**
- Modify: `app/ai/config.py`

- [ ] **Step 1: Add the setting**

Find the `question_bank_*` settings group in `app/ai/config.py` (near `question_bank_model` / `question_bank_prompt_version`) and add:

```python
    # Planning estimate: minutes a single SCORED question (a scenario lead + its
    # escalation ladder) consumes. Sizes the coverage planner's scored-slot budget
    # (slot_budget = floor(stage_duration / this)). NOT a hard runtime cap — the
    # post-gen over-budget invariant + coverage-aware trim reconcile against the
    # LLM's actual estimated_minutes. Env-overridable; never hardcode at call sites.
    question_bank_min_per_scored_slot_minutes: float = 3.0
```

- [ ] **Step 2: Verify it imports**

Run: `docker compose run --rm nexus python -c "from app.ai.config import ai_config; print(ai_config.question_bank_min_per_scored_slot_minutes)"`
Expected: `3.0`

- [ ] **Step 3: Commit**

```bash
git add app/ai/config.py
git commit -m "feat(question_bank): AIConfig min-per-scored-slot constant for coverage planner"
```

---

## Task 2: `coverage_planner.py` — pure planner (TDD)

**Files:**
- Create: `app/modules/question_bank/coverage_planner.py`
- Test: `tests/question_bank/test_coverage_planner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/question_bank/test_coverage_planner.py`:

```python
from app.modules.question_bank.coverage_planner import build_coverage_plan, CoveragePlan


def _sig(value, *, weight=2, priority="required", purpose="skill"):
    return {"value": value, "weight": weight, "priority": priority,
            "purpose": purpose, "type": "competency"}


def test_feasible_all_must_cover_become_primaries():
    signals = [_sig("A", weight=3), _sig("B", weight=2), _sig("C", priority="required", weight=2)]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.slot_budget == 6
    assert set(plan.required_primaries) == {"A", "B", "C"}
    assert plan.secondary_only == []
    assert plan.dropped == []
    assert plan.feasible is True
    assert plan.recommended_minutes == 20


def test_optional_tail_is_bundle_eligible_not_dropped_when_feasible():
    signals = [_sig("A", weight=3), _sig("opt", weight=1, priority="preferred")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert "A" in plan.required_primaries
    assert "opt" in plan.bundle_eligible
    assert plan.dropped == []
    assert plan.feasible is True


def test_over_subscription_overflow_must_covers_are_secondary_only():
    # 8 must-covers, slot_budget = floor(15/3) = 5 -> 3 overflow
    signals = [_sig(f"S{i}", weight=3) for i in range(8)]
    plan = build_coverage_plan(signals, stage_duration_minutes=15, min_per_scored_slot=3.0)
    assert plan.slot_budget == 5
    assert len(plan.required_primaries) == 5
    assert len(plan.secondary_only) == 3
    # overflow must-covers ride in bundle_eligible too (LLM folds where coherent)
    assert set(plan.secondary_only).issubset(set(plan.bundle_eligible))
    assert plan.feasible is False
    assert plan.recommended_minutes == 24  # ceil(8 * 3.0)


def test_ranking_prefers_required_then_weight():
    # required beats preferred; within priority, higher weight first
    signals = [
        _sig("low", weight=2, priority="preferred"),
        _sig("req1", weight=2, priority="required"),
        _sig("req3", weight=3, priority="required"),
    ]
    # slot_budget = floor(3/3) = 1 -> only the top-ranked survives as primary
    plan = build_coverage_plan(signals, stage_duration_minutes=3, min_per_scored_slot=3.0)
    assert plan.required_primaries == ["req3"]
    assert set(plan.secondary_only) == {"req1", "low"}


def test_eligibility_signals_are_ignored():
    signals = [_sig("skill", weight=2), _sig("years", weight=3, purpose="eligibility")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.required_primaries == ["skill"]
    assert "years" not in plan.required_primaries
    assert "years" not in plan.bundle_eligible


def test_legacy_signal_missing_metadata_is_must_cover():
    # No weight / priority keys -> weight defaults to 2, priority -> preferred,
    # purpose -> skill. weight==2 makes it must-cover (conservative, no silent drop).
    plan = build_coverage_plan([{"value": "legacy"}], stage_duration_minutes=20,
                               min_per_scored_slot=3.0)
    assert plan.required_primaries == ["legacy"]


def test_preferred_weight1_is_optional_tail():
    signals = [_sig("must", weight=2), _sig("opt", weight=1, priority="preferred")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.required_primaries == ["must"]
    assert "opt" in plan.bundle_eligible


def test_zero_must_cover_is_feasible_empty():
    signals = [_sig("opt", weight=1, priority="preferred")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.required_primaries == []
    assert plan.feasible is True


def test_slot_budget_floor_at_least_one():
    plan = build_coverage_plan([_sig("A")], stage_duration_minutes=1, min_per_scored_slot=3.0)
    assert plan.slot_budget == 1


def test_report_is_human_readable_string():
    signals = [_sig(f"S{i}", weight=3) for i in range(8)]
    plan = build_coverage_plan(signals, stage_duration_minutes=15, min_per_scored_slot=3.0)
    assert isinstance(plan.report, str) and plan.report
    assert "secondary" in plan.report.lower() or "extend" in plan.report.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_coverage_planner.py -q`
Expected: FAIL — `ModuleNotFoundError: ... coverage_planner`

- [ ] **Step 3: Implement the planner**

Create `app/modules/question_bank/coverage_planner.py`:

```python
"""Deterministic coverage-vs-budget planner for an AI-screening bank (pure — no DB, no LLM).

The AI screen has only ~5-6 SCORED slots (a scored skill = a question's primary_signal,
which is what the report grades as a potential gap). When a JD's must-have skills exceed
that, important skills silently go untested. This planner solves the knapsack in CODE:
which must-have skills own a scored slot, which ride along as bundled secondaries, and —
when the must-cover set genuinely overflows the budget — which are secondary-only (live +
cross-credit, NOT gap-scored) so it can be reported, never silently dropped.

It decides the SCORED SET only (countable, deterministic). It never assigns which secondary
bundles into which primary, and never promotes a secondary to primary — that is semantic
judgment the LLM owns (bundling coherence + scenario text). Mirrors invariants.py: pure,
unit-tested, ai_screening-only at the call site.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CoveragePlan:
    slot_budget: int
    must_cover_count: int
    required_primaries: list[str] = field(default_factory=list)
    bundle_eligible: list[str] = field(default_factory=list)
    secondary_only: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    feasible: bool = True
    recommended_minutes: int = 0
    report: str = ""


def _is_must_cover(sig: dict) -> bool:
    """Must-cover := purpose==skill AND (priority==required OR weight>=2).

    Legacy-safe: missing purpose -> 'skill', missing priority -> 'preferred', missing
    weight -> 2 (so a metadata-less skill is must-cover by the weight default — conservative).
    """
    if sig.get("purpose", "skill") != "skill":
        return False
    weight = int(sig.get("weight", 2))
    priority = sig.get("priority", "preferred")
    return priority == "required" or weight >= 2


def _rank_key(sig: dict) -> tuple:
    """Descending importance: required first, then higher weight, then knockout."""
    return (
        sig.get("priority", "preferred") == "required",
        int(sig.get("weight", 2)),
        bool(sig.get("knockout", False)),
    )


def build_coverage_plan(
    signals: list[dict],
    *,
    stage_duration_minutes: int,
    min_per_scored_slot: float,
) -> CoveragePlan:
    """Compute the scored-slot plan from the skill-filtered snapshot signals.

    `signals` must already exclude eligibility signals (pass
    `_signals_for_generation(snapshot_signals, stage_type="ai_screening")`).
    """
    slot_budget = max(1, math.floor(stage_duration_minutes / min_per_scored_slot))

    skill_signals = [s for s in signals if s.get("purpose", "skill") == "skill"]
    must_cover = [s for s in skill_signals if _is_must_cover(s)]
    optional_tail = [s for s in skill_signals if not _is_must_cover(s)]

    # Stable rank: importance desc, original order as tie-break (enumerate index).
    ranked = [s for _, s in sorted(
        enumerate(must_cover), key=lambda pair: (_rank_key(pair[1]), -pair[0]), reverse=True
    )]

    must_cover_count = len(ranked)
    primaries = [s["value"] for s in ranked[:slot_budget]]
    overflow = [s["value"] for s in ranked[slot_budget:]]
    optional_values = [s["value"] for s in optional_tail]

    if not overflow:
        # Feasible: every must-cover gets a scored slot; optionals are best-effort bundles.
        plan = CoveragePlan(
            slot_budget=slot_budget,
            must_cover_count=must_cover_count,
            required_primaries=primaries,
            bundle_eligible=optional_values,
            secondary_only=[],
            dropped=[],
            feasible=True,
            recommended_minutes=stage_duration_minutes,
            report=(
                f"{must_cover_count} must-have skills fit {slot_budget} scored slots "
                f"in {stage_duration_minutes} min. All covered as scored questions."
            ),
        )
        return plan

    # Over-subscription: overflow must-covers ride as secondaries (bundle where coherent),
    # optionals have no room at all.
    recommended = math.ceil(must_cover_count * min_per_scored_slot)
    report = (
        f"OVER-SUBSCRIBED: {must_cover_count} must-have skills exceed the "
        f"{slot_budget}-scored-slot budget at {stage_duration_minutes} min. "
        f"Secondary-only (probed live but NOT independently scored): {', '.join(overflow)}. "
        f"Recommend extending this stage to ~{recommended} min."
    )
    return CoveragePlan(
        slot_budget=slot_budget,
        must_cover_count=must_cover_count,
        required_primaries=primaries,
        bundle_eligible=overflow + optional_values,
        secondary_only=overflow,
        dropped=optional_values,
        feasible=False,
        recommended_minutes=recommended,
        report=report,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_coverage_planner.py -q`
Expected: PASS (all 10)

- [ ] **Step 5: Export from the module (if `__all__` is curated)**

Check `app/modules/question_bank/__init__.py`. The planner is consumed only intra-module (by `actors.py`), so a public-API export is NOT required (intra-module deep imports are allowed per backend CLAUDE.md). No change unless `__init__.py` already re-exports planner-adjacent symbols — leave it.

- [ ] **Step 6: Commit**

```bash
git add app/modules/question_bank/coverage_planner.py tests/question_bank/test_coverage_planner.py
git commit -m "feat(question_bank): pure coverage planner (scored-slot knapsack + feasibility)"
```

---

## Task 3: Generalize the invariant gate — coverage on `primary_signal` + coverage-aware trim (TDD)

**Files:**
- Modify: `app/modules/question_bank/invariants.py`
- Modify: `tests/question_bank/test_invariants.py`

- [ ] **Step 1: Update the tests for the new signature + semantics**

In `tests/question_bank/test_invariants.py`:

1. Add the import at the top:
```python
from app.modules.question_bank.coverage_planner import CoveragePlan
```

2. Replace every `check_bank_invariants(..., signals=...)` call with `..., plan=...`. For the non-coverage tests (`test_two_project_deepdives_flagged`, `test_forbidden_kinds_flagged`, `test_two_behavioral_flagged`, `test_over_budget_flagged`, `test_non_ai_screening_stage_no_rules`) pass `plan=None`. Concretely those four `signals=[]` become `plan=None`.

3. Replace `test_uncovered_high_weight_skill_detected_not_repairable` and `test_clean_ai_screen_has_no_violations` with:

```python
def test_uncovered_required_primary_detected_not_repairable():
    qs = [_q("technical_scenario", signals=("Workato workflow development",))]
    plan = CoveragePlan(
        slot_budget=6, must_cover_count=2,
        required_primaries=["Workato workflow development", "AI-driven workflows"],
    )
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    cov = [v for v in vs if v.code == "uncovered_required_primary"]
    assert cov and cov[0].hard_repairable is False
    assert "AI-driven workflows" in cov[0].description


def test_covered_required_primary_via_primary_signal_no_violation():
    # The skill is the question's PRIMARY_SIGNAL -> covered (scored).
    qs = [_q("technical_scenario", signals=("Workato workflow development",)),
          _q("project_deepdive", signals=("Workato workflow development",))]
    plan = CoveragePlan(slot_budget=6, must_cover_count=1,
                        required_primaries=["Workato workflow development"])
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    assert vs == []


def test_required_primary_only_in_signal_values_is_NOT_covered():
    # Skill rides as a SECONDARY (in signal_values, not primary_signal) -> still uncovered
    # because the report scores primary_signal only.
    qs = [_q("technical_scenario",
             signals=("Workato workflow development", "AI-driven workflows"))]
    # primary_signal == signals[0] == "Workato workflow development" (see _q)
    plan = CoveragePlan(slot_budget=6, must_cover_count=1,
                        required_primaries=["AI-driven workflows"])
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    assert any(v.code == "uncovered_required_primary" for v in vs)


def test_plan_none_skips_coverage_check():
    qs = [_q("technical_scenario")]
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=None)
    assert all(v.code != "uncovered_required_primary" for v in vs)
```

4. Add coverage-aware trim tests:

```python
def test_hard_repair_coverage_aware_never_drops_sole_required_primary():
    # 3 x 8min = 24min over a 20min budget. The required-primary's question is the
    # SOLE cover of "must" and must survive even though it's last/non-mandatory.
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("opt1",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("opt2",)),
          _q("technical_scenario", mins=8.0, pos=2, signals=("must",))]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20,
                      required_primaries={"must"})
    assert any(q.primary_signal == "must" for q in out)  # protected
    assert sum(float(q.estimated_minutes) for q in out) <= 20


def test_hard_repair_drops_optional_primary_first():
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("must",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("opt",)),
          _q("technical_scenario", mins=8.0, pos=2, signals=("must",))]
    # Two "must" questions (redundant cover) — the optional one drops first.
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20,
                      required_primaries={"must"})
    assert all(q.primary_signal != "opt" for q in out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -q`
Expected: FAIL — `check_bank_invariants() got an unexpected keyword argument 'plan'` (and `hard_repair` likewise).

- [ ] **Step 3: Update `invariants.py`**

Replace the `signals` parameter + the weight-3 coverage block in `check_bank_invariants`, and add coverage-awareness to the trim. Full updated file:

```python
"""Pure, deterministic invariant checks for an AI-screening bank.

The LLM critic is unreliable at COUNTABLE invariants (it falsely claims compliance), so the
guarantee lives here in code. check_bank_invariants reports violations (for the critic re-pass
+ audit log); hard_repair unconditionally enforces the hard invariants. Both are pure (no DB,
no LLM) and operate on GeneratedQuestion objects.

Coverage is keyed on primary_signal (the SCORED denominator the report grades — see
coverage_planner.py), NOT signal_values (live-only). A must-have skill that is merely bundled
as a secondary is still 'uncovered' here, because it cannot register as a gap in the report.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.question_bank.coverage_planner import CoveragePlan
from app.modules.question_bank.schemas import GeneratedQuestion

_MAX_PROJECT_DEEPDIVE = 1
_MAX_BEHAVIORAL = 1
_FORBIDDEN_KINDS = ("experience_check", "compliance_binary")


@dataclass(frozen=True)
class Violation:
    code: str
    description: str       # concrete, fed to the critic re-pass + the audit note
    hard_repairable: bool


def check_bank_invariants(
    questions: list[GeneratedQuestion],
    *,
    stage_type: str,
    stage_duration_minutes: int,
    plan: CoveragePlan | None,
) -> list[Violation]:
    """Countable invariants for an AI skills screen. Returns [] for other stage types."""
    if stage_type != "ai_screening":
        return []
    out: list[Violation] = []
    kinds = [q.question_kind for q in questions]

    n_dd = kinds.count("project_deepdive")
    if n_dd > _MAX_PROJECT_DEEPDIVE:
        out.append(Violation(
            "too_many_project_deepdive",
            f"There are {n_dd} project_deepdive questions; an AI skills screen must have "
            "EXACTLY ONE. Reduce to one and replace the extra(s) with technical_scenario "
            "questions that test an uncovered high-weight skill.",
            True,
        ))
    n_beh = kinds.count("behavioral")
    if n_beh > _MAX_BEHAVIORAL:
        out.append(Violation(
            "too_many_behavioral",
            f"There are {n_beh} behavioral questions; at most one is allowed. Convert the "
            "extra(s) to technical_scenario questions.",
            True,
        ))
    forbidden = sorted({k for k in kinds if k in _FORBIDDEN_KINDS})
    if forbidden:
        out.append(Violation(
            "forbidden_kind",
            f"These question kinds are not allowed in an AI skills screen: {forbidden}. "
            "Replace each with a technical_scenario that makes the candidate demonstrate the skill.",
            True,
        ))
    total = sum(float(q.estimated_minutes) for q in questions)
    if total > stage_duration_minutes:
        out.append(Violation(
            "over_budget",
            f"Total estimated time is {total:.0f} min, over the {stage_duration_minutes} min "
            "budget. Remove the lowest-priority question(s) so the bank fits.",
            True,
        ))

    # Scored-coverage check: every must-cover skill the planner assigned a scored slot must
    # be SOME question's primary_signal. Not hard_repairable — code can't author a scenario,
    # so a miss drives the targeted critic re-pass.
    if plan is not None:
        covered = {q.primary_signal for q in questions}
        for sig in plan.required_primaries:
            if sig not in covered:
                out.append(Violation(
                    "uncovered_required_primary",
                    f"The must-have skill '{sig}' has no scored question — it must be some "
                    "question's primary_signal (a bundled secondary does NOT count, the report "
                    f"only grades primary_signal). Add or repurpose a technical_scenario whose "
                    f"primary_signal is exactly '{sig}'.",
                    False,
                ))
    return out


def _cap_kind(
    questions: list[GeneratedQuestion], kind: str, n: int
) -> list[GeneratedQuestion]:
    idxs = [i for i, q in enumerate(questions) if q.question_kind == kind]
    if len(idxs) <= n:
        return questions
    # Keep `n`: mandatory first, then earliest position; drop the rest.
    keep = set(sorted(idxs, key=lambda i: (not questions[i].is_mandatory, questions[i].position))[:n])
    return [q for i, q in enumerate(questions) if q.question_kind != kind or i in keep]


def _trim_to_budget(
    questions: list[GeneratedQuestion],
    budget_minutes: int,
    required_primaries: set[str],
) -> list[GeneratedQuestion]:
    """Drop lowest-priority questions until within budget — coverage-aware.

    Never drops a question that is the SOLE primary cover of a required_primary. Drops
    non-mandatory, non-protected questions from the end first (optional padding / a redundant
    2nd question on an already-covered competency). If only mandatory/protected questions
    remain over budget, stops (a must-cover is never sacrificed for the time budget — the
    planner already reconciled the must-cover set against the slot budget upstream).
    """
    qs = list(questions)

    def _is_sole_required_cover(idx: int) -> bool:
        sig = qs[idx].primary_signal
        if sig not in required_primaries:
            return False
        return sum(1 for q in qs if q.primary_signal == sig) == 1

    while sum(float(q.estimated_minutes) for q in qs) > budget_minutes and len(qs) > 1:
        drop = None
        for i in range(len(qs) - 1, -1, -1):
            if not qs[i].is_mandatory and not _is_sole_required_cover(i):
                drop = i
                break
        if drop is None:
            break  # nothing droppable without sacrificing a mandatory/must-cover
        qs.pop(drop)
    return qs


def hard_repair(
    questions: list[GeneratedQuestion],
    *,
    stage_type: str,
    stage_duration_minutes: int,
    required_primaries: set[str] | None = None,
) -> list[GeneratedQuestion]:
    """Unconditionally enforce the HARD AI-screen invariants (idempotent on a clean bank):
    drop forbidden kinds, cap project_deepdive/behavioral to one, coverage-aware trim to
    budget. Re-packs positions 0..N-1. Returns the questions UNCHANGED for non-ai_screening
    stages (their rules differ — e.g. phone_screen legitimately uses experience_check/
    compliance_binary). Pure."""
    if stage_type != "ai_screening":
        return questions
    qs = [q for q in questions if q.question_kind not in _FORBIDDEN_KINDS]
    qs = _cap_kind(qs, "project_deepdive", _MAX_PROJECT_DEEPDIVE)
    qs = _cap_kind(qs, "behavioral", _MAX_BEHAVIORAL)
    qs = _trim_to_budget(qs, stage_duration_minutes, required_primaries or set())
    for i, q in enumerate(qs):
        q.position = i
    return qs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/question_bank/test_invariants.py -q`
Expected: PASS (all, including the new coverage + trim tests)

- [ ] **Step 5: Commit**

```bash
git add app/modules/question_bank/invariants.py tests/question_bank/test_invariants.py
git commit -m "feat(question_bank): gate coverage keys on primary_signal + coverage-aware trim"
```

---

## Task 4: Migration 0058 — `coverage_feasibility` column

**Files:**
- Create: `migrations/versions/0058_bank_coverage_feasibility.py`

- [ ] **Step 1: Write the migration**

Create `migrations/versions/0058_bank_coverage_feasibility.py`:

```python
"""bank coverage feasibility column

Revision ID: 0058_bank_coverage_feasibility
Revises: 0057_bank_v3_kinds_and_self_reviewing
Create Date: 2026-06-13

Adds stage_question_banks.coverage_feasibility (JSONB, nullable) — the typed over-
subscription verdict from the coverage planner (feasible / secondary_only / recommended
minutes) surfaced as a recruiter badge. Existing table; inherits its RLS policy pair. No
data backfill — legacy banks read NULL (no badge).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0058_bank_coverage_feasibility"
down_revision = "0057_bank_v3_kinds_and_self_reviewing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stage_question_banks",
        sa.Column("coverage_feasibility", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stage_question_banks", "coverage_feasibility")
```

- [ ] **Step 2: Verify the down_revision matches the current head**

Run: `docker compose run --rm nexus alembic heads`
Expected: a single head `0057_bank_v3_kinds_and_self_reviewing` (the new file's `down_revision`). If `alembic heads` shows multiple, STOP and reconcile before continuing.

- [ ] **Step 3: Apply + roll back to prove reversibility (dev DB)**

Run:
```bash
docker compose run --rm nexus alembic upgrade head
docker compose run --rm nexus alembic downgrade -1
docker compose run --rm nexus alembic upgrade head
```
Expected: each completes without error; final state has the column.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0058_bank_coverage_feasibility.py
git commit -m "feat(question_bank): migration 0058 — coverage_feasibility JSONB column"
```

---

## Task 5: ORM + schema + router mapping

**Files:**
- Modify: `app/modules/question_bank/models.py`
- Modify: `app/modules/question_bank/schemas.py`
- Modify: `app/modules/question_bank/router.py:257-282`

- [ ] **Step 1: Add the ORM column**

In `app/modules/question_bank/models.py`, in the `StageQuestionBank` class right after the `extracted_keyterms` column (around line 66), add:

```python
    coverage_feasibility: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
    )
```

(`JSONB` is already imported — it's used by `stage_config_snapshot` / `rubric`.)

- [ ] **Step 2: Add the schema model + BankResponse field**

In `app/modules/question_bank/schemas.py`, add this model just above `class BankResponse`:

```python
class CoverageFeasibility(BaseModel):
    """Typed over-subscription verdict from the coverage planner (recruiter badge source)."""

    model_config = ConfigDict(extra="ignore")

    feasible: bool
    slot_budget: int
    must_cover_count: int
    secondary_only: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    recommended_minutes: int
```

Then add the field to `BankResponse` (after `coverage_notes`):

```python
    coverage_feasibility: CoverageFeasibility | None = None
```

- [ ] **Step 3: Map it in the router**

In `app/modules/question_bank/router.py`, inside `_bank_to_response` (the `BankResponse(...)` literal), add after `coverage_notes=bank.coverage_notes,`:

```python
        coverage_feasibility=(
            CoverageFeasibility(**bank.coverage_feasibility)
            if bank.coverage_feasibility else None
        ),
```

And add `CoverageFeasibility` to the existing schema import block at the top of `router.py` (the `from app.modules.question_bank.schemas import (...)` group).

- [ ] **Step 4: Verify imports + schema build**

Run: `docker compose run --rm nexus python -c "from app.modules.question_bank.router import _bank_to_response; from app.modules.question_bank.schemas import CoverageFeasibility; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add app/modules/question_bank/models.py app/modules/question_bank/schemas.py app/modules/question_bank/router.py
git commit -m "feat(question_bank): coverage_feasibility ORM column + schema + router mapping"
```

---

## Task 6: Actor wiring — compute plan, inject, gate/trim, persist (TDD)

**Files:**
- Modify: `app/modules/question_bank/actors.py`
- Test: `tests/test_question_banks_actors.py`

- [ ] **Step 1: Write a failing wiring test**

Add to `tests/test_question_banks_actors.py` (follow the existing fixtures/patterns in that file — find an existing `_generate_one_bank` test such as the deepdive-cap test near line 2067 and mirror its setup). The test asserts that an over-subscribed signal set leaves `coverage_feasibility.feasible == False` persisted on the bank and `secondary_only` non-empty. Sketch (adapt names to the file's existing helpers):

```python
async def test_generate_persists_coverage_feasibility_on_oversubscription(...):
    # Arrange: a stage (ai_screening, 15 min) + a confirmed snapshot with 8 weight-3
    # skill signals (must_cover=8, slot_budget=floor(15/3)=5 -> over-subscribed).
    # Stub the streamed generation + critic to return a small clean bank (mirror the
    # existing monkeypatch of `_create_question_iterable` / `run_bank_critic`).
    ...
    await _generate_one_bank(bank_id=bank_id, tenant_id=tenant_id, started_by=user_id)
    # Assert
    bank = await _reload_bank(db, bank_id)
    assert bank.coverage_feasibility is not None
    assert bank.coverage_feasibility["feasible"] is False
    assert len(bank.coverage_feasibility["secondary_only"]) == 3
    assert "OVER-SUBSCRIBED" in bank.coverage_notes
```

> If wiring a full actor test against the streaming stubs is heavy, an acceptable lighter
> alternative is a focused unit test on a new pure helper `_feasibility_dict(plan)` (Step 3)
> PLUS asserting `build_coverage_plan` is called with the eligibility-filtered signals.
> Prefer the integration-style test if the file already has a `_generate_one_bank` harness.

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_question_banks_actors.py -k coverage_feasibility -q`
Expected: FAIL (`coverage_feasibility` is None / column unset)

- [ ] **Step 3: Wire the plan into `_generate_one_bank`**

In `app/modules/question_bank/actors.py`:

**(a)** Add the import near the other question_bank imports at the top:
```python
from app.modules.question_bank.coverage_planner import CoveragePlan, build_coverage_plan
```

**(b)** Add a small pure helper near `_signals_for_generation`:
```python
def _feasibility_dict(plan: CoveragePlan | None) -> dict | None:
    """Serialize a CoveragePlan into the coverage_feasibility JSONB payload (recruiter badge)."""
    if plan is None:
        return None
    return {
        "feasible": plan.feasible,
        "slot_budget": plan.slot_budget,
        "must_cover_count": plan.must_cover_count,
        "secondary_only": list(plan.secondary_only),
        "dropped": list(plan.dropped),
        "recommended_minutes": plan.recommended_minutes,
    }
```

**(c)** In `_generate_one_bank` Phase A, right after `snapshot_signals = list(snapshot.signals)` and the `stage_duration` capture (~line 599), compute the plan:
```python
        # Deterministic coverage plan (ai_screening only): which must-have skills own a
        # scored primary_signal slot vs. ride as bundled secondaries within the time budget.
        coverage_plan = (
            build_coverage_plan(
                _signals_for_generation(snapshot_signals, stage_type=stage_type),
                stage_duration_minutes=stage_duration,
                min_per_scored_slot=ai_config.question_bank_min_per_scored_slot_minutes,
            )
            if stage_type == "ai_screening"
            else None
        )
```

**(d)** Thread `coverage_plan` into the stream call (Phase B, ~line 617):
```python
        await _stream_bank_questions(
            bank_id=bank_id,
            tenant_id=tenant_id,
            job_id=job_id,
            stage_id=stage_id,
            snapshot_id=snapshot_id,
            eligible_signals=_signals_for_generation(snapshot_signals, stage_type=stage_type),
            prompt_name=prompt_name,
            start_position=0,
            correlation_id=correlation_id,
            coverage_plan=coverage_plan,
        )
```

**(e)** Update the gate section (B3.gate, ~lines 719-742):
```python
        from app.modules.question_bank.invariants import check_bank_invariants, hard_repair

        working = corrected if corrected is not None else draft_questions
        violations = check_bank_invariants(
            working, stage_type=stage_type, stage_duration_minutes=stage_duration,
            plan=coverage_plan,
        )
        gate_codes = [v.code for v in violations]
        if violations and corrected is not None:
            try:
                working, _repass_note = await run_bank_critic(
                    draft=working, seniority=seniority, role_title=role_title,
                    signals=snapshot_signals, stage_difficulty=stage_difficulty,
                    stage_duration=stage_duration, bank_id=bank_id, tenant_id=tenant_id,
                    job_id=job_id, violations=[v.description for v in violations],
                )
            except Exception as repass_exc:
                logger.warning(
                    "question_bank.critic.repass_failed",
                    bank_id=str(bank_id), error_type=type(repass_exc).__name__,
                )
        working = hard_repair(
            working, stage_type=stage_type, stage_duration_minutes=stage_duration,
            required_primaries=set(coverage_plan.required_primaries) if coverage_plan else None,
        )
        if gate_codes:
            critique_note = (
                f"{critique_note} | gate: {', '.join(sorted(set(gate_codes)))} "
                "(re-pass + hard-repair applied)."
            )
        if coverage_plan is not None and coverage_plan.report:
            critique_note = f"{critique_note} | coverage: {coverage_plan.report}"
```

**(f)** In Phase C, where `bank.coverage_notes = critique_note` is set (~line 887), add right after it:
```python
            bank.coverage_feasibility = _feasibility_dict(coverage_plan)
```

**(g)** Update `_stream_bank_questions` signature + the `_build_user_message` call. Add the parameter to the signature (after `correlation_id`):
```python
    coverage_plan: "CoveragePlan | None" = None,
```
and pass it through to `_build_user_message`:
```python
            user_message = _build_user_message(
                job=job,
                snapshot=snapshot,
                company_profile=ctx.company_profile,
                stage=stage,
                pipeline_stages=ctx.pipeline_stages,
                prior_stages_questions=ctx.prior_stages_questions,
                coverage_plan=coverage_plan,
            )
```

**(h)** Update `_build_user_message` to render the plan for ai_screening. Add the parameter (after `prior_stages_questions`):
```python
    coverage_plan: "CoveragePlan | None" = None,
```
Then locate the `"\n# BUDGET FOR THIS STAGE ..."` block (~lines 229-249) and wrap it so the plan replaces the soft tier text when present:
```python
    if coverage_plan is not None:
        parts.append(
            "\n# COVERAGE PLAN FOR THIS STAGE (deterministic — follow exactly)\n"
        )
        parts.append(
            f"This ~{stage.duration_minutes}-minute screen fits about "
            f"{coverage_plan.slot_budget} SCORED questions. Produce EXACTLY ONE scored "
            "question per REQUIRED PRIMARY below — each as that question's `primary_signal` "
            "(this is what the report grades as a potential gap):\n"
        )
        for v in coverage_plan.required_primaries:
            parts.append(f"  - REQUIRED PRIMARY: {v!r}\n")
        if coverage_plan.bundle_eligible:
            parts.append(
                "\nWhere these related skills GENUINELY co-exercise in one realistic task, "
                "fold them into a scenario's `signal_values` (≤3 total) instead of spending a "
                "separate scored slot — only where coherent, never force unrelated skills "
                "together:\n"
            )
            for v in coverage_plan.bundle_eligible:
                parts.append(f"  - bundle-eligible: {v!r}\n")
        if coverage_plan.secondary_only:
            parts.append(
                "\nThese must-have skills could NOT fit as scored questions (the budget is "
                "full). Fold them in as secondaries where coherent, but do NOT expand the "
                "bank beyond the scored-question budget for them:\n"
            )
            for v in coverage_plan.secondary_only:
                parts.append(f"  - secondary-only: {v!r}\n")
        parts.append(
            "\nOptimize for SIGNAL DENSITY, not question count. Fewer, deeper, "
            "skill-revealing scenarios beat a long shallow list.\n"
        )
    else:
        # (existing soft weight-tier BUDGET block — unchanged for non-ai_screening stages)
        include_types = stage.signal_filter.get("include_types", [])
        eligible_signals = [
            s for s in snapshot.signals if s.get("type") in include_types
        ]
        # ... KEEP the rest of the existing block verbatim ...
```

> Implementation note: keep the existing eligibility-tier block exactly as-is inside the
> `else:` branch — do not delete it; only indent it under `else`. The `coverage_plan is None`
> path (phone_screen, legacy) must produce byte-identical guidance to today.

**(i)** Add `from app.modules.question_bank.coverage_planner import CoveragePlan` to the actor imports if not already added in (a) — needed for the type hints. (The string annotations `"CoveragePlan | None"` also work without a runtime import, but the import is already present from (a).)

- [ ] **Step 4: Run the wiring test + the full question_bank actor suite**

Run:
```bash
docker compose run --rm nexus pytest tests/test_question_banks_actors.py -q
```
Expected: PASS, including the new `coverage_feasibility` test. Fix any test that asserted the old `signals=` gate signature (search the file for `check_bank_invariants(` / `hard_repair(` usages and update to `plan=` / `required_primaries=`).

- [ ] **Step 5: Run the broader question_bank suite for regressions**

Run: `docker compose run --rm nexus pytest tests/question_bank tests/test_question_banks_actors.py -q -m "not prompt_quality"`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/modules/question_bank/actors.py tests/test_question_banks_actors.py
git commit -m "feat(question_bank): wire coverage planner into generation (inject + gate + persist)"
```

---

## Task 7: Recipe — density principle (no examples)

**Files:**
- Modify: `prompts/v3/question_bank_ai_screening.txt`

- [ ] **Step 1: Rewrite authoring step 1 + add the density principle**

In `prompts/v3/question_bank_ai_screening.txt`, replace authoring-recipe item **1** ("TECHNICAL SCENARIOS (the bulk)…") with:

```
1. SCORED SCENARIOS (the bulk). The user message gives you a COVERAGE PLAN listing the
   REQUIRED PRIMARY skills. Author exactly ONE `technical_scenario` per required primary,
   set as that question's `primary_signal` — this is the skill the report scores as a
   potential gap. Make the candidate USE the skill: design a workflow, debug a failing
   integration, transform a payload, reason about a data model under load, choose between
   approaches. NOT "have you used X" — make them DO X out loud. One self-contained spoken
   scenario per lead; depth ladders into the escalating follow-ups.
```

Then add a new section immediately after the "Authoring recipe" list (before "FORBIDDEN in this stage"):

```
# Density — cover more skills per scenario, WITHOUT diluting depth

`signal_values` (up to 3 per question) is your density vehicle; `primary_signal` is the ONE
skill that question is scored on. When the coverage plan marks skills as "bundle-eligible"
or "secondary-only", fold them into a scenario's `signal_values` *only where they genuinely
co-exercise in one realistic task* — e.g. a single task that naturally requires both
transforming a payload and persisting it touches two skills honestly.

WHY this matters: a forced bundle of unrelated skills produces a vague, two-questions-
crammed-into-one lead — it dilutes depth and reads badly to the candidate. Bundle only where
a real engineer would hit both skills in the same piece of work. If two skills do not
naturally meet in one task, keep them in separate scenarios (or, for a secondary-only skill,
let it ride lightly rather than distort a scenario). Never inflate the bank past the scored-
question budget in the plan.
```

- [ ] **Step 2: Sanity-check the prompt still loads**

Run: `docker compose run --rm nexus python -c "from app.ai.prompts import PromptLoader; from app.ai.config import ai_config; print(len(PromptLoader(version=ai_config.question_bank_prompt_version).load_pair('question_bank_common','question_bank_ai_screening')))"`
Expected: a positive integer (prompt pair loads).

- [ ] **Step 3: Commit**

```bash
git add prompts/v3/question_bank_ai_screening.txt
git commit -m "feat(question_bank): recipe teaches density principle (bundle where coherent)"
```

---

## Task 8: Prompt-quality evals (opt-in, real API)

**Files:**
- Modify/Create: `tests/question_bank/prompt_evals/` (mirror the existing eval file structure there)

- [ ] **Step 1: Add the coverage/density evals**

Find the existing bank prompt-eval module under `tests/question_bank/prompt_evals/` (created in the ai-screening-skills-test work) and add two `@pytest.mark.prompt_quality` tests. They generate a real bank for an over-subscribed synthetic JD and assert:

```python
import pytest

pytestmark = pytest.mark.prompt_quality


async def test_every_required_primary_is_scored_or_secondary_only(...):
    # Build a synthetic ai_screening stage (20 min) + a snapshot with ~8 weight>=2 skill
    # signals (mirror the harness the other prompt_quality bank evals use).
    plan = build_coverage_plan(skill_signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    bank = await _generate_real_bank(...)
    primaries = {q.primary_signal for q in bank}
    for sig in plan.required_primaries:
        assert sig in primaries, f"required primary {sig!r} not scored"
    # overflow must be acknowledged as secondary_only, never silently absent
    assert set(plan.secondary_only) == set(plan.secondary_only)  # documented in coverage_notes


async def test_density_bundling_present_for_oversubscribed_jd(...):
    bank = await _generate_real_bank(...)
    # At least one scored question bundles >1 skill via signal_values (density in action).
    assert any(len(q.signal_values) > 1 for q in bank)
```

> These are illustrative shapes — adapt argument plumbing to the existing prompt-eval harness
> in that directory. They are NOT run in CI; the user runs `pytest -m prompt_quality` when
> iterating prompts.

- [ ] **Step 2: Do NOT run by default; confirm collection only**

Run: `docker compose run --rm nexus pytest tests/question_bank/prompt_evals -q -m "not prompt_quality" --collect-only`
Expected: the new tests are collected but deselected (marked `prompt_quality`).

- [ ] **Step 3: Commit**

```bash
git add tests/question_bank/prompt_evals/
git commit -m "test(question_bank): opt-in evals — coverage of required primaries + density"
```

---

## Task 9: Frontend — coverage_feasibility type + recruiter badge

**Files:**
- Modify: `frontend/app/lib/api/question-banks.ts`
- Modify: `frontend/app/components/dashboard/question-bank/BankHeader.tsx`

- [ ] **Step 1: Add the type + field**

In `frontend/app/lib/api/question-banks.ts`, add a `CoverageFeasibility` type and a `coverage_feasibility` field on the bank response type (find the type carrying `coverage_notes` / `total_minutes` / `is_stale`):

```typescript
export type CoverageFeasibility = {
  feasible: boolean
  slot_budget: number
  must_cover_count: number
  secondary_only: string[]
  dropped: string[]
  recommended_minutes: number
}
```

Add to the bank response type (next to `coverage_notes`):
```typescript
  coverage_feasibility: CoverageFeasibility | null
```

- [ ] **Step 2: Render the badge (infeasible only)**

In `frontend/app/components/dashboard/question-bank/BankHeader.tsx`, mirror the existing `bank.is_stale` caution block. Add right after the `is_stale` block:

```tsx
        {bank.coverage_feasibility && !bank.coverage_feasibility.feasible && (
          <div
            className="mt-2 rounded-md border px-2 py-1 text-[11.5px]"
            style={{
              color: 'var(--px-caution)',
              background: 'var(--px-caution-bg)',
              borderColor: 'var(--px-caution)',
            }}
          >
            ⚠ {bank.coverage_feasibility.secondary_only.length} must-have skill
            {bank.coverage_feasibility.secondary_only.length === 1 ? '' : 's'} don’t fit this
            screen’s time budget — extend the stage to ~
            {bank.coverage_feasibility.recommended_minutes} min to score
            {' '}
            {bank.coverage_feasibility.secondary_only.join(', ')}
            {' '}as their own questions.
          </div>
        )}
```

> If `--px-caution-bg` / `--px-caution` are the tokens used by the `is_stale` block, reuse
> them exactly (copy from the adjacent block). Do not introduce new color tokens.

- [ ] **Step 3: Type-check + build**

Run (from `frontend/app/`):
```bash
npm run type-check && npm run build
```
Expected: both green. (If `BankWithQuestionsResponse` is a distinct type from the list type, ensure `coverage_feasibility` is on whichever type `BankHeader`'s `bank` prop uses — it is `BankWithQuestionsResponse`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/app/lib/api/question-banks.ts frontend/app/components/dashboard/question-bank/BankHeader.tsx
git commit -m "feat(question-bank): recruiter badge for over-subscribed coverage"
```

---

## Final verification

- [ ] **Backend — touched-area suite green**

Run: `docker compose run --rm nexus pytest tests/question_bank tests/test_question_banks_actors.py -q -m "not prompt_quality"`
Expected: all pass.

- [ ] **Module boundaries lint**

Run: `docker compose run --rm nexus pytest tests/test_module_boundaries.py -q`
Expected: pass (planner is intra-module; no new cross-module deep import).

- [ ] **Frontend green**

Run (from `frontend/app/`): `npm run type-check && npm run build`
Expected: both green.

- [ ] **Grep guard — no stale references**

Run: `grep -rn "uncovered_high_weight_skill" app/ tests/`
Expected: zero matches.

- [ ] **Live smoke (USER runs)** — regenerate the Workato bank (job `ce6dad9a-8903-4396-8f29-8e36da9bd2a3`, stage `2ea4f4a3-4199-4403-9e2b-744284c8233f`) after restarting `nexus-worker`; confirm the DB skill is now a scored `primary_signal` (or, if over-subscribed, appears in the `coverage_feasibility.secondary_only` badge with a recommended duration). Worker has no hot-reload — restart it first.

---

## Self-Review notes (author)

- **Spec coverage:** planner (§3.1)→T2; pre-gen injection (§3.2)→T6(h); generalized gate + coverage-aware trim (§3.3)→T3; persistence (§3.4)→T6(f); recipe (§3.5)→T7; feasibility surface (§3.6)→T4/T5/T9; config constant→T1; evals→T8. All spec sections mapped.
- **Type consistency:** `CoveragePlan` fields (`required_primaries`, `bundle_eligible`, `secondary_only`, `dropped`, `feasible`, `recommended_minutes`, `slot_budget`, `must_cover_count`, `report`) are identical across planner (T2), gate (T3), actor (T6), `_feasibility_dict` (T6b), schema `CoverageFeasibility` (T5 — omits `report`, which goes to `coverage_notes`, and omits `required_primaries`/`bundle_eligible` which the badge doesn't need: intentional). `check_bank_invariants(..., plan=)` and `hard_repair(..., required_primaries=)` signatures match between T3 definition and T6 call sites.
- **Removed symbol:** `uncovered_high_weight_skill` deleted in T3, test rewritten in T3, grep-guarded in Final verification.
</content>
