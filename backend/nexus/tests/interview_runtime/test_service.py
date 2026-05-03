"""Tests for interview_runtime.service — Phase 4: build_session_config
reads StageQuestion.question_kind into QuestionConfig.question_kind.

This is the first test of build_session_config in the codebase, so the
fixture composition is inlined for self-containment. Future tests of
the same function should factor out a shared helper if more than two
land here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sql_text

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.interview_runtime import build_session_config
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
    "company_stage": "Series C",
    "hiring_bar": "Senior engineers who own outcomes end to end.",
}


@pytest.mark.asyncio
async def test_build_session_config_reads_question_kind(db):
    """build_session_config plumbs each StageQuestion's question_kind into
    the corresponding QuestionConfig.question_kind. Tests with a mix of
    default-kind and non-default-kind rows so the read path is exercised
    for every Literal value the engine cares about."""
    # ---- tenant + user + company org_unit (with company_profile) ----
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(
        sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'")
    )

    # ---- job + confirmed snapshot ----
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="UK Customer Support Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
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
            {
                "value": "UK shift", "type": "experience", "priority": "required",
                "weight": 3, "knockout": True, "stage": "screen",
                "evaluation_method": "verbal_response", "evaluation_hint": None,
                "source": "ai_extracted", "inference_basis": None,
            },
            {
                "value": "Conflict resolution", "type": "behavioral",
                "priority": "preferred", "weight": 2, "knockout": False,
                "stage": "interview", "evaluation_method": "behavioral_question",
                "evaluation_hint": None, "source": "ai_extracted",
                "inference_basis": None,
            },
            {
                "value": "Python", "type": "competency", "priority": "preferred",
                "weight": 2, "knockout": False, "stage": "screen",
                "evaluation_method": "verbal_response", "evaluation_hint": None,
                "source": "ai_extracted", "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="Customer support engineer role for UK enterprise.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    # ---- pipeline + stage + bank + 3 questions with mixed kinds ----
    instance = JobPipelineInstance(
        tenant_id=tenant.id, job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "experience", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
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

    rubric = {
        "excellent": "x" * 30, "meets_bar": "y" * 30, "below_bar": "z" * 30,
    }
    base_q = dict(
        tenant_id=tenant.id,
        bank_id=bank.id,
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
    )
    q0 = StageQuestion(
        position=0, text="Can you work UK shift (1pm-9pm)?",
        signal_values=["UK shift"], estimated_minutes=1.5, is_mandatory=True,
        question_kind="compliance_binary",
        **base_q,
    )
    q1 = StageQuestion(
        position=1, text="Tell me about a time you handled a tough peer conflict.",
        signal_values=["Conflict resolution"], estimated_minutes=4.0, is_mandatory=False,
        question_kind="behavioral_star",
        **base_q,
    )
    q2 = StageQuestion(
        position=2, text="Walk me through your last Python production debug.",
        signal_values=["Python"], estimated_minutes=4.0, is_mandatory=False,
        question_kind="technical_depth",
        **base_q,
    )
    db.add_all([q0, q1, q2])
    await db.flush()

    # ---- candidate + assignment + session ----
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Charlie",
        email=f"charlie-{uuid.uuid4()}@example.com",
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

    # ---- exercise the read path ----
    config = await build_session_config(
        db, session_id=session.id, tenant_id=tenant.id,
    )

    kinds_by_position = {q.position: q.question_kind for q in config.stage.questions}
    assert kinds_by_position[0] == "compliance_binary"
    assert kinds_by_position[1] == "behavioral_star"
    assert kinds_by_position[2] == "technical_depth"
