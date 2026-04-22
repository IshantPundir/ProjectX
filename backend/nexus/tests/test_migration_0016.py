"""ORM smoke tests for migration 0016 (stage type v5 + participants table).

Covers:
- PipelineStageParticipant ORM round-trip (all 7 columns).
- UNIQUE(stage_id, user_id, role) enforcement.
- ON DELETE CASCADE from stage.
- ON DELETE CASCADE from user.
- ON DELETE SET NULL from assigned_by.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    PipelineStageParticipant,
    User,
)
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


async def _make_minimum_graph(db):
    """Build: tenant -> org_unit -> user -> job_posting -> instance -> stage.

    Returns ``(tenant, user, stage)`` — the three objects needed by every
    participant test.  A second user can be created by the caller if the test
    needs a distinct ``assigned_by`` actor.
    """
    tenant = await create_test_client(db)
    await db.flush()

    user = await create_test_user(db, tenant.id)
    await db.flush()

    org_unit = await create_test_org_unit(db, tenant.id)
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="Staff Engineer",
        description_raw="D" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add(job)
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
        name="Technical Screen",
        stage_type="ai_interview",
        duration_minutes=45,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    db.add(stage)
    await db.flush()

    return tenant, user, stage


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_participant_insert_round_trips_all_columns(db):
    """Insert a PipelineStageParticipant with all 7 columns; verify they round-trip."""
    tenant, user, stage = await _make_minimum_graph(db)

    assigner = await create_test_user(db, tenant.id)
    await db.flush()

    participant = PipelineStageParticipant(
        tenant_id=tenant.id,
        stage_id=stage.id,
        user_id=user.id,
        role="interviewer",
        assigned_by=assigner.id,
    )
    db.add(participant)
    await db.flush()
    await db.refresh(participant)

    assert participant.id is not None
    assert participant.tenant_id == tenant.id
    assert participant.stage_id == stage.id
    assert participant.user_id == user.id
    assert participant.role == "interviewer"
    assert participant.assigned_by == assigner.id
    assert participant.assigned_at is not None


@pytest.mark.asyncio
async def test_unique_constraint_blocks_duplicate_tuple(db):
    """Inserting a duplicate (stage_id, user_id, role) tuple raises IntegrityError."""
    tenant, user, stage = await _make_minimum_graph(db)

    participant_a = PipelineStageParticipant(
        tenant_id=tenant.id,
        stage_id=stage.id,
        user_id=user.id,
        role="interviewer",
    )
    db.add(participant_a)
    await db.flush()

    participant_b = PipelineStageParticipant(
        tenant_id=tenant.id,
        stage_id=stage.id,
        user_id=user.id,
        role="interviewer",  # exact same tuple — must be rejected
    )
    db.add(participant_b)

    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_cascade_on_stage_delete(db):
    """Deleting the parent stage cascades and removes participant rows."""
    tenant, user, stage = await _make_minimum_graph(db)

    participant = PipelineStageParticipant(
        tenant_id=tenant.id,
        stage_id=stage.id,
        user_id=user.id,
        role="interviewer",
    )
    db.add(participant)
    await db.flush()

    # Confirm the row exists before the delete.
    result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.stage_id == stage.id
        )
    )
    assert result.scalars().first() is not None

    await db.delete(stage)
    await db.flush()

    result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.stage_id == stage.id
        )
    )
    assert result.scalars().first() is None, (
        "Participant row should have been cascaded away when stage was deleted"
    )


@pytest.mark.asyncio
async def test_cascade_on_user_delete(db):
    """Deleting the assigned user cascades and removes their participant rows.

    We create a *second* user (``participant_user``) who is the assignee on
    the participant row but has no other FK references, so the DELETE succeeds.
    The graph's primary ``user`` owns the job posting and must not be deleted.
    """
    tenant, user, stage = await _make_minimum_graph(db)

    # A dedicated user for the participant — no other FK references hold this one.
    participant_user = await create_test_user(db, tenant.id)
    await db.flush()

    participant = PipelineStageParticipant(
        tenant_id=tenant.id,
        stage_id=stage.id,
        user_id=participant_user.id,
        role="interviewer",
    )
    db.add(participant)
    await db.flush()

    participant_id = participant.id

    # Confirm row exists before delete.
    result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.id == participant_id
        )
    )
    assert result.scalars().first() is not None

    await db.delete(participant_user)
    await db.flush()

    result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.id == participant_id
        )
    )
    assert result.scalars().first() is None, (
        "Participant row should have been cascaded away when user_id user was deleted"
    )


@pytest.mark.asyncio
async def test_assigned_by_set_null_on_user_delete(db):
    """Deleting the assigning user sets assigned_by to NULL; participant row survives."""
    tenant, user, stage = await _make_minimum_graph(db)

    # A second user who performs the assignment — this is the one we delete.
    assigner = await create_test_user(db, tenant.id)
    await db.flush()

    participant = PipelineStageParticipant(
        tenant_id=tenant.id,
        stage_id=stage.id,
        user_id=user.id,        # the assigned user — will NOT be deleted
        role="interviewer",
        assigned_by=assigner.id,  # the assigning user — will BE deleted
    )
    db.add(participant)
    await db.flush()

    participant_id = participant.id

    # Delete the assigning user only.
    await db.delete(assigner)
    await db.flush()

    # Participant row must still exist (user.id is untouched).
    result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.id == participant_id
        )
    )
    surviving = result.scalars().first()
    assert surviving is not None, (
        "Participant row should survive deletion of the assigned_by user"
    )

    await db.refresh(surviving)
    assert surviving.assigned_by is None, (
        "assigned_by should be SET NULL after the assigning user was deleted"
    )
