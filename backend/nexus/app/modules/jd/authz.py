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

from app.modules.auth import UserContext
from app.modules.jd.models import JobPosting
from app.modules.org_units import get_org_unit_ancestry


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
    ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    for unit in ancestry:
        if user.has_permission_in_unit(unit.id, permission):
            return job

    raise HTTPException(
        status_code=403,
        detail=f"Missing {permission} in job's org unit ancestry",
    )
