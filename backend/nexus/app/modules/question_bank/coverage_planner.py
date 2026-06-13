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
