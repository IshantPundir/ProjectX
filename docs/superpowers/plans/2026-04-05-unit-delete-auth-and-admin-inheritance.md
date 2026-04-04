# Unit Delete Authorization & Admin Inheritance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add creator tracking, delegated delete authority, deletion lock, and admin inheritance to org units.

**Architecture:** Three new columns on `organizational_units` (created_by, deletable_by, admin_delete_disabled). Delete authorization moves from simple super-admin gate to a multi-step check. Sub-unit creation copies parent Admin assignments. Role removal auto-nullifies deletable_by.

**Tech Stack:** FastAPI, SQLAlchemy async, Supabase Postgres, Next.js 14 App Router, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-04-05-unit-delete-auth-and-admin-inheritance-design.md`

---

## File Structure

### Files to modify
- `backend/supabase/migrations/20260405000000_initial_schema.sql` — add 3 columns + index
- `backend/nexus/app/models.py` — add 3 fields to OrganizationalUnit
- `backend/nexus/app/modules/org_units/schemas.py` — update OrgUnitResponse, UpdateOrgUnitRequest
- `backend/nexus/app/modules/org_units/service.py` — update create, delete, remove_role, remove_user, list, update
- `backend/nexus/app/modules/org_units/router.py` — update delete endpoint, update response construction, update update endpoint
- `frontend/app/app/(dashboard)/settings/org-units/page.tsx` — update OrgUnit interface
- `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx` — delete logic, lock toggle, reassign dropdown, creator display

---

## Task 1: Schema & Model Changes

**Files:**
- Modify: `backend/supabase/migrations/20260405000000_initial_schema.sql`
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Update the SQL migration**

In `backend/supabase/migrations/20260405000000_initial_schema.sql`, replace the `organizational_units` table definition (lines 61-69):

```sql
CREATE TABLE public.organizational_units (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES public.clients(id),
    parent_unit_id  UUID REFERENCES public.organizational_units(id),
    name            TEXT NOT NULL,
    unit_type       TEXT NOT NULL
                        CHECK (unit_type IN ('client_account', 'department', 'team', 'branch', 'region')),
    created_by      UUID REFERENCES public.users(id),
    deletable_by    UUID REFERENCES public.users(id),
    admin_delete_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

After the existing indexes (line 73), add:

```sql
CREATE INDEX org_units_created_by_idx ON public.organizational_units (created_by);
```

- [ ] **Step 2: Update the SQLAlchemy model**

In `backend/nexus/app/models.py`, add three fields to `OrganizationalUnit` after the `unit_type` line and before `created_at`:

```python
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    deletable_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    admin_delete_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
```

- [ ] **Step 3: Verify import works**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -c "from app.models import OrganizationalUnit; print(OrganizationalUnit.__table__.columns.keys())"
```

Expected output includes: `created_by`, `deletable_by`, `admin_delete_disabled`

- [ ] **Step 4: Commit**

```bash
git add backend/supabase/migrations/20260405000000_initial_schema.sql backend/nexus/app/models.py
git commit -m "schema: add created_by, deletable_by, admin_delete_disabled to organizational_units"
```

---

## Task 2: Update Schemas

**Files:**
- Modify: `backend/nexus/app/modules/org_units/schemas.py`

- [ ] **Step 1: Replace the entire schemas file**

```python
from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None          # super admin only — user ID or null to clear
    admin_delete_disabled: bool | None = None  # super admin only


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    created_at: str
    created_by: str | None
    created_by_email: str | None
    deletable_by: str | None
    deletable_by_email: str | None
    admin_delete_disabled: bool


class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str


class MemberRole(BaseModel):
    role_id: str
    role_name: str
    assigned_at: str


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    roles: list[MemberRole]
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/org_units/schemas.py
git commit -m "schemas: add created_by, deletable_by, admin_delete_disabled to OrgUnitResponse"
```

---

## Task 3: Update Service — create_org_unit with Admin Inheritance

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`

- [ ] **Step 1: Update create_org_unit signature and body**

Replace the `create_org_unit` function (lines 16-36) with:

```python
async def create_org_unit(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
    created_by: uuid_mod.UUID | None = None,
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
        # Find the Admin role
        admin_role_result = await db.execute(
            select(Role).where(Role.name == "Admin", Role.is_system == True)
        )
        admin_role = admin_role_result.scalar_one_or_none()

        if admin_role:
            # Get all parent admin assignments
            parent_admins_result = await db.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.org_unit_id == parent_unit_id,
                    UserRoleAssignment.role_id == admin_role.id,
                )
            )
            for parent_assignment in parent_admins_result.scalars().all():
                # Skip if this user already has Admin in the new unit (e.g., creator)
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

    logger.info("org_units.created", unit_id=str(unit.id), name=name, parent_unit_id=str(parent_unit_id) if parent_unit_id else None)
    return unit
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/org_units/service.py
git commit -m "service: create_org_unit sets created_by/deletable_by and inherits parent admins"
```

---

## Task 4: Update Service — Delete Authorization & Auto-Nullification

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`

- [ ] **Step 1: Rewrite delete_org_unit with authorization logic**

Replace the `delete_org_unit` function (lines 198-229) with:

```python
async def delete_org_unit(
    db: AsyncSession,
    org_unit_id: uuid_mod.UUID,
    caller_user_id: uuid_mod.UUID,
    is_super_admin: bool,
    caller_has_admin_role: bool,
) -> None:
    """Delete an org unit with authorization checks.

    Authorization flow:
    1. Super admin → always allowed
    2. admin_delete_disabled → only super admin
    3. deletable_by is NULL → no admin authorized
    4. Caller is Admin in unit AND caller == deletable_by → allowed
    5. Otherwise → denied with specific error
    """
    result = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.id == org_unit_id)
    )
    unit = result.scalar_one_or_none()
    if not unit:
        raise ValueError("Org unit not found")

    # Authorization
    if not is_super_admin:
        if unit.admin_delete_disabled:
            raise PermissionError("Only a super admin can delete this unit")

        if unit.deletable_by is None:
            raise PermissionError("No admin is authorized to delete this unit. Contact your super admin.")

        if not caller_has_admin_role or caller_user_id != unit.deletable_by:
            # Look up the deletable_by user's email for the error message
            deletable_user_result = await db.execute(
                select(User).where(User.id == unit.deletable_by)
            )
            deletable_user = deletable_user_result.scalar_one_or_none()
            deletable_email = deletable_user.email if deletable_user else "unknown"
            raise PermissionError(f"Only the super admin or {deletable_email} can delete this unit")

    # Existing safeguards
    child_result = await db.execute(
        select(func.count()).select_from(OrganizationalUnit).where(
            OrganizationalUnit.parent_unit_id == org_unit_id
        )
    )
    if (child_result.scalar() or 0) > 0:
        raise ValueError("Cannot delete a unit that has sub-units. Remove sub-units first.")

    member_result = await db.execute(
        select(func.count()).select_from(UserRoleAssignment).where(
            UserRoleAssignment.org_unit_id == org_unit_id
        )
    )
    if (member_result.scalar() or 0) > 0:
        raise ValueError("Cannot delete a unit that has members. Remove all members first.")

    await db.delete(unit)
    logger.info("org_units.deleted", unit_id=str(org_unit_id), name=unit.name)
```

- [ ] **Step 2: Add auto-nullification helper**

Add this function after `delete_org_unit`, before `remove_role_from_user`:

```python
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

    # Check if user still has Admin role in this unit
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
        logger.info("org_units.deletable_by_nullified", unit_id=str(org_unit_id), user_id=str(user_id))
```

- [ ] **Step 3: Add auto-nullification call to remove_role_from_user**

At the end of `remove_role_from_user`, after the `await db.delete(assignment)` line and before the logger call, add:

```python
    await _nullify_deletable_by_if_needed(db, org_unit_id, user_id)
```

- [ ] **Step 4: Add auto-nullification call to remove_user_from_unit**

At the end of `remove_user_from_unit`, after the `for a in assignments: await db.delete(a)` loop and before the logger call, add:

```python
    await _nullify_deletable_by_if_needed(db, org_unit_id, user_id)
```

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/org_units/service.py
git commit -m "service: delete authorization logic, auto-nullification of deletable_by on role removal"
```

---

## Task 5: Update Service — list_org_units and update_org_unit

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`

- [ ] **Step 1: Update list_org_units to include new fields + emails**

Replace the `list_org_units` function (lines 39-89) with:

```python
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

    # Batch-load emails for created_by and deletable_by
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
        }
        for u in units
    ]
```

- [ ] **Step 2: Update update_org_unit to handle new fields**

Replace the `update_org_unit` function (lines 92-104) with:

```python
async def update_org_unit(
    db: AsyncSession,
    unit: OrganizationalUnit,
    name: str | None,
    unit_type: str | None,
    deletable_by: str | None = None,
    set_deletable_by: bool = False,
    admin_delete_disabled: bool | None = None,
) -> OrganizationalUnit:
    """Update an org unit. deletable_by/admin_delete_disabled are super-admin-only (checked in router)."""
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
            # Validate target is an Admin of this unit
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
                    raise ValueError("User must be an admin of this unit to be assigned as deletable_by")
            unit.deletable_by = uuid_mod.UUID(deletable_by)
        else:
            unit.deletable_by = None
    return unit
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/org_units/service.py
git commit -m "service: list_org_units returns new fields with emails, update_org_unit handles deletable_by/lock"
```

---

## Task 6: Update Router

**Files:**
- Modify: `backend/nexus/app/modules/org_units/router.py`

- [ ] **Step 1: Replace the entire router file**

```python
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import OrganizationalUnit, User
from app.modules.auth.context import UserContext, get_current_user_roles, require_super_admin
from app.modules.org_units.schemas import (
    AssignRoleRequest,
    CreateOrgUnitRequest,
    OrgUnitMember,
    OrgUnitResponse,
    UpdateOrgUnitRequest,
)
from app.modules.org_units.service import (
    assign_role,
    create_org_unit,
    delete_org_unit,
    list_org_units,
    list_unit_members,
    remove_role_from_user,
    remove_user_from_unit,
    update_org_unit,
)

router = APIRouter(prefix="/api/org-units", tags=["org-units"])


def _require_unit_admin(ctx: UserContext, org_unit_id: uuid_mod.UUID) -> None:
    """Check super admin OR Admin role in the specific unit."""
    if ctx.is_super_admin:
        return
    if ctx.has_role_in_unit(org_unit_id, "Admin"):
        return
    raise HTTPException(status_code=403, detail="Requires super admin or Admin role in this unit")


def _build_response(unit: OrganizationalUnit, member_count: int, email_map: dict[uuid_mod.UUID, str]) -> OrgUnitResponse:
    """Build OrgUnitResponse with email lookups."""
    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=member_count,
        created_at=unit.created_at.isoformat(),
        created_by=str(unit.created_by) if unit.created_by else None,
        created_by_email=email_map.get(unit.created_by) if unit.created_by else None,
        deletable_by=str(unit.deletable_by) if unit.deletable_by else None,
        deletable_by_email=email_map.get(unit.deletable_by) if unit.deletable_by else None,
        admin_delete_disabled=unit.admin_delete_disabled,
    )


async def _load_email_map(db: AsyncSession, *user_ids: uuid_mod.UUID | None) -> dict[uuid_mod.UUID, str]:
    """Load emails for a set of user IDs."""
    ids = [uid for uid in user_ids if uid is not None]
    if not ids:
        return {}
    result = await db.execute(select(User.id, User.email).where(User.id.in_(ids)))
    return {row[0]: row[1] for row in result.all()}


@router.post("", response_model=OrgUnitResponse, dependencies=[require_super_admin()])
async def create_unit(
    data: CreateOrgUnitRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    """Create an org unit. Super admin only."""
    parent_id = uuid_mod.UUID(data.parent_unit_id) if data.parent_unit_id else None
    try:
        unit = await create_org_unit(
            db, ctx.user.tenant_id, data.name, data.unit_type, parent_id,
            created_by=ctx.user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    email_map = await _load_email_map(db, unit.created_by, unit.deletable_by)
    return _build_response(unit, 0, email_map)


@router.get("", response_model=list[OrgUnitResponse])
async def list_units(
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrgUnitResponse]:
    """List org units. Super admin: all. Others: assigned units only."""
    units = await list_org_units(db, ctx.user.tenant_id, ctx.user.id, ctx.is_super_admin)
    return [OrgUnitResponse(**u) for u in units]


@router.put("/{unit_id}", response_model=OrgUnitResponse)
async def update_unit(
    unit_id: str,
    data: UpdateOrgUnitRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    """Update an org unit. Super admin or Admin in unit.
    deletable_by and admin_delete_disabled are super-admin-only fields.
    """
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    # Guard: only super admin can change deletable_by or admin_delete_disabled
    if (data.deletable_by is not None or data.admin_delete_disabled is not None) and not ctx.is_super_admin:
        raise HTTPException(status_code=403, detail="Only a super admin can change delete settings")

    result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == uid))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Org unit not found")

    # Determine if deletable_by was explicitly sent (even as null)
    # Pydantic default is None, so we check if the field was in the request body
    raw_body = data.model_dump(exclude_unset=True)
    set_deletable_by = "deletable_by" in raw_body

    try:
        unit = await update_org_unit(
            db, unit, data.name, data.unit_type,
            deletable_by=data.deletable_by,
            set_deletable_by=set_deletable_by,
            admin_delete_disabled=data.admin_delete_disabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    email_map = await _load_email_map(db, unit.created_by, unit.deletable_by)
    return _build_response(unit, 0, email_map)


@router.delete("/{unit_id}")
async def delete_unit(
    unit_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Delete an org unit. Authorization per spec: super admin always, or deletable_by admin."""
    uid = uuid_mod.UUID(unit_id)

    try:
        await delete_org_unit(
            db,
            org_unit_id=uid,
            caller_user_id=ctx.user.id,
            is_super_admin=ctx.is_super_admin,
            caller_has_admin_role=ctx.has_role_in_unit(uid, "Admin"),
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted"}


@router.get("/{unit_id}/members", response_model=list[OrgUnitMember])
async def get_members(
    unit_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrgUnitMember]:
    """List members of an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    members = await list_unit_members(db, uid)
    return [OrgUnitMember(**m) for m in members]


@router.post("/{unit_id}/members")
async def assign_member_role(
    unit_id: str,
    data: AssignRoleRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Assign a role to a user in an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    try:
        await assign_role(
            db,
            org_unit_id=uid,
            user_id=uuid_mod.UUID(data.user_id),
            role_id=uuid_mod.UUID(data.role_id),
            tenant_id=ctx.user.tenant_id,
            assigned_by=ctx.user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "assigned"}


@router.delete("/{unit_id}/members/{user_id}")
async def remove_member(
    unit_id: str,
    user_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Remove all roles for a user in an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    try:
        count = await remove_user_from_unit(db, uid, uuid_mod.UUID(user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "removed", "count": str(count)}


@router.delete("/{unit_id}/members/{user_id}/roles/{role_id}")
async def remove_member_role(
    unit_id: str,
    user_id: str,
    role_id: str,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Remove a specific role from a user in an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    try:
        await remove_role_from_user(db, uid, uuid_mod.UUID(user_id), uuid_mod.UUID(role_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "removed"}
```

- [ ] **Step 2: Run backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: all 26 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/org_units/router.py
git commit -m "router: delete auth with PermissionError, update endpoint handles deletable_by/lock, email map helper"
```

---

## Task 7: Update Frontend — List Page Interface

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/page.tsx`

- [ ] **Step 1: Update the OrgUnit interface**

In the `OrgUnit` interface (lines 7-15), add the new fields:

```typescript
interface OrgUnit {
  id: string;
  client_id: string;
  parent_unit_id: string | null;
  name: string;
  unit_type: string;
  member_count: number;
  created_at: string;
  created_by: string | null;
  created_by_email: string | null;
  deletable_by: string | null;
  deletable_by_email: string | null;
  admin_delete_disabled: boolean;
}
```

- [ ] **Step 2: Commit**

```bash
git add "frontend/app/app/(dashboard)/settings/org-units/page.tsx"
git commit -m "frontend: update OrgUnit interface with delete auth fields"
```

---

## Task 8: Update Frontend — Detail Page

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`

This task updates the detail page with:
- Updated `OrgUnit` interface with new fields
- Creator display in metadata
- Delete button visibility logic (super admin or deletable_by admin)
- Lock toggle (super admin only)
- Reassign deletable_by dropdown (super admin only)

- [ ] **Step 1: Update the OrgUnit interface**

Add the same 5 new fields to the `OrgUnit` interface in the detail page:

```typescript
interface OrgUnit {
  id: string;
  client_id: string;
  parent_unit_id: string | null;
  name: string;
  unit_type: string;
  member_count: number;
  created_at: string;
  created_by: string | null;
  created_by_email: string | null;
  deletable_by: string | null;
  deletable_by_email: string | null;
  admin_delete_disabled: boolean;
}
```

- [ ] **Step 2: Update the unit header metadata section**

In the unit header (the non-editing view), update the metadata line to show creator. Find the `<div className="flex items-center gap-3 mt-1.5">` section and add after the "Created {date}" span:

```tsx
                  {unit.created_by_email && (
                    <span className="text-xs text-zinc-400">
                      by {unit.created_by_email}
                    </span>
                  )}
```

- [ ] **Step 3: Update delete button visibility and behavior**

The delete button should now be visible to:
- Super admin (always)
- The `deletable_by` user who is Admin in the unit (when `admin_delete_disabled` is false)

Replace the existing delete button block with logic that checks these conditions. The `handleDeleteUnit` function already exists and doesn't need changes — the backend enforces the auth. The frontend just controls visibility.

Add a helper above the return statement:

```typescript
  const canDelete = useMemo(() => {
    if (!me || !unit) return false;
    if (me.is_super_admin) return true;
    if (unit.admin_delete_disabled) return false;
    if (!unit.deletable_by) return false;
    // Check user is Admin in unit and is the deletable_by user
    const isAdmin = me.assignments.some((a) => a.org_unit_id === unitId && a.role_name === "Admin");
    // We need the current user's ID — get it from me
    return false; // We don't have me.user_id in MeData yet
  }, [me, unit, unitId]);
```

Wait — `MeData` doesn't include `user_id`. We need it for this check. Add `user_id: string` to the `MeData` interface and update the `canDelete` logic:

```typescript
interface MeData {
  user_id: string;
  is_super_admin: boolean;
  assignments: {
    org_unit_id: string;
    org_unit_name: string;
    role_name: string;
    permissions: string[];
  }[];
}
```

Then `canDelete`:

```typescript
  const canDelete = useMemo(() => {
    if (!me || !unit) return false;
    if (me.is_super_admin) return true;
    if (unit.admin_delete_disabled) return false;
    if (!unit.deletable_by) return false;
    const isAdmin = me.assignments.some((a) => a.org_unit_id === unitId && a.role_name === "Admin");
    return isAdmin && me.user_id === unit.deletable_by;
  }, [me, unit, unitId]);
```

Replace the `{me?.is_super_admin && (` guard on the delete button with `{canDelete && (`.

- [ ] **Step 4: Add deletion settings section (super admin only)**

Below the unit header card and above the sub-unit creation form, add a new section visible only to super admins:

```tsx
        {/* ─── Deletion Settings (super admin only) ─── */}
        {me?.is_super_admin && unit && (
          <div className="bg-white border border-zinc-200 rounded-xl p-6 mb-6">
            <h2 className="text-sm font-semibold text-zinc-900 mb-4">Deletion Settings</h2>
            <div className="space-y-4">
              {/* Lock toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-zinc-700">Lock deletion</p>
                  <p className="text-xs text-zinc-400">When enabled, only a super admin can delete this unit</p>
                </div>
                <button
                  type="button"
                  onClick={async () => {
                    const token = await getToken();
                    if (!token) return;
                    try {
                      await apiFetch(`/api/org-units/${unitId}`, {
                        method: "PUT",
                        token,
                        body: JSON.stringify({ admin_delete_disabled: !unit.admin_delete_disabled }),
                      });
                      await loadAll();
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Failed to update");
                    }
                  }}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors duration-200 cursor-pointer ${
                    unit.admin_delete_disabled ? "bg-green-600" : "bg-zinc-200"
                  }`}
                  role="switch"
                  aria-checked={unit.admin_delete_disabled}
                  aria-label="Lock deletion"
                >
                  <span
                    className={`inline-block h-4 w-4 rounded-full bg-white transition-transform duration-200 ${
                      unit.admin_delete_disabled ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
              </div>

              {/* Reassign deletable_by */}
              {!unit.admin_delete_disabled && (
                <div>
                  <label htmlFor="deletable-by" className="block text-sm text-zinc-700 mb-1">
                    Admin authorized to delete
                  </label>
                  <p className="text-xs text-zinc-400 mb-2">
                    Only this admin (and super admins) can delete this unit
                  </p>
                  <select
                    id="deletable-by"
                    value={unit.deletable_by || ""}
                    onChange={async (e) => {
                      const token = await getToken();
                      if (!token) return;
                      try {
                        await apiFetch(`/api/org-units/${unitId}`, {
                          method: "PUT",
                          token,
                          body: JSON.stringify({ deletable_by: e.target.value || null }),
                        });
                        await loadAll();
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Failed to update");
                      }
                    }}
                    className="w-full max-w-xs border border-zinc-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-green-600 cursor-pointer"
                  >
                    <option value="">None (only super admin)</option>
                    {members
                      .filter((m) => m.roles.some((r) => r.role_name === "Admin"))
                      .map((m) => (
                        <option key={m.user_id} value={m.user_id}>
                          {m.full_name || m.email}
                        </option>
                      ))}
                  </select>
                </div>
              )}
            </div>
          </div>
        )}
```

- [ ] **Step 5: Build and verify**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app && npm run build
```

Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add "frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx"
git commit -m "frontend: delete auth UI — lock toggle, deletable_by dropdown, creator display, canDelete logic"
```

---

## Task 9: Verification

- [ ] **Step 1: Run backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Build frontend**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app && npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Final commit if needed**

```bash
git add -A && git status
```

If clean, no commit needed. If uncommitted changes, commit with appropriate message.
