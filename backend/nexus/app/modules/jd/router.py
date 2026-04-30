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

from app import pubsub
from app.database import get_tenant_db, get_tenant_session
from app.models import JobPostingSignalSnapshot
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.jd.actors import extract_and_enhance_jd
from app.modules.jd.authz import require_job_access
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.jd.schemas import (
    JobPostingCreate,
    JobPostingSummary,
    JobPostingWithSnapshot,
    SaveSignalsRequest,
    SignalItemResponse,
    SignalSnapshotResponse,
)
from app.modules.jd.errors import ActivationPredicatesFailed, IllegalTransitionError
from app.modules.jd.service import (
    _job_to_summary,
    activate_job,
    confirm_signals,
    create_job_posting,
    enrich_job_summaries,
    get_job_posting_with_latest_snapshot,
    get_job_status,
    list_job_postings,
    retry_failed_extraction,
    save_signals,
    trigger_reenrichment,
)
from app.modules.jd.state_machine import transition
from app.modules.jd.sse import job_status_event_generator

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
_log = structlog.get_logger()

# Max length for an inbound x-correlation-id header. 128 is generous — uuid4
# is 36 chars — but caps log-field growth and blocks pathological values.
_MAX_CORRELATION_ID_LEN = 128


def _get_correlation_id(request: Request) -> str:
    """Extract x-correlation-id or mint a fresh uuid4.

    The header is untrusted input, so we validate before propagating it to
    logs, OTel span attributes, and actor kwargs:
      - must be non-empty and <= 128 chars
      - must be printable ASCII (no control chars, no unicode)
    Invalid values are discarded and replaced with a fresh uuid4 so a
    forensic trail is still preserved per-request.
    """
    raw = request.headers.get("x-correlation-id")
    if raw and 0 < len(raw) <= _MAX_CORRELATION_ID_LEN and raw.isascii() and raw.isprintable():
        return raw
    return str(uuid.uuid4())


def _snapshot_to_response(
    snap: JobPostingSignalSnapshot | None,
) -> SignalSnapshotResponse | None:
    if snap is None:
        return None

    from app.modules.jd.schemas import default_evaluation_method

    response_signals = []
    for item in snap.signals:
        eval_method = item.get("evaluation_method") or default_evaluation_method(
            item["type"], item["stage"]
        )
        response_signals.append(
            SignalItemResponse(
                value=item["value"],
                type=item["type"],
                priority=item["priority"],
                weight=item.get("weight", 2),
                knockout=item.get("knockout", False),
                stage=item["stage"],
                evaluation_method=eval_method,
                evaluation_hint=item.get("evaluation_hint"),
                source=item["source"],
                inference_basis=item.get("inference_basis"),
            )
        )

    return SignalSnapshotResponse(
        version=snap.version,
        signals=response_signals,
        seniority_level=snap.seniority_level,
        role_summary=snap.role_summary,
        confirmed_by=snap.confirmed_by,
        confirmed_at=snap.confirmed_at,
    )



def _job_with_snapshot_to_response(
    job,
    snap,
    *,
    can_manage: bool = False,
    enriched: "JobPostingSummary | None" = None,
) -> JobPostingWithSnapshot:
    return JobPostingWithSnapshot(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        org_unit_name=enriched.org_unit_name if enriched else None,
        created_by_email=enriched.created_by_email if enriched else None,
        updated_by_email=enriched.updated_by_email if enriched else None,
        signal_count=enriched.signal_count if enriched else 0,
        needs_review_count=enriched.needs_review_count if enriched else 0,
        description_raw=job.description_raw,
        project_scope_raw=job.project_scope_raw,
        description_enriched=job.description_enriched,
        status=job.status,
        status_error=job.status_error,
        target_headcount=job.target_headcount,
        deadline=job.deadline,
        employment_type=job.employment_type,
        work_arrangement=job.work_arrangement,
        location=job.location,
        salary_range_min=job.salary_range_min,
        salary_range_max=job.salary_range_max,
        salary_currency=job.salary_currency,
        travel_required=job.travel_required,
        start_date_pref=job.start_date_pref,
        created_at=job.created_at,
        updated_at=job.updated_at,
        latest_snapshot=_snapshot_to_response(snap),
        enrichment_status=job.enrichment_status,
        enrichment_error=job.enrichment_error,
        is_confirmed=snap.confirmed_at is not None if snap else False,
        can_manage=can_manage,
    )


async def _safe_dispatch_extraction(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    skip_enrichment: bool = False,
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
            skip_enrichment=skip_enrichment,
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
                from app.modules.audit.service import log_event

                await log_event(
                    db,
                    tenant_id=job.tenant_id,
                    actor_id=None,
                    actor_email=None,
                    action="job_posting.reenrich_dispatch_failed",
                    resource="job_posting",
                    resource_id=job.id,
                    payload={"error": str(exc)},
                    ip_address=None,
                )


def _visible_unit_ids(user: UserContext, permission: str) -> list[UUID] | None:
    """Return org unit IDs where the user can see jobs — the directly
    assigned units PLUS all their descendants.

    A recruiter with jobs.view on a 'division' should see jobs in child
    'team' units too. Without descendant expansion, jobs under child units
    are invisible in the list (even though require_job_access walks
    ancestry for single-job access)."""
    if user.is_super_admin:
        return None
    return [a.org_unit_id for a in user.assignments if permission in a.permissions]


async def _expand_with_descendants(
    db: AsyncSession, unit_ids: list[UUID],
) -> list[UUID]:
    """Given a list of org unit IDs, return those IDs plus all their
    descendants (children, grandchildren, etc.) by walking the tree."""
    if not unit_ids:
        return []

    from sqlalchemy import select as sa_select

    from app.models import OrganizationalUnit

    # Load all units in the tenant (already RLS-scoped by the session)
    result = await db.execute(sa_select(OrganizationalUnit))
    all_units = result.scalars().all()

    # Build parent→children map
    children_map: dict[UUID, list[UUID]] = {}
    for u in all_units:
        if u.parent_unit_id:
            children_map.setdefault(u.parent_unit_id, []).append(u.id)

    # BFS to collect all descendants
    expanded: set[UUID] = set(unit_ids)
    queue = list(unit_ids)
    while queue:
        current = queue.pop()
        for child_id in children_map.get(current, []):
            if child_id not in expanded:
                expanded.add(child_id)
                queue.append(child_id)

    return list(expanded)


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
        ancestry = await get_org_unit_ancestry(db, body.org_unit_id)
        if not any(user.has_permission_in_unit(u.id, "jobs.create") for u in ancestry):
            raise HTTPException(
                status_code=403, detail="Missing jobs.create in ancestry"
            )

    correlation_id = _get_correlation_id(request)
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
        employment_type=body.employment_type,
        work_arrangement=body.work_arrangement,
        location=body.location,
        salary_range_min=body.salary_range_min,
        salary_range_max=body.salary_range_max,
        salary_currency=body.salary_currency,
        travel_required=body.travel_required,
        start_date_pref=body.start_date_pref,
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
        skip_enrichment=body.skip_enrichment,
    )

    # Publish the initial state so SSE subscribers connecting immediately
    # after creation see the status without waiting for the 5s backstop poll.
    # get_job_status is called here (inside the handler, while the session is
    # still alive) so its return value is captured into the closure at
    # add_task time — safe to use after the session closes.
    status_event = await get_job_status(db, job.id)
    if status_event is not None:
        background_tasks.add_task(
            pubsub.publish,
            pubsub.job_channel(job.id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
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
    # Expand to include descendant org units so a recruiter assigned to
    # a parent division can see jobs in child teams.
    if visible is not None:
        visible = await _expand_with_descendants(db, visible)
    jobs = await list_job_postings(
        db,
        visible_org_unit_ids=visible,
        org_unit_filter=org_unit_id,
        status_filter=status,
    )
    return await enrich_job_summaries(jobs, db)


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

    # Check if the user has manage permission (for the Edit/Confirm UI).
    # Super admins can always manage. For others, walk the ancestry.
    can_manage = user.is_super_admin
    if not can_manage:
        ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
        can_manage = any(
            user.has_permission_in_unit(u.id, "jobs.manage") for u in ancestry
        )

    summaries = await enrich_job_summaries([job], db)
    enriched = summaries[0]
    return _job_with_snapshot_to_response(job, snap, can_manage=can_manage, enriched=enriched)


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
    correlation_id = _get_correlation_id(request)
    job = await retry_failed_extraction(
        db,
        job_id=job_id,
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )

    # Infer skip_enrichment intent from persisted state:
    # A job created with skip_enrichment=True never enters the enrichment
    # phase, so enrichment_status stays 'idle' and description_enriched
    # remains NULL. Phase 1 always transitions enrichment_status to at
    # least 'streaming' on first attempt, so idle+null uniquely identifies
    # a skip-enrichment job that failed before phase 1 ever began.
    inferred_skip_enrichment = (
        job.enrichment_status == "idle" and job.description_enriched is None
    )

    # Same post-commit enqueue pattern as create_job.
    background_tasks.add_task(
        _safe_dispatch_extraction,
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
        skip_enrichment=inferred_skip_enrichment,
    )

    status_event = await get_job_status(db, job_id)
    if status_event is not None:
        background_tasks.add_task(
            pubsub.publish,
            pubsub.job_channel(job_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
            correlation_id=correlation_id,
        )

    enriched = await enrich_job_summaries([job], db)
    return enriched[0]


@router.patch("/{job_id}/signals", response_model=SignalSnapshotResponse)
async def update_signals(
    job_id: UUID,
    body: SaveSignalsRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> SignalSnapshotResponse:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status not in ("signals_extracted", "signals_confirmed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit signals in status '{job.status}'",
        )
    correlation_id = _get_correlation_id(request)
    snapshot = await save_signals(
        db,
        job=job,
        body=body,
        actor_id=user.user.id,
        correlation_id=correlation_id,
    )

    # Publish so SSE subscribers see the new snapshot version without waiting
    # for the backstop poll. This was previously silent for SSE — only
    # status/enrichment_status diffs emitted. New behavior: every save emits.
    status_event = await get_job_status(db, job_id)
    if status_event is not None:
        background_tasks.add_task(
            pubsub.publish,
            pubsub.job_channel(job_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
            correlation_id=correlation_id,
        )

    return _snapshot_to_response(snapshot)  # type: ignore[return-value]


@router.post("/{job_id}/signals/confirm", response_model=JobPostingSummary)
async def confirm_signals_endpoint(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingSummary:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status != "signals_extracted":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm signals in status '{job.status}'",
        )
    correlation_id = _get_correlation_id(request)
    try:
        job = await confirm_signals(
            db,
            job=job,
            actor_id=user.user.id,
            correlation_id=correlation_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    status_event = await get_job_status(db, job_id)
    if status_event is not None:
        background_tasks.add_task(
            pubsub.publish,
            pubsub.job_channel(job_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
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
    correlation_id = _get_correlation_id(request)
    await trigger_reenrichment(db, job=job, actor_id=user.user.id)

    background_tasks.add_task(
        _safe_dispatch_reenrichment,
        job_posting_id=str(job.id),
        tenant_id=str(job.tenant_id),
        correlation_id=correlation_id,
    )

    status_event = await get_job_status(db, job_id)
    if status_event is not None:
        background_tasks.add_task(
            pubsub.publish,
            pubsub.job_channel(job_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
            correlation_id=correlation_id,
        )

    return {"status": "accepted"}


@router.post("/{job_id}/activate")
async def activate_job_endpoint(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict:
    """Transition a job from pipeline_built → active after running all activation
    gate predicates.

    Returns 409 if the job is not in pipeline_built state.
    Returns 422 with predicates_failed array if any predicate fails.
    Returns 200 {"status": "active", "job_id": "..."} on success.
    """
    job = await require_job_access(db, job_id, user, "manage")
    correlation_id = _get_correlation_id(request)
    try:
        await activate_job(
            db, job=job, actor_id=user.user.id, correlation_id=correlation_id,
        )
    except IllegalTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "job_not_in_pipeline_built_state",
                "message": str(e),
            },
        )
    except ActivationPredicatesFailed as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "activation_predicates_failed",
                "predicates_failed": [
                    {
                        "code": f.code,
                        "message": f.message,
                        "stage_id": str(f.stage_id) if f.stage_id else None,
                    }
                    for f in e.failures
                ],
            },
        )
    return {"status": "active", "job_id": str(job.id)}


@router.delete("/{job_id}", status_code=200)
async def delete_job(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    from app.modules.jd.service import delete_job_posting

    job = await require_job_access(db, job_id, user, "manage")
    try:
        await delete_job_posting(
            db,
            job=job,
            actor_id=user.user.id,
            actor_email=user.user.email,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "deleted"}
