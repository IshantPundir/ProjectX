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
    ensure_minimal_pipeline_for_job,
    human_led_stage_types,
    middle_stage_types_for_activation,
)
from app.modules.question_bank import StageQuestionBank, recompute_and_persist_stale
from app.modules.jd.errors import (
    ActivationPredicateFailure,
    ActivationPredicatesFailed,
    CompanyProfileIncompleteError,
    IllegalTransitionError,
    JobNotEditableError,
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
    profile_ready: bool = False,
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
        source=job.source,
        external_id=job.external_id,
        external_status=job.external_status,
        profile_ready=profile_ready,
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

    # Collect unique lookup keys. org_unit_id is nullable for ATS-imported
    # jobs without a client mapping — skip those when batching org unit names.
    unit_ids = {j.org_unit_id for j in jobs if j.org_unit_id is not None}
    user_ids = {j.created_by for j in jobs}
    for j in jobs:
        if j.updated_by:
            user_ids.add(j.updated_by)
    job_ids = [j.id for j in jobs]

    tenant_id = jobs[0].tenant_id

    # Batch-load every org unit in the tenant so we can (a) resolve the
    # job's direct org_unit_name AND (b) walk ancestry in memory for the
    # profile-ready check. Two birds, one query. The tenant tree is bounded
    # (CLAUDE.md: <10 deep, dozens of units in practice).
    unit_map: dict[UUID, OrganizationalUnit] = {}
    if unit_ids:
        unit_result = await db.execute(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == tenant_id,
            )
        )
        unit_map = {u.id: u for u in unit_result.scalars().all()}
    unit_names: dict[UUID, str] = {uid: u.name for uid, u in unit_map.items()}

    # Per-org_unit_id: does the ancestry walk find the owning unit
    # (client_account or company) AND is that owner's profile complete?
    # Mirrors find_company_profile_in_ancestry's owner-walk semantics
    # (org_units/service.py); pass-through containers (division/region/
    # team) are skipped, and the walk does NOT fall through to a higher
    # owner if the closest one's profile is incomplete. Cached for the
    # duration of this list call.
    profile_ready_cache: dict[UUID, bool] = {}
    _OWNER_TYPES = {"client_account", "company"}

    def _is_profile_ready(org_unit_id: UUID | None) -> bool:
        if org_unit_id is None:
            return False
        if org_unit_id in profile_ready_cache:
            return profile_ready_cache[org_unit_id]
        current_id: UUID | None = org_unit_id
        seen: set[UUID] = set()
        ready = False
        while current_id is not None:
            if current_id in seen:
                break  # defensive: corrupted parent chain
            seen.add(current_id)
            unit = unit_map.get(current_id)
            if unit is None:
                break
            if unit.unit_type in _OWNER_TYPES:
                about = (unit.about or "").strip()
                industry = (unit.industry or "").strip()
                hiring_bar = (unit.hiring_bar or "").strip()
                ready = bool(about and industry and hiring_bar)
                break  # owner found, do NOT fall through
            current_id = unit.parent_unit_id
        profile_ready_cache[org_unit_id] = ready
        return ready

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

    # Build enriched summaries. org_unit_name remains None when the job is
    # unlinked (ATS-imported without a client mapping).
    return [
        _job_to_summary(
            j,
            org_unit_name=(
                unit_names.get(j.org_unit_id) if j.org_unit_id is not None else None
            ),
            created_by_email=user_emails.get(j.created_by),
            updated_by_email=user_emails.get(j.updated_by) if j.updated_by else None,
            signal_count=counts_by_job.get(j.id, (0, 0))[0],
            needs_review_count=counts_by_job.get(j.id, (0, 0))[1],
            profile_ready=_is_profile_ready(j.org_unit_id),
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
    description_raw: str = "",
    project_scope_raw: str | None = None,
    target_headcount: int | None = None,
    deadline: date | None = None,
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
    """INSERT job_postings in 'draft'. DOES NOT commit, does NOT transition,
    does NOT enqueue any actor.

    The profile-completion gate moved to /enrich and /extract-signals — a
    draft job can exist on an incomplete profile; only the explicit recruiter
    actions that depend on the profile (enrichment, signal extraction) check.

    `description_raw` defaults to empty — the recruiter is expected to paste
    it on /jobs/{id} after create. ATS-imported jobs populate it on import.

    Commit is handled by the dependency's context manager (get_tenant_db
    wraps the session in `async with session.begin()` — auto-commits on
    successful exit).
    """
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

    logger.info(
        "jd.service.created",
        job_posting_id=str(job.id),
        org_unit_id=str(org_unit_id),
        correlation_id=correlation_id,
    )
    return job


# Fields a recruiter can edit on a draft job via PATCH /api/jobs/{id}.
# Excludes org_unit_id (would invalidate ancestry context), source/external_id
# (provenance), status (state machine only), description_enriched (only the
# enrich actor writes it).
_DRAFT_EDITABLE_FIELDS: tuple[str, ...] = (
    "title",
    "description_raw",
    "project_scope_raw",
    "target_headcount",
    "deadline",
    "employment_type",
    "work_arrangement",
    "location",
    "salary_range_min",
    "salary_range_max",
    "salary_currency",
    "travel_required",
    "start_date_pref",
)


async def update_job_posting_draft(
    db: AsyncSession,
    *,
    job: JobPosting,
    updates: dict,
    actor_id: UUID,
    actor_email: str | None,
    ip_address: str | None,
    correlation_id: str,
) -> JobPosting:
    """Update editable fields on a draft job.

    Only fields listed in ``_DRAFT_EDITABLE_FIELDS`` are written; unknown keys
    in ``updates`` are ignored (the Pydantic body schema already validated them).

    Raises:
        JobNotEditableError: when ``job.status != 'draft'``. Editing after
            signal extraction would invalidate the snapshot.
    """
    if job.status != "draft":
        raise JobNotEditableError(job.status)

    changed: dict[str, dict] = {}
    for field in _DRAFT_EDITABLE_FIELDS:
        if field not in updates:
            continue
        new_value = updates[field]
        old_value = getattr(job, field)
        if old_value == new_value:
            continue
        setattr(job, field, new_value)
        changed[field] = {
            "from": old_value.isoformat() if hasattr(old_value, "isoformat") else old_value,
            "to": new_value.isoformat() if hasattr(new_value, "isoformat") else new_value,
        }

    if not changed:
        return job

    job.updated_by = actor_id
    job.updated_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=job.tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.JOB_POSTING_UPDATED,
        resource="job_posting",
        resource_id=job.id,
        payload={"changed": changed, "correlation_id": correlation_id},
        ip_address=ip_address,
    )

    logger.info(
        "jd.service.draft_updated",
        job_posting_id=str(job.id),
        fields=list(changed.keys()),
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
    """Confirm the latest snapshot, auto-create the bookend Intake → Debrief
    pipeline, and transition the job to pipeline_built — all in one
    transaction.

    signals_confirmed is therefore transient: observable inside this
    transaction but never as steady state. The recruiter lands directly
    on pipeline_built ("In review") and must add at least one middle
    stage and confirm each stage's question bank before the activation
    gate opens. ensure_minimal_pipeline_for_job is idempotent — safe
    under any race the state machine might allow.

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

    await ensure_minimal_pipeline_for_job(db, job=job)

    await transition(
        db,
        job,
        to_state="pipeline_built",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    await db.flush()

    logger.info(
        "jd.service.signals_confirmed_and_pipeline_built",
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

        # Predicate 5: bank-eligible stage has a confirmed bank. 'reviewing'
        # is post-generation pre-approval — the recruiter hasn't clicked
        # "Confirm bank" yet, so the bank is not ready. 'confirmed' is the
        # only state that opens the gate. Same failure code in both shapes
        # so the frontend's in-flight-generation suppression check still
        # works; only the message differs.
        if s.stage_type in bank_types:
            bank = banks_by_stage.get(s.id)
            if bank is None:
                failures.append(ActivationPredicateFailure(
                    code="missing_bank",
                    message=f"Generate a question bank for '{s.name}'.",
                    stage_id=s.id,
                ))
            elif bank.status != "confirmed":
                failures.append(ActivationPredicateFailure(
                    code="missing_bank",
                    message=f"Confirm the question bank for '{s.name}'.",
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
