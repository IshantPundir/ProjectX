"""Opt-in prompt-quality eval: distinct follow-up dimensions + non-empty listen_for.

Run via:
    docker compose exec -T nexus pytest tests/question_bank/test_generation_quality.py \
        -m prompt_quality -v

These tests hit the REAL OpenAI API (slow, token-consuming). They are EXCLUDED from the
default test gate (``-m 'not prompt_quality'`` in pyproject.toml addopts).

Core invariants verified (the "stage safely x4" bug target):
  1. Every follow-up ``dimension`` slug across ALL questions in the generated bank is
     DISTINCT — no cross-question collision (this is exactly the bug that was fixed when
     the prompt gained governed dimensions).
  2. Every follow-up has a non-empty ``listen_for`` list.

Entry point: the Fallback path — builds system+user messages with a small but
representative hand-built signal set via the same PromptLoader + instructor path
the real actor uses. No DB session required; no fixture IDs needed. The test is
self-contained and runs wherever an OPENAI_API_KEY is set.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.modules.question_bank.schemas import GeneratedQuestion


pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Skip guard — clean skip when no API key is configured
# ---------------------------------------------------------------------------

def _skip_if_no_api_key() -> None:
    """Raise pytest.skip if the OpenAI API key is absent.

    Checks the same key the client factory reads (settings.openai_api_key),
    with a direct env-var fallback so the check works before the app settings
    are loaded.
    """
    # app.config.settings reads OPENAI_API_KEY from the environment.
    # We mirror that here without pulling in the full settings object
    # (which may fail to initialise in CI without a DB).
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set — skipping real-API eval")


# ---------------------------------------------------------------------------
# Representative test case: "rollout-flavored" signals that would have
# triggered the OLD duplicate-dimension bug (multiple distinct questions
# probing the same broad signal area → prior to the fix the generator
# recycled the same dimension slugs across questions)
# ---------------------------------------------------------------------------

_ROLE_TITLE = "Senior Integration Engineer"
_SENIORITY = "senior"
_COMPANY_PROFILE = {
    "about": "Enterprise iPaaS platform automating workflows for Fortune 500 clients.",
    "industry": "Technology",
    "hiring_bar": "high",
}

# 7 signals spanning different types and weights — enough that the LLM is
# pressured to produce multiple questions, each with follow-ups, which is
# exactly the scenario where the old prompt produced colliding dimension slugs.
_SIGNALS: list[dict[str, Any]] = [
    {
        "value": "Workato or similar iPaaS platform experience",
        "type": "experience",
        "priority": "required",
        "weight": 3,
        "knockout": True,
        "stage": "interview",
    },
    {
        "value": "REST API integration design",
        "type": "competency",
        "priority": "required",
        "weight": 3,
        "knockout": False,
        "stage": "interview",
    },
    {
        "value": "Error handling and retry strategy in integrations",
        "type": "competency",
        "priority": "required",
        "weight": 3,
        "knockout": False,
        "stage": "interview",
    },
    {
        "value": "Cross-functional collaboration with product and support",
        "type": "behavioral",
        "priority": "required",
        "weight": 2,
        "knockout": False,
        "stage": "interview",
    },
    {
        "value": "Technical documentation ownership",
        "type": "behavioral",
        "priority": "required",
        "weight": 2,
        "knockout": False,
        "stage": "interview",
    },
    {
        "value": "OAuth2 / API authentication patterns",
        "type": "competency",
        "priority": "required",
        "weight": 2,
        "knockout": False,
        "stage": "interview",
    },
    {
        "value": "Debugging and root-cause analysis of integration failures",
        "type": "competency",
        "priority": "preferred",
        "weight": 1,
        "knockout": False,
        "stage": "interview",
    },
]


def _build_user_message() -> str:
    """Build a self-contained user message mirroring actors.py::_build_user_message.

    Inlined here so the eval is decoupled from the production actor (the existing
    prompt_evals/test_bank_gen_evals.py does the same). Uses only the signals above —
    no DB lookup required.
    """
    parts: list[str] = []

    parts.append("# JOB CONTEXT\n\n")
    parts.append(f"Job title: {_ROLE_TITLE}\n")
    parts.append(f"Seniority: {_SENIORITY}\n")

    parts.append("\n# COMPANY PROFILE\n\n")
    for key in ("about", "industry", "hiring_bar"):
        if key in _COMPANY_PROFILE:
            parts.append(f"{key}: {_COMPANY_PROFILE[key]}\n")

    parts.append("\n# SIGNALS TO ASSESS (pinned snapshot)\n\n")
    parts.append(
        "Each signal is listed with its metadata. Use the `value` field exactly "
        "as-is in your question's `signal_values` output.\n\n"
    )
    for signal in _SIGNALS:
        parts.append(
            f"- value: {signal['value']!r}\n"
            f"  type: {signal['type']}\n"
            f"  priority: {signal['priority']}\n"
            f"  weight: {signal['weight']}\n"
            f"  knockout: {signal.get('knockout', False)}\n"
            f"  stage_tag: {signal['stage']}\n"
        )

    parts.append("\n# PIPELINE CONTEXT\n\n")
    parts.append("This pipeline has 1 stage. You are generating questions for STAGE 1.\n\n")
    parts.append(
        "## Stage 1 — AI Interview (CURRENT — you are generating this)\n"
        "  Type: ai_screening, Duration: 25 min, Difficulty: hard\n"
    )

    parts.append("\n# THIS STAGE'S METADATA\n\n")
    parts.append(
        "Name: AI Interview\n"
        "Type: ai_screening\n"
        "Duration: 25 min\n"
        "Difficulty: hard\n"
        "Signal type filter (include_types): ['competency', 'experience', 'credential', 'behavioral']\n"
        "Advance behavior: manual_review\n"
    )

    # Budget guidance block — mirrors actors.py
    eligible_knockouts = [s for s in _SIGNALS if s.get("knockout", False)]
    eligible_w3 = [
        s for s in _SIGNALS
        if int(s.get("weight", 1)) == 3 and not s.get("knockout", False)
    ]
    eligible_w2 = [s for s in _SIGNALS if int(s.get("weight", 1)) == 2]
    eligible_w1 = [s for s in _SIGNALS if int(s.get("weight", 1)) == 1]

    parts.append(
        "\n# BUDGET FOR THIS STAGE "
        "(soft guidance — optimize for signal density, not count)\n\n"
        "Target time for this phase: ~25 min\n"
        "Stage duration overall: 25 min\n\n"
        "Eligible signals (after include_types filter):\n"
        f"  - knockouts: {len(eligible_knockouts)} (each warrants ONE mandatory question)\n"
        f"  - weight=3 non-knockout: {len(eligible_w3)} (high-priority depth probes)\n"
        f"  - weight=2: {len(eligible_w2)} (depth probes)\n"
        f"  - weight=1: {len(eligible_w1)} (only if every higher-weight signal is covered)\n\n"
        "Optimize for SIGNAL DENSITY, not question count. "
        "Under-using the budget is fine; padding shallow questions is not.\n"
    )

    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


async def _generate_technical_bank() -> list[GeneratedQuestion]:
    """Run the real technical-phase bank generation using the v2 prompt pair.

    Mirrors the path in actors.py::_generate_questions_for_kind (technical phase):
      - Loads the prompt pair via PromptLoader at the current prompt_version.
      - Calls instructor's create_iterable with response_model=GeneratedQuestion.
      - Collects all streamed questions.
    No DB session is required; all context is self-contained in the user message.
    """
    loader = PromptLoader(version=ai_config.question_bank_prompt_version)
    system_prompt = loader.load_pair("question_bank_common", "question_bank_ai_screening")

    client = get_openai_client()
    call_kwargs: dict[str, Any] = dict(
        model=ai_config.question_bank_model,
        response_model=GeneratedQuestion,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_message()},
        ],
        max_retries=1,
    )
    if ai_config.question_bank_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_effort

    questions: list[GeneratedQuestion] = [
        q async for q in client.chat.completions.create_iterable(**call_kwargs)
    ]
    return questions


# ---------------------------------------------------------------------------
# Test: distinct dimensions + non-empty listen_for across the whole bank
# ---------------------------------------------------------------------------

async def test_followup_dimensions_distinct_and_listen_for_nonempty() -> None:
    """Core invariant eval for the governed follow-up dimensions feature.

    Asserts (for a technical-phase bank generated against the representative
    iPaaS integration signal set above):

    1. Every follow-up ``dimension`` slug across ALL questions is DISTINCT.
       The old bug ("stage safely x4"): the generator recycled dimension slugs
       across questions, so the live engine would silently skip follow-ups on
       questions after the first one that shared a slug. One global set covers
       all cross-question collisions.

    2. Every follow-up has a non-empty ``listen_for`` list (at least one
       observable specific). The Pydantic schema enforces min_length=1 on the
       field, but this test confirms the real LLM output respects it end-to-end
       through the full prompt+schema pipeline.
    """
    _skip_if_no_api_key()

    questions = await _generate_technical_bank()
    assert questions, (
        "generator returned zero questions for the representative integration-engineer case"
    )

    # --- Invariant 1: all dimension slugs are globally distinct ---
    slugs = [d.dimension for q in questions for d in q.follow_ups]
    assert len(slugs) == len(set(slugs)), (
        f"duplicate follow-up dimensions across questions: {slugs!r}\n"
        f"Questions: {[q.text for q in questions]}"
    )

    # --- Invariant 2: every follow-up has at least one listen_for entry ---
    for q in questions:
        for d in q.follow_ups:
            assert d.listen_for, (
                f"empty listen_for on question {q.text!r} / dimension {d.dimension!r}"
            )
