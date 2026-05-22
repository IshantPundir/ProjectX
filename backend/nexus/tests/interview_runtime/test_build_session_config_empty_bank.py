"""Safety gate: build_session_config must raise QuestionBankNotReadyError when
a bank is confirmed but has zero questions (e.g. after a dev-mode clear-and-
regenerate that emptied stage_questions without resetting the bank status).

Seed mirrors test_build_session_config_difficulty.py exactly — full valid
session graph — EXCEPT no StageQuestion rows are inserted.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sql_text

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.interview_runtime import build_session_config
from app.modules.interview_runtime.errors import QuestionBankNotReadyError
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestionBank
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


@pytest.mark.asyncio
async def test_confirmed_but_empty_bank_is_not_ready(db):
    """build_session_config must raise QuestionBankNotReadyError when the bank
    is status='confirmed', is_stale=False, but has zero StageQuestion rows.

    This is the safety gate against migration 0045's clear-and-regenerate
    leaving 'confirmed' banks with no questions — which would previously
    dispatch a candidate into a zero-question interview.
    """
    # ---- tenant + user + company org_unit (with company_profile) ----
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))

    # ---- job + confirmed snapshot ----
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Empty Bank Safety Gate Test",
        description_raw="A" * 200,
        description_enriched="Enriched description for empty bank test.",
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

    # ---- pipeline + stage + bank (NO questions added) ----
    instance = JobPipelineInstance(
        tenant_id=tenant.id, job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="AI Screen",
        stage_type="ai_screening",
        duration_minutes=20,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    # Bank is confirmed, not stale — but has ZERO StageQuestion rows.
    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        is_stale=False,
        prompt_version="v1",
    )
    db.add(bank)
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

    # ---- assert the gate fires ----
    with pytest.raises(QuestionBankNotReadyError):
        await build_session_config(db, session_id=session.id, tenant_id=tenant.id)
