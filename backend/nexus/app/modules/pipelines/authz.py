"""Pipeline authorization — ancestry-walking permission checks.

Follows the same pattern as app.modules.jd.authz.require_job_access:
super admin shortcut, ancestry walk, permission check on each ancestor."""

import uuid as uuid_mod
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPosting,
    PipelineTemplate,
)
from app.modules.auth.context import UserContext
from app.modules.org_units.service import get_org_unit_ancestry


async def require_template_access(
    db: AsyncSession,
    template_id: uuid_mod.UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> PipelineTemplate:
    """Load a template and verify the user has `org_units.manage` in
    the template's org unit ancestry.

    The `action` parameter is accepted for API symmetry with require_job_access,
    but pipeline templates always require `org_units.manage` (they're an org
    unit admin concern)."""
    result = await db.execute(
        select(PipelineTemplate).where(PipelineTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Pipeline template not found")

    if user.is_super_admin:
        return template

    ancestry = await get_org_unit_ancestry(db, template.org_unit_id)
    for unit in ancestry:
        if user.has_permission_in_unit(unit.id, "org_units.manage"):
            return template

    raise HTTPException(
        status_code=403,
        detail="Missing org_units.manage in template's org unit ancestry",
    )


async def require_instance_access(
    db: AsyncSession,
    job_id: uuid_mod.UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> tuple[JobPosting, JobPipelineInstance | None]:
    """Load a job + its pipeline instance. Verifies jobs.{action} in
    the job's org unit ancestry.

    Returns (job, instance). Instance may be None — callers handle missing
    instances (e.g. GET returns 404, POST creates fresh)."""
    job_result = await db.execute(
        select(JobPosting).where(JobPosting.id == job_id)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_super_admin:
        permission = f"jobs.{action}"
        ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
        if not any(
            user.has_permission_in_unit(unit.id, permission) for unit in ancestry
        ):
            raise HTTPException(
                status_code=403,
                detail=f"Missing {permission} in job's org unit ancestry",
            )

    instance_result = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job_id
        )
    )
    instance = instance_result.scalar_one_or_none()
    return job, instance
