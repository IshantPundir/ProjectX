"""JD module HTTP surface.

All business logic lives in service.py; this module is request/response
orchestration only. API prefix /api/jobs matches the Phase 1 convention
(no /v1/ versioning segment)."""

import uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_tenant_db, get_tenant_session
from app.models import JobPostingSignalSnapshot
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.jd.actors import extract_and_enhance_jd
from app.modules.jd.authz import _get_org_unit_ancestry, require_job_access
from app.modules.jd.schemas import (
    JobPostingCreate,
    JobPostingSummary,
    JobPostingWithSnapshot,
    SaveSignalsRequest,
    SignalItemResponse,
    SignalSnapshotResponse,
)
from app.modules.jd.service import (
    confirm_signals,
    create_job_posting,
    get_job_posting_with_latest_snapshot,
    list_job_postings,
    retry_failed_extraction,
    save_signals,
    trigger_reenrichment,
)
from app.modules.jd.state_machine import transition
from app.modules.jd.sse import job_status_event_generator

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
_log = structlog.get_logger()


def _snapshot_to_response(
    snap: JobPostingSignalSnapshot | None,
) -> SignalSnapshotResponse | None:
    if snap is None:
        return None
    return SignalSnapshotResponse(
        version=snap.version,
        required_skills=[SignalItemResponse(**item) for item in snap.required_skills],
        preferred_skills=[SignalItemResponse(**item) for item in snap.preferred_skills],
        must_haves=[SignalItemResponse(**item) for item in snap.must_haves],
        good_to_haves=[SignalItemResponse(**item) for item in snap.good_to_haves],
        min_experience_years=snap.min_experience_years,
        seniority_level=snap.seniority_level,
        role_summary=snap.role_summary,
    )


def _job_to_summary(job) -> JobPostingSummary:
    return JobPostingSummary(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        status=job.status,
        status_error=job.status_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _job_with_snapshot_to_response(job, snap) -> JobPostingWithSnapshot:
    return JobPostingWithSnapshot(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        description_raw=job.description_raw,
        project_scope_raw=job.project_scope_raw,
        description_enriched=job.description_enriched,
        status=job.status,
        status_error=job.status_error,
        target_headcount=job.target_headcount,
        deadline=job.deadline,
        created_at=job.created_at,
        updated_at=job.updated_at,
        latest_snapshot=_snapshot_to_response(snap),
        enrichment_status=job.enrichment_status,
        enrichment_error=job.enrichment_error,
        is_confirmed=snap.confirmed_at is not None if snap else False,
    )


async def _safe_dispatch_extraction(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Enqueue the Dramatiq actor, transitioning the job to failed if Redis
    is unreachable. FastAPI BackgroundTasks silently swallow exceptions, so
    without this wrapper a Redis outage leaves the job stuck in
    signals_extracting forever with no error visible to the user."""
    try:
        extract_and_enhance_jd.send(
            job_posting_id=job_posting_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        _log.error(
            "jd.dispatch_failed",
            job_posting_id=job_posting_id,
            exc_info=exc,
        )
        # Open a new session to transition the job to failed — the request's
        # session is already closed by the time BackgroundTasks run.
        from sqlalchemy import select as sa_select

        from app.models import JobPosting

        async with get_tenant_session(tenant_id) as db:
            result = await db.execute(
                sa_select(JobPosting).where(JobPosting.id == UUID(job_posting_id))
            )
            job = result.scalar_one_or_none()
            if job and job.status == "signals_extracting":
                job.status_error = (
                    "Failed to dispatch extraction job — please retry. "
                    "If this persists, contact support."
                )
                await transition(
                    db,
                    job,
                    to_state="signals_extraction_failed",
                    actor_id=None,
                    correlation_id=correlation_id,
                )


async def _safe_dispatch_reenrichment(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Enqueue the reenrich_jd Dramatiq actor, setting enrichment_status to
    'failed' if Redis is unreachable. Same pattern as _safe_dispatch_extraction."""
    try:
        from app.modules.jd.actors import reenrich_jd

        reenrich_jd.send(
            job_posting_id=job_posting_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        _log.error(
            "jd.reenrich_dispatch_failed",
            job_posting_id=job_posting_id,
            exc_info=exc,
        )
        from sqlalchemy import select as sa_select

        from app.models import JobPosting

        async with get_tenant_session(tenant_id) as db:
            result = await db.execute(
                sa_select(JobPosting).where(JobPosting.id == UUID(job_posting_id))
            )
            job = result.scalar_one_or_none()
            if job:
                job.enrichment_status = "failed"
                job.enrichment_error = (
                    "Failed to dispatch re-enrichment job — please retry. "
                    "If this persists, contact support."
                )


def _visible_unit_ids(user: UserContext, permission: str) -> list[UUID] | None:
    """Return the flat list of org unit IDs where the user holds `permission`,
    or None if the user is a super admin (no filter needed).

    Note: this is the immediate-grant set, not the ancestry-expanded set.
    For listing, this is the right semantic — we want jobs whose org unit
    matches an ancestor where the user has the permission. The service
    layer (list_job_postings) handles the visibility query."""
    if user.is_super_admin:
        return None
    return [a.org_unit_id for a in user.assignments if permission in a.permissions]


@router.post("", status_code=201, response_model=JobPostingWithSnapshot)
async def create_job(
    body: JobPostingCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    # jobs.create is enforced via ancestry walk on the target org unit
    if not user.is_super_admin:
        ancestry = await _get_org_unit_ancestry(db, body.org_unit_id)
        if not any(user.has_permission_in_unit(u.id, "jobs.create") for u in ancestry):
            raise HTTPException(
                status_code=403, detail="Missing jobs.create in ancestry"
            )

    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    job = await create_job_posting(
        db,
        tenant_id=user.user.tenant_id,
        created_by=user.user.id,
        org_unit_id=body.org_unit_id,
        title=body.title,
        description_raw=body.description_raw,
        project_scope_raw=body.project_scope_raw,
        target_headcount=body.target_headcount,
        deadline=body.deadline,
        correlation_id=correlation_id,
    )

    # Enqueue the Dramatiq actor AFTER the response is sent. FastAPI's
    # BackgroundTasks run after the dependency's `async with session.begin()`
    # context manager exits (which auto-commits the transaction), so the
    # worker is guaranteed to see the committed job row when it dequeues.
    # _safe_dispatch_extraction wraps the send() call so a Redis outage
    # transitions the job to failed instead of leaving it stuck forever.
    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_posting_id=str(job.id),
        tenant_id=str(user.user.tenant_id),
        correlation_id=correlation_id,
    )

    # Build response directly from the in-memory job. latest_snapshot is
    # always None at creation time (the actor hasn't run yet). expire_on_commit
    # is False on async_session_factory, so attribute access after the
    # dependency's auto-commit remains safe — but we don't rely on that:
    # we build the response BEFORE returning, while the session is still
    # alive inside the context manager.
    return _job_with_snapshot_to_response(job, None)


@router.get("", response_model=list[JobPostingSummary])
async def list_jobs(
    org_unit_id: UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[JobPostingSummary]:
    visible = _visible_unit_ids(user, "jobs.view")
    jobs = await list_job_postings(
        db,
        visible_org_unit_ids=visible,
        org_unit_filter=org_unit_id,
        status_filter=status,
    )
    return [_job_to_summary(j) for j in jobs]


@router.get("/{job_id}", response_model=JobPostingWithSnapshot)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    await require_job_access(db, job_id, user, "view")
    job, snap = await get_job_posting_with_latest_snapshot(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_with_snapshot_to_response(job, snap)


@router.get("/{job_id}/status/stream")
async def stream_status(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> EventSourceResponse:
    await require_job_access(db, job_id, user, "view")
    # Pass tenant_id instead of the DB session — the SSE generator opens
    # short-lived sessions per poll to avoid holding a pool connection for
    # the entire stream duration.
    return EventSourceResponse(
        job_status_event_generator(
            str(user.user.tenant_id), job_id, request
        )
    )


@router.post("/{job_id}/retry", status_code=202, response_model=JobPostingSummary)
async def retry_extraction(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingSummary:
    await require_job_access(db, job_id, user, "manage")
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    job = await retry_failed_extraction(
        db,
        job_id=job_id,
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )

    # Same post-commit enqueue pattern as create_job.
    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
    )

    return _job_to_summary(job)


@router.patch("/{job_id}/signals", response_model=SignalSnapshotResponse)
async def update_signals(
    job_id: UUID,
    body: SaveSignalsRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> SignalSnapshotResponse:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status not in ("signals_extracted", "signals_confirmed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit signals in status '{job.status}'",
        )
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    snapshot = await save_signals(
        db,
        job=job,
        body=body,
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )
    return _snapshot_to_response(snapshot)  # type: ignore[return-value]


@router.post("/{job_id}/signals/confirm", response_model=JobPostingSummary)
async def confirm_signals_endpoint(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingSummary:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status != "signals_extracted":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm signals in status '{job.status}'",
        )
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    job = await confirm_signals(
        db,
        job=job,
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )
    return _job_to_summary(job)


@router.post("/{job_id}/enrich", status_code=202)
async def enrich_job(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status not in ("signals_extracted", "signals_confirmed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot trigger re-enrichment in status '{job.status}'",
        )
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    await trigger_reenrichment(db, job=job)

    background_tasks.add_task(
        _safe_dispatch_reenrichment,
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
    )

    return {"status": "accepted"}
