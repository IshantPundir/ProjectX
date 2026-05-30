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
    transcript = [{"role": "agent", "text": "Q1?", "timestamp_ms": 1000, "question_id": "q1"}]
    flagged = [{"kind": "off_screen_sustained", "start_ms": 5000, "end_ms": 6000,
                "confidence": 0.65}]

    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails",
                      return_value={1000: b"RIFF0000WEBP", 5000: b"RIFF0000WEBP"}):
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
    assert ("question", "q1") in kinds
    assert ("flag", "5000") in kinds
    assert fake_storage.upload_bytes.await_count == 2
