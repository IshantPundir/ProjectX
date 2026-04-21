"""FastAPI surface for the candidates module.

All business logic lives in service.py / resume_service.py; this module is
request/response orchestration only. Two routers are exposed:

  - `router`           - /api/candidates/*        (CRUD, assignments, resume, GDPR)
  - `kanban_router`    - /api/jobs/{id}/candidates/kanban

Both are registered in main.py (Task 15).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.candidates import resume_service, service
from app.modules.candidates.authz import require_candidate_access
from app.modules.candidates.schemas import (
    AssignmentCreateRequest,
    AssignmentResponse,
    AssignmentUpdateRequest,
    CandidateCreateRequest,
    CandidateResponse,
    CandidateUpdateRequest,
    KanbanBoardResponse,
    RedactPIIRequest,
    ResumeConfirmRequest,
    ResumeUploadUrlResponse,
    StageTransitionRequest,
)
from app.modules.candidates.sources import ManualSource
from app.modules.jd.authz import require_job_access

router = APIRouter(prefix="/api/candidates", tags=["candidates"])
kanban_router = APIRouter(prefix="/api/jobs", tags=["candidates"])


# --- Candidates CRUD ---------------------------------------------------------

@router.post("", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
async def create_candidate_endpoint(
    body: CandidateCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> CandidateResponse:
    if "candidates.manage" not in user.all_permissions() and not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Missing candidates.manage")
    candidate = await service.create_candidate(
        db, body, ManualSource(), user, user.user.tenant_id
    )
    return CandidateResponse.model_validate(candidate)


@router.get("")
async def list_candidates_endpoint(
    q: str | None = Query(None, max_length=200),
    job_id: UUID | None = None,
    stage_id: UUID | None = None,
    status_: str | None = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict:
    page = await service.list_candidates(
        db,
        user,
        user.user.tenant_id,
        filters={"q": q, "job_id": job_id, "stage_id": stage_id, "status": status_},
        offset=offset,
        limit=limit,
    )
    return {
        "items": [
            CandidateResponse.model_validate(c).model_dump(mode="json")
            for c in page.items
        ],
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
    }


@router.get("/{candidate_id}", response_model=CandidateResponse)
async def get_candidate_endpoint(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> CandidateResponse:
    candidate = await require_candidate_access(db, candidate_id, user, "view")
    return CandidateResponse.model_validate(candidate)


@router.get(
    "/{candidate_id}/assignments",
    response_model=list[AssignmentResponse],
)
async def list_candidate_assignments_endpoint(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[AssignmentResponse]:
    """List every assignment (any status) for a candidate.

    Authz: view access on the candidate. Per-assignment job visibility is
    implicit — any assignment the caller can see the candidate through is
    surfaced; the underlying job rows already filtered by RLS at DB level.
    """
    await require_candidate_access(db, candidate_id, user, "view")
    return await service.list_assignments(db, candidate_id)


@router.patch("/{candidate_id}", response_model=CandidateResponse)
async def update_candidate_endpoint(
    candidate_id: UUID,
    body: CandidateUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> CandidateResponse:
    await require_candidate_access(db, candidate_id, user, "manage")
    candidate = await service.update_candidate(db, candidate_id, body, user)
    return CandidateResponse.model_validate(candidate)


@router.post("/{candidate_id}/redact-pii", status_code=status.HTTP_204_NO_CONTENT)
async def redact_pii_endpoint(
    candidate_id: UUID,
    body: RedactPIIRequest,  # noqa: ARG001 - validated but not used
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="PII redaction requires super admin")
    await require_candidate_access(db, candidate_id, user, "manage")
    await service.redact_pii(db, candidate_id, user)


# --- Resume ------------------------------------------------------------------

@router.post("/{candidate_id}/resume", response_model=ResumeUploadUrlResponse)
async def request_resume_upload_endpoint(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> ResumeUploadUrlResponse:
    await require_candidate_access(db, candidate_id, user, "manage")
    return await resume_service.request_resume_upload(db, candidate_id, user)


@router.post("/{candidate_id}/resume/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_resume_upload_endpoint(
    candidate_id: UUID,
    body: ResumeConfirmRequest,  # noqa: ARG001 — body schema kept for API compat; s3_key is ignored
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    await require_candidate_access(db, candidate_id, user, "manage")
    await resume_service.confirm_resume_upload(db, candidate_id, user)


@router.delete("/{candidate_id}/resume", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume_endpoint(
    candidate_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    await require_candidate_access(db, candidate_id, user, "manage")
    await resume_service.delete_resume(db, candidate_id, user)


# --- Assignments -------------------------------------------------------------

@router.post(
    "/{candidate_id}/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment_endpoint(
    candidate_id: UUID,
    body: AssignmentCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> AssignmentResponse:
    await require_candidate_access(db, candidate_id, user, "manage")
    await require_job_access(db, body.job_posting_id, user, "manage")
    assignment = await service.create_assignment(db, candidate_id, body, user)
    return await service.assignment_response(db, assignment)


@router.patch(
    "/{candidate_id}/assignments/{assignment_id}",
    response_model=AssignmentResponse,
)
async def update_assignment_endpoint(
    candidate_id: UUID,
    assignment_id: UUID,
    body: AssignmentUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> AssignmentResponse:
    await require_candidate_access(db, candidate_id, user, "manage")
    assignment = await service.update_assignment_status(db, assignment_id, body, user)
    # Defense-in-depth: verify the caller still has jobs.manage on the assignment's JD.
    await require_job_access(db, assignment.job_posting_id, user, "manage")
    return await service.assignment_response(db, assignment)


@router.post(
    "/{candidate_id}/assignments/{assignment_id}/transition",
    response_model=AssignmentResponse,
)
async def transition_assignment_endpoint(
    candidate_id: UUID,
    assignment_id: UUID,
    body: StageTransitionRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> AssignmentResponse:
    await require_candidate_access(db, candidate_id, user, "manage")
    assignment = await service.transition_stage(db, assignment_id, body, user)
    await require_job_access(db, assignment.job_posting_id, user, "manage")
    return await service.assignment_response(db, assignment)


# --- Kanban ------------------------------------------------------------------

@kanban_router.get(
    "/{job_id}/candidates/kanban",
    response_model=KanbanBoardResponse,
)
async def get_kanban_board_endpoint(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> KanbanBoardResponse:
    await require_job_access(db, job_id, user, "view")
    return await service.get_kanban_board(db, job_id)
