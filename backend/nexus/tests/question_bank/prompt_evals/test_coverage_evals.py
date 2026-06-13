"""Coverage-planner prompt-quality evals — over-subscribed ai_screening bank.

Opt-in tier: run via
    docker compose exec nexus pytest tests/question_bank/prompt_evals/test_coverage_evals.py -m prompt_quality

These tests hit the REAL OpenAI API (and are therefore SLOW and CONSUME TOKENS).
Do NOT include in the default test gate. The default addopts in pyproject.toml
already excludes them via ``-m 'not prompt_quality'``.

These two tests validate the coverage-planner + prompt integration for the
OVER-SUBSCRIBED case: a 20-minute ai_screening stage with 8 must-cover skills
(weight>=2) that exceeds the ~6-slot scored budget.

Test A — every required primary is scored OR explicitly secondary-only.
    Every CoveragePlan.required_primaries entry must appear as the primary_signal
    of at least one generated question, and every secondary_only entry must
    NOT be silently absent (it must appear in at least one question's
    signal_values as a co-signal, since the prompt instructs "fold them in as
    secondaries where coherent").

Test B — density principle fires.
    At least one question must bundle >1 skill via signal_values (len > 1),
    i.e. the prompt's "fold into a scenario's signal_values" instruction is
    actually followed in this over-subscribed case.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.modules.question_bank.coverage_planner import build_coverage_plan
from app.modules.question_bank.schemas import GeneratedQuestion


pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Synthetic over-subscribed scenario
# ---------------------------------------------------------------------------

def _mk_signal(
    value: str,
    *,
    sig_type: str = "competency",
    priority: str = "required",
    weight: int = 2,
    knockout: bool = False,
    stage_tag: str = "interview",
    purpose: str = "skill",
) -> dict[str, Any]:
    """Mirror of the same helper in test_bank_gen_evals.py."""
    return {
        "value": value,
        "type": sig_type,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage_tag,
        "purpose": purpose,
    }


# 8 must-cover skill signals on a 20-min stage.
# slot_budget = floor(20 / ai_config.question_bank_min_per_scored_slot_minutes)
# With the default 3.0 min/slot that gives 6 slots, so 2 signals will be
# secondary_only — an oversubscribed plan.
_OVERSUBSCRIBED_SIGNALS: list[dict[str, Any]] = [
    _mk_signal("Distributed systems design",             weight=3, knockout=True),
    _mk_signal("AWS production experience",              weight=3),
    _mk_signal("PostgreSQL at scale",                    weight=3),
    _mk_signal("Kubernetes cluster operations",          weight=2),
    _mk_signal("CI/CD pipeline ownership",               weight=2),
    _mk_signal("Observability and alerting (Datadog)",   weight=2),
    _mk_signal("Service mesh (Istio or Linkerd)",        weight=2),
    _mk_signal("Infrastructure-as-code (Terraform)",     weight=2),
]

_STAGE_DURATION = 20
_STAGE_DIFFICULTY = "hard"
_ROLE_TITLE = "Senior Platform Engineer"
_SENIORITY = "senior"
_COMPANY_PROFILE: dict[str, str] = {
    "about": "High-growth fintech running payments infrastructure at global scale.",
    "industry": "Financial services",
    "hiring_bar": "high",
}


# ---------------------------------------------------------------------------
# Coverage-plan-aware user message builder
# Mirrors actors.py::_build_user_message with coverage_plan rendering.
# Decoupled from the production actor so the eval is self-contained.
# ---------------------------------------------------------------------------

def _build_user_message_with_plan(
    signals: list[dict[str, Any]],
    *,
    role_title: str,
    seniority: str,
    company_profile: dict[str, str],
    stage_duration: int,
    stage_difficulty: str,
    coverage_plan,  # CoveragePlan
) -> str:
    parts: list[str] = []

    parts.append("# JOB CONTEXT\n\n")
    parts.append(f"Job title: {role_title}\n")
    parts.append(f"Seniority: {seniority}\n")

    if company_profile:
        parts.append("\n# COMPANY PROFILE\n\n")
        for key in ("about", "industry", "hiring_bar"):
            if key in company_profile:
                parts.append(f"{key}: {company_profile[key]}\n")

    parts.append("\n# SIGNALS TO ASSESS (pinned snapshot)\n\n")
    parts.append(
        "Each signal is listed with its metadata. Use the `value` field exactly "
        "as-is in your question's `signal_values` output.\n\n"
    )
    for signal in signals:
        parts.append(
            f"- value: {signal['value']!r}\n"
            f"  type: {signal['type']}\n"
            f"  priority: {signal['priority']}\n"
            f"  weight: {signal['weight']}\n"
            f"  knockout: {signal.get('knockout', False)}\n"
            f"  stage_tag: {signal['stage']}\n"
            f"  purpose: {signal.get('purpose', 'skill')}\n"
        )

    parts.append("\n# PIPELINE CONTEXT\n\n")
    parts.append("This pipeline has 1 stage. You are generating questions for STAGE 1.\n\n")
    parts.append(
        f"## Stage 1 — AI Interview (CURRENT — you are generating this)\n"
        f"  Type: ai_screening, Duration: {stage_duration} min, "
        f"Difficulty: {stage_difficulty}\n"
    )

    parts.append("\n# THIS STAGE'S METADATA\n\n")
    parts.append(
        f"Name: AI Interview\n"
        f"Type: ai_screening\n"
        f"Duration: {stage_duration} min\n"
        f"Difficulty: {stage_difficulty}\n"
        f"Signal type filter (include_types): ['competency', 'experience', 'credential', 'behavioral']\n"
        f"Advance behavior: manual_review\n"
    )

    # Render the coverage plan block (mirrors actors.py verbatim).
    parts.append(
        "\n# COVERAGE PLAN FOR THIS STAGE (deterministic — follow exactly)\n"
    )
    parts.append(
        f"This ~{stage_duration}-minute screen fits about "
        f"{coverage_plan.slot_budget} SCORED questions. Produce EXACTLY ONE scored "
        "question per REQUIRED PRIMARY below — each as that question's `primary_signal` "
        "(this is what the report grades as a potential gap):\n"
    )
    for v in coverage_plan.required_primaries:
        parts.append(f"  - REQUIRED PRIMARY: {v!r}\n")

    _secondary_set = set(coverage_plan.secondary_only)
    pure_bundle = [v for v in coverage_plan.bundle_eligible if v not in _secondary_set]
    if pure_bundle:
        parts.append(
            "\nWhere these related skills GENUINELY co-exercise in one realistic task, "
            "fold them into a scenario's `signal_values` (≤3 total) instead of spending a "
            "separate scored slot — only where coherent, never force unrelated skills "
            "together:\n"
        )
        for v in pure_bundle:
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

    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


async def _generate_oversubscribed() -> tuple[list[GeneratedQuestion], object]:
    """Generate a bank for the over-subscribed scenario.

    Returns (questions, coverage_plan). The coverage plan is computed
    deterministically (pure function, no LLM, no DB) so the test can
    inspect required_primaries / secondary_only without any DB setup.
    """
    coverage_plan = build_coverage_plan(
        _OVERSUBSCRIBED_SIGNALS,
        stage_duration_minutes=_STAGE_DURATION,
        min_per_scored_slot=ai_config.question_bank_min_per_scored_slot_minutes,
    )

    loader = PromptLoader(version=ai_config.question_bank_prompt_version)
    system_prompt = loader.load_pair("question_bank_common", "question_bank_ai_screening")

    user_message = _build_user_message_with_plan(
        _OVERSUBSCRIBED_SIGNALS,
        role_title=_ROLE_TITLE,
        seniority=_SENIORITY,
        company_profile=_COMPANY_PROFILE,
        stage_duration=_STAGE_DURATION,
        stage_difficulty=_STAGE_DIFFICULTY,
        coverage_plan=coverage_plan,
    )

    client = get_openai_client()
    call_kwargs: dict[str, Any] = dict(
        model=ai_config.question_bank_model,
        response_model=GeneratedQuestion,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_retries=1,
    )
    if ai_config.question_bank_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_effort

    questions: list[GeneratedQuestion] = [
        q async for q in client.chat.completions.create_iterable(**call_kwargs)
    ]
    return questions, coverage_plan


# ---------------------------------------------------------------------------
# Test A — every required primary is scored; every secondary_only is acknowledged
# ---------------------------------------------------------------------------

async def test_oversubscribed_required_primaries_are_scored() -> None:
    """For an over-subscribed ai_screening JD every required primary must be the
    primary_signal of at least one generated question.

    The coverage planner computes `required_primaries` deterministically from the
    signals and the time budget (pure function, no LLM). The prompt renders each
    required primary with the instruction "REQUIRED PRIMARY: …" so the LLM knows
    to score it. This test confirms the integration holds: the LLM actually
    honours the plan and produces a scored question for each required primary.

    Additionally, every `secondary_only` signal must appear somewhere in the
    generated bank's `signal_values` lists — the prompt says "fold them in as
    secondaries where coherent". A secondary that neither appears in any
    question's signal_values nor in any primary_signal would be silently absent,
    breaking the "never silently dropped" invariant the coverage planner reports.
    """
    questions, plan = await _generate_oversubscribed()
    assert questions, "generator returned zero questions for oversubscribed scenario"

    # The plan must be over-subscribed for this test to be meaningful.
    assert not plan.feasible, (
        f"expected an over-subscribed plan but got feasible=True "
        f"(slot_budget={plan.slot_budget}, must_cover_count={plan.must_cover_count})"
    )
    assert plan.secondary_only, (
        "expected at least one secondary_only skill in the over-subscribed plan"
    )

    primary_signals_emitted = {q.primary_signal for q in questions if q.primary_signal}
    all_signal_values_emitted: set[str] = set()
    for q in questions:
        all_signal_values_emitted.update(q.signal_values)

    # Every required primary must appear as some question's primary_signal.
    missing_primaries = [
        v for v in plan.required_primaries
        if v not in primary_signals_emitted
    ]
    assert not missing_primaries, (
        f"These REQUIRED_PRIMARY skills were not scored (missing as primary_signal): "
        f"{missing_primaries}\n"
        f"Primary signals emitted: {sorted(primary_signals_emitted)}\n"
        f"Coverage plan required_primaries: {plan.required_primaries}"
    )

    # Every secondary_only skill must appear in at least one question's signal_values
    # (as a co-signal — not necessarily as primary_signal).
    silently_absent = [
        v for v in plan.secondary_only
        if v not in all_signal_values_emitted
    ]
    assert not silently_absent, (
        f"These SECONDARY_ONLY skills were silently absent (not in any question's "
        f"signal_values): {silently_absent}\n"
        f"All signal_values emitted across the bank: {sorted(all_signal_values_emitted)}\n"
        f"Coverage plan secondary_only: {plan.secondary_only}"
    )


# ---------------------------------------------------------------------------
# Test B — density principle fires (at least one question bundles >1 skill)
# ---------------------------------------------------------------------------

async def test_oversubscribed_density_principle_fires() -> None:
    """For an over-subscribed JD at least one scored question must bundle more than
    one skill via signal_values (len(signal_values) > 1).

    The coverage plan renders both `required_primaries` and `secondary_only` /
    `bundle_eligible` skills. The prompt instructs the LLM to "fold [related skills]
    into a scenario's signal_values (≤3 total)" when they genuinely co-exercise in
    one realistic task. In an over-subscribed 8-skill scenario this bundling is not
    optional — the secondary-only skills can ONLY appear if they ride along in a
    multi-signal question. This test confirms the density instruction actually fires.
    """
    questions, plan = await _generate_oversubscribed()
    assert questions, "generator returned zero questions for oversubscribed scenario"

    # The plan must be over-subscribed for this test to be meaningful.
    assert not plan.feasible, (
        f"expected an over-subscribed plan but got feasible=True "
        f"(slot_budget={plan.slot_budget}, must_cover_count={plan.must_cover_count})"
    )

    bundled_questions = [q for q in questions if len(q.signal_values) > 1]
    assert bundled_questions, (
        f"density principle did not fire: no question has more than one signal_value "
        f"in an over-subscribed bank with {plan.must_cover_count} must-cover skills "
        f"competing for {plan.slot_budget} scored slots.\n"
        f"All signal_values per question: "
        f"{[q.signal_values for q in questions]}"
    )
