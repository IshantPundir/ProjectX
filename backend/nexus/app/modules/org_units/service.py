"""Org unit CRUD and role assignment service."""

import uuid as uuid_mod

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit, Role, User, UserRoleAssignment
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event

logger = structlog.get_logger()

VALID_UNIT_TYPES = {"client_account", "department", "team", "branch", "region"}


async def create_org_unit(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
    created_by: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> OrganizationalUnit:
    if unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")

    unit = OrganizationalUnit(
        client_id=client_id,
        name=name,
        unit_type=unit_type,
        parent_unit_id=parent_unit_id,
        created_by=created_by,
        deletable_by=created_by,
    )
    db.add(unit)
    await db.flush()

    # Admin inheritance: copy Admin role assignments from parent
    if parent_unit_id is not None:
        admin_role_result = await db.execute(
            select(Role).where(Role.name == "Admin", Role.is_system == True)
        )
        admin_role = admin_role_result.scalar_one_or_none()

        if admin_role:
            parent_admins_result = await db.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.org_unit_id == parent_unit_id,
                    UserRoleAssignment.role_id == admin_role.id,
                )
            )
            for parent_assignment in parent_admins_result.scalars().all():
                existing = await db.execute(
                    select(UserRoleAssignment).where(
                        UserRoleAssignment.user_id == parent_assignment.user_id,
                        UserRoleAssignment.org_unit_id == unit.id,
                        UserRoleAssignment.role_id == admin_role.id,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    continue

                child_assignment = UserRoleAssignment(
                    user_id=parent_assignment.user_id,
                    org_unit_id=unit.id,
                    role_id=admin_role.id,
                    tenant_id=client_id,
                    assigned_by=created_by,
                )
                db.add(child_assignment)

            await db.flush()

    logger.info(
        "org_units.created",
        unit_id=str(unit.id),
        name=name,
        parent_unit_id=str(parent_unit_id) if parent_unit_id else None,
    )

    await log_event(
        db,
        tenant_id=client_id,
        actor_id=created_by,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_CREATED,
        resource="org_unit",
        resource_id=unit.id,
        payload={
            "name": name,
            "unit_type": unit_type,
            "parent_unit_id": str(parent_unit_id) if parent_unit_id else None,
        },
        ip_address=ip_address,
    )

    return unit


async def list_org_units(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    is_super_admin: bool,
) -> list[dict]:
    """List org units.

    Super admin sees all. Others see units they're assigned to PLUS
    ancestor units up the chain (for tree rendering). Ancestor-only
    units are flagged with is_accessible=False.
    """
    if is_super_admin:
        result = await db.execute(
            select(OrganizationalUnit)
            .where(OrganizationalUnit.client_id == client_id)
            .order_by(OrganizationalUnit.created_at.asc())
        )
        units = result.scalars().all()
        accessible_ids = {u.id for u in units}
    else:
        # 1. Get units where user has Admin role
        admin_role_q = await db.execute(
            select(Role).where(Role.name == "Admin", Role.is_system == True)
        )
        admin_role = admin_role_q.scalar_one_or_none()

        if admin_role:
            assignment_result = await db.execute(
                select(UserRoleAssignment.org_unit_id)
                .where(
                    UserRoleAssignment.user_id == user_id,
                    UserRoleAssignment.role_id == admin_role.id,
                )
                .distinct()
            )
            accessible_ids: set[uuid_mod.UUID] = {row[0] for row in assignment_result.all()}
        else:
            accessible_ids: set[uuid_mod.UUID] = set()

        # 2. Load ALL tenant units to walk up ancestor chains
        all_result = await db.execute(
            select(OrganizationalUnit)
            .where(OrganizationalUnit.client_id == client_id)
            .order_by(OrganizationalUnit.created_at.asc())
        )
        all_units = all_result.scalars().all()
        unit_map = {u.id: u for u in all_units}

        # 3. Walk up from each assigned unit to collect ancestors
        needed_ids = set(accessible_ids)
        for uid in list(accessible_ids):
            current = unit_map.get(uid)
            while current and current.parent_unit_id:
                needed_ids.add(current.parent_unit_id)
                current = unit_map.get(current.parent_unit_id)

        # 4. Filter to only needed units (assigned + ancestors)
        units = [u for u in all_units if u.id in needed_ids]

    # Batch member counts
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

    # Batch emails for created_by / deletable_by
    user_ids_to_load: set[uuid_mod.UUID] = set()
    for u in units:
        if u.created_by:
            user_ids_to_load.add(u.created_by)
        if u.deletable_by:
            user_ids_to_load.add(u.deletable_by)

    email_map: dict[uuid_mod.UUID, str] = {}
    if user_ids_to_load:
        email_result = await db.execute(
            select(User.id, User.email).where(User.id.in_(user_ids_to_load))
        )
        email_map = {row[0]: row[1] for row in email_result.all()}

    # For inaccessible units, load admin emails so frontend can show "ask for access"
    inaccessible_ids = [u.id for u in units if u.id not in accessible_ids]
    admin_emails_by_unit: dict[uuid_mod.UUID, list[str]] = {}
    if inaccessible_ids:
        admin_role_result = await db.execute(
            select(Role).where(Role.name == "Admin", Role.is_system == True)
        )
        admin_role = admin_role_result.scalar_one_or_none()
        if admin_role:
            admin_assign_result = await db.execute(
                select(UserRoleAssignment.org_unit_id, User.email)
                .join(User, UserRoleAssignment.user_id == User.id)
                .where(
                    UserRoleAssignment.org_unit_id.in_(inaccessible_ids),
                    UserRoleAssignment.role_id == admin_role.id,
                )
            )
            for org_id, email in admin_assign_result.all():
                admin_emails_by_unit.setdefault(org_id, []).append(email)

    return [
        {
            "id": str(u.id),
            "client_id": str(u.client_id),
            "parent_unit_id": str(u.parent_unit_id) if u.parent_unit_id else None,
            "name": u.name,
            "unit_type": u.unit_type,
            "member_count": counts.get(u.id, 0),
            "created_at": u.created_at.isoformat(),
            "created_by": str(u.created_by) if u.created_by else None,
            "created_by_email": email_map.get(u.created_by) if u.created_by else None,
            "deletable_by": str(u.deletable_by) if u.deletable_by else None,
            "deletable_by_email": email_map.get(u.deletable_by) if u.deletable_by else None,
            "admin_delete_disabled": u.admin_delete_disabled,
            "is_accessible": u.id in accessible_ids,
            "admin_emails": admin_emails_by_unit.get(u.id, []),
        }
        for u in units
    ]


async def update_org_unit(
    db: AsyncSession,
    unit: OrganizationalUnit,
    name: str | None,
    unit_type: str | None,
    deletable_by: str | None = None,
    set_deletable_by: bool = False,
    admin_delete_disabled: bool | None = None,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> OrganizationalUnit:
    before = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
    }

    if unit_type is not None and unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")
    if name is not None:
        unit.name = name
    if unit_type is not None:
        unit.unit_type = unit_type
    if admin_delete_disabled is not None:
        unit.admin_delete_disabled = admin_delete_disabled
    if set_deletable_by:
        if deletable_by is not None:
            admin_role_result = await db.execute(
                select(Role).where(Role.name == "Admin", Role.is_system == True)
            )
            admin_role = admin_role_result.scalar_one_or_none()
            if admin_role:
                assignment = await db.execute(
                    select(UserRoleAssignment).where(
                        UserRoleAssignment.user_id == uuid_mod.UUID(deletable_by),
                        UserRoleAssignment.org_unit_id == unit.id,
                        UserRoleAssignment.role_id == admin_role.id,
                    )
                )
                if assignment.scalar_one_or_none() is None:
                    raise ValueError(
                        "User must be an admin of this unit to be assigned as deletable_by"
                    )
            unit.deletable_by = uuid_mod.UUID(deletable_by)
        else:
            unit.deletable_by = None

    after = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
    }
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        await log_event(
            db,
            tenant_id=unit.client_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=audit_actions.ORG_UNIT_UPDATED,
            resource="org_unit",
            resource_id=unit.id,
            payload={"changed": changed},
            ip_address=ip_address,
        )

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
        members_map[user.id]["roles"].append(
            {
                "role_id": str(role.id),
                "role_name": role.name,
                "assigned_at": ura.created_at.isoformat(),
            }
        )

    return list(members_map.values())


async def assign_role(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    role_id: uuid_mod.UUID,
    tenant_id: uuid_mod.UUID,
    assigned_by: uuid_mod.UUID,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> UserRoleAssignment:
    """Assign a role to a user in an org unit."""
    user_result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
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

    logger.info(
        "org_units.role_assigned",
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        role_id=str(role_id),
    )

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=assigned_by,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_MEMBER_ADDED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"user_id": str(user_id), "role_id": str(role_id)},
        ip_address=ip_address,
    )

    return assignment


async def remove_user_from_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
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

    await _nullify_deletable_by_if_needed(db, org_unit_id, user_id)
    logger.info(
        "org_units.user_removed",
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        count=len(assignments),
    )

    await log_event(
        db,
        tenant_id=assignments[0].tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_MEMBER_REMOVED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"user_id": str(user_id), "roles_removed": len(assignments)},
        ip_address=ip_address,
    )

    return len(assignments)


async def delete_org_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    caller_user_id: uuid_mod.UUID,
    is_super_admin: bool,
    caller_has_admin_role: bool,
    actor_email: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Delete an org unit with authorization checks."""
    result = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.id == org_unit_id)
    )
    unit = result.scalar_one_or_none()
    if not unit:
        raise ValueError("Org unit not found")

    if not is_super_admin:
        if unit.admin_delete_disabled:
            raise PermissionError("Only a super admin can delete this unit")

        if unit.deletable_by is None:
            raise PermissionError(
                "No admin is authorized to delete this unit. Contact your super admin."
            )

        if not caller_has_admin_role or caller_user_id != unit.deletable_by:
            deletable_user_result = await db.execute(
                select(User).where(User.id == unit.deletable_by)
            )
            deletable_user = deletable_user_result.scalar_one_or_none()
            deletable_email = deletable_user.email if deletable_user else "unknown"
            raise PermissionError(f"Only the super admin or {deletable_email} can delete this unit")

    child_result = await db.execute(
        select(func.count())
        .select_from(OrganizationalUnit)
        .where(OrganizationalUnit.parent_unit_id == org_unit_id)
    )
    if (child_result.scalar() or 0) > 0:
        raise ValueError("Cannot delete a unit that has sub-units. Remove sub-units first.")

    member_result = await db.execute(
        select(func.count())
        .select_from(UserRoleAssignment)
        .where(UserRoleAssignment.org_unit_id == org_unit_id)
    )
    if (member_result.scalar() or 0) > 0:
        raise ValueError("Cannot delete a unit that has members. Remove all members first.")

    await log_event(
        db,
        tenant_id=unit.client_id,
        actor_id=caller_user_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_DELETED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"name": unit.name},
        ip_address=ip_address,
    )

    await db.delete(unit)
    logger.info("org_units.deleted", unit_id=str(org_unit_id), name=unit.name)


async def _nullify_deletable_by_if_needed(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> None:
    """If user is deletable_by for this unit and no longer Admin, set deletable_by to NULL."""
    unit_result = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.id == org_unit_id)
    )
    unit = unit_result.scalar_one_or_none()
    if not unit or unit.deletable_by != user_id:
        return

    admin_role_result = await db.execute(
        select(Role).where(Role.name == "Admin", Role.is_system == True)
    )
    admin_role = admin_role_result.scalar_one_or_none()
    if not admin_role:
        return

    remaining = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.org_unit_id == org_unit_id,
            UserRoleAssignment.role_id == admin_role.id,
        )
    )
    if remaining.scalar_one_or_none() is None:
        unit.deletable_by = None
        logger.info(
            "org_units.deletable_by_nullified", unit_id=str(org_unit_id), user_id=str(user_id)
        )


async def remove_role_from_user(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    role_id: uuid_mod.UUID,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
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
    await _nullify_deletable_by_if_needed(db, org_unit_id, user_id)
    logger.info(
        "org_units.role_removed",
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        role_id=str(role_id),
    )

    await log_event(
        db,
        tenant_id=assignment.tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_ROLE_REMOVED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={"user_id": str(user_id), "role_id": str(role_id)},
        ip_address=ip_address,
    )


async def nullify_deletable_by_for_user(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
) -> int:
    """Set deletable_by = NULL on all org units in this tenant where deletable_by == user_id.

    Used when a user is deactivated. Returns the count of units updated.
    """
    from sqlalchemy import update

    result = await db.execute(
        update(OrganizationalUnit)
        .where(
            OrganizationalUnit.client_id == tenant_id,
            OrganizationalUnit.deletable_by == user_id,
        )
        .values(deletable_by=None)
    )
    return result.rowcount
