"""Authz helpers for the question_bank module.

Walks bank/question → stage → instance → job → org_unit → ancestry to check
`jobs.view` / `jobs.manage` permission. Matches Phase 2C.1's pipelines/authz.py
pattern. Cross-tenant access returns 404 (RLS hides other tenants' rows).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.auth.context import UserContext
from app.modules.org_units.service import get_org_unit_ancestry

Action = Literal["view", "manage"]


async def _check_permission(
    db: AsyncSession,
    user: UserContext,
    org_unit_id: UUID,
    action: Action,
) -> bool:
    """Super-admin short-circuit, then walk the org unit's ancestry checking jobs.{action}."""
    if user.is_super_admin:
        return True
    required = f"jobs.{action}"
    ancestry = await get_org_unit_ancestry(db, org_unit_id)
    for unit in ancestry:
        if user.has_permission_in_unit(unit.id, required):
            return True
    return False


async def require_bank_access(
    db: AsyncSession,
    bank_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[StageQuestionBank, JobPipelineStage, JobPosting]:
    """Load a bank and verify the user has `jobs.{action}` on some ancestor org unit.

    - Raises 404 if the bank doesn't exist (including cross-tenant via RLS)
    - Raises 403 if the bank exists but the user lacks permission
    """
    result = await db.execute(
        select(StageQuestionBank, JobPipelineStage, JobPipelineInstance, JobPosting)
        .join(JobPipelineStage, StageQuestionBank.stage_id == JobPipelineStage.id)
        .join(
            JobPipelineInstance,
            JobPipelineStage.instance_id == JobPipelineInstance.id,
        )
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(StageQuestionBank.id == bank_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Bank not found")
    bank, stage, _instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} questions for this job",
        )
    return bank, stage, job


async def require_bank_access_by_stage(
    db: AsyncSession,
    job_id: UUID,
    stage_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[StageQuestionBank | None, JobPipelineStage, JobPosting]:
    """Like require_bank_access but starts from a (job_id, stage_id) tuple.

    Returns (bank_or_None, stage, job). If the bank doesn't exist yet (draft
    before any generation), bank is None but stage + job are loaded so the
    service can create the bank.
    """
    result = await db.execute(
        select(JobPipelineStage, JobPipelineInstance, JobPosting)
        .join(
            JobPipelineInstance,
            JobPipelineStage.instance_id == JobPipelineInstance.id,
        )
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(
            JobPipelineStage.id == stage_id,
            JobPosting.id == job_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Stage not found for this job")
    stage, _instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} questions for this job",
        )

    # Try to load an existing bank for this stage (may not exist yet)
    bank_result = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage_id)
    )
    bank = bank_result.scalar_one_or_none()
    return bank, stage, job


async def require_question_access(
    db: AsyncSession,
    question_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[StageQuestion, StageQuestionBank, JobPipelineStage, JobPosting]:
    """Load a question and walk up through bank → stage → instance → job for authz."""
    result = await db.execute(
        select(StageQuestion, StageQuestionBank, JobPipelineStage, JobPipelineInstance, JobPosting)
        .join(StageQuestionBank, StageQuestion.bank_id == StageQuestionBank.id)
        .join(JobPipelineStage, StageQuestionBank.stage_id == JobPipelineStage.id)
        .join(
            JobPipelineInstance,
            JobPipelineStage.instance_id == JobPipelineInstance.id,
        )
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(StageQuestion.id == question_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    question, bank, stage, _instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} this question",
        )
    return question, bank, stage, job


async def require_pipeline_access(
    db: AsyncSession,
    job_id: UUID,
    user: UserContext,
    action: Action,
) -> tuple[JobPipelineInstance, JobPosting]:
    """For pipeline-level operations (generate-all, banks overview, SSE stream).

    Raises 404 if no pipeline instance exists for the job.
    """
    result = await db.execute(
        select(JobPipelineInstance, JobPosting)
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(JobPosting.id == job_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No pipeline found for this job")
    instance, job = row

    if not await _check_permission(db, user, job.org_unit_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} this pipeline",
        )
    return instance, job
