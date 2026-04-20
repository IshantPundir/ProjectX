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
    CandidateCreateRequest,
    CandidateUpdateRequest,
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
