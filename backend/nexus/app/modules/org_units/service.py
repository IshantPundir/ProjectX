"""Org unit CRUD — scoped by caller's org_unit_id.

Super Admin (org_unit_id=NULL): sees all units in the tenant.
Admin (org_unit_id=<uuid>): sees their own unit + descendants only.
"""

import uuid as uuid_mod

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit, User, UserOrgAssignment

logger = structlog.get_logger()

ALLOWED_UNIT_TYPES = {"client_account", "department", "team", "branch", "region"}


async def _get_unit_and_descendant_ids(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    root_unit_id: uuid_mod.UUID,
) -> set[uuid_mod.UUID]:
    """Return the root unit ID + all descendant IDs (BFS traversal)."""
    # Fetch all units for the tenant, then walk the tree in-memory
    result = await db.execute(
        select(OrganizationalUnit)
        .where(OrganizationalUnit.client_id == client_id)
    )
    all_units = list(result.scalars().all())

    children_map: dict[uuid_mod.UUID | None, list[OrganizationalUnit]] = {}
    for u in all_units:
        children_map.setdefault(u.parent_unit_id, []).append(u)

    ids: set[uuid_mod.UUID] = {root_unit_id}
    queue = [root_unit_id]
    while queue:
        parent_id = queue.pop()
        for child in children_map.get(parent_id, []):
            ids.add(child.id)
            queue.append(child.id)

    return ids


async def create_org_unit(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
    creator_user_id: uuid_mod.UUID,
) -> OrganizationalUnit:
    """Create an org unit and auto-assign admins.

    1. The creator is always assigned as an admin of the new unit.
    2. If this is a nested unit, all admins from the parent unit are
       also assigned to the new unit (inherited admin access).
    """
    if unit_type not in ALLOWED_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type: {unit_type}")

    unit = OrganizationalUnit(
        client_id=client_id,
        name=name,
        unit_type=unit_type,
        parent_unit_id=parent_unit_id,
    )
    db.add(unit)
    await db.flush()

    # Auto-assign the creator as admin of this unit
    creator_assignment = UserOrgAssignment(
        user_id=creator_user_id,
        org_unit_id=unit.id,
        assigned_by=creator_user_id,
        role="Admin",
        is_admin=True,
    )
    db.add(creator_assignment)

    # If nested, inherit parent's admin assignments (skip creator to avoid duplicate)
    if parent_unit_id is not None:
        parent_members = await db.execute(
            select(UserOrgAssignment).where(
                UserOrgAssignment.org_unit_id == parent_unit_id,
                UserOrgAssignment.is_admin == True,  # only inherit admins
            )
        )
        for parent_assignment in parent_members.scalars().all():
            if parent_assignment.user_id != creator_user_id:
                child_assignment = UserOrgAssignment(
                    user_id=parent_assignment.user_id,
                    org_unit_id=unit.id,
                    assigned_by=creator_user_id,
                    role=parent_assignment.role,       # inherit role
                    is_admin=True,                      # inherit admin status
                    permissions=parent_assignment.permissions or [],
                )
                db.add(child_assignment)

    await db.flush()
    logger.info("org_unit.created", unit_id=str(unit.id), name=name, creator=str(creator_user_id))
    return unit


async def list_org_units(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    caller_org_unit_id: uuid_mod.UUID | None = None,
) -> list[OrganizationalUnit]:
    """List org units. Super Admin sees all; Admin sees own unit + descendants."""
    if caller_org_unit_id is None:
        # Super Admin — see all units in tenant
        result = await db.execute(
            select(OrganizationalUnit)
            .where(OrganizationalUnit.client_id == client_id)
            .order_by(OrganizationalUnit.created_at.asc())
        )
        return list(result.scalars().all())

    # Admin — see own unit + descendants
    visible_ids = await _get_unit_and_descendant_ids(db, client_id, caller_org_unit_id)
    result = await db.execute(
        select(OrganizationalUnit)
        .where(OrganizationalUnit.id.in_(visible_ids))
        .order_by(OrganizationalUnit.created_at.asc())
    )
    return list(result.scalars().all())


async def get_org_unit(
    db: AsyncSession, unit_id: uuid_mod.UUID, client_id: uuid_mod.UUID
) -> OrganizationalUnit:
    result = await db.execute(
        select(OrganizationalUnit).where(
            OrganizationalUnit.id == unit_id,
            OrganizationalUnit.client_id == client_id,
        )
    )
    unit = result.scalar_one_or_none()
    if not unit:
        raise ValueError("Org unit not found")
    return unit


async def update_org_unit(
    db: AsyncSession,
    unit_id: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
    name: str | None = None,
    unit_type: str | None = None,
) -> OrganizationalUnit:
    unit = await get_org_unit(db, unit_id, client_id)
    if name is not None:
        unit.name = name
    if unit_type is not None:
        if unit_type not in ALLOWED_UNIT_TYPES:
            raise ValueError(f"Invalid unit_type: {unit_type}")
        unit.unit_type = unit_type
    return unit


async def list_org_unit_members(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
) -> list[dict]:
    """List all users assigned to an org unit.

    Role, permissions, and is_admin come from the ASSIGNMENT, not the user.
    A user can have different roles in different units.
    """
    await get_org_unit(db, org_unit_id, client_id)

    members: list[dict] = []

    # All assignments come from the junction table — role is per-assignment
    result = await db.execute(
        select(UserOrgAssignment, User)
        .join(User, UserOrgAssignment.user_id == User.id)
        .where(UserOrgAssignment.org_unit_id == org_unit_id, User.is_active == True)
        .order_by(UserOrgAssignment.created_at.asc())
    )
    for assignment, user in result.all():
        members.append({
            "user_id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": assignment.role,            # from the assignment, not user
            "is_admin": assignment.is_admin,    # admin of THIS unit
            "permissions": assignment.permissions or [],
            "assignment_type": "assigned",
            "assigned_at": assignment.created_at.isoformat(),
        })

    return members


async def assign_user_to_org_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    assigned_by: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
    role: str | None = None,
    is_admin: bool = False,
) -> UserOrgAssignment:
    """Assign an existing user to an org unit with a per-unit role."""
    await get_org_unit(db, org_unit_id, client_id)

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError("User not found or inactive")

    existing = await db.execute(
        select(UserOrgAssignment).where(
            UserOrgAssignment.user_id == user_id,
            UserOrgAssignment.org_unit_id == org_unit_id,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("User is already assigned to this org unit")

    assignment = UserOrgAssignment(
        user_id=user_id,
        org_unit_id=org_unit_id,
        assigned_by=assigned_by,
        role=role,
        is_admin=is_admin,
    )
    db.add(assignment)
    await db.flush()
    logger.info("org_unit.user_assigned", org_unit_id=str(org_unit_id), user_id=str(user_id), role=role)
    return assignment


async def update_member_assignment(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
    role: str | None = None,
    is_admin: bool | None = None,
    permissions: list[str] | None = None,
) -> UserOrgAssignment:
    """Update a member's role/permissions within an org unit."""
    await get_org_unit(db, org_unit_id, client_id)

    result = await db.execute(
        select(UserOrgAssignment).where(
            UserOrgAssignment.user_id == user_id,
            UserOrgAssignment.org_unit_id == org_unit_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise ValueError("Assignment not found")

    if role is not None:
        assignment.role = role
    if is_admin is not None:
        assignment.is_admin = is_admin
    if permissions is not None:
        assignment.permissions = permissions

    logger.info("org_unit.member_updated", org_unit_id=str(org_unit_id), user_id=str(user_id))
    return assignment


async def unassign_user_from_org_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
) -> None:
    """Remove a user's additional assignment (not their primary org unit)."""
    await get_org_unit(db, org_unit_id, client_id)

    result = await db.execute(
        select(UserOrgAssignment).where(
            UserOrgAssignment.user_id == user_id,
            UserOrgAssignment.org_unit_id == org_unit_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise ValueError("Assignment not found")

    await db.delete(assignment)
    logger.info("org_unit.user_unassigned", org_unit_id=str(org_unit_id), user_id=str(user_id))
