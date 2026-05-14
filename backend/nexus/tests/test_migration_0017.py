"""Migration 0017: stage_questions.updated_at auto-refresh trigger.

Tests that a BEFORE UPDATE trigger on stage_questions bumps updated_at on
every raw-SQL UPDATE, independently of whether the ORM onupdate fires.

Because the test suite uses Base.metadata.create_all (not Alembic) to set up
the test database, the trigger is created inline at test start via raw SQL —
exactly the same SQL the migration executes.  The transaction is rolled back
after the test, so the trigger is cleaned up automatically.

Red phase (before this file exists): the test fails because the trigger
function does not exist, so updated_at is not bumped on raw UPDATE.
Green phase (after the migration is applied and the trigger SQL is wired in
here): the assertion passes.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

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
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PROFILE = {
    "about": "Minimal seed company for trigger test.",
    "industry": "Fintech / Financial Services",
    "hiring_bar": "High bar.",
}


async def _build_fk_chain(db):
    """Build the minimal FK graph needed to INSERT into stage_questions.

    Returns (tenant_id, question_bank) — the question bank is already flushed
    so its `id` can be used as `bank_id` in a stage_questions INSERT.

    Chain:
        clients → organizational_units → users
        → job_postings → job_posting_signal_snapshots
        → job_pipeline_instances → job_pipeline_stages
        → stage_question_banks
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE
    )
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="Trigger Test Job",
        description_raw="D" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[],
        seniority_level="mid",
        role_summary="Minimal snapshot for trigger test.",
        prompt_version="v1",
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="AI Screen",
        stage_type="ai_screening",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
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

    return tenant.id, bank


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_bumps_updated_at_on_raw_sql_update(db):
    """Raw SQL UPDATE (bypassing ORM onupdate) must still bump updated_at.

    The trigger function (touch_updated_at) and the stage_questions trigger
    are created here using the same SQL as migration 0017, so the test can
    run against the test DB (which uses create_all, not Alembic).  The
    transaction rollback at test teardown removes both.
    """
    # Step 1: create the trigger function + trigger (mirrors migration 0017).
    # Uses clock_timestamp() rather than NOW() so the timestamp advances even
    # within a single transaction (NOW() is pinned to transaction start time).
    await db.execute(text("""
        CREATE OR REPLACE FUNCTION touch_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))

    await db.execute(text("""
        CREATE TRIGGER stage_questions_touch_updated_at
            BEFORE UPDATE ON stage_questions
            FOR EACH ROW
            EXECUTE FUNCTION touch_updated_at()
    """))

    # Step 2: build the FK chain and get a bank to hang a question on.
    tenant_id, bank = await _build_fk_chain(db)

    # Step 3: insert a stage_questions row via ORM (server_default sets updated_at).
    question = StageQuestion(
        tenant_id=tenant_id,
        bank_id=bank.id,
        position=1,
        source="generated",
        text="original question text",
        signal_values=["signal_one"],
        estimated_minutes=3.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric={},
        evaluation_hint="hint",
        edited_by_recruiter=False,
    )
    db.add(question)
    await db.flush()
    await db.refresh(question)

    before = question.updated_at

    # Step 4: guarantee NOW() ticks forward (asyncpg statement clock may be
    # sub-millisecond on a fast host).
    await asyncio.sleep(0.02)

    # Step 5: raw SQL UPDATE — bypasses SQLAlchemy's onupdate entirely.
    await db.execute(
        text("UPDATE stage_questions SET text = 'edited text' WHERE id = :qid"),
        {"qid": question.id},
    )

    # Step 6: re-read from DB to see what the trigger wrote.
    after = (
        await db.execute(
            text("SELECT updated_at FROM stage_questions WHERE id = :qid"),
            {"qid": question.id},
        )
    ).scalar_one()

    assert after > before, (
        f"trigger did not bump updated_at on raw UPDATE: before={before} after={after}"
    )
    assert (after - before) < timedelta(seconds=5), (
        f"updated_at jumped unexpectedly far: before={before} after={after}"
    )
