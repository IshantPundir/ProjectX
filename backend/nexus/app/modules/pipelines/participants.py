"""Pipeline stage participants service helpers.

Three public helpers:

- replace_stage_participants(db, *, stage, participants, assigned_by)
    Diff-and-sync within a single stage. Preserves row identity for
    (stage_id, user_id, role) tuples that survive the edit; inserts
    missing; deletes removed.

- list_assignable_users(db, *, job, role)
    Returns the eligible-user pool for a picker slot. Filters by system
    role name gate (see docs/superpowers/specs/2026-04-22-pipeline-stage-types-design.md §3)
    and the job's org unit ancestry.

- validate_participants_eligible(db, *, job, participants)
    Re-runs the pool query for every user_id supplied. Raises 422 if any
    user is outside the pool. Called by create-from-scratch and PATCH paths.
"""

from typing import Literal
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import User, UserRoleAssignment
from app.modules.jd import JobPosting
from app.modules.org_units import OrganizationalUnit, get_org_unit_ancestry
from app.modules.pipelines.models import JobPipelineStage, PipelineStageParticipant
from app.modules.roles import Role
from app.modules.pipelines.schemas import (
    ParticipantRole,
    StageParticipantInput,
)

logger = structlog.get_logger()


_ROLE_GATE: dict[str, tuple[str, ...]] = {
    "interviewer": ("Interviewer", "Hiring Manager"),
    "observer":    ("Observer", "Interviewer", "Hiring Manager", "Recruiter"),
    "reviewer":    ("Hiring Manager",),
}


async def _ancestor_unit_ids(
    db: AsyncSession, org_unit_id: UUID
) -> list[UUID]:
    ancestry = await get_org_unit_ancestry(db, org_unit_id)
    return [u.id for u in ancestry]


async def list_assignable_users(
    db: AsyncSession,
    *,
    job: JobPosting,
    role: Literal["interviewer", "observer", "reviewer"],
) -> list[dict]:
    gate_names = _ROLE_GATE[role]
    ancestor_ids = await _ancestor_unit_ids(db, job.org_unit_id)
    if not ancestor_ids:
        return []

    result = await db.execute(
        select(User, OrganizationalUnit, Role)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(OrganizationalUnit, OrganizationalUnit.id == UserRoleAssignment.org_unit_id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            and_(
                User.is_active == True,  # noqa: E712 — SQLAlchemy expects this form
                UserRoleAssignment.org_unit_id.in_(ancestor_ids),
                Role.name.in_(gate_names),
            )
        )
    )

    out: dict[UUID, dict] = {}
    for user, unit, role_row in result.all():
        entry = out.setdefault(
            user.id,
            {
                "user_id": user.id,
                "full_name": user.full_name or "",
                "email": user.email,
                "role_labels": set(),
                "org_unit_name": unit.name,
            },
        )
        entry["role_labels"].add(role_row.name)

    return [
        {**e, "role_labels": sorted(e["role_labels"])} for e in out.values()
    ]


async def validate_participants_eligible(
    db: AsyncSession,
    *,
    job: JobPosting,
    participants: list[StageParticipantInput],
) -> None:
    """Raise 422 if any participant is not in the eligibility pool for their slot."""
    if not participants:
        return

    # Group by slot to avoid N queries.
    by_role: dict[ParticipantRole, set[UUID]] = {}
    for p in participants:
        by_role.setdefault(p.role, set()).add(p.user_id)

    for role, user_ids in by_role.items():
        pool = await list_assignable_users(db, job=job, role=role)
        allowed_ids = {u["user_id"] for u in pool}
        missing = user_ids - allowed_ids
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"User(s) not eligible for role={role!r} on this job's "
                    f"org unit: {sorted(str(m) for m in missing)}"
                ),
            )


async def replace_stage_participants(
    db: AsyncSession,
    *,
    stage: JobPipelineStage,
    participants: list[StageParticipantInput],
    assigned_by: UUID,
) -> None:
    """Diff-and-sync participants for a single stage.

    Preserves rows whose (user_id, role) still appears in `participants`.
    Deletes rows whose tuple is absent. Inserts rows for new tuples.
    """
    existing_result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.stage_id == stage.id
        )
    )
    existing = list(existing_result.scalars().all())

    incoming_keys = {(p.user_id, p.role) for p in participants}
    existing_keys = {(row.user_id, row.role) for row in existing}

    # Delete rows not in incoming.
    to_delete_ids = [r.id for r in existing if (r.user_id, r.role) not in incoming_keys]
    if to_delete_ids:
        await db.execute(
            delete(PipelineStageParticipant).where(
                PipelineStageParticipant.id.in_(to_delete_ids)
            )
        )

    # Insert rows that aren't already present.
    for p in participants:
        if (p.user_id, p.role) in existing_keys:
            continue
        db.add(
            PipelineStageParticipant(
                tenant_id=stage.tenant_id,
                stage_id=stage.id,
                user_id=p.user_id,
                role=p.role,
                assigned_by=assigned_by,
            )
        )

    await db.flush()
    logger.info(
        "pipelines.stage_participants_synced",
        stage_id=str(stage.id),
        kept=len(existing_keys & incoming_keys),
        inserted=len(incoming_keys - existing_keys),
        deleted=len(existing_keys - incoming_keys),
    )
