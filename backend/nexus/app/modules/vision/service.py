# app/modules/vision/service.py
from __future__ import annotations

import contextlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.vision.models import (
    SessionProctoringAnalysis,
    SessionTimelineThumbnail,
)
from app.modules.vision.schemas import ProctoringAnalysisRead
from app.storage import get_object_storage


async def attach_flag_thumbnails(
    flagged_intervals: list[dict], thumbs: list
) -> list[dict]:
    """Return a copy of flagged_intervals with thumbnail_url attached where a
    'flag' thumbnail matches by start_ms. Best-effort presign."""
    by_start = {t.ref_id: t.s3_key for t in thumbs if t.kind == "flag"}
    if not by_start:
        return flagged_intervals
    storage = get_object_storage()
    ttl = settings.recording_signed_url_ttl_seconds
    out: list[dict] = []
    for f in flagged_intervals:
        f2 = dict(f)
        key = by_start.get(str(f.get("start_ms")))
        if key:
            with contextlib.suppress(Exception):
                f2["thumbnail_url"] = await storage.presign_get_url(key, ttl_seconds=ttl)
        out.append(f2)
    return out


async def get_session_proctoring_analysis(
    db: AsyncSession, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> ProctoringAnalysisRead:
    """Tenant-scoped read. Returns status='absent' when no row exists."""
    row = (
        await db.execute(
            select(SessionProctoringAnalysis).where(
                SessionProctoringAnalysis.session_id == session_id,
                SessionProctoringAnalysis.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return ProctoringAnalysisRead(status="absent")
    flagged = await attach_flag_thumbnails(
        row.flagged_intervals or [],
        await get_session_timeline_thumbnails(
            db, session_id=session_id, tenant_id=tenant_id),
    )
    return ProctoringAnalysisRead(
        status=row.status,
        risk_band=row.risk_band,
        detector_summary=row.detector_summary,
        gaze_heatmap=row.gaze_heatmap,
        flagged_intervals=flagged,
        gaze_signal_quality=row.gaze_signal_quality,
        unscorable_pct=float(row.unscorable_pct) if row.unscorable_pct is not None else None,
    )


async def get_session_timeline_thumbnails(
    db: AsyncSession, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[SessionTimelineThumbnail]:
    """Tenant-scoped: all timeline thumbnail rows for a session (questions + flags)."""
    return list((await db.execute(
        select(SessionTimelineThumbnail).where(
            SessionTimelineThumbnail.session_id == session_id,
            SessionTimelineThumbnail.tenant_id == tenant_id,
        )
    )).scalars().all())
