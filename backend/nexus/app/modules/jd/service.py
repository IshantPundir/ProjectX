"""JD module business logic.

All mutations to job_postings.status go through state_machine.transition().
The Dramatiq actor is imported lazily inside create_job_posting() to avoid
a circular import (actors.py imports service.py for the snapshot persist)."""

from datetime import UTC, date, datetime
from uuid import UUID

import structlog
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import actions as audit_actions, log_event
from app.modules.auth import User
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.org_units import OrganizationalUnit, find_company_profile_in_ancestry
from app.modules.pipelines import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    bank_eligible_stage_types,
    human_led_stage_types,
    middle_stage_types_for_activation,
)
from app.modules.question_bank import StageQuestionBank, recompute_and_persist_stale
from app.modules.jd.errors import (
    ActivationPredicateFailure,
    ActivationPredicatesFailed,
    CompanyProfileIncompleteError,
    IllegalTransitionError,
)
from app.modules.jd.schemas import (
    JobPostingSummary,
    JobStatusEvent,
    SaveSignalsRequest,
)
from app.modules.jd.state_machine import transition

logger = structlog.get_logger()


def _job_to_summary(
    job: JobPosting,
    org_unit_name: str | None = None,
    created_by_email: str | None = None,
    updated_by_email: str | None = None,
    signal_count: int = 0,
    needs_review_count: int = 0,
) -> JobPostingSummary:
    return JobPostingSummary(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        org_unit_name=org_unit_name,
        created_by_email=created_by_email,
        updated_by_email=updated_by_email,
        status=job.status,
        status_error=job.status_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        signal_count=signal_count,
        needs_review_count=needs_review_count,
    )


async def enrich_job_summaries(
    jobs: list[JobPosting],
    db: AsyncSession,
) -> list[JobPostingSummary]:
    """Enrich a list of JobPosting rows with org_unit_name, creator/updater
    emails, signal_count, and needs_review_count.

    Single query per enrichment dimension (org units, users, snapshots) —
    no N+1. Safe to call from list, detail, and retry handlers alike.
    """
    if not jobs:
        return []

    # Collect unique lookup keys.
    unit_ids = {j.org_unit_id for j in jobs}
    user_ids = {j.created_by for j in jobs}
    for j in jobs:
        if j.updated_by:
            user_ids.add(j.updated_by)
    job_ids = [j.id for j in jobs]

    # Batch-load org unit names.
    unit_result = await db.execute(
        select(OrganizationalUnit.id, OrganizationalUnit.name).where(
            OrganizationalUnit.id.in_(unit_ids)
        )
    )
    unit_names: dict[UUID, str] = {row[0]: row[1] for row in unit_result.all()}

    # Batch-load user emails.
    user_result = await db.execute(
        select(User.id, User.email).where(User.id.in_(user_ids))
    )
    user_emails: dict[UUID, str] = {row[0]: row[1] for row in user_result.all()}

    # Batch-load the latest signal snapshot per job for aggregate counts.
    # We load full rows (not just the count) because the needs-review
    # count requires inspecting each signal's source + weight — the same
    # heuristic the frontend JD Review page uses for its "double-check"
    # chip, centralized here so the list and detail views agree.
    latest_version_subq = (
        select(
            JobPostingSignalSnapshot.job_posting_id,
            func.max(JobPostingSignalSnapshot.version).label("max_version"),
        )
        .where(JobPostingSignalSnapshot.job_posting_id.in_(job_ids))
        .group_by(JobPostingSignalSnapshot.job_posting_id)
        .subquery()
    )
    snapshot_result = await db.execute(
        select(JobPostingSignalSnapshot).join(
            latest_version_subq,
            and_(
                JobPostingSignalSnapshot.job_posting_id
                == latest_version_subq.c.job_posting_id,
                JobPostingSignalSnapshot.version == latest_version_subq.c.max_version,
            ),
        )
    )
    counts_by_job: dict[UUID, tuple[int, int]] = {}
    for snap in snapshot_result.scalars().all():
        signals = snap.signals or []
        needs = sum(
            1
            for s in signals
            if (
                isinstance(s, dict)
                and s.get("source") == "ai_inferred"
                and isinstance(s.get("weight"), (int, float))
                and s["weight"] < 2
            )
        )
        counts_by_job[snap.job_posting_id] = (len(signals), needs)

    # Build enriched summaries.
    return [
        _job_to_summary(
            j,
            org_unit_name=unit_names.get(j.org_unit_id),
            created_by_email=user_emails.get(j.created_by),
            updated_by_email=user_emails.get(j.updated_by) if j.updated_by else None,
            signal_count=counts_by_job.get(j.id, (0, 0))[0],
            needs_review_count=counts_by_job.get(j.id, (0, 0))[1],
        )
        for j in jobs
    ]


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

    # Recompute is_stale for all banks on this job's pipeline (§11.5).
    # The new snapshot is not yet confirmed, so signal_drift won't fire here —
    # it will fire once the recruiter confirms the new snapshot. We still call
    # recompute so that any prior drift stays correctly persisted.
    await _recompute_stale_for_job_banks(db, job)

    return snapshot


async def _recompute_stale_for_job_banks(db: AsyncSession, job: JobPosting) -> None:
    """Recompute and persist is_stale for every bank on the job's pipeline.

    Fires after signal save so banks that are already pinned to an older
    confirmed snapshot remain marked stale after the edit. Import is lazy to
    avoid a circular import chain (jd ← question_bank ← jd).
    """
    instance_result = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    instance = instance_result.scalar_one_or_none()
    if instance is None:
        return  # no pipeline yet; nothing to recompute

    banks_result = await db.execute(
        select(StageQuestionBank)
        .join(JobPipelineStage, StageQuestionBank.stage_id == JobPipelineStage.id)
        .where(JobPipelineStage.instance_id == instance.id)
    )
    banks = list(banks_result.scalars().all())
    for bank in banks:
        await recompute_and_persist_stale(db, bank)


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


async def evaluate_activation_predicates(
    db: AsyncSession, *, job: JobPosting,
) -> list:
    """Run the activation gate predicates. Returns a list of ActivationPredicateFailure
    objects (empty list means ready to activate)."""
    failures: list = []

    # Predicate 1: pipeline instance exists
    _select = select

    instance_result = await db.execute(
        _select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    instance = instance_result.scalar_one_or_none()
    if instance is None:
        return [ActivationPredicateFailure(code="no_pipeline", message="Pipeline not yet built")]

    # Load all stages ordered by position
    stages_result = await db.execute(
        _select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stages_result.scalars().all())

    # Predicate 2: at least one middle stage
    middle_types = middle_stage_types_for_activation()
    middle_stages = [s for s in stages if s.stage_type in middle_types]
    if not middle_stages:
        failures.append(ActivationPredicateFailure(
            code="no_middle_stage",
            message="Add at least one screening stage between Intake and Debrief.",
        ))

    # Bulk-load participants for all stages
    stage_ids = [s.id for s in stages]
    participants_by_stage: dict = {s.id: [] for s in stages}
    if stage_ids:
        part_result = await db.execute(
            _select(PipelineStageParticipant).where(
                PipelineStageParticipant.stage_id.in_(stage_ids)
            )
        )
        for p in part_result.scalars().all():
            participants_by_stage.setdefault(p.stage_id, []).append(p)

    # Bulk-load banks for all stages
    banks_by_stage: dict = {}
    if stage_ids:
        bank_result = await db.execute(
            _select(StageQuestionBank).where(
                StageQuestionBank.stage_id.in_(stage_ids)
            )
        )
        for b in bank_result.scalars().all():
            banks_by_stage[b.stage_id] = b

    # take_home is disabled — exclude from bank-eligible check
    bank_types = bank_eligible_stage_types() - {"take_home"}
    human_led = human_led_stage_types()

    for s in stages:
        # Predicate 6: stage name non-empty
        if not (s.name or "").strip():
            failures.append(ActivationPredicateFailure(
                code="empty_stage_name",
                message=f"Stage at position {s.position} has no name.",
                stage_id=s.id,
            ))

        # Predicate 3: human_led stages need ≥1 interviewer
        if s.stage_type in human_led:
            interviewers = [
                p for p in participants_by_stage.get(s.id, [])
                if p.role == "interviewer"
            ]
            if not interviewers:
                failures.append(ActivationPredicateFailure(
                    code="missing_interviewer",
                    message=f"Assign an interviewer to '{s.name}'.",
                    stage_id=s.id,
                ))

        # Predicate 4: debrief stage needs ≥1 reviewer
        if s.stage_type == "debrief":
            reviewers = [
                p for p in participants_by_stage.get(s.id, [])
                if p.role == "reviewer"
            ]
            if not reviewers:
                failures.append(ActivationPredicateFailure(
                    code="missing_reviewer",
                    message=f"Assign a reviewer to '{s.name}'.",
                    stage_id=s.id,
                ))

        # Predicate 5: bank-eligible stage has a reviewing/confirmed bank.
        # 'reviewing' is the post-generation state (recruiter hasn't
        # confirmed yet); 'confirmed' is post-recruiter-approval. Both pass
        # the gate per spec §7.1 #5 ("any non-empty generated bank passes
        # activation").
        if s.stage_type in bank_types:
            bank = banks_by_stage.get(s.id)
            if bank is None or bank.status not in ("reviewing", "confirmed"):
                failures.append(ActivationPredicateFailure(
                    code="missing_bank",
                    message=f"Generate a question bank for '{s.name}'.",
                    stage_id=s.id,
                ))

    # Predicate 7: positions sequential 0..N-1 (defensive)
    sorted_positions = sorted(s.position for s in stages)
    if sorted_positions != list(range(len(stages))):
        failures.append(ActivationPredicateFailure(
            code="positions_not_sequential",
            message=f"Stage positions are not sequential: {sorted_positions}",
        ))

    return failures


async def activate_job(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    correlation_id: str,
) -> JobPosting:
    """Run activation gate; on success, transition job to active.

    Raises:
        IllegalTransitionError: if job is not in pipeline_built (or already active).
        ActivationPredicatesFailed: if one or more predicates fail.
    """
    if job.status != "pipeline_built":
        raise IllegalTransitionError(from_state=job.status, to_state="active")

    failures = await evaluate_activation_predicates(db, job=job)
    if failures:
        raise ActivationPredicatesFailed(failures)

    await transition(
        db,
        job,
        to_state="active",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    await db.flush()

    logger.info(
        "jd.service.job_activated",
        job_posting_id=str(job.id),
        correlation_id=correlation_id,
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
