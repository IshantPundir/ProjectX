"""JD module business logic.

All mutations to job_postings.status go through state_machine.transition().
The Dramatiq actor is imported lazily inside create_job_posting() to avoid
a circular import (actors.py imports service.py for the snapshot persist)."""

from datetime import date
from uuid import UUID

import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.errors import CompanyProfileIncompleteError
from app.modules.jd.schemas import JobStatusEvent
from app.modules.jd.state_machine import transition
from app.modules.org_units.service import find_company_profile_in_ancestry

logger = structlog.get_logger()


async def create_job_posting(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    created_by: UUID,
    org_unit_id: UUID,
    title: str,
    description_raw: str,
    project_scope_raw: str | None,
    target_headcount: int | None,
    deadline: date | None,
    correlation_id: str,
) -> JobPosting:
    """Validate profile ancestry, INSERT job_postings in 'draft', transition
    to 'signals_extracting'. DOES NOT commit and DOES NOT enqueue the actor.

    Commit is handled by the dependency's context manager (get_tenant_db
    wraps the session in `async with session.begin()` — auto-commits on
    successful exit). Enqueue is handled by the router via FastAPI
    BackgroundTasks so the .send() call happens AFTER the transaction
    commits — this narrows (but does not eliminate) the dual-write race
    where a fast worker could dequeue before the DB commit lands. See
    Deferred Hardening #9 in the spec.

    Raises:
        CompanyProfileIncompleteError: no ancestor has a completed profile.
    """
    profile = await find_company_profile_in_ancestry(db, org_unit_id)
    if profile is None:
        raise CompanyProfileIncompleteError(org_unit_id)

    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title=title,
        description_raw=description_raw,
        project_scope_raw=project_scope_raw,
        target_headcount=target_headcount,
        deadline=deadline,
        status="draft",
        source="native",
        created_by=created_by,
    )
    db.add(job)
    await db.flush()

    await transition(
        db,
        job,
        to_state="signals_extracting",
        actor_id=created_by,
        correlation_id=correlation_id,
    )
    await db.flush()

    logger.info(
        "jd.service.created",
        job_posting_id=str(job.id),
        org_unit_id=str(org_unit_id),
        correlation_id=correlation_id,
    )
    return job


async def get_job_posting_with_latest_snapshot(
    db: AsyncSession, job_id: UUID
) -> tuple[JobPosting | None, JobPostingSignalSnapshot | None]:
    """Load a job and its latest snapshot in a single call. RLS scopes
    the query to the current tenant. Returns (None, None) if not found."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None, None

    snap_result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job_id)
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    return job, snapshot


async def list_job_postings(
    db: AsyncSession,
    *,
    visible_org_unit_ids: list[UUID] | None,
    org_unit_filter: UUID | None = None,
    status_filter: str | None = None,
) -> list[JobPosting]:
    """List jobs in the current tenant (RLS) optionally constrained to a
    set of visible org unit IDs.

    visible_org_unit_ids carries the pre-computed union of all org units
    where the user has jobs.view permission in ancestry. If None, the
    caller is a super admin and all tenant rows are returned."""
    stmt = select(JobPosting)
    if visible_org_unit_ids is not None:
        stmt = stmt.where(JobPosting.org_unit_id.in_(visible_org_unit_ids))
    if org_unit_filter is not None:
        stmt = stmt.where(JobPosting.org_unit_id == org_unit_filter)
    if status_filter is not None:
        stmt = stmt.where(JobPosting.status == status_filter)
    stmt = stmt.order_by(desc(JobPosting.created_at))

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_job_status(db: AsyncSession, job_id: UUID) -> JobStatusEvent | None:
    """Build a JobStatusEvent from the current DB state. Used by sse.py."""
    job, snapshot = await get_job_posting_with_latest_snapshot(db, job_id)
    if job is None:
        return None
    return JobStatusEvent(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        error=job.status_error,
        signal_snapshot_version=snapshot.version if snapshot else None,
    )


async def retry_failed_extraction(
    db: AsyncSession,
    *,
    job_id: UUID,
    actor_id: UUID,
    correlation_id: str,
) -> JobPosting:
    """Precondition: job.status == 'signals_extraction_failed'.
    Transitions via state_machine (which enforces the precondition) and
    clears status_error. DOES NOT commit and DOES NOT enqueue the actor —
    the router handles both via BackgroundTasks (see create_job_posting
    docstring for rationale)."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one()

    await transition(
        db,
        job,
        to_state="signals_extracting",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    job.status_error = None  # clear the previous error message
    await db.flush()

    return job
