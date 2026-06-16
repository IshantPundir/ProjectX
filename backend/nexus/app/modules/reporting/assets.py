"""Best-effort presigning of report assets (timeline thumbnails + reference
photo). Shared by the authenticated reports router and the public share path."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.reporting.schemas import ReportRead
from app.modules.session.models import Session  # models cross-module carve-out
from app.modules.vision import get_session_timeline_thumbnails
from app.storage import get_object_storage


async def attach_question_thumbnails(
    *, db: AsyncSession, report: ReportRead, session_id: Any, tenant_id: Any
) -> None:
    """Presign per-question timeline thumbnails and attach by question_id.
    Best-effort: a lookup/presign failure leaves thumbnail_url as None."""
    if not report.questions:
        return
    try:
        thumbs = await get_session_timeline_thumbnails(
            db, session_id=session_id, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        return
    by_qid = {t.ref_id: t.s3_key for t in thumbs if t.kind == "question"}
    if not by_qid:
        return
    storage = get_object_storage()
    ttl = settings.recording_signed_url_ttl_seconds
    for q in report.questions:
        key = by_qid.get(q.question_id)
        if not key:
            continue
        try:
            q.thumbnail_url = await storage.presign_get_url(key, ttl_seconds=ttl)
        except Exception:  # noqa: BLE001
            continue


async def attach_reference_photo(
    *, db: AsyncSession, report: ReportRead, session_id: Any, tenant_id: Any
) -> None:
    """Presign the candidate reference photo (the main session thumbnail).

    Best-effort: missing key / presign failure leaves reference_photo_url None.
    """
    try:
        sess = (await db.execute(
            select(Session).where(
                Session.id == session_id, Session.tenant_id == tenant_id)
        )).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return
    if not sess or not sess.reference_photo_key:
        return
    try:
        report.reference_photo_url = await get_object_storage().presign_get_url(
            sess.reference_photo_key,
            ttl_seconds=settings.recording_signed_url_ttl_seconds,
        )
    except Exception:  # noqa: BLE001
        return
