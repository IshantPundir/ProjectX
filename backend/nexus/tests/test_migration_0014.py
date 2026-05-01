"""ORM smoke tests for migration 0014 (Phase 3C.1).

Covers:
- Session ORM upgraded shape (round-trip insert with new column set).
- CandidateSessionToken ORM (round-trip insert).
- JobPipelineStage.otp_required_default — default false and explicit true.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import (
    CandidateSessionToken,
    Session,
)
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)


@pytest.mark.asyncio
async def test_session_round_trip(db):
    """Insert a Session with the upgraded column set; verify defaults."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    await db.flush()
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    assert session.id is not None
    assert session.state == "created"
    assert session.state_changed_at is not None
    assert session.created_at is not None
    assert session.updated_at is not None
    assert session.otp_required is False
    assert session.otp_attempts == 0
    assert session.consent_recorded_at is None
    assert session.otp_hash is None
    assert session.otp_issued_at is None
    assert session.otp_verified_at is None
    assert session.scheduled_for is None
    assert session.started_at is None
    assert session.completed_at is None
    assert session.livekit_room_name is None
    assert session.recording_s3_key is None


@pytest.mark.asyncio
async def test_candidate_session_token_round_trip(db):
    """Insert a CandidateSessionToken tied to a Session; verify defaults."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    await db.flush()
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    jti = uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(days=7)
    token = CandidateSessionToken(
        jti=jti,
        tenant_id=tenant.id,
        session_id=session.id,
        expires_at=expires,
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)

    assert token.jti == jti
    assert token.tenant_id == tenant.id
    assert token.session_id == session.id
    assert token.issued_at is not None
    assert token.expires_at is not None
    assert token.used_at is None
    assert token.used_ip is None
    assert token.used_user_agent is None
    assert token.superseded_at is None
    assert token.superseded_by is None


@pytest.mark.asyncio
async def test_stage_otp_required_default_defaults_to_false(db):
    """Stage created without otp_required_default gets False from server_default."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    await db.flush()
    _, stage = await make_assignment_with_stage(db, tenant, user, otp_default=False)

    await db.refresh(stage)
    assert stage.otp_required_default is False


@pytest.mark.asyncio
async def test_stage_otp_required_default_can_be_set_true(db):
    """Stage created with otp_required_default=True persists True."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    await db.flush()
    _, stage = await make_assignment_with_stage(db, tenant, user, otp_default=True)

    await db.refresh(stage)
    assert stage.otp_required_default is True
