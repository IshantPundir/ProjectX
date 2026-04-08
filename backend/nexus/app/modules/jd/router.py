"""JD module HTTP surface.

All business logic lives in service.py; this module is request/response
orchestration only. API prefix /api/jobs matches the Phase 1 convention
(no /v1/ versioning segment)."""

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_tenant_db
from app.models import JobPostingSignalSnapshot
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.jd.authz import _get_org_unit_ancestry, require_job_access
from app.modules.jd.schemas import (
    JobPostingCreate,
    JobPostingSummary,
    JobPostingWithSnapshot,
    SignalItemResponse,
    SignalSnapshotResponse,
)
from app.modules.jd.service import (
    create_job_posting,
    get_job_posting_with_latest_snapshot,
    list_job_postings,
    retry_failed_extraction,
)
from app.modules.jd.sse import job_status_event_generator

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


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
    await db.commit()
    job, snap = await get_job_posting_with_latest_snapshot(db, job.id)
    return _job_with_snapshot_to_response(job, snap)


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
    return EventSourceResponse(job_status_event_generator(db, job_id, request))


@router.post("/{job_id}/retry", status_code=202, response_model=JobPostingSummary)
async def retry_extraction(
    job_id: UUID,
    request: Request,
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
    await db.commit()
    return _job_to_summary(job)
