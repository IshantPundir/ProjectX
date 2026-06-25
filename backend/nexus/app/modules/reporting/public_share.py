"""Public recordings share — token resolution + envelope assembly.

Security: tokens are resolved on a bypass-RLS session (we don't know the tenant
until the row is read — mirrors candidate-session token resolution). Once the
row is found, the tenant_id from the row scopes every downstream read (explicit
WHERE tenant_id filters in the reused service functions). Any invalid / revoked
/ expired / unknown token resolves to None → the endpoint returns a uniform 404
(no enumeration oracle).
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reel import build_playback, check_eligibility, get_reel
from app.modules.reporting.assets import (
    attach_question_thumbnails,
    attach_reference_photo,
    attach_report_header,
)
from app.modules.reporting.labels import load_session_labels
from app.modules.reporting.models import ReportShare, SessionReport
from app.modules.reporting.schemas import PublicRecordingsEnvelope
from app.modules.reporting.serialization import report_read_from_row
from app.modules.reporting.share_tokens import hash_share_token
from app.modules.session import (
    SessionNotFoundError,
    get_session_recording_playback,
)
from app.modules.vision import get_session_proctoring_analysis

_log = structlog.get_logger("reporting.public_share")


async def resolve_share_token(db: AsyncSession, token: str) -> ReportShare | None:
    """Look up a share by token hash and validate it. None on any failure
    (unknown / revoked / expired) — the caller maps every None to a uniform
    404 so the endpoint is not an enumeration oracle."""
    if not token or len(token) > 256:
        return None
    token_hash = hash_share_token(token)
    share = (await db.execute(
        select(ReportShare).where(ReportShare.share_token_hash == token_hash)
    )).scalar_one_or_none()
    if share is None:
        return None
    if share.revoked_at is not None:
        return None
    if share.share_expires_at is not None and share.share_expires_at <= datetime.now(UTC):
        return None
    return share


async def build_public_envelope(
    db: AsyncSession, share: ReportShare
) -> PublicRecordingsEnvelope | None:
    """Assemble the full envelope for a validated share. None if the report is
    not ready (treated as 404 by the endpoint)."""
    tenant_id = share.tenant_id
    session_id = share.session_id

    report_row = (await db.execute(
        select(SessionReport).where(
            SessionReport.id == share.report_id,
            SessionReport.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if report_row is None or report_row.status != "ready":
        return None

    report = report_read_from_row(report_row)
    await attach_question_thumbnails(
        db=db, report=report, session_id=session_id, tenant_id=tenant_id)
    await attach_reference_photo(
        db=db, report=report, session_id=session_id, tenant_id=tenant_id)
    await attach_report_header(
        db=db, report=report, session_id=session_id, tenant_id=tenant_id)

    try:
        recording = await get_session_recording_playback(
            db, session_id=session_id, tenant_id=tenant_id, reconcile=False)
    except SessionNotFoundError:
        # The session row is gone (e.g. hard-deleted after the share was
        # minted). Treated as 404 by the endpoint — no orphan playback.
        return None
    proctoring = await get_session_proctoring_analysis(
        db, session_id=session_id, tenant_id=tenant_id)

    reel_row = await get_reel(db, session_id=session_id, tenant_id=tenant_id)
    eligible, reason = await check_eligibility(
        db, session_id=session_id, tenant_id=tenant_id)
    reel = await build_playback(reel_row, eligible=eligible, reason=reason)

    candidate_name, job_title, stage_label = await load_session_labels(
        db, session_id=session_id, tenant_id=tenant_id)

    return PublicRecordingsEnvelope(
        candidate_name=candidate_name,
        job_title=job_title,
        stage_label=stage_label,
        report=report,
        recording=recording,
        proctoring=proctoring,
        reel=reel,
    )
