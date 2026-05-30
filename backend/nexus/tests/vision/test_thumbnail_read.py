# tests/vision/test_thumbnail_read.py
import pytest

from tests.conftest import seed_minimal_session
from app.modules.vision import get_session_timeline_thumbnails
from app.modules.vision.models import SessionTimelineThumbnail


@pytest.mark.asyncio
async def test_returns_rows_for_session(db):
    sess, tenant_id = await seed_minimal_session(db)
    db.add(SessionTimelineThumbnail(
        tenant_id=tenant_id, session_id=sess.id,
        kind="question", ref_id="q1", t_ms=1000,
        s3_key="thumbs/t/s/question_q1.webp"))
    await db.flush()

    rows = await get_session_timeline_thumbnails(
        db, session_id=sess.id, tenant_id=tenant_id)
    assert len(rows) == 1
    assert rows[0].kind == "question" and rows[0].ref_id == "q1"
    assert rows[0].s3_key == "thumbs/t/s/question_q1.webp"


@pytest.mark.asyncio
async def test_returns_empty_for_session_with_no_thumbnails(db):
    sess, tenant_id = await seed_minimal_session(db)
    rows = await get_session_timeline_thumbnails(
        db, session_id=sess.id, tenant_id=tenant_id)
    assert rows == []
