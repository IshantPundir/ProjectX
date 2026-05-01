"""Org unit CRUD and role assignment service."""

import uuid as uuid_mod
from datetime import UTC, datetime
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import actions as audit_actions, log_event
# NOTE: auth.models and roles.models are deep-path imports (NOT through their
# public __init__) to break the auth → org_units → auth circular __init__
# chain. Using the public API (from app.modules.auth import User, ...) would
# re-trigger auth's partially-initialized package during startup. These are
# model-only imports (no business logic dependency), so the deep path is
# safe and acceptable per Phase 4's documented exception.
from app.modules.auth.models import User, UserRoleAssignment
from app.modules.org_units.models import OrganizationalUnit
from app.modules.roles.models import Role
from app.modules.org_units.company_profile import CompanyProfile

logger = structlog.get_logger()

VALID_UNIT_TYPES = {"company", "division", "client_account", "region", "team"}


async def _collect_descendant_ids(
    db: AsyncSession,
    root_unit_id: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
) -> list[uuid_mod.UUID]:
    """Return ids of every unit transitively under `root_unit_id` (root excluded).

    Loads the tenant's units once and walks parent_unit_id in-memory. Mirrors
    the in-memory walk used by `list_org_units` — the tree is shallow and
    bounded (<10 deep in practice), so the constant-query approach beats a
    recursive CTE for clarity and consistency."""
    result = await db.execute(
        select(OrganizationalUnit.id, OrganizationalUnit.parent_unit_id).where(
            OrganizationalUnit.client_id == client_id,
        )
    )
    children_of: dict[uuid_mod.UUID, list[uuid_mod.UUID]] = {}
    for uid, parent_id in result.all():
        if parent_id is not None:
            children_of.setdefault(parent_id, []).append(uid)

    out: list[uuid_mod.UUID] = []
    stack: list[uuid_mod.UUID] = list(children_of.get(root_unit_id, []))
    seen: set[uuid_mod.UUID] = set()
    while stack:
        uid = stack.pop()
        if uid in seen:
            continue  # defensive: corrupted parent-chain cycle
        seen.add(uid)
        out.append(uid)
        stack.extend(children_of.get(uid, []))
    return out


async def _get_admin_role(db: AsyncSession) -> Role | None:
    """Look up the seeded system 'Admin' role. Returns None if missing
    (only possible in test fixtures that skip role seeding)."""
    result = await db.execute(
        select(Role).where(Role.name == "Admin", Role.is_system == True)
    )
    return result.scalar_one_or_none()


def _validate_and_normalize_company_profile(profile: dict | None) -> dict | None:
    """Strict validation of the 4-field Phase 2A company profile shape.
    Returns the validated dict (Pydantic round-trip) or raises ValueError
    with a user-facing message."""
    if profile is None:
        return None
    try:
        return CompanyProfile(**profile).model_dump()
    except ValidationError as e:
        raise ValueError(
            "Company profile validation failed: "
            + "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
        )


async def create_org_unit(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
    created_by: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    company_profile: dict | None = None,
    metadata: dict | None = None,
) -> OrganizationalUnit:
    if unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")

    # Rule 1: company must be root (no parent)
    if unit_type == "company" and parent_unit_id is not None:
        raise ValueError("A company root unit cannot have a parent unit.")

    # Rule 2: only one company unit per tenant
    if unit_type == "company":
        existing_root = await db.execute(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == client_id,
                OrganizationalUnit.parent_unit_id.is_(None),
            )
        )
        if existing_root.scalar_one_or_none():
            raise ValueError("A root company unit already exists for this tenant.")

    # Rule 3 (Phase 2A): company_profile is OPTIONAL on create. The invite
    # completion flow creates the root company unit before the onboarding
    # wizard has collected the 4-field profile; the wizard PATCHes the
    # profile onto the unit as a follow-up step. JD creation enforces
    # "profile must exist in ancestry" via find_company_profile_in_ancestry(),
    # so the create-time relaxation does not weaken the invariant that matters.
    # Strict shape validation still runs when a profile IS provided.
    validated_profile = _validate_and_normalize_company_profile(company_profile)

    # Rule 4: parent-based nesting enforcement
    if parent_unit_id is not None:
        parent_result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == parent_unit_id)
        )
        parent_unit = parent_result.scalar_one_or_none()
        if not parent_unit:
            raise ValueError("Parent unit not found.")

        if parent_unit.unit_type == "team":
            raise ValueError("Teams are leaf nodes and cannot contain sub-units.")

        if unit_type == "client_account" and parent_unit.unit_type == "client_account":
            raise ValueError("A client account cannot be nested under another client account.")

    unit = OrganizationalUnit(
        client_id=client_id,
        name=name,
        unit_type=unit_type,
        parent_unit_id=parent_unit_id,
        created_by=created_by,
        deletable_by=created_by,
        is_root=(unit_type == "company"),
        company_profile=validated_profile,
        unit_metadata=metadata,
    )
    db.add(unit)
    await db.flush()

    # Stamp completion tracking columns if a profile was saved
    if validated_profile is not None:
        unit.company_profile_completed_at = datetime.now(UTC)
        unit.company_profile_completed_by = created_by

    # Admin inheritance: copy Admin role assignments from parent
    if parent_unit_id is not None:
        admin_role = await _get_admin_role(db)

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


async def get_org_unit(
    db: AsyncSession,
    unit_id: uuid_mod.UUID,
    client_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    is_super_admin: bool,
) -> dict | None:
    """Fetch a single org unit by id, scoped to the tenant.

    Visibility follows the same rules as list_org_units:
    - Super admin sees any unit in the tenant.
    - Others see units where they hold Admin role, PLUS ancestors of
      those units (ancestor-only units are returned with is_accessible=False
      so the frontend can show a greyed "ask for access" state).

    Returns None if the unit is not found, not in the tenant, or not visible
    to the caller.
    """
    unit_result = await db.execute(
        select(OrganizationalUnit).where(
            OrganizationalUnit.id == unit_id,
            OrganizationalUnit.client_id == client_id,
        )
    )
    unit = unit_result.scalar_one_or_none()
    if unit is None:
        return None

    if is_super_admin:
        is_accessible = True
    else:
        # Find units where the caller holds the Admin role.
        admin_role_q = await db.execute(
            select(Role).where(Role.name == "Admin", Role.is_system == True)
        )
        admin_role = admin_role_q.scalar_one_or_none()

        accessible_ids: set[uuid_mod.UUID] = set()
        if admin_role:
            assignment_result = await db.execute(
                select(UserRoleAssignment.org_unit_id)
                .where(
                    UserRoleAssignment.user_id == user_id,
                    UserRoleAssignment.role_id == admin_role.id,
                )
                .distinct()
            )
            accessible_ids = {row[0] for row in assignment_result.all()}

        if not accessible_ids:
            return None

        # Walk up from each assigned unit to collect ancestors — the caller
        # can see an ancestor even if they don't hold Admin on it directly,
        # to support tree rendering / breadcrumbs.
        all_result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.client_id == client_id)
        )
        unit_map = {u.id: u for u in all_result.scalars().all()}

        visible_ids = set(accessible_ids)
        for uid in list(accessible_ids):
            current = unit_map.get(uid)
            while current and current.parent_unit_id:
                visible_ids.add(current.parent_unit_id)
                current = unit_map.get(current.parent_unit_id)

        if unit.id not in visible_ids:
            return None

        is_accessible = unit.id in accessible_ids

    # Member count for this unit.
    count_result = await db.execute(
        select(func.count(func.distinct(UserRoleAssignment.user_id))).where(
            UserRoleAssignment.org_unit_id == unit.id
        )
    )
    member_count = count_result.scalar() or 0

    # Created-by / deletable-by emails.
    user_ids_to_load: set[uuid_mod.UUID] = set()
    if unit.created_by:
        user_ids_to_load.add(unit.created_by)
    if unit.deletable_by:
        user_ids_to_load.add(unit.deletable_by)

    email_map: dict[uuid_mod.UUID, str] = {}
    if user_ids_to_load:
        email_result = await db.execute(
            select(User.id, User.email).where(User.id.in_(user_ids_to_load))
        )
        email_map = {row[0]: row[1] for row in email_result.all()}

    # For inaccessible (ancestor-only) units, surface admin emails so the
    # frontend can render an "ask for access" affordance.
    admin_emails: list[str] = []
    if not is_accessible:
        admin_role_result = await db.execute(
            select(Role).where(Role.name == "Admin", Role.is_system == True)
        )
        admin_role = admin_role_result.scalar_one_or_none()
        if admin_role:
            admin_assign_result = await db.execute(
                select(User.email)
                .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
                .where(
                    UserRoleAssignment.org_unit_id == unit.id,
                    UserRoleAssignment.role_id == admin_role.id,
                )
            )
            admin_emails = [row[0] for row in admin_assign_result.all()]

    inherited_locale = await find_locale_defaults_in_ancestry(db, unit.id)
    inherited_compliance = await find_compliance_flags_in_ancestry(db, unit.id)

    return {
        "id": str(unit.id),
        "client_id": str(unit.client_id),
        "parent_unit_id": str(unit.parent_unit_id) if unit.parent_unit_id else None,
        "name": unit.name,
        "unit_type": unit.unit_type,
        "member_count": member_count,
        "is_root": unit.is_root,
        "company_profile": unit.company_profile,
        "company_profile_completed_at": (
            unit.company_profile_completed_at.isoformat()
            if unit.company_profile_completed_at
            else None
        ),
        "metadata": unit.unit_metadata,
        "created_at": unit.created_at.isoformat(),
        "created_by": str(unit.created_by) if unit.created_by else None,
        "created_by_email": email_map.get(unit.created_by) if unit.created_by else None,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "deletable_by_email": email_map.get(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "is_accessible": is_accessible,
        "admin_emails": admin_emails,
        "inherited_locale": inherited_locale,
        "inherited_compliance": inherited_compliance,
    }


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
        units = list(result.scalars().all())
        accessible_ids = {u.id for u in units}
        unit_map = {u.id: u for u in units}
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
            "is_root": u.is_root,
            "company_profile": u.company_profile,
            "company_profile_completed_at": (
                u.company_profile_completed_at.isoformat()
                if u.company_profile_completed_at
                else None
            ),
            "metadata": u.unit_metadata,
            "created_at": u.created_at.isoformat(),
            "created_by": str(u.created_by) if u.created_by else None,
            "created_by_email": email_map.get(u.created_by) if u.created_by else None,
            "deletable_by": str(u.deletable_by) if u.deletable_by else None,
            "deletable_by_email": email_map.get(u.deletable_by) if u.deletable_by else None,
            "admin_delete_disabled": u.admin_delete_disabled,
            "is_accessible": u.id in accessible_ids,
            "admin_emails": admin_emails_by_unit.get(u.id, []),
            "inherited_locale": _serialize_inheritance(
                _walk_metadata_in_map(u, unit_map, LOCALE_KEYS)
            ),
            "inherited_compliance": _serialize_inheritance(
                _walk_metadata_in_map(u, unit_map, COMPLIANCE_KEYS)
            ),
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
    company_profile: dict | None = None,
    set_company_profile: bool = False,
    metadata: dict | None = None,
    set_metadata: bool = False,
) -> OrganizationalUnit:
    before = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "company_profile": str(unit.company_profile) if unit.company_profile else None,
        "metadata": str(unit.unit_metadata) if unit.unit_metadata else None,
    }

    if unit_type is not None and unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")

    # Prevent changing unit_type of a root company unit
    if unit_type is not None and unit.unit_type == "company" and unit_type != "company":
        raise ValueError("The unit type of the root company unit cannot be changed.")

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

    # Update company_profile if explicitly requested
    if set_company_profile:
        if unit.unit_type in ("company", "client_account") and not company_profile:
            raise ValueError(f"A company_profile is required for units of type '{unit.unit_type}'.")
        validated_profile = _validate_and_normalize_company_profile(company_profile)
        unit.company_profile = validated_profile
        if validated_profile is not None:
            unit.company_profile_completed_at = datetime.now(UTC)
            unit.company_profile_completed_by = actor_id

    # Update metadata if explicitly requested. Opaque dict validated at the
    # application layer — the DB keeps it as JSONB so the shape can evolve
    # per unit_type without migrations. None clears the field; {} is a
    # valid empty object.
    if set_metadata:
        unit.unit_metadata = metadata

    after = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "company_profile": str(unit.company_profile) if unit.company_profile else None,
        "metadata": str(unit.unit_metadata) if unit.unit_metadata else None,
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
    """Assign a role to a user in an org unit.

    Admin inheritance: when the role is the system 'Admin' role, also create
    Admin assignments for every existing descendant of `org_unit_id`. This
    mirrors `create_org_unit`, which copies parent Admin assignments down
    when a child is created. Without this cascade, granting Admin on a unit
    after its tree was built would leave the user without access to the
    pre-existing children — they'd see the unit but none of its sub-units,
    which is the bug this hook fixes.
    """
    user_result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    if not user_result.scalar_one_or_none():
        raise ValueError("User not found or inactive")

    role_result = await db.execute(select(Role).where(Role.id == role_id))
    role = role_result.scalar_one_or_none()
    if role is None:
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

    cascaded_to: list[uuid_mod.UUID] = []
    if role.is_system and role.name == "Admin":
        descendant_ids = await _collect_descendant_ids(db, org_unit_id, tenant_id)
        if descendant_ids:
            existing_result = await db.execute(
                select(UserRoleAssignment.org_unit_id).where(
                    UserRoleAssignment.user_id == user_id,
                    UserRoleAssignment.role_id == role_id,
                    UserRoleAssignment.org_unit_id.in_(descendant_ids),
                )
            )
            already_have = {row[0] for row in existing_result.all()}
            for desc_id in descendant_ids:
                if desc_id in already_have:
                    continue
                db.add(
                    UserRoleAssignment(
                        user_id=user_id,
                        org_unit_id=desc_id,
                        role_id=role_id,
                        tenant_id=tenant_id,
                        assigned_by=assigned_by,
                    )
                )
                cascaded_to.append(desc_id)
            if cascaded_to:
                await db.flush()

    logger.info(
        "org_units.role_assigned",
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        role_id=str(role_id),
        cascaded_descendants=len(cascaded_to),
    )

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=assigned_by,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_MEMBER_ADDED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={
            "user_id": str(user_id),
            "role_id": str(role_id),
            "cascaded_to": [str(d) for d in cascaded_to],
        },
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
    """Remove ALL role assignments for a user in an org unit.

    Admin inheritance: if any of the removed roles is the system 'Admin'
    role, also remove that user's Admin assignment from every descendant.
    Symmetrical to `assign_role`'s cascade so the denormalized model stays
    coherent.
    """
    result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id,
            UserRoleAssignment.user_id == user_id,
        )
    )
    assignments = result.scalars().all()
    if not assignments:
        raise ValueError("No assignments found for this user in this unit")

    tenant_id = assignments[0].tenant_id
    admin_role = await _get_admin_role(db)
    removed_admin = admin_role is not None and any(
        a.role_id == admin_role.id for a in assignments
    )

    for a in assignments:
        await db.delete(a)

    await _nullify_deletable_by_if_needed(db, org_unit_id, user_id)

    cascaded_to: list[uuid_mod.UUID] = []
    if removed_admin and admin_role is not None:
        cascaded_to = await _cascade_remove_admin_from_descendants(
            db, org_unit_id, user_id, admin_role.id, tenant_id
        )

    logger.info(
        "org_units.user_removed",
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        count=len(assignments),
        cascaded_descendants=len(cascaded_to),
    )

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_MEMBER_REMOVED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={
            "user_id": str(user_id),
            "roles_removed": len(assignments),
            "cascaded_to": [str(d) for d in cascaded_to],
        },
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

    if unit.is_root:
        raise ValueError("The root company unit cannot be deleted.")

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


async def _cascade_remove_admin_from_descendants(
    db: AsyncSession,
    root_unit_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    admin_role_id: uuid_mod.UUID,
    tenant_id: uuid_mod.UUID,
) -> list[uuid_mod.UUID]:
    """Drop `user_id`'s Admin assignment from every descendant of
    `root_unit_id` and run the deletable_by-nullification helper on each.

    Returns the list of descendant unit ids that actually had a row removed
    (used by callers for audit logging). Symmetric to the inheritance cascade
    in `assign_role`.
    """
    descendant_ids = await _collect_descendant_ids(db, root_unit_id, tenant_id)
    if not descendant_ids:
        return []

    existing_result = await db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == admin_role_id,
            UserRoleAssignment.org_unit_id.in_(descendant_ids),
        )
    )
    cascaded: list[uuid_mod.UUID] = []
    for ura in existing_result.scalars().all():
        cascaded.append(ura.org_unit_id)
        await db.delete(ura)

    for desc_id in cascaded:
        await _nullify_deletable_by_if_needed(db, desc_id, user_id)

    return cascaded


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
    """Remove a specific role assignment.

    If the role being removed is the system 'Admin' role, the user's Admin
    assignment is also removed from every descendant — symmetric to
    `assign_role`'s cascade.
    """
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

    tenant_id = assignment.tenant_id

    role_result = await db.execute(select(Role).where(Role.id == role_id))
    role = role_result.scalar_one_or_none()
    is_admin_role = role is not None and role.is_system and role.name == "Admin"

    await db.delete(assignment)
    await _nullify_deletable_by_if_needed(db, org_unit_id, user_id)

    cascaded_to: list[uuid_mod.UUID] = []
    if is_admin_role:
        cascaded_to = await _cascade_remove_admin_from_descendants(
            db, org_unit_id, user_id, role_id, tenant_id
        )

    logger.info(
        "org_units.role_removed",
        user_id=str(user_id),
        org_unit_id=str(org_unit_id),
        role_id=str(role_id),
        cascaded_descendants=len(cascaded_to),
    )

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=audit_actions.ORG_UNIT_ROLE_REMOVED,
        resource="org_unit",
        resource_id=org_unit_id,
        payload={
            "user_id": str(user_id),
            "role_id": str(role_id),
            "cascaded_to": [str(d) for d in cascaded_to],
        },
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


async def get_org_unit_ancestry(
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


async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Walk parent_unit_id chain from the given unit up to root.
    Return the first company_profile dict encountered. None if no ancestor
    has one.

    Used by create_job_posting() to validate that a JD can be created under
    a given org unit, and by the Dramatiq actor's _build_user_message() to
    pass company context into Call 1.

    Tenant scoping: this helper does NOT filter by tenant_id. The caller
    is responsible for ensuring ``org_unit_id`` belongs to the expected
    tenant. Today's callers all derive ``org_unit_id`` from a row that was
    already tenant-filtered (e.g. ``job.org_unit_id`` after the job row
    was loaded with a tenant_id check), so the walk stays inside one
    tenant's tree by transitivity. Bypass-session callers in particular
    must verify this invariant — RLS is not a backstop here."""
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            return None  # defensive: corrupted data loop
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            return None
        if unit.company_profile:
            return unit.company_profile
        current_id = unit.parent_unit_id
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Inheritance walks for unit_metadata keys (Phase 2C — org unit redesign)
# ─────────────────────────────────────────────────────────────────────────────
# Locale defaults (timezone/currency/locale) and compliance flags
# (AIVIA/GDPR/CCPA) are sourced at the Company root and may be overridden
# at any descendant. The frontend renders inherited values + per-field
# override toggles. To keep that UX honest without a chatty client-side
# parent walk, we surface the resolved-from-ancestry values on the GET
# response itself. The lookup mirrors `find_company_profile_in_ancestry`:
# walk the parent chain, first non-null value wins, per-key independently.

LOCALE_KEYS: tuple[str, ...] = (
    "default_timezone",
    "default_currency",
    "default_locale",
)

COMPLIANCE_KEYS: tuple[str, ...] = (
    "compliance_aivia_il",
    "compliance_gdpr_eu",
    "compliance_ccpa_ca",
)


def _walk_metadata_in_map(
    unit: OrganizationalUnit,
    unit_map: dict[UUID, OrganizationalUnit],
    keys: tuple[str, ...],
) -> tuple[dict[str, object | None], UUID | None] | None:
    """Walk parent chain in-memory using a pre-loaded unit_map.

    For each key in `keys`, return the first non-null value encountered while
    walking from `unit` up through its parents. Also returns the closest
    ancestor that contributed at least one key (used by the frontend to label
    the inheritance source — "Inherited from {ancestor name}").

    Returns None if no key is set anywhere in the chain.

    `unit_map` should contain `unit` and every reachable parent. Used by
    `list_org_units` where the full tenant tree is already in memory.
    """
    found: dict[str, object | None] = {k: None for k in keys}
    source_unit_id: UUID | None = None
    current: OrganizationalUnit | None = unit
    seen: set[UUID] = set()
    while current is not None:
        if current.id in seen:
            break  # defensive: corrupted parent-chain loop
        seen.add(current.id)
        meta = current.unit_metadata or {}
        contributed_here = False
        for key in keys:
            if found[key] is None and meta.get(key) is not None:
                found[key] = meta[key]
                contributed_here = True
        if contributed_here and source_unit_id is None:
            source_unit_id = current.id
        if all(v is not None for v in found.values()):
            break
        if current.parent_unit_id is None:
            break
        current = unit_map.get(current.parent_unit_id)
    if all(v is None for v in found.values()):
        return None
    return (found, source_unit_id)


async def _walk_metadata_in_db(
    db: AsyncSession,
    org_unit_id: UUID,
    keys: tuple[str, ...],
) -> tuple[dict[str, object | None], UUID | None] | None:
    """Walk parent chain via DB queries — used by single-unit GET path.

    Same semantics as `_walk_metadata_in_map`, but issues one query per
    ancestor. Acceptable because tree depth is bounded (<10 in practice)
    and the GET endpoint is a single request, not a hot loop.
    """
    found: dict[str, object | None] = {k: None for k in keys}
    source_unit_id: UUID | None = None
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            break  # defensive
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            break
        meta = unit.unit_metadata or {}
        contributed_here = False
        for key in keys:
            if found[key] is None and meta.get(key) is not None:
                found[key] = meta[key]
                contributed_here = True
        if contributed_here and source_unit_id is None:
            source_unit_id = unit.id
        if all(v is not None for v in found.values()):
            break
        current_id = unit.parent_unit_id
    if all(v is None for v in found.values()):
        return None
    return (found, source_unit_id)


def _serialize_inheritance(
    result: tuple[dict[str, object | None], UUID | None] | None,
) -> dict | None:
    """Wrap a walk result into the JSON shape consumed by the frontend.

    Shape: {"values": {...}, "source_unit_id": "uuid-or-null"}.
    Returns None when nothing is set anywhere in the chain — the frontend
    uses this as a signal that the unit (and all ancestors) have no value
    for that group.
    """
    if result is None:
        return None
    values, source_unit_id = result
    return {
        "values": values,
        "source_unit_id": str(source_unit_id) if source_unit_id else None,
    }


async def find_locale_defaults_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Single-unit ancestry walk for locale defaults.

    Returns the inherited-shape dict (`{"values": {...}, "source_unit_id":
    "..."}`) or None if no locale key is set anywhere in the chain.
    """
    return _serialize_inheritance(
        await _walk_metadata_in_db(db, org_unit_id, LOCALE_KEYS)
    )


async def find_compliance_flags_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Single-unit ancestry walk for compliance flags.

    Treats `False` as a meaningful set value (not "missing") — only `None`
    or absent keys are skipped during the walk.
    """
    return _serialize_inheritance(
        await _walk_metadata_in_db(db, org_unit_id, COMPLIANCE_KEYS)
    )
