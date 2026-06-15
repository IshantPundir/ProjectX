"""Session recording playback service — reconcile, presign, tenant isolation."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
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

# pytest-asyncio runs in ``auto`` mode (see pyproject [tool.pytest.ini_options]),
# so async tests are collected without an explicit mark. We deliberately do NOT
# set a module-level ``pytestmark = pytest.mark.asyncio`` — that would also tag
# the synchronous ``_recording_offset_ms`` unit tests below and emit a warning.


async def _seed_session(
    db,
    *,
    recording_status,
    egress_id=None,
    key=None,
    transcript=None,
    agent_completed_at=None,
    recording_started_at=None,
    session_evidence_json=None,
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
        recording_started_at=recording_started_at,
        session_evidence_json=session_evidence_json,
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
    rec_start = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    tenant_id, session_id = await _seed_session(
        db,
        recording_status="ready",
        key="recordings/t/s.mp4",
        recording_started_at=rec_start,
        # Engine session started 1.140s after the recording clock. The transcript
        # lives in the gen-3 SessionEvidence (sessions.transcript is empty in gen-3).
        session_evidence_json={
            "meta": {"started_at": (rec_start + timedelta(milliseconds=1140)).isoformat()},
            "transcript": [
                {"speaker": "agent", "text": "Hi", "span": {"start_ms": 0, "end_ms": 900}},
                {
                    "speaker": "candidate",
                    "text": "Hello",
                    "span": {"start_ms": 1500, "end_ms": 2800},
                },
            ],
        },
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "ready"
    assert out.signed_url == "https://signed.example/v.mp4?sig=x"
    assert out.expires_at is not None
    assert out.offset_ms == 1140
    assert [s.text for s in out.transcript] == ["Hi", "Hello"]
    fake_storage.presign_get_url.assert_awaited_once()


# --- _recording_offset_ms pure helper (synchronous) ------------------------

def test_offset_ms_normal_positive():
    rec_start = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    evidence = {"meta": {"started_at": (rec_start + timedelta(milliseconds=1140)).isoformat()}}
    assert recording_mod._recording_offset_ms(evidence, rec_start) == 1140


def test_offset_ms_parses_z_suffixed_iso():
    rec_start = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    # Z-suffixed (Zulu) ISO form, 2s after the recording clock.
    evidence = {"meta": {"started_at": "2026-06-14T12:00:02Z"}}
    assert recording_mod._recording_offset_ms(evidence, rec_start) == 2000


def test_offset_ms_missing_evidence_returns_zero():
    rec_start = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    assert recording_mod._recording_offset_ms(None, rec_start) == 0


def test_offset_ms_missing_meta_started_at_returns_zero():
    rec_start = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    assert recording_mod._recording_offset_ms({"meta": {}}, rec_start) == 0
    assert recording_mod._recording_offset_ms({}, rec_start) == 0


def test_offset_ms_missing_recording_started_at_returns_zero():
    evidence = {"meta": {"started_at": "2026-06-14T12:00:02Z"}}
    assert recording_mod._recording_offset_ms(evidence, None) == 0


def test_offset_ms_unparseable_started_at_returns_zero():
    rec_start = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
    bad = {"meta": {"started_at": "not-a-date"}}
    assert recording_mod._recording_offset_ms(bad, rec_start) == 0
    # Non-dict meta must not raise either.
    assert recording_mod._recording_offset_ms({"meta": "oops"}, rec_start) == 0


# --- _build_transcript over gen-3 SessionEvidence transcript ---------------

def test_build_transcript_maps_gen3_evidence_entries():
    raw = [
        {"speaker": "agent", "text": "Hi", "span": {"start_ms": 0, "end_ms": 900}},
        {
            "speaker": "candidate",
            "text": "Hello there",
            "span": {"start_ms": 1500, "end_ms": 3200},
            "turn_ref": "t1",
            "question_id": "q1",
        },
    ]
    out = recording_mod._build_transcript(raw)
    assert [(s.role, s.text, s.t_ms) for s in out] == [
        ("agent", "Hi", 0),
        ("candidate", "Hello there", 1500),
    ]


def test_build_transcript_skips_malformed_and_partial_entries():
    raw = [
        "not-a-dict",
        {"text": "no speaker", "span": {"start_ms": 0}},
        {"speaker": "agent", "span": {"start_ms": 0}},  # missing text
        {"speaker": "agent", "text": "no span"},  # missing span
        {"speaker": "agent", "text": "span missing start", "span": {"end_ms": 5}},
        {"speaker": "agent", "text": "span not a dict", "span": "oops"},
        {"speaker": "agent", "text": "good", "span": {"start_ms": 42}},
    ]
    out = recording_mod._build_transcript(raw)
    assert [(s.role, s.text, s.t_ms) for s in out] == [("agent", "good", 42)]


def test_build_transcript_empty_or_none_yields_empty_list():
    assert recording_mod._build_transcript(None) == []
    assert recording_mod._build_transcript([]) == []


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
