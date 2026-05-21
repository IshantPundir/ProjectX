"""Verify SessionConfig.keyterms is propagated from stage_question_banks.extracted_keyterms.

Task 8 of the Deepgram keyterm-migration plan (spec
``docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md``).

Two cases:

1. Column populated -> ``SessionConfig.keyterms`` reflects the list verbatim.
2. Column NULL (legacy banks pre-dating migration 0041 or freshly re-generated
   banks where extraction is skipped) -> ``SessionConfig.keyterms`` is the
   empty list; the engine then falls back to candidate-name-only boosting.

Mirrors the seed style in ``test_signal_metadata_plumbing.py``: build the
session graph inline against the ``db`` fixture from ``tests/conftest.py``,
then call ``build_session_config`` directly.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy import update

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
) -> dict:
    return {
        "value": value,
        "type": type_,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage,
        "evaluation_method": evaluation_method,
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


async def _seed_session_graph(db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Build a minimal session/assignment/candidate/job/stage/bank graph.

    Returns ``(session_id, tenant_id, bank_id)`` — the three handles the
    keyterm tests need to (a) call ``build_session_config`` and (b) mutate
    the bank's ``extracted_keyterms`` column directly.
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
        title="Integration Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for keyterm test.",
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
                value="MuleSoft", type_="competency", priority="required",
                weight=3, knockout=False, stage="screen",
            ),
        ],
        seniority_level="senior",
        role_summary="Integration engineer role for keyterm test.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="AI Screen",
        stage_type="ai_screening",
        duration_minutes=15,
        difficulty="medium",
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
    question = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        text="Tell me about your MuleSoft experience.",
        signal_values=["MuleSoft"],
        estimated_minutes=2.0,
        is_mandatory=True,
        question_kind="technical_scenario",
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
    )
    db.add(question)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Riya",
        email=f"riya-{uuid.uuid4()}@example.com",
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

    return session.id, tenant.id, bank.id


@pytest.mark.asyncio
async def test_session_config_keyterms_populated_when_column_set(db) -> None:
    """When extracted_keyterms is set on the bank row, SessionConfig.keyterms reflects it."""
    session_id, tenant_id, bank_id = await _seed_session_graph(db)

    await db.execute(
        update(StageQuestionBank)
        .where(StageQuestionBank.id == bank_id)
        .values(extracted_keyterms=["MuleSoft", "TIBCO", "Boomi"]),
    )
    await db.flush()

    config = await build_session_config(
        db, session_id=session_id, tenant_id=tenant_id,
    )
    assert config.keyterms == ["MuleSoft", "TIBCO", "Boomi"]


@pytest.mark.asyncio
async def test_session_config_keyterms_empty_when_column_null(db) -> None:
    """When extracted_keyterms IS NULL, SessionConfig.keyterms is [] (engine fallback)."""
    # By default the bank row is created without extracted_keyterms — null.
    session_id, tenant_id, _bank_id = await _seed_session_graph(db)

    config = await build_session_config(
        db, session_id=session_id, tenant_id=tenant_id,
    )
    assert config.keyterms == []
