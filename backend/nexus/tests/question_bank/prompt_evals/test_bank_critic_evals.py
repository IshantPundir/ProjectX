"""Critic prompt-quality eval: the critic must catch + fix a planted defect.

Opt-in: docker compose exec nexus pytest tests/question_bank/prompt_evals/test_bank_critic_evals.py -m prompt_quality
Hits the REAL OpenAI API.
"""
from __future__ import annotations

import uuid

import pytest

from app.modules.question_bank.critic import run_bank_critic
from app.modules.question_bank.schemas import (
    FollowUpDimension,
    GeneratedQuestion,
    QuestionRubric,
)

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


def _q(pos: int, text: str, kind: str, dim: str, probe: str) -> GeneratedQuestion:
    """Build a valid GeneratedQuestion with a single follow-up dimension.

    All field lengths are chosen to satisfy the schema validators:
    - text: 10–240 chars
    - rubric bands: ≥20 chars each
    - positive_evidence: 3–5 items
    - red_flags: 2–3 items
    - evaluation_hint: 10–200 chars
    - follow_ups[*].listen_for: non-empty (GeneratedQuestion validator)
    """
    return GeneratedQuestion(
        position=pos,
        text=text,
        primary_signal="Kubernetes in production",
        signal_values=["Kubernetes in production"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[
            FollowUpDimension(
                dimension=dim,
                intent="Probe for production depth on this dimension.",
                seed_probe=probe,
                listen_for=["a specific tool", "a concrete number"],
            )
        ],
        positive_evidence=[
            "names a specific tool or system",
            "states a concrete number or metric",
            "uses first-person ownership ('I did X')",
        ],
        red_flags=[
            "uses 'we' without claiming personal ownership",
            "gives only a vague or hypothetical answer",
        ],
        rubric=QuestionRubric(
            excellent=(
                "Names the specific failure mode, the root cause, and the fix applied; "
                "states measurable impact."
            ),
            meets_bar=(
                "Describes a real incident with at least one concrete detail and "
                "a clear personal role."
            ),
            below_bar=(
                "Vague or entirely hypothetical; no specific tools, numbers, or "
                "personal ownership visible."
            ),
        ),
        evaluation_hint="Tests real Kubernetes production depth and incident ownership.",
        question_kind=kind,  # type: ignore[arg-type]
    )


async def test_critic_flags_duplicate_dimension_and_missing_deepdive() -> None:
    """Plant two probes with the SAME dimension slug + no project_deepdive for a senior
    role. The critic must fix the duplicate and the senior bank must end with exactly one
    project_deepdive."""
    draft = [
        _q(
            0,
            "Tell me about running Kubernetes in production.",
            "technical_scenario",
            "failure_handling",
            "What specifically broke and how did you personally fix it?",
        ),
        _q(
            1,
            "How do you keep a Kubernetes cluster healthy under sustained load?",
            "technical_scenario",
            "failure_handling",  # intentional duplicate dimension slug
            "What specific runbook or alert threshold did you own?",
        ),
    ]
    corrected, critique = await run_bank_critic(
        draft=draft,
        seniority="senior",
        role_title="Senior SRE",
        signals=[
            {
                "value": "Kubernetes in production",
                "type": "competency",
                "priority": "required",
                "weight": 3,
                "knockout": True,
                "stage": "interview",
            }
        ],
        stage_difficulty="hard",
        stage_duration=20,
        bank_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
    )

    # Critic must de-duplicate follow-up dimension slugs across the corrected bank.
    dims = [fu.dimension for q in corrected for fu in q.follow_ups]
    assert len(dims) == len(set(dims)), (
        f"critic left duplicate dimensions in the corrected bank: {dims}"
    )

    # Critic must insert a project_deepdive for the senior role (one, not zero or many).
    kinds = [q.question_kind for q in corrected]
    assert kinds.count("project_deepdive") == 1, (
        f"critic did not ensure a single project_deepdive for a senior bank; "
        f"kinds={kinds}. critique={critique}"
    )
