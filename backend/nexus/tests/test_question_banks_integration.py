"""End-to-end integration tests for the question_bank module.

These exercise multi-step flows by calling service-layer functions directly
(no HTTP transport). Goal: verify the seams between actor, service, state
machine, and DB hold up when chained together.

3 flows:
  1. Full happy path: create job + pipeline + confirmed signals → run
     `_generate_one_bank` with a mocked LLM → reviewing → patch one
     question (edited_by_recruiter=True) → confirm bank.
  2. Cascade delete: removing a JobPipelineStage cascades to its bank and
     all its questions via the FK ON DELETE CASCADE.
  3. Staleness detection: bank pinned to snapshot v1; create a confirmed v2
     → `compute_is_stale` returns True.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
import sqlalchemy
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.question_bank.models import (
    StageQuestion,
    StageQuestionBank,
)
from app.modules.question_bank.actors import _generate_one_bank
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    StageQuestionBankOutput,
    UpdateQuestionBody,
)
from app.modules.question_bank.service import (
    compute_is_stale,
    confirm_bank,
    ensure_bank_exists,
    get_bank_questions,
    recompute_and_persist_stale,
    transition_to_generating,
    update_question,
)
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "Fintech / Financial Services",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers (copied — keeps this file self-contained)
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


async def _setup_tenant_user_unit(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


def _signal(
    *,
    value: str,
    signal_type: str = "competency",
    priority: str = "required",
    weight: int = 2,
    knockout: bool = False,
    stage: str = "screen",
) -> dict:
    return {
        "value": value,
        "type": signal_type,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage,
        "evaluation_method": "verification",
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


async def _make_job_with_signals(
    db: AsyncSession,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
    *,
    signals: list[dict],
    version: int = 1,
    confirm: bool = True,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=version,
        signals=signals,
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id if confirm else None,
        confirmed_at=datetime.now(UTC) if confirm else None,
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_and_stage(
    db: AsyncSession,
    *,
    job: JobPosting,
    stage_type: str = "phone_screen",
    duration_minutes: int = 30,
    signal_filter: dict | None = None,
    pass_criteria: dict | None = None,
    advance_behavior: str = "auto_advance",
    difficulty: str = "medium",
    name: str = "Phone Screen",
    position: int = 0,
    instance: JobPipelineInstance | None = None,
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    if instance is None:
        instance = JobPipelineInstance(
            tenant_id=job.tenant_id,
            job_posting_id=job.id,
            source_template_id=None,
        )
        db.add(instance)
        await db.flush()

    stage = JobPipelineStage(
        tenant_id=job.tenant_id,
        instance_id=instance.id,
        position=position,
        name=name,
        stage_type=stage_type,
        duration_minutes=duration_minutes,
        difficulty=difficulty,
        signal_filter=signal_filter
        or {"include_types": ["competency", "experience", "credential", "behavioral"]},
        pass_criteria=pass_criteria or {"type": "all_knockouts_pass"},
        advance_behavior=advance_behavior,
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _mock_llm_output(
    signal_values: list[str],
    *,
    is_mandatory: bool = True,
    estimated_minutes: float = 5.0,
) -> StageQuestionBankOutput:
    return StageQuestionBankOutput(
        questions=[
            GeneratedQuestion(
                position=i,
                text=f"Tell me about your experience with {v} in production systems.",
                signal_values=[v],
                estimated_minutes=estimated_minutes,
                is_mandatory=is_mandatory,
                follow_ups=[f"What specifically did you use {v} for?"],
                positive_evidence=[
                    f"Names specific {v} tooling clearly",
                    "Describes production usage in detail",
                    "Mentions metrics or incidents handled",
                ],
                red_flags=[
                    f"Cannot describe {v} specifics or details",
                    "Only tutorial-level experience",
                ],
                rubric=QuestionRubric(
                    excellent=f"Strong {v} experience with production incidents handled.",
                    meets_bar=f"Basic {v} experience with one production deployment.",
                    below_bar=f"Only tutorial or POC {v} exposure with no real use.",
                ),
                evaluation_hint=f"Strong = production {v} usage with specific incidents.",
                question_kind="technical_depth",
            )
            for i, v in enumerate(signal_values)
        ],
    )


def _patch_llm(monkeypatch, output: StageQuestionBankOutput) -> None:
    fake_client = MagicMock()
    fake_create = AsyncMock(return_value=output)
    fake_client.chat.completions.create = fake_create
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_openai_client",
        lambda: fake_client,
    )


# ---------------------------------------------------------------------------
# 1. Full flow: create → generate → edit → confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_create_confirm_generate_edit_confirm(
    db: AsyncSession, monkeypatch
):
    """Walk a bank from draft → generating → reviewing (via _generate_one_bank
    with a mocked LLM) → recruiter edits one question → confirm_bank succeeds."""
    tenant, user, unit = await _setup_tenant_user_unit(db)

    # 4 signals — generates 4 ai questions of 5min each = 20min total,
    # which is inside the 15-45min budget for a 30min stage.
    signals = [
        _signal(value="Python", knockout=True),
        _signal(value="Apigee", knockout=True),
        _signal(value="Kubernetes"),
        _signal(value="PostgreSQL"),
    ]
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=signals,
    )
    instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=30,
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # --- Phase A: generate ---
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(
        monkeypatch,
        _mock_llm_output(
            ["Python", "Apigee", "Kubernetes", "PostgreSQL"],
            is_mandatory=True,
            estimated_minutes=5.0,
        ),
    )
    await _generate_one_bank(
        db,
        bank=bank,
        stage=stage,
        instance=instance,
        job=job,
        snapshot=snapshot,
        started_by=user.id,
    )
    await db.flush()
    assert bank.status == "reviewing"

    questions = await get_bank_questions(db, bank.id)
    assert len(questions) == 4
    assert all(q.source == "ai_generated" for q in questions)
    assert all(q.is_mandatory is True for q in questions)

    # --- Phase B: recruiter edits one question ---
    target = questions[0]
    body = UpdateQuestionBody(text="Updated python question text from recruiter.")
    updated = await update_question(
        db,
        question=target,
        bank=bank,
        body=body,
        user_id=user.id,
        user_email=user.email,
        snapshot=snapshot,
        allowed_types=stage.signal_filter["include_types"],
    )
    await db.flush()
    assert updated.edited_by_recruiter is True
    assert updated.text == "Updated python question text from recruiter."
    # Bank still in reviewing (auto_revert is a no-op when already reviewing)
    assert bank.status == "reviewing"

    # --- Phase C: confirm ---
    await confirm_bank(
        db, bank=bank, user_id=user.id, user_email=user.email
    )
    await db.flush()
    assert bank.status == "confirmed"
    assert bank.confirmed_at is not None
    assert bank.confirmed_by == user.id


# ---------------------------------------------------------------------------
# 2. Cascade delete: removing a stage drops the bank + its questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_delete_on_stage_removal(
    db: AsyncSession, monkeypatch
):
    """Deleting a JobPipelineStage row should cascade through the bank and
    all its questions via the FK ON DELETE CASCADE chain."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(
        monkeypatch,
        _mock_llm_output(["Python"], is_mandatory=False),
    )
    await _generate_one_bank(
        db,
        bank=bank,
        stage=stage,
        instance=instance,
        job=job,
        snapshot=snapshot,
        started_by=user.id,
    )
    await db.flush()

    bank_id = bank.id
    questions_before = await get_bank_questions(db, bank_id)
    assert len(questions_before) >= 1

    # Cascade delete via raw SQL DELETE on the stage. We expire all loaded
    # ORM state first so the session doesn't try to flush the stale rows
    # that were just removed at the DB level.
    await db.execute(
        delete(JobPipelineStage).where(JobPipelineStage.id == stage.id)
    )
    await db.flush()
    db.expire_all()

    # Bank should be gone
    bank_row = (
        await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
        )
    ).scalar_one_or_none()
    assert bank_row is None

    # All questions should be gone
    q_rows = (
        await db.execute(
            select(StageQuestion).where(StageQuestion.bank_id == bank_id)
        )
    ).scalars().all()
    assert list(q_rows) == []


# ---------------------------------------------------------------------------
# 3. Staleness detection after a new confirmed snapshot is created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staleness_detection_after_signal_edit(
    db: AsyncSession,
):
    """A bank pinned to snapshot v1 becomes stale once a confirmed v2 exists."""
    tenant, user, unit = await _setup_tenant_user_unit(db)

    # v1 confirmed snapshot
    job, v1 = await _make_job_with_signals(
        db,
        tenant.id,
        unit.id,
        user.id,
        signals=[_signal(value="Python")],
        version=1,
        confirm=True,
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    assert bank.signal_snapshot_id == v1.id

    # Initially not stale (it IS the latest)
    assert await compute_is_stale(db, bank) is False

    # Create v2, confirmed
    v2 = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=2,
        signals=[
            _signal(value="Python"),
            _signal(value="Kubernetes"),
        ],
        seniority_level="senior",
        role_summary="Senior backend engineer with k8s experience.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(v2)
    await db.flush()

    # Write-side: recompute and persist the column.
    await recompute_and_persist_stale(db, bank)

    # Now bank is pinned to v1, but latest confirmed is v2 → stale
    assert await compute_is_stale(db, bank) is True
