# app/modules/vision/service.py
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.vision.models import (
    SessionProctoringAnalysis,
    SessionTimelineThumbnail,
)
from app.modules.vision.schemas import ProctoringAnalysisRead


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
    return ProctoringAnalysisRead(
        status=row.status,
        risk_band=row.risk_band,
        detector_summary=row.detector_summary,
        gaze_heatmap=row.gaze_heatmap,
        flagged_intervals=row.flagged_intervals or [],
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
