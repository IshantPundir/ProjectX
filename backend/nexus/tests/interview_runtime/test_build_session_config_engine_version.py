"""Verify build_session_config resolves SessionConfig.interview_engine_version.

Task 5 of the Interview Engine v2 Milestone 1 plan. The selector is:

    job.interview_engine_version or ai_config.interview_engine_default_version

so a per-job override wins, otherwise the global env default applies.

Three cases:

1. Job column NULL + default env -> "v1" (the schema/Settings default).
2. Job column set to "v2" -> "v2" (per-job override wins).
3. Job column NULL + INTERVIEW_ENGINE_DEFAULT_VERSION=v2 -> "v2"
   (global default applies when there's no per-job override).

Mirrors the seed style in ``test_build_session_config_keyterms.py``: build the
session graph inline against the ``db`` fixture from ``tests/conftest.py``,
then call ``build_session_config`` directly. The test DB schema is built from
``Base.metadata.create_all`` (see ``tests/conftest.py``), so the JobPosting
``interview_engine_version`` ORM column is present without running migrations.
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

    Returns ``(session_id, tenant_id, job_id)`` — the engine-version tests need
    the session + tenant handles to call ``build_session_config`` and the job
    handle to mutate the ``interview_engine_version`` column directly. The job
    column is left NULL here (the ORM default); the override case sets it.
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
        title="Engine Version Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for engine-version test.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
        # interview_engine_version intentionally left unset -> NULL in DB.
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
            ),
        ],
        seniority_level="senior",
        role_summary="Role summary for engine-version test.",
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
        text="Tell me about your Python experience.",
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
    )
    db.add(question)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Maya",
        email=f"maya-{uuid.uuid4()}@example.com",
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

    return session.id, tenant.id, job.id


@pytest.mark.asyncio
async def test_engine_version_defaults_to_v1(db) -> None:
    """Job column NULL + default env -> SessionConfig.interview_engine_version == 'v1'."""
    session_id, tenant_id, _job_id = await _seed_session_graph(db)

    config = await build_session_config(
        db, session_id=session_id, tenant_id=tenant_id,
    )
    assert config.interview_engine_version == "v1"


@pytest.mark.asyncio
async def test_engine_version_job_override_v2(db) -> None:
    """Job column set to 'v2' wins over the global default."""
    session_id, tenant_id, job_id = await _seed_session_graph(db)

    await db.execute(
        update(JobPosting)
        .where(JobPosting.id == job_id)
        .values(interview_engine_version="v2"),
    )
    await db.flush()

    config = await build_session_config(
        db, session_id=session_id, tenant_id=tenant_id,
    )
    assert config.interview_engine_version == "v2"


@pytest.mark.asyncio
async def test_engine_version_global_default_v2_when_job_null(db, monkeypatch) -> None:
    """Job column NULL + INTERVIEW_ENGINE_DEFAULT_VERSION=v2 -> 'v2'."""
    monkeypatch.setenv("INTERVIEW_ENGINE_DEFAULT_VERSION", "v2")
    session_id, tenant_id, _job_id = await _seed_session_graph(db)

    config = await build_session_config(
        db, session_id=session_id, tenant_id=tenant_id,
    )
    assert config.interview_engine_version == "v2"
