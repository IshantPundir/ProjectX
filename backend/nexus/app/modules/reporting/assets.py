"""Best-effort presigning of report assets (timeline thumbnails + reference
photo). Shared by the authenticated reports router and the public share path."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.reporting.schemas import ReportHeader, ReportRead, SignalAssessmentOut
from app.modules.session.models import Session  # models cross-module carve-out
from app.modules.vision import get_session_timeline_thumbnails
from app.storage import get_object_storage

# ---------------------------------------------------------------------------
# Skills derivation (pure — no IO)
# ---------------------------------------------------------------------------

_DEMONSTRATED = {"solid", "strong"}


def skills_from_assessments(
    assessments: list[SignalAssessmentOut], *, cap: int = 6
) -> list[str]:
    """Return signal names where level is solid or strong, sorted by weight desc.

    Pure function — no DB access. Used by attach_report_header and testable
    independently of the DB join.
    """
    demonstrated = [a for a in assessments if a.level in _DEMONSTRATED]
    demonstrated.sort(key=lambda a: a.weight, reverse=True)
    return [a.signal for a in demonstrated[:cap]]


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


# ---------------------------------------------------------------------------
# Report header — identity / job / session / skills (DB join)
# ---------------------------------------------------------------------------


async def attach_report_header(
    *, db: AsyncSession, report: ReportRead, session_id: Any, tenant_id: Any
) -> None:
    """Populate report.header from session → candidate/job/stage joins.

    Uses an explicit tenant_id filter so it works under both RLS (prod) and the
    test DB where RLS is disabled. Best-effort: if the session row is missing
    (e.g. orphaned share), the header is left as None.

    Column mapping verified against ORM models and the list_report_index query:
      - sessions.started_at          (not agent_started_at — the column in migration
                                       0024 named agent_started_at was for the
                                       agent's own start; sessions.started_at is
                                       the canonical session-start timestamp)
      - sessions.recording_duration_seconds   (migration 0050)
      - sessions.assignment_id / stage_id     (session model)
      - candidates.name / candidates.email    (candidates model)
      - job_postings.title                    (jd model)
      - job_pipeline_stages.name              (pipelines model)
    """
    try:
        row = (
            await db.execute(
                text("""
                    SELECT c.name AS candidate_name,
                           c.email AS candidate_email,
                           j.title AS job_title,
                           st.name AS stage_name,
                           s.started_at,
                           s.recording_duration_seconds
                      FROM sessions s
                      LEFT JOIN candidate_job_assignments a ON a.id = s.assignment_id
                      LEFT JOIN candidates c ON c.id = a.candidate_id
                      LEFT JOIN job_postings j ON j.id = a.job_posting_id
                      LEFT JOIN job_pipeline_stages st ON st.id = s.stage_id
                     WHERE s.id = :sid AND s.tenant_id = :tid
                """),
                {"sid": str(session_id), "tid": str(tenant_id)},
            )
        ).mappings().first()
    except Exception:  # noqa: BLE001
        return
    if row is None:
        return
    report.header = ReportHeader(
        candidate_name=row["candidate_name"] or "Candidate",
        candidate_email=row["candidate_email"],
        job_title=row["job_title"] or "",
        stage_label=row["stage_name"] or "",
        session_started_at=(
            row["started_at"].isoformat() if row["started_at"] else None
        ),
        duration_seconds=row["recording_duration_seconds"],
        skills=skills_from_assessments(report.signal_assessments),
        reference_photo_url=report.reference_photo_url,
    )
