"""Session recording playback service — reconcile, presign, tenant isolation."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.session import recording as recording_mod
from app.modules.session.errors import SessionNotFoundError
from app.modules.session.livekit import EgressSnapshot
from app.modules.session.models import Session as SessionRow
from app.modules.session.schemas import SessionState
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)

pytestmark = pytest.mark.asyncio


async def _seed_session(
    db, *, recording_status, egress_id=None, key=None, transcript=None, agent_completed_at=None
):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)
    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
        state=SessionState.COMPLETED.value,
        livekit_room_name="session-test",
        recording_status=recording_status,
        recording_egress_id=egress_id,
        recording_s3_key=key,
        transcript=transcript,
        agent_completed_at=agent_completed_at,
    )
    db.add(sess)
    await db.flush()
    return tenant.id, sess.id


@pytest.fixture
def fake_storage(monkeypatch):
    storage = MagicMock()
    storage.presign_get_url = AsyncMock(return_value="https://signed.example/v.mp4?sig=x")
    storage.head = AsyncMock(return_value=None)
    monkeypatch.setattr(recording_mod, "get_object_storage", lambda: storage)
    return storage


async def test_ready_recording_returns_signed_url_and_transcript(db, fake_storage):
    tenant_id, session_id = await _seed_session(
        db,
        recording_status="ready",
        key="recordings/t/s.mp4",
        transcript=[
            {"role": "agent", "text": "Hi", "timestamp_ms": 0, "question_id": None},
            {"role": "candidate", "text": "Hello", "timestamp_ms": 1500},
        ],
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "ready"
    assert out.signed_url == "https://signed.example/v.mp4?sig=x"
    assert out.expires_at is not None
    assert [s.text for s in out.transcript] == ["Hi", "Hello"]
    fake_storage.presign_get_url.assert_awaited_once()


async def test_recording_in_progress_reconciles_to_ready(db, fake_storage, monkeypatch):
    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", egress_id="eg_1", key="recordings/t/s.mp4"
    )
    monkeypatch.setattr(
        recording_mod,
        "get_recording_status",
        AsyncMock(
            return_value=EgressSnapshot(
                status="ready",
                egress_id="eg_1",
                key="recordings/t/s.mp4",
                duration_seconds=620,
                size_bytes=12345,
            )
        ),
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "ready"
    assert out.duration_seconds == 620
    # Row advanced + persisted.
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "ready"
    assert row.recording_ready_at is not None
    assert row.recording_bytes == 12345


async def test_recording_still_processing_stays_recording(db, monkeypatch):
    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", egress_id="eg_1"
    )
    monkeypatch.setattr(
        recording_mod,
        "get_recording_status",
        AsyncMock(
            return_value=EgressSnapshot(
                status="recording", egress_id="eg_1", key=None,
                duration_seconds=None, size_bytes=None,
            )
        ),
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "recording"
    assert out.signed_url is None


async def test_absent_recording_returns_absent(db):
    tenant_id, session_id = await _seed_session(db, recording_status="absent")
    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )
    assert out.status == "absent"
    assert out.signed_url is None


async def test_cross_tenant_access_raises_not_found(db, fake_storage):
    """A recording must never be reachable from another tenant's context."""
    _, session_id = await _seed_session(
        db, recording_status="ready", key="recordings/t/s.mp4"
    )
    other_tenant = uuid.uuid4()

    with pytest.raises(SessionNotFoundError):
        await recording_mod.get_session_recording_playback(
            db, session_id=session_id, tenant_id=other_tenant
        )
    fake_storage.presign_get_url.assert_not_awaited()


async def test_reconcile_swallows_livekit_errors(db, monkeypatch):
    """A transient LiveKit failure during reconcile leaves the row unchanged."""
    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", egress_id="eg_1"
    )
    monkeypatch.setattr(
        recording_mod,
        "get_recording_status",
        AsyncMock(side_effect=RuntimeError("livekit down")),
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )
    assert out.status == "recording"


async def test_no_egress_but_object_in_storage_marks_ready(db, fake_storage, monkeypatch):
    """Egress record gone (purged or never created) but the MP4 exists in R2 →
    resolve to ready from storage, not an eternal spinner."""
    from app.storage.base import ObjectMeta

    tenant_id, session_id = await _seed_session(db, recording_status="recording")
    monkeypatch.setattr(
        recording_mod, "get_recording_status", AsyncMock(return_value=None)
    )
    fake_storage.head = AsyncMock(
        return_value=ObjectMeta(key="k", size_bytes=98765, content_type="video/mp4")
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "ready"
    assert out.signed_url == "https://signed.example/v.mp4?sig=x"
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "ready"
    assert row.recording_bytes == 98765
    assert row.recording_ready_at is not None
    assert row.recording_s3_key is not None


async def test_no_egress_no_object_past_grace_marks_failed(db, fake_storage, monkeypatch):
    from datetime import UTC, datetime, timedelta

    completed = datetime.now(UTC) - timedelta(seconds=3600)
    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", agent_completed_at=completed
    )
    monkeypatch.setattr(recording_mod, "get_recording_status", AsyncMock(return_value=None))
    fake_storage.head = AsyncMock(return_value=None)

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "failed"
    assert out.signed_url is None
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "failed"


async def test_no_egress_no_object_within_grace_stays_recording(db, fake_storage, monkeypatch):
    from datetime import UTC, datetime

    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", agent_completed_at=datetime.now(UTC)
    )
    monkeypatch.setattr(recording_mod, "get_recording_status", AsyncMock(return_value=None))
    fake_storage.head = AsyncMock(return_value=None)

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "recording"
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "recording"


async def test_no_egress_no_object_no_completion_ts_stays_recording(db, fake_storage, monkeypatch):
    """A recording with no agent_completed_at must never be auto-failed — we
    can't time a recording we never timestamped."""
    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", agent_completed_at=None
    )
    monkeypatch.setattr(recording_mod, "get_recording_status", AsyncMock(return_value=None))
    fake_storage.head = AsyncMock(return_value=None)

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "recording"
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "recording"
