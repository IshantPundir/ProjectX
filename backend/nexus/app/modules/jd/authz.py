"""Authorization helpers for the JD module.

require_job_access() loads the job, walks the org unit ancestry from the
job's unit up to the root, and checks whether the user holds the required
permission on any ancestor.

Day-1 Task 1 verified that UserContext.has_permission_in_unit() does NOT
inherit from ancestors — it's exact-match only. So this helper's local
ancestry walk is the PRIMARY enforcement path. Without it, recruiters with
permissions on a parent division would silently 403 on jobs in child teams."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobPosting, OrganizationalUnit
from app.modules.auth.context import UserContext


async def _get_org_unit_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> list[OrganizationalUnit]:
    """Walk parent_unit_id chain from the given unit up to root.
    Returns units in order: [starting_unit, parent, grandparent, ..., root]."""
    chain: list[OrganizationalUnit] = []
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            break  # defensive: avoid infinite loop on corrupted data
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            break
        chain.append(unit)
        current_id = unit.parent_unit_id
    return chain


async def require_job_access(
    db: AsyncSession,
    job_id: UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> JobPosting:
    """Load the job and enforce ancestry-based RBAC.

    Raises:
        HTTPException(404): job doesn't exist in the current tenant scope (RLS).
        HTTPException(403): user lacks the required permission in any ancestor
                            of the job's org unit.

    Returns the loaded JobPosting on success so callers don't re-fetch."""
    result = await db.execute(select(JobPosting).where(JobPosting.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Super admin short-circuit — matches Phase 1 pattern
    if user.is_super_admin:
        return job

    permission = f"jobs.{action}"
    ancestry = await _get_org_unit_ancestry(db, job.org_unit_id)
    for unit in ancestry:
        if user.has_permission_in_unit(unit.id, permission):
            return job

    raise HTTPException(
        status_code=403,
        detail=f"Missing {permission} in job's org unit ancestry",
    )
