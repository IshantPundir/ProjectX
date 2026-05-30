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


@pytest.mark.asyncio
async def test_does_not_return_other_tenants_rows(db):
    # Tenant A's session has a thumbnail; querying it as Tenant B returns nothing.
    sess_a, tenant_a = await seed_minimal_session(db)
    _sess_b, tenant_b = await seed_minimal_session(db)
    db.add(SessionTimelineThumbnail(
        tenant_id=tenant_a, session_id=sess_a.id,
        kind="question", ref_id="q1", t_ms=1000,
        s3_key="thumbs/a/s/question_q1.webp"))
    await db.flush()

    # Same session_id, wrong tenant → no rows (application-layer tenant filter).
    rows = await get_session_timeline_thumbnails(
        db, session_id=sess_a.id, tenant_id=tenant_b)
    assert rows == []
