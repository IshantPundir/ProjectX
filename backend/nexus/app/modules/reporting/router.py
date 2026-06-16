"""FastAPI router for the reporting module — /api/reports/*.

Four endpoints:

  GET  /api/reports/session/{session_id}          — report by session (tenant-scoped)
  GET  /api/reports/{report_id}                   — report by id (tenant-scoped)
  POST /api/reports/session/{session_id}/regenerate — re-enqueue scoring (super-admin)
  POST /api/reports/{report_id}/decision          — record human decision + audit

Rate limiting: this codebase uses global middleware only (no per-route limiter
decorator in any router). No per-route decorator is added here.
"""
from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.modules.audit import log_event
from app.modules.auth import UserContext, get_current_user_roles
from app.modules.reporting.actors import score_session_report, share_report_pdf
from app.modules.reporting.assets import (
    attach_question_thumbnails,
    attach_reference_photo,
)
from app.modules.reporting.models import ReportShare, SessionReport
from app.modules.reporting.schemas import (
    HumanDecisionIn,
    ReportIndexItem,
    ReportIndexPage,
    ReportRead,
    ShareReportIn,
    ShareReportOut,
)
from app.modules.reporting.serialization import report_read_from_row
from app.modules.session import (
    SessionNotFoundError,
    get_session_recording_playback,
)
from app.modules.vision import get_session_proctoring_analysis

router = APIRouter(prefix="/api/reports", tags=["reporting"])

_log = structlog.get_logger("reporting.router")

_MAX_CORRELATION_ID_LEN = 128


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_correlation_id(request: Request) -> str:
    """Extract x-correlation-id header or mint a fresh uuid4."""
    raw = request.headers.get("x-correlation-id")
    if (
        raw
        and 0 < len(raw) <= _MAX_CORRELATION_ID_LEN
        and raw.isascii()
        and raw.isprintable()
    ):
        return raw
    return str(uuid_mod.uuid4())


def _row_to_read(row: SessionReport) -> ReportRead:
    return report_read_from_row(row)


def _require_reports_view(user: UserContext) -> None:
    """Raise 403 if the caller lacks reports.view and is not super-admin."""
    if "reports.view" not in user.all_permissions() and not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Missing reports.view")


def _require_super_admin(user: UserContext) -> None:
    """Raise 403 if caller is not super-admin (privileged / destructive gate)."""
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")


# ---------------------------------------------------------------------------
# GET /api/reports/session/{session_id}
#
# NOTE: This route MUST be defined before GET /{report_id} so that FastAPI
# does not match "session" as a report_id UUID (it would fail UUID parsing,
# but ordering avoids the ambiguity cleanly).
# ---------------------------------------------------------------------------


@router.get(
    "/session/{session_id}",
    summary="Get the current report for a session",
)
async def get_report_by_session(
    session_id: uuid_mod.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Return the report for a session.

    - `pending` / `generating` → 202 `{"status": <status>}`
    - `ready` / `failed` → 200 with full ReportRead body
    - No report row → 404
    RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id

    result = await db.execute(
        select(SessionReport).where(
            SessionReport.session_id == session_id,
            SessionReport.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")

    if row.status in ("pending", "generating"):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": row.status},
        )

    read = _row_to_read(row)
    await attach_question_thumbnails(
        db=db, report=read, session_id=session_id, tenant_id=tenant_id)
    await attach_reference_photo(
        db=db, report=read, session_id=session_id, tenant_id=tenant_id)
    return read.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /api/reports/session/{session_id}/recording
#
# Session-recording playback for the report page's player. Pull-based: if the
# recording is still processing, this polls LiveKit and advances the row.
# Returns a short-lived presigned URL only when ready. Same reports.view gate
# as the report itself — anyone who can see the report can see the recording.
# ---------------------------------------------------------------------------


@router.get(
    "/session/{session_id}/recording",
    summary="Get the session recording playback (signed URL + transcript)",
)
async def get_session_recording_endpoint(
    session_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Return `{status, signed_url?, expires_at?, duration_seconds?, offset_ms,
    transcript[]}` for a session's recording. RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)
    try:
        playback = await get_session_recording_playback(
            db, session_id=session_id, tenant_id=user.user.tenant_id
        )
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    return playback.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /api/reports/session/{session_id}/proctoring
#
# Post-session vision proctoring analysis for the report page's integrity
# panel. Pull-based: returns status='absent' when no analysis row exists yet.
# Same reports.view gate as the recording endpoint — anyone who can see the
# report can see the proctoring panel.
# ---------------------------------------------------------------------------


@router.get(
    "/session/{session_id}/proctoring",
    summary="Get the post-session vision proctoring analysis (evidence for review)",
)
async def get_session_proctoring_endpoint(
    session_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Return `{status, risk_band?, detector_summary?, gaze_heatmap?,
    flagged_intervals[], gaze_signal_quality?, unscorable_pct?}`. Evidence for
    human review — never an auto-decision. RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)
    analysis = await get_session_proctoring_analysis(
        db, session_id=session_id, tenant_id=user.user.tenant_id
    )
    return analysis.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /api/reports  — hub index of completed sessions + report status
# ---------------------------------------------------------------------------


@router.get("", summary="List completed sessions with report status (hub)")
async def list_report_index(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Paginated list of completed sessions that are reportable.

    A session appears when it is `completed`. Tenant-scoped by an explicit
    `s.tenant_id` filter (works in tests where RLS is disabled) plus RLS in
    prod. RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)
    tenant_id: uuid_mod.UUID = user.user.tenant_id

    base = """
        FROM sessions s
        LEFT JOIN candidate_job_assignments a ON a.id = s.assignment_id
        LEFT JOIN candidates c ON c.id = a.candidate_id
        LEFT JOIN job_postings j ON j.id = a.job_posting_id
        LEFT JOIN job_pipeline_stages st ON st.id = s.stage_id
        LEFT JOIN session_reports sr ON sr.session_id = s.id
        WHERE s.tenant_id = :tenant_id
          AND s.state = 'completed'
    """
    params: dict[str, Any] = {"tenant_id": str(tenant_id)}

    total = (
        await db.execute(text("SELECT count(*) " + base), params)
    ).scalar_one()

    rows = (
        await db.execute(
            text(
                "SELECT s.id AS session_id, a.candidate_id, c.name AS candidate_name, "
                "j.title AS job_title, st.name AS stage_name, s.completed_at, "
                "COALESCE(sr.status, 'none') AS report_status, sr.verdict AS verdict, "
                "sr.overall_score AS overall_score "
                + base
                + " ORDER BY s.completed_at DESC NULLS LAST, s.created_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).mappings().all()

    items = [
        ReportIndexItem(
            session_id=str(r["session_id"]),
            candidate_id=str(r["candidate_id"]) if r["candidate_id"] else None,
            candidate_name=r["candidate_name"],
            job_title=r["job_title"],
            stage_name=r["stage_name"],
            completed_at=r["completed_at"].isoformat() if r["completed_at"] else None,
            report_status=r["report_status"],
            verdict=r["verdict"],
            overall_score=r["overall_score"],
        )
        for r in rows
    ]
    return ReportIndexPage(
        items=items, total=int(total), offset=offset, limit=limit
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /api/reports/{report_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{report_id}",
    summary="Get a report by its id",
)
async def get_report_by_id(
    report_id: uuid_mod.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Return the report identified by report_id (tenant-scoped).

    RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id

    result = await db.execute(
        select(SessionReport).where(
            SessionReport.id == report_id,
            SessionReport.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")

    read = _row_to_read(row)
    await attach_question_thumbnails(
        db=db, report=read, session_id=row.session_id, tenant_id=tenant_id)
    await attach_reference_photo(
        db=db, report=read, session_id=row.session_id, tenant_id=tenant_id)
    return read.model_dump(mode="json")


# ---------------------------------------------------------------------------
# POST /api/reports/session/{session_id}/regenerate
# ---------------------------------------------------------------------------


@router.post(
    "/session/{session_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-enqueue report scoring for a session",
)
async def regenerate_report(
    session_id: uuid_mod.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    """Enqueue score_session_report with force=True and return 202.

    RBAC: super-admin only (privileged/destructive — matches the PII-redaction
    gate pattern from candidates/router.py which also uses `user.is_super_admin`
    for the destructive-action gate).
    """
    _require_super_admin(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id
    correlation_id = _get_correlation_id(request)

    score_session_report.send(
        str(session_id),
        str(tenant_id),
        correlation_id,
        True,  # force=True
    )

    _log.info(
        "reporting.regenerate.enqueued",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )

    return {"status": "accepted", "session_id": str(session_id)}


# ---------------------------------------------------------------------------
# POST /api/reports/session/{session_id}/share
# ---------------------------------------------------------------------------


@router.post(
    "/session/{session_id}/share",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email this report as a PDF to an external recipient",
)
async def share_report(
    session_id: uuid_mod.UUID,
    body: ShareReportIn,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Create a report_shares row, enqueue the PDF render+email actor, return 202.

    RBAC: reports.view or super-admin (same set that can view the report).
    Preconditions: the report exists and is ``ready`` (else 409). Note: this
    codebase enforces rate limits via global middleware only — no per-route
    decorator exists; the intended 'report share' class (10/min per-IP +
    per-tenant daily cap) is documented in the design spec.
    """
    _require_reports_view(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id
    correlation_id = _get_correlation_id(request)

    report_row = (await db.execute(
        select(SessionReport).where(
            SessionReport.session_id == session_id,
            SessionReport.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if report_row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report_row.status != "ready":
        raise HTTPException(status_code=409, detail="Report is not ready to share")

    share = ReportShare(
        tenant_id=tenant_id,
        session_id=session_id,
        report_id=report_row.id,
        recipient_email=str(body.recipient_email),
        status="pending",
        requested_by=user.user.id,
    )
    db.add(share)
    await db.flush()  # populate share.id

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="session_report.shared",
        resource="session_report",
        resource_id=report_row.id,
        payload={"share_id": str(share.id), "correlation_id": correlation_id},
    )

    share_report_pdf.send(str(share.id), str(tenant_id), correlation_id)

    _log.info(
        "reporting.share.enqueued",
        session_id=str(session_id),
        share_id=str(share.id),
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    return ShareReportOut(share_id=str(share.id), status="pending").model_dump()


# ---------------------------------------------------------------------------
# POST /api/reports/session/{session_id}/proctoring/retry
# ---------------------------------------------------------------------------


@router.post(
    "/session/{session_id}/proctoring/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run post-session vision proctoring analysis",
)
async def retry_proctoring(
    session_id: uuid_mod.UUID,
    request: Request,
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    """Re-enqueue vision proctoring for a session and return 202.

    A ``failed`` proctoring row is terminal from the report-read enqueue path
    (Dramatiq has already exhausted its own per-message retries), so recovery is
    an explicit action — not a side effect of viewing the report. The actor
    reclaims a failed/running/pending row and re-runs it; a successfully
    completed (ready/unscorable) row is left as-is. RBAC: super-admin only
    (matches report regenerate).
    """
    _require_super_admin(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id
    correlation_id = _get_correlation_id(request)

    # Lazy import (mirrors session/recording.py): keeps the API process's import
    # graph light and makes the enqueue trivially monkeypatchable in tests.
    from app.modules.vision import analyze_session_proctoring  # noqa: PLC0415

    analyze_session_proctoring.send(str(session_id), str(tenant_id))

    _log.info(
        "reporting.proctoring.retry.enqueued",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    return {"status": "accepted", "session_id": str(session_id)}


# ---------------------------------------------------------------------------
# POST /api/reports/{report_id}/decision
# ---------------------------------------------------------------------------


@router.post(
    "/{report_id}/decision",
    summary="Record a human advancement decision on a report",
)
async def post_human_decision(
    report_id: uuid_mod.UUID,
    body: HumanDecisionIn,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Update SessionReport.human_decision JSONB and write an audit row.

    RBAC: reports.view or super-admin (the recruiter-class roles that make
    advancement decisions are the same set that can view reports).
    """
    _require_reports_view(user)

    tenant_id: uuid_mod.UUID = user.user.tenant_id
    correlation_id = _get_correlation_id(request)

    result = await db.execute(
        select(SessionReport).where(
            SessionReport.id == report_id,
            SessionReport.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")

    # Update the human_decision JSONB field
    now_iso = datetime.now(UTC).isoformat()
    row.human_decision = {
        "decided_by": str(user.user.id),
        "decision": body.decision,
        "rationale": body.rationale,
        "decided_at": now_iso,
    }
    await db.flush()

    # Append to audit log — never raises (audit module swallows)
    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="session_report.decision_recorded",
        resource="session_report",
        resource_id=report_id,
        payload={
            "decision": body.decision,
            "correlation_id": correlation_id,
        },
    )

    _log.info(
        "reporting.decision.recorded",
        report_id=str(report_id),
        decision=body.decision,
        actor_id=str(user.user.id),
        correlation_id=correlation_id,
    )

    return _row_to_read(row).model_dump(mode="json")
