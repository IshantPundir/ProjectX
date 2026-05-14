"""ORM smoke tests for migration 0026 (Phase 4).

Covers:
- StageQuestion.question_kind default = 'technical_depth' on plain insert.
- All four engine-side Literal values round-trip through the column.
- CHECK constraint rejects an out-of-allowlist value.

Tested against the create_all-built test DB (see tests/conftest.py).
The CHECK constraint and server_default are mirrored on the ORM model
in app/modules/question_bank/models.py via __table_args__ +
server_default so this test file exercises the same behavior under
create_all that production gets via the raw-SQL Alembic migration.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


_VALID_PROFILE = {
    "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
    "industry": "Technology",
    "hiring_bar": "standard",
}


async def _seed_bank_and_question_kwargs(db) -> tuple[StageQuestionBank, dict]:
    """Build the minimum graph (client → user → org_unit → job → snapshot →
    instance → stage → bank) and return (bank, kwargs-for-StageQuestion)."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(
        __import__("sqlalchemy").text(
            f"SET LOCAL app.current_tenant = '{tenant.id}'"
        )
    )

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test Job",
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
                "value": "Python", "type": "competency", "priority": "required",
                "weight": 2, "knockout": False, "stage": "screen",
                "evaluation_method": "verbal_response",
                "evaluation_hint": None, "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

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
        signal_filter={"include_types": ["competency"]},
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
        status="draft",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    base_kwargs = dict(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Walk me through a production incident you handled.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric={
            "excellent": "x" * 25, "meets_bar": "y" * 25, "below_bar": "z" * 25,
        },
        evaluation_hint="Strong answer names tools, describes structured approach.",
    )
    return bank, base_kwargs


@pytest.mark.asyncio
async def test_question_kind_default_is_technical_depth(db):
    """Inserting a StageQuestion without question_kind reads back as 'technical_depth'."""
    _bank, base_kwargs = await _seed_bank_and_question_kwargs(db)
    question = StageQuestion(**base_kwargs)
    db.add(question)
    await db.flush()
    await db.refresh(question)
    assert question.question_kind == "technical_depth"


@pytest.mark.parametrize(
    "kind",
    ["technical_depth", "behavioral_star", "compliance_binary", "open_culture"],
)
@pytest.mark.asyncio
async def test_question_kind_accepts_each_allowlist_value(db, kind):
    """All 4 engine-side Literal values round-trip through the DB column."""
    _bank, base_kwargs = await _seed_bank_and_question_kwargs(db)
    question = StageQuestion(**base_kwargs, question_kind=kind)
    db.add(question)
    await db.flush()
    await db.refresh(question)
    assert question.question_kind == kind


@pytest.mark.asyncio
async def test_question_kind_check_rejects_invalid_value(db):
    """The CHECK constraint rejects any value outside the 4-value allowlist."""
    _bank, base_kwargs = await _seed_bank_and_question_kwargs(db)
    bad = StageQuestion(**base_kwargs, question_kind="not_a_real_kind")
    db.add(bad)
    with pytest.raises(IntegrityError):
        await db.flush()
