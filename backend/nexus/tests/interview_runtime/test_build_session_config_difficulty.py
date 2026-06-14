"""B3 — QuestionConfig.difficulty field + stage-difficulty fallback in build_session_config.

Two sections:

(a) Pure unit tests for the QuestionConfig.difficulty field:
    - Explicit value accepted.
    - Omitted field defaults to "medium" (backward-compat for legacy banks).

(b) Integration test for the NULL → stage fallback in build_session_config:
    - Stage difficulty set to "easy".
    - Question row inserted without a per-question difficulty (NULL, the default).
    - build_session_config must propagate stage.difficulty → QuestionConfig.difficulty.

Integration harness mirrors test_signal_metadata_plumbing.py exactly.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sql_text

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.interview_runtime import build_session_config
from app.modules.interview_runtime.schemas import QuestionConfig
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.session.models import Session
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_VALID_PROFILE = {
    "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
    "industry": "Technology",
    "hiring_bar": "Senior engineers who own outcomes end to end.",
}


def _signal_dict(
    *,
    value: str,
    type_: str,
    priority: str,
    weight: int,
    knockout: bool,
    stage: str,
    evaluation_method: str | None = "verbal_response",
    evaluation_hint: str | None = None,
) -> dict:
    return {
        "value": value,
        "type": type_,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage,
        "evaluation_method": evaluation_method,
        "evaluation_hint": evaluation_hint,
        "source": "ai_extracted",
        "inference_basis": None,
    }


# ---------------------------------------------------------------------------
# (a) Pure unit tests for QuestionConfig.difficulty field
# ---------------------------------------------------------------------------


def test_question_config_accepts_explicit_difficulty():
    q = QuestionConfig(
        id="q1", position=0, text="A question about the topic, walk me through it.",
        signal_values=["s1"], follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric={"excellent": "x"*20, "meets_bar": "y"*20, "below_bar": "z"*20},
        evaluation_hint="Look for specifics here.", question_kind="technical_scenario",
        difficulty="hard",
    )
    assert q.difficulty == "hard"


def test_question_config_difficulty_defaults_to_medium_when_omitted():
    q = QuestionConfig(
        id="q1", position=0, text="A question about the topic, walk me through it.",
        signal_values=["s1"], follow_ups=[],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric={"excellent": "x"*20, "meets_bar": "y"*20, "below_bar": "z"*20},
        evaluation_hint="Look for specifics here.", question_kind="technical_scenario",
    )
    assert q.difficulty == "medium"


# ---------------------------------------------------------------------------
# (b) Integration test: NULL per-question difficulty falls back to stage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_session_config_difficulty_falls_back_to_stage(db):
    """Stage difficulty='easy', question row has difficulty=NULL.

    build_session_config must apply the fallback so that
    config.stage.questions[0].difficulty == 'easy'.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Difficulty Fallback Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for difficulty fallback test.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            _signal_dict(
                value="Python", type_="competency", priority="required",
                weight=3, knockout=False, stage="screen",
                evaluation_method="verbal_response",
            ),
        ],
        seniority_level="mid",
        role_summary="Role summary for difficulty fallback test.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()

    # Stage difficulty is explicitly set to "easy" — this is what should
    # be inherited by the question row whose difficulty column is NULL.
    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="AI Screen",
        stage_type="ai_screening",
        duration_minutes=20,
        difficulty="easy",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    rubric = {"excellent": "x" * 30, "meets_bar": "y" * 30, "below_bar": "z" * 30}
    # Intentionally leave difficulty unset (NULL default) to exercise the
    # NULL → stage fallback path in build_session_config.
    question = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        text="Tell me about your Python experience in depth.",
        signal_values=["Python"],
        estimated_minutes=2.0,
        is_mandatory=True,
        question_kind="technical_scenario",
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
        # difficulty is intentionally omitted — NULL in DB
    )
    db.add(question)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Evelyn",
        email=f"evelyn-{uuid.uuid4()}@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    config = await build_session_config(
        db, session_id=session.id, tenant_id=tenant.id,
    )

    # The question row had NULL difficulty; build_session_config must fall
    # back to the stage difficulty ("easy").
    assert len(config.stage.questions) == 1
    assert config.stage.questions[0].difficulty == "easy", (
        f"Expected 'easy' (stage fallback), got {config.stage.questions[0].difficulty!r}"
    )
