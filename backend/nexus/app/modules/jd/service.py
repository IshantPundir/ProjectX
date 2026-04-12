"""JD module business logic.

All mutations to job_postings.status go through state_machine.transition().
The Dramatiq actor is imported lazily inside create_job_posting() to avoid
a circular import (actors.py imports service.py for the snapshot persist)."""

from datetime import UTC, date, datetime
from uuid import UUID

import structlog
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.errors import CompanyProfileIncompleteError, IllegalTransitionError
from app.modules.jd.schemas import JobStatusEvent, SaveSignalsRequest
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
    employment_type: str | None = None,
    work_arrangement: str | None = None,
    location: str | None = None,
    salary_range_min: int | None = None,
    salary_range_max: int | None = None,
    salary_currency: str | None = None,
    travel_required: str | None = None,
    start_date_pref: str | None = None,
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
        employment_type=employment_type,
        work_arrangement=work_arrangement,
        location=location,
        salary_range_min=salary_range_min,
        salary_range_max=salary_range_max,
        salary_currency=salary_currency,
        travel_required=travel_required,
        start_date_pref=start_date_pref,
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
        enrichment_status=job.enrichment_status,
        is_confirmed=snapshot.confirmed_at is not None if snapshot else False,
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


async def save_signals(
    db: AsyncSession,
    *,
    job: JobPosting,
    body: SaveSignalsRequest,
    actor_id: UUID,
    correlation_id: str,
) -> JobPostingSignalSnapshot:
    """Write a new snapshot version from recruiter edits.

    If job was signals_confirmed, auto-transitions back to signals_extracted
    so the recruiter can re-confirm after editing. The new snapshot has
    confirmed_by=None, confirmed_at=None."""
    if job.status == "signals_confirmed":
        await transition(
            db,
            job,
            to_state="signals_extracted",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    # Signals changed — enriched JD is now stale. Clear any prior
    # enrichment error so the frontend doesn't show a contradictory state
    # (idle status + old error message).
    job.enrichment_status = "idle"
    job.enrichment_error = None
    job.updated_by = actor_id

    # Lock the job row to prevent concurrent save_signals calls from
    # computing the same MAX(version) and hitting a UniqueConstraint.
    await db.execute(
        select(JobPosting.id)
        .where(JobPosting.id == job.id)
        .with_for_update()
    )

    # Determine next snapshot version
    max_version_result = await db.execute(
        select(func.max(JobPostingSignalSnapshot.version)).where(
            JobPostingSignalSnapshot.job_posting_id == job.id
        )
    )
    current_max = max_version_result.scalar() or 0

    snapshot = JobPostingSignalSnapshot(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        version=current_max + 1,
        signals=[item.model_dump() for item in body.signals],
        seniority_level=body.seniority_level,
        role_summary=body.role_summary,
        confirmed_by=None,
        confirmed_at=None,
    )
    db.add(snapshot)
    await db.flush()

    logger.info(
        "jd.service.signals_saved",
        job_posting_id=str(job.id),
        snapshot_version=snapshot.version,
        correlation_id=correlation_id,
    )
    return snapshot


async def confirm_signals(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    correlation_id: str,
) -> JobPosting:
    """Confirm the latest snapshot — sets confirmed_by/at and transitions
    job to signals_confirmed.

    Raises:
        ValueError: if no snapshot exists for this job.
    """
    snap_result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job.id)
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    if snapshot is None:
        raise ValueError("No snapshot to confirm")

    snapshot.confirmed_by = actor_id
    snapshot.confirmed_at = datetime.now(UTC)
    job.updated_by = actor_id

    await transition(
        db,
        job,
        to_state="signals_confirmed",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    await db.flush()

    logger.info(
        "jd.service.signals_confirmed",
        job_posting_id=str(job.id),
        snapshot_version=snapshot.version,
        correlation_id=correlation_id,
    )

    # Auto-apply pipeline on signal confirmation.
    # Failures here must NOT block the confirmation — the job is already
    # confirmed. Log the error and continue.
    try:
        from app.modules.pipelines.service import auto_apply_pipeline_on_confirmation

        await auto_apply_pipeline_on_confirmation(
            db, job=job, actor_id=actor_id,
        )
    except Exception as exc:
        logger.error(
            "jd.pipeline_auto_apply_failed",
            job_posting_id=str(job.id),
            exc_info=exc,
        )
        from app.modules.audit.service import log_event
        try:
            await log_event(
                db,
                tenant_id=job.tenant_id,
                actor_id=actor_id,
                actor_email=None,
                action="job_pipeline.auto_apply_failed",
                resource="job_posting",
                resource_id=job.id,
                payload={"error": str(exc)[:500]},
            )
        except Exception:
            pass  # audit log failure should never cascade

    return job


async def trigger_reenrichment(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID | None = None,
) -> JobPosting:
    """Set enrichment_status to 'streaming' and clear any previous error.

    Raises:
        IllegalTransitionError: if already streaming (prevents double-dispatch).
    """
    if job.enrichment_status == "streaming":
        raise IllegalTransitionError(
            from_state="enrichment:streaming",
            to_state="enrichment:streaming",
        )

    job.enrichment_status = "streaming"
    job.enrichment_error = None
    if actor_id:
        job.updated_by = actor_id
    await db.flush()

    logger.info(
        "jd.service.reenrichment_triggered",
        job_posting_id=str(job.id),
    )
    return job


async def delete_job_posting(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Delete a job posting and its snapshots (CASCADE).

    Does NOT delete jobs that are actively being processed
    (signals_extracting or enrichment streaming)."""
    if job.status == "signals_extracting":
        raise ValueError("Cannot delete a job while signals are being extracted")
    if job.enrichment_status == "streaming":
        raise ValueError("Cannot delete a job while re-enrichment is in progress")

    from app.modules.audit import actions as audit_actions
    from app.modules.audit.service import log_event

    await log_event(
        db,
        tenant_id=job.tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.JOB_POSTING_DELETED
        if hasattr(audit_actions, "JOB_POSTING_DELETED")
        else "job_posting.deleted",
        resource="job_posting",
        resource_id=job.id,
        payload={"title": job.title, "status": job.status},
        ip_address=ip_address,
    )

    await db.delete(job)
    logger.info("jd.service.job_deleted", job_posting_id=str(job.id))
