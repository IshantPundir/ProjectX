# tests/vision/test_thumbnail_actor.py
"""Tests for the split thumbnail helpers (_persist_flag_thumbnails,
_persist_question_thumbnails) and the generate_session_thumbnails actor runner.

After the refactor:
  - _persist_flag_thumbnails  → kind='flag' only (one per proctoring violation)
  - _persist_question_thumbnails → kind='question' only (one per asked question)
  - generate_session_thumbnails actor → calls _persist_question_thumbnails
  - analyze_session_proctoring → calls _persist_flag_thumbnails (no question frames)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.modules.vision import actors as vision_actors
from app.modules.vision.models import SessionTimelineThumbnail
from tests.conftest import seed_minimal_session


# ---------------------------------------------------------------------------
# _persist_flag_thumbnails — produces ONLY kind='flag' rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_flag_thumbnails_only_flag_rows(db):
    sess, tenant_id = await seed_minimal_session(db)
    flagged = [
        {"kind": "off_screen_sustained", "start_ms": 5000, "end_ms": 6000, "confidence": 0.65},
        {"kind": "down_glance", "start_ms": 7000, "end_ms": 7200, "confidence": 0.6},
    ]
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()
    grabbed = {5000: b"WEBPFLAG1", 7000: b"WEBPFLAG2"}

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_flag_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", flagged_intervals=flagged,
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()

    kinds = {r.kind for r in rows}
    assert kinds == {"flag"}, f"Expected only 'flag' rows, got: {kinds}"

    ref_ids = {r.ref_id for r in rows}
    assert "5000" in ref_ids
    assert "7000" in ref_ids
    assert fake_storage.upload_bytes.await_count == 2


@pytest.mark.asyncio
async def test_persist_flag_thumbnails_no_question_rows(db):
    """Ensures that even with a transcript available, flag helper never writes question rows."""
    sess, tenant_id = await seed_minimal_session(db)
    flagged = [{"kind": "multiple_faces", "start_ms": 9000, "end_ms": 9100, "confidence": 0.9}]
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()
    grabbed = {9000: b"WEBPFLAG3"}

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_flag_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", flagged_intervals=flagged,
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()

    assert all(r.kind == "flag" for r in rows)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_persist_flag_thumbnails_empty_intervals(db):
    """Empty flagged_intervals → no rows, no uploads."""
    sess, tenant_id = await seed_minimal_session(db)
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value={}):
        await vision_actors._persist_flag_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", flagged_intervals=[],
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()
    assert rows == []
    fake_storage.upload_bytes.assert_not_awaited()


# ---------------------------------------------------------------------------
# _persist_question_thumbnails — produces ONLY kind='question' rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_question_thumbnails_only_question_rows(db):
    sess, tenant_id = await seed_minimal_session(db)
    transcript = [
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 1000, "end_ms": 1500}},
        {"speaker": "candidate", "question_id": "q1", "span": {"start_ms": 1600, "end_ms": 2000}},
        {"speaker": "agent", "question_id": "q2", "span": {"start_ms": 8000, "end_ms": 8500}},
    ]
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()
    grabbed = {1000: b"WEBPQ1", 8000: b"WEBPQ2"}

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_question_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", transcript=transcript,
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()

    kinds = {r.kind for r in rows}
    assert kinds == {"question"}, f"Expected only 'question' rows, got: {kinds}"

    ref_ids = {r.ref_id for r in rows}
    assert "q1" in ref_ids
    assert "q2" in ref_ids
    assert fake_storage.upload_bytes.await_count == 2


@pytest.mark.asyncio
async def test_persist_question_thumbnails_no_flag_rows(db):
    """Even when flagged intervals are conceptually present, question helper writes no flag rows."""
    sess, tenant_id = await seed_minimal_session(db)
    transcript = [
        {"speaker": "agent", "question_id": "qA", "span": {"start_ms": 3000, "end_ms": 3500}},
    ]
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()
    grabbed = {3000: b"WEBPQA"}

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_question_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", transcript=transcript,
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()

    assert all(r.kind == "question" for r in rows)


@pytest.mark.asyncio
async def test_persist_question_thumbnails_grab_failure_swallowed(db):
    """A grab failure must not propagate — best-effort, no rows upserted."""
    sess, tenant_id = await seed_minimal_session(db)
    transcript = [
        {"speaker": "agent", "question_id": "q1", "span": {"start_ms": 1000, "end_ms": 1500}},
    ]

    def _boom(*a, **k):
        raise RuntimeError("decode exploded")

    with patch.object(vision_actors, "get_object_storage", return_value=MagicMock()), \
         patch.object(vision_actors, "grab_thumbnails", side_effect=_boom):
        # must NOT raise
        await vision_actors._persist_question_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", transcript=transcript,
        )
        await db.flush()

    rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id)
    )).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# Negative control: the two helpers are truly independent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_helper_leaves_zero_question_rows(db):
    """_persist_flag_thumbnails must never insert kind='question' rows."""
    sess, tenant_id = await seed_minimal_session(db)
    flagged = [{"kind": "off_screen_sustained", "start_ms": 2000, "end_ms": 3000, "confidence": 0.7}]
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()
    grabbed = {2000: b"WEBPNEG"}

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_flag_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", flagged_intervals=flagged,
        )
        await db.flush()

    question_rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id,
            SessionTimelineThumbnail.kind == "question",
        )
    )).scalars().all()
    assert question_rows == []


@pytest.mark.asyncio
async def test_question_helper_leaves_zero_flag_rows(db):
    """_persist_question_thumbnails must never insert kind='flag' rows."""
    sess, tenant_id = await seed_minimal_session(db)
    transcript = [
        {"speaker": "agent", "question_id": "qB", "span": {"start_ms": 4000, "end_ms": 4500}},
    ]
    fake_storage = MagicMock()
    fake_storage.upload_bytes = AsyncMock()
    grabbed = {4000: b"WEBPNEG2"}

    with patch.object(vision_actors, "get_object_storage", return_value=fake_storage), \
         patch.object(vision_actors, "grab_thumbnails", return_value=grabbed):
        await vision_actors._persist_question_thumbnails(
            db, session_id=str(sess.id), tenant_id=str(tenant_id),
            local_video_path="/tmp/rec.mp4", transcript=transcript,
        )
        await db.flush()

    flag_rows = (await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == sess.id,
            SessionTimelineThumbnail.kind == "flag",
        )
    )).scalars().all()
    assert flag_rows == []
