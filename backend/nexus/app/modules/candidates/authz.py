"""Authorization helpers for the candidates module.

require_candidate_access mirrors require_job_access (app/modules/jd/authz.py)
but resolves the authoritative org unit(s) from the candidate's assignments.

Visibility rules:
  1. Super admin: always allowed.
  2. Candidate with at least one assignment: user must have
     candidates.{action} in the ancestry of at least one assigned JD's org unit.
  3. Candidate without assignments (talent-pool): user must have
     candidates.{action} anywhere in their role assignments.
"""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, CandidateJobAssignment, JobPosting
from app.modules.auth.context import UserContext
from app.modules.org_units.service import get_org_unit_ancestry


async def require_candidate_access(
    db: AsyncSession,
    candidate_id: UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> Candidate:
    """Load the candidate and enforce RBAC.

    Raises:
        HTTPException(404): candidate doesn't exist (or isn't visible under
                            the current RLS tenant scope).
        HTTPException(403): user lacks candidates.{action} in any ancestor of
                            any assigned JD — or, for unassigned candidates,
                            nowhere in their role assignments.

    Returns the loaded Candidate so callers don't re-fetch.
    """
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if user.is_super_admin:
        return candidate

    permission = f"candidates.{action}"

    assignments_result = await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.candidate_id == candidate_id
        )
    )
    assignments = list(assignments_result.scalars().all())

    if assignments:
        for assignment in assignments:
            job_result = await db.execute(
                select(JobPosting).where(JobPosting.id == assignment.job_posting_id)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                continue  # Shouldn't happen under RLS; skip defensively.
            ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
            for unit in ancestry:
                if user.has_permission_in_unit(unit.id, permission):
                    return candidate
        raise HTTPException(
            status_code=403,
            detail=f"Missing {permission} in any assigned job's org unit ancestry",
        )

    # Unassigned (talent-pool) candidate — tenant-level check.
    if permission in user.all_permissions():
        return candidate
    raise HTTPException(
        status_code=403,
        detail=f"Missing {permission} anywhere in role assignments",
    )
