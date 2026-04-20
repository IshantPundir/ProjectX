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

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.candidates.errors import (
    CandidateNotFoundError,
    DuplicateEmailError,
)
from app.modules.candidates.schemas import (
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
