"""Org unit CRUD and role assignment service."""

import uuid as uuid_mod

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit, Role, User, UserRoleAssignment

logger = structlog.get_logger()

VALID_UNIT_TYPES = {"client_account", "department", "team", "branch", "region"}


async def create_org_unit(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
) -> OrganizationalUnit:
    if unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")

    unit = OrganizationalUnit(
        client_id=client_id,
        name=name,
        unit_type=unit_type,
        parent_unit_id=parent_unit_id,
    )
    db.add(unit)
    await db.flush()

    logger.info("org_units.created", unit_id=str(unit.id), name=name)
    return unit


async def list_org_units(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    is_super_admin: bool,
) -> list[dict]:
    """List org units. Super admin sees all; others see assigned units only."""
    if is_super_admin:
        query = select(OrganizationalUnit).where(
            OrganizationalUnit.client_id == client_id
        )
    else:
        assigned_unit_ids = (
            select(UserRoleAssignment.org_unit_id)
            .where(UserRoleAssignment.user_id == user_id)
            .distinct()
            .scalar_subquery()
        )
        query = select(OrganizationalUnit).where(
            OrganizationalUnit.client_id == client_id,
            OrganizationalUnit.id.in_(assigned_unit_ids),
        )

    result = await db.execute(query.order_by(OrganizationalUnit.created_at.asc()))
    units = result.scalars().all()

    unit_ids = [u.id for u in units]
    counts: dict[uuid_mod.UUID, int] = {}
    if unit_ids:
        count_result = await db.execute(
            select(
                UserRoleAssignment.org_unit_id,
                func.count(func.distinct(UserRoleAssignment.user_id)),
            )
            .where(UserRoleAssignment.org_unit_id.in_(unit_ids))
            .group_by(UserRoleAssignment.org_unit_id)
        )
        counts = {row[0]: row[1] for row in count_result.all()}

    return [
        {
            "id": str(u.id),
            "client_id": str(u.client_id),
            "parent_unit_id": str(u.parent_unit_id) if u.parent_unit_id else None,
            "name": u.name,
            "unit_type": u.unit_type,
            "member_count": counts.get(u.id, 0),
            "created_at": u.created_at.isoformat(),
        }
        for u in units
    ]


async def update_org_unit(
    db: AsyncSession,
    unit: OrganizationalUnit,
    name: str | None,
    unit_type: str | None,
) -> OrganizationalUnit:
    if unit_type is not None and unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")
    if name is not None:
        unit.name = name
    if unit_type is not None:
        unit.unit_type = unit_type
    return unit


async def list_unit_members(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
) -> list[dict]:
    """List members of an org unit grouped by user with their roles."""
    result = await db.execute(
        select(UserRoleAssignment, User, Role)
        .join(User, UserRoleAssignment.user_id == User.id)
        .join(Role, UserRoleAssignment.role_id == Role.id)
        .where(UserRoleAssignment.org_unit_id == org_unit_id)
        .order_by(User.email.asc(), Role.name.asc())
    )

    members_map: dict[uuid_mod.UUID, dict] = {}
    for ura, user, role in result.all():
        if user.id not in members_map:
            members_map[user.id] = {
                "user_id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "roles": [],
            }
        members_map[user.id]["roles"].append({
            "role_id": str(role.id),
            "role_name": role.name,
            "assigned_at": ura.created_at.isoformat(),
        })

    return list(members_map.values())


async def assign_role(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    role_id: uuid_mod.UUID,
    tenant_id: uuid_mod.UUID,
    assigned_by: uuid_mod.UUID,
) -> UserRoleAssignment:
    """Assign a role to a user in an org unit."""
    user_result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    if not user_result.scalar_one_or_none():
        raise ValueError("User not found or inactive")

    role_result = await db.execute(select(Role).where(Role.id == role_id))
    if not role_result.scalar_one_or_none():
        raise ValueError("Role not found")

    assignment = UserRoleAssignment(
        user_id=user_id,
        org_unit_id=org_unit_id,
        role_id=role_id,
        tenant_id=tenant_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)

    try:
        await db.flush()
    except Exception:
        raise ValueError("User already has this role in this unit")

    logger.info("org_units.role_assigned", user_id=str(user_id), org_unit_id=str(org_unit_id), role_id=str(role_id))
    return assignment


async def remove_user_from_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> int:
    """Remove ALL role assignments for a user in an org unit."""
    result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id,
            UserRoleAssignment.user_id == user_id,
        )
    )
    assignments = result.scalars().all()
    if not assignments:
        raise ValueError("No assignments found for this user in this unit")

    for a in assignments:
        await db.delete(a)

    logger.info("org_units.user_removed", user_id=str(user_id), org_unit_id=str(org_unit_id), count=len(assignments))
    return len(assignments)


async def delete_org_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
) -> None:
    """Delete an org unit. Fails if it has children or members."""
    # Check for child units
    child_result = await db.execute(
        select(func.count()).select_from(OrganizationalUnit).where(
            OrganizationalUnit.parent_unit_id == org_unit_id
        )
    )
    if (child_result.scalar() or 0) > 0:
        raise ValueError("Cannot delete a unit that has sub-units. Remove sub-units first.")

    # Check for members
    member_result = await db.execute(
        select(func.count()).select_from(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id
        )
    )
    if (member_result.scalar() or 0) > 0:
        raise ValueError("Cannot delete a unit that has members. Remove all members first.")

    result = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.id == org_unit_id)
    )
    unit = result.scalar_one_or_none()
    if not unit:
        raise ValueError("Org unit not found")

    await db.delete(unit)
    logger.info("org_units.deleted", unit_id=str(org_unit_id), name=unit.name)


async def remove_role_from_user(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    role_id: uuid_mod.UUID,
) -> None:
    """Remove a specific role assignment."""
    result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id,
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == role_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise ValueError("Assignment not found")

    await db.delete(assignment)
    logger.info("org_units.role_removed", user_id=str(user_id), org_unit_id=str(org_unit_id), role_id=str(role_id))
