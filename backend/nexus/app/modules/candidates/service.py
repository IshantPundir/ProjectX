"""Candidates service layer.

Phase 3B scope:
  - create_candidate, get_candidate, update_candidate       (this task)
  - list_candidates                                         (Task 8)
  - create_assignment                                       (Task 9)
  - update_assignment_status, transition_stage              (Task 10)
  - get_kanban_board                                        (Task 11)
  - redact_pii                                              (Task 13)

Every state-changing operation writes to audit_log via log_event().
Service functions flush; the surrounding session factory commits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.candidates.errors import (
    AssignmentAlreadyExistsError,
    CandidateNotFoundError,
    DuplicateEmailError,
    StageNotInPipelineError,
)
from app.modules.candidates.schemas import (
    AssignmentCreateRequest,
    AssignmentUpdateRequest,
    CandidateCreateRequest,
    CandidateUpdateRequest,
    KanbanBoardResponse,
    KanbanCandidateCard,
    KanbanColumnResponse,
    StageTransitionRequest,
)
from app.modules.candidates.sources import CandidateSource


async def create_candidate(
    db: AsyncSession,
    request: CandidateCreateRequest,
    source: CandidateSource,
    user: UserContext,
    tenant_id: UUID,
) -> Candidate:
    """Insert a candidate and log `candidate.created`.

    Raises DuplicateEmailError on partial-unique-index collision with a
    non-redacted candidate that already uses this email.
    """
    normalized = source.normalize(request)
    candidate = Candidate(
        tenant_id=tenant_id,
        name=normalized.name,
        email=normalized.email,
        phone=normalized.phone,
        location=normalized.location,
        current_title=normalized.current_title,
        linkedin_url=normalized.linkedin_url,
        notes=normalized.notes,
        source=normalized.source,
        external_id=normalized.external_id,
        source_metadata=normalized.source_metadata,
        created_by=user.user.id,
    )
    db.add(candidate)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        if "candidates_tenant_email_active_idx" in str(e.orig):
            raise DuplicateEmailError(normalized.email) from e
        raise

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.created",
        resource="candidate",
        resource_id=candidate.id,
        payload={"source": normalized.source, "has_resume": False},
    )
    return candidate


async def get_candidate(db: AsyncSession, candidate_id: UUID) -> Candidate:
    """Load a candidate by id. Raises CandidateNotFoundError if missing."""
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise CandidateNotFoundError()
    return candidate


async def update_candidate(
    db: AsyncSession,
    candidate_id: UUID,
    request: CandidateUpdateRequest,
    user: UserContext,
) -> Candidate:
    """Apply partial update to an existing candidate and log `candidate.updated`.

    Only fields present in the request (exclude_unset=True) are written.
    """
    candidate = await get_candidate(db, candidate_id)
    changes = request.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "linkedin_url" and value is not None:
            value = str(value)  # HttpUrl → plain str for DB
        setattr(candidate, field, value)
    await db.flush()
    await log_event(
        db,
        tenant_id=candidate.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.updated",
        resource="candidate",
        resource_id=candidate.id,
        payload={"fields": list(changes.keys())},
    )
    return candidate


@dataclass
class CandidateListPage:
    items: list[Candidate]
    total: int
    offset: int
    limit: int


async def list_candidates(
    db: AsyncSession,
    user: UserContext,
    tenant_id: UUID,
    filters: dict,
    offset: int = 0,
    limit: int = 50,
) -> CandidateListPage:
    """List candidates with ancestry-filtered visibility.

    filters:
      q          — substring match on name/email (ILIKE)
      job_id     — restrict to candidates assigned to this JD
      stage_id   — restrict to candidates currently in this stage
      status     — restrict to this assignment status

    MVP simplification: if the user has `candidates.view` anywhere in their
    role assignments, they see all tenant candidates. A tighter ancestry-filtered
    SQL form is a known future refinement target once real Fortune-500 tenant
    ancestries surface perf issues — tracked in the plan's `Known gaps`.
    """
    q = filters.get("q")
    job_id = filters.get("job_id")
    stage_id = filters.get("stage_id")
    status = filters.get("status")

    base = select(Candidate).where(
        Candidate.tenant_id == tenant_id,
        Candidate.pii_redacted_at.is_(None),
    )

    if q:
        like = f"%{q}%"
        base = base.where(
            or_(Candidate.name.ilike(like), Candidate.email.ilike(like))
        )

    if job_id or stage_id or status:
        base = base.join(
            CandidateJobAssignment,
            CandidateJobAssignment.candidate_id == Candidate.id,
        )
        if job_id:
            base = base.where(CandidateJobAssignment.job_posting_id == job_id)
        if stage_id:
            base = base.where(CandidateJobAssignment.current_stage_id == stage_id)
        if status:
            base = base.where(CandidateJobAssignment.status == status)

    if not user.is_super_admin and "candidates.view" not in user.all_permissions():
        base = base.where(False)

    total_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = total_result.scalar_one()

    page_result = await db.execute(
        base.order_by(Candidate.created_at.desc()).offset(offset).limit(limit)
    )
    items = list(page_result.scalars().unique().all())
    return CandidateListPage(items=items, total=total, offset=offset, limit=limit)


async def create_assignment(
    db: AsyncSession,
    candidate_id: UUID,
    request: AssignmentCreateRequest,
    user: UserContext,
) -> CandidateJobAssignment:
    """Assign a candidate to a JD at the first stage (or `request.target_stage_id`).

    Writes the assignment row + the initial stage_progress row + the
    `candidate.assigned` audit event, all in one flush.

    Raises:
        CandidateNotFoundError: candidate_id does not exist.
        StageNotInPipelineError: the JD has no pipeline, no stages, or the
            requested target_stage_id does not belong to the pipeline.
        AssignmentAlreadyExistsError: (candidate_id, job_posting_id) already
            exists (partial unique constraint).
    """
    candidate = await get_candidate(db, candidate_id)

    pipeline = (await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == request.job_posting_id
        )
    )).scalar_one_or_none()
    if pipeline is None:
        raise StageNotInPipelineError(str(request.target_stage_id or "<default>"))

    stages = list((await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == pipeline.id)
        .order_by(JobPipelineStage.position.asc())
    )).scalars().all())
    if not stages:
        raise StageNotInPipelineError("pipeline has no stages")

    if request.target_stage_id is not None:
        target_stage = next(
            (s for s in stages if s.id == request.target_stage_id), None
        )
        if target_stage is None:
            raise StageNotInPipelineError(str(request.target_stage_id))
    else:
        target_stage = stages[0]

    assignment = CandidateJobAssignment(
        tenant_id=candidate.tenant_id,
        candidate_id=candidate_id,
        job_posting_id=request.job_posting_id,
        current_stage_id=target_stage.id,
        status="active",
        assigned_by=user.user.id,
    )
    db.add(assignment)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        if "candidate_job_assignments_unique_candidate_job" in str(e.orig):
            raise AssignmentAlreadyExistsError() from e
        raise

    progress = CandidateStageProgress(
        tenant_id=candidate.tenant_id,
        assignment_id=assignment.id,
        stage_id=target_stage.id,
        moved_by=user.user.id,
    )
    db.add(progress)
    await db.flush()

    await log_event(
        db,
        tenant_id=candidate.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.assigned",
        resource="candidate",
        resource_id=candidate.id,
        payload={
            "job_posting_id": str(request.job_posting_id),
            "target_stage_id": str(target_stage.id),
            "assignment_id": str(assignment.id),
        },
    )
    return assignment


async def update_assignment_status(
    db: AsyncSession,
    assignment_id: UUID,
    request: AssignmentUpdateRequest,
    user: UserContext,
) -> CandidateJobAssignment:
    """Update the status of a candidate-job assignment.

    All transitions are legal — recruiter has final say. An audit row records
    the from/to pair. Raises CandidateNotFoundError if the assignment is
    missing (re-used 404 code for the candidates module).
    """
    assignment = (await db.execute(
        select(CandidateJobAssignment).where(CandidateJobAssignment.id == assignment_id)
    )).scalar_one_or_none()
    if assignment is None:
        raise CandidateNotFoundError()

    from_status = assignment.status
    assignment.status = request.status.value
    assignment.status_changed_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=assignment.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.assignment_status_changed",
        resource="assignment",
        resource_id=assignment.id,
        payload={"from_status": from_status, "to_status": request.status.value},
    )
    return assignment


async def transition_stage(
    db: AsyncSession,
    assignment_id: UUID,
    request: StageTransitionRequest,
    user: UserContext,
) -> CandidateJobAssignment:
    """Atomically move an assignment to a new stage.

    Row-locks the assignment for the duration of the transition, closes the
    open progress row with outcome='advanced', flips current_stage_id, and
    appends a new open progress row. Audit row records from/to stages,
    override flag, and reason.

    Raises:
        CandidateNotFoundError: assignment_id missing.
        StageNotInPipelineError: target_stage_id does not belong to the
            current JD's pipeline instance.
    """
    assignment = (await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.id == assignment_id)
        .with_for_update()
    )).scalar_one_or_none()
    if assignment is None:
        raise CandidateNotFoundError()

    pipeline = (await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == assignment.job_posting_id
        )
    )).scalar_one_or_none()
    if pipeline is None:
        raise StageNotInPipelineError(str(request.target_stage_id))

    target = (await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.id == request.target_stage_id,
            JobPipelineStage.instance_id == pipeline.id,
        )
    )).scalar_one_or_none()
    if target is None:
        raise StageNotInPipelineError(str(request.target_stage_id))

    from_stage_id = assignment.current_stage_id
    now = datetime.now(UTC)

    current = (await db.execute(
        select(CandidateStageProgress).where(
            CandidateStageProgress.assignment_id == assignment_id,
            CandidateStageProgress.exited_at.is_(None),
        )
    )).scalar_one_or_none()
    if current is not None:
        current.exited_at = now
        current.outcome = "advanced"

    assignment.current_stage_id = target.id
    new_progress = CandidateStageProgress(
        tenant_id=assignment.tenant_id,
        assignment_id=assignment_id,
        stage_id=target.id,
        entered_at=now,
        moved_by=user.user.id,
        override=request.override,
        reason=request.reason,
    )
    db.add(new_progress)
    await db.flush()

    await log_event(
        db,
        tenant_id=assignment.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.stage_transitioned",
        resource="assignment",
        resource_id=assignment.id,
        payload={
            "from_stage": str(from_stage_id),
            "to_stage": str(target.id),
            "override": request.override,
            "reason": request.reason,
        },
    )
    return assignment


async def get_kanban_board(
    db: AsyncSession,
    job_posting_id: UUID,
) -> KanbanBoardResponse:
    """Fetch the kanban board for a JD in four queries.

    Mirrors the bulk-load pattern used by question_bank.get_banks_for_pipeline:
    the kanban is the hottest read in the recruiter workflow, so we deliberately
    keep the query count constant regardless of candidate volume.

    Callers (router) are responsible for authz — this returns raw structured data.
    Returns an empty board (stages=[]) if the JD has no pipeline yet.
    """
    pipeline = (await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job_posting_id
        )
    )).scalar_one_or_none()
    if pipeline is None:
        return KanbanBoardResponse(job_posting_id=job_posting_id, stages=[])

    stages = list((await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == pipeline.id)
        .order_by(JobPipelineStage.position.asc())
    )).scalars().all())

    assignments = list((await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.job_posting_id == job_posting_id,
            CandidateJobAssignment.status == "active",
        )
    )).scalars().all())

    candidate_ids = {a.candidate_id for a in assignments}
    candidates_by_id: dict[UUID, Candidate] = {}
    if candidate_ids:
        rows = (await db.execute(
            select(Candidate).where(Candidate.id.in_(candidate_ids))
        )).scalars().all()
        candidates_by_id = {c.id: c for c in rows}

    cards_by_stage: dict[UUID, list[KanbanCandidateCard]] = {}
    for a in assignments:
        c = candidates_by_id.get(a.candidate_id)
        if c is None:
            continue  # defensive: RLS could hide a candidate we can see the assignment for
        cards_by_stage.setdefault(a.current_stage_id, []).append(
            KanbanCandidateCard(
                candidate_id=c.id,
                assignment_id=a.id,
                name=c.name,
                email=c.email,
                status=a.status,
                current_stage_id=a.current_stage_id,
                latest_session_state=None,  # Populated in Phase 3C.
            )
        )

    return KanbanBoardResponse(
        job_posting_id=job_posting_id,
        stages=[
            KanbanColumnResponse(
                stage_id=s.id,
                stage_name=s.name,
                position=s.position,
                candidates=cards_by_stage.get(s.id, []),
            )
            for s in stages
        ],
    )


async def redact_pii(
    db: AsyncSession, candidate_id: UUID, user: UserContext
) -> None:
    """GDPR hard-delete of PII columns. Audit row persists for compliance.

    Phase 3B: placeholder — sessions table lands in 3C. Once sessions exist,
    add an active-session guard here that raises CandidateHasActiveSessionError
    when any assignment has a session in the `active` state:

        active_count = (await db.execute(
            select(func.count()).select_from(Session).where(
                Session.candidate_id == candidate_id,
                Session.state == "active",
            )
        )).scalar_one()
        if active_count > 0:
            raise CandidateHasActiveSessionError()

    For Phase 3B this check is a no-op.
    """
    candidate = await get_candidate(db, candidate_id)

    candidate.name = None
    candidate.email = None
    candidate.phone = None
    candidate.location = None
    candidate.current_title = None
    candidate.linkedin_url = None
    candidate.resume_s3_key = None
    candidate.resume_uploaded_at = None
    candidate.notes = None
    candidate.source_metadata = None
    candidate.pii_redacted_at = datetime.now(UTC)
    candidate.pii_redacted_by = user.user.id
    await db.flush()

    await log_event(
        db,
        tenant_id=candidate.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.pii_redacted",
        resource="candidate",
        resource_id=candidate.id,
        payload={},
    )
