# tests/vision/test_thumbnail_actor.py
"""Test that _persist_timeline_thumbnails uploads frames and upserts ORM rows."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.modules.vision import actors as vision_actors
from app.modules.vision.models import SessionTimelineThumbnail
from tests.conftest import seed_minimal_session



@pytest.mark.asyncio
async def test_persist_thumbnails_uploads_and_upserts(db):
    sess, tenant_id = await seed_minimal_session(db)
    # Gen-3 transcript shape (sessions.session_evidence_json["transcript"]).
    transcript = [
        {"speaker": "agent", "question_id": "q1",
         "span": {"start_ms": 1000, "end_ms": 1500}},
        {"speaker": "candidate", "question_id": "q1",
         "span": {"start_ms": 1600, "end_ms": 2000}},
        {"speaker": "agent", "question_id": "q2",
         "span": {"start_ms": 8000, "end_ms": 8500}},
    ]
    # Every flagged interval gets a thumbnail (not just the top-N).
    flagged = [
        {"kind": "off_screen_sustained", "start_ms": 5000, "end_ms": 6000, "confidence": 0.65},
        {"kind": "down_glance", "start_ms": 7000, "end_ms": 7200, "confidence": 0.6},
        {"kind": "multiple_faces", "start_ms": 9000, "end_ms": 9100, "confidence": 0.9},
    ]

    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()

    grabbed = {ms: b"RIFF0000WEBP" for ms in (1000, 8000, 5000, 7000, 9000)}
    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_timeline_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", transcript=transcript,
            flagged_intervals=flagged,
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()
    kinds = {(r.kind, r.ref_id) for r in rows}
    # both questions resolved from agent span.start_ms
    assert ("question", "q1") in kinds
    assert ("question", "q2") in kinds
    # ALL three flagged intervals (per-violation), keyed by start_ms
    assert ("flag", "5000") in kinds
    assert ("flag", "7000") in kinds
    assert ("flag", "9000") in kinds
    # 2 questions + 3 flags = 5 uploads
    assert fake_storage.upload_bytes.await_count == 5


@pytest.mark.asyncio
async def test_grab_failure_is_swallowed(db):
    sess, tenant_id = await seed_minimal_session(db)
    transcript = [
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 1000, "end_ms": 1500}},
    ]

    def _boom(*a, **k):
        raise RuntimeError("decode exploded")

    with patch.object(vision_actors, "get_object_storage", return_value=MagicMock()), \
         patch.object(vision_actors, "grab_thumbnails", side_effect=_boom):
        # must NOT raise
        await vision_actors._persist_timeline_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", transcript=transcript,
            flagged_intervals=[],
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()
    assert rows == []
