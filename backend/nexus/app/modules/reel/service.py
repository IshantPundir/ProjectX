"""Persistence + eligibility for the Candidate Reel.

Eligibility: report ready AND recording ready, for ANY verdict. The reel is the
video evidence behind the verdict (positive for advance; balanced for borderline;
evidence-for-shortfall for reject). A reel needs the report (for the EDL) + the
recording (to cut from). The decision function is pure + table-tested; the DB
wrappers feed it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.reel.models import SessionReel
from app.modules.reel.schemas import ReelChapter, ReelPlayback
from app.storage import get_object_storage

def eligibility_decision(*, report_status: str | None, verdict: str | None,
                         recording_key: str | None) -> tuple[bool, str | None]:
    """Pure eligibility decision → (eligible, ineligible_reason).

    The Evidence Reel is available for EVERY verdict (advance / borderline /
    reject) — it voices the verdict the report reached. It only needs the report
    (for the EDL) and the recording (to cut from). ``verdict`` is accepted for
    call-site stability but no longer gates eligibility.
    """
    if report_status != "ready":
        return False, "Report is not ready yet."
    if not recording_key:
        return False, "Session recording is not ready yet."
    return True, None


async def check_eligibility(db: AsyncSession, *, session_id: UUID,
                            tenant_id: UUID) -> tuple[bool, str | None]:
    """Read report + recording state and apply :func:`eligibility_decision`."""
    row = (await db.execute(text(
        "SELECT r.status AS report_status, r.verdict AS verdict, "
        "       s.recording_s3_key AS recording_key "
        "FROM sessions s "
        "LEFT JOIN session_reports r ON r.session_id = s.id AND r.tenant_id = s.tenant_id "
        "WHERE s.id = :sid AND s.tenant_id = :tid"
    ), {"sid": str(session_id), "tid": str(tenant_id)})).mappings().first()
    if row is None:
        return False, "Session not found."
    return eligibility_decision(
        report_status=row["report_status"], verdict=row["verdict"],
        recording_key=row["recording_key"],
    )


async def get_reel(db: AsyncSession, *, session_id: UUID,
                   tenant_id: UUID) -> SessionReel | None:
    return (await db.execute(
        select(SessionReel).where(
            SessionReel.session_id == session_id,
            SessionReel.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()


async def request_reel(db: AsyncSession, *, session_id: UUID, tenant_id: UUID,
                       created_by: UUID) -> SessionReel:
    """Create or reset the reel row to ``pending`` (regenerate bumps version)."""
    assignment_id = (await db.execute(text(
        "SELECT assignment_id FROM sessions WHERE id = :sid AND tenant_id = :tid"
    ), {"sid": str(session_id), "tid": str(tenant_id)})).scalar_one()

    row = await get_reel(db, session_id=session_id, tenant_id=tenant_id)
    if row is None:
        row = SessionReel(
            session_id=session_id, tenant_id=tenant_id, assignment_id=assignment_id,
            created_by=created_by, status="pending", version=1,
        )
        db.add(row)
    else:
        row.status = "pending"
        row.generation_error = None
        row.generation_started_at = None
        row.version = row.version + 1
        row.created_by = created_by
    await db.flush()
    return row


async def build_playback(reel: SessionReel | None, *, eligible: bool,
                         reason: str | None) -> ReelPlayback:
    """Assemble the playback envelope, presigning the R2 URL for ``ready`` reels."""
    if reel is None:
        return ReelPlayback(status="absent", eligible=eligible, ineligible_reason=reason)

    signed_url: str | None = None
    expires_at: str | None = None
    if reel.status == "ready" and reel.r2_key:
        ttl = settings.recording_signed_url_ttl_seconds
        signed_url = await get_object_storage().presign_get_url(reel.r2_key, ttl_seconds=ttl)
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()

    return ReelPlayback(
        status=reel.status,  # type: ignore[arg-type]
        signed_url=signed_url,
        expires_at=expires_at,
        duration_seconds=float(reel.duration_seconds) if reel.duration_seconds else None,
        chapters=[ReelChapter(**c) for c in (reel.chapters or [])],
        generation_error=reel.generation_error,
        eligible=eligible,
        ineligible_reason=reason,
        version=reel.version,
    )
