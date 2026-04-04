import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import OrganizationalUnit
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


@router.post("", response_model=OrgUnitResponse, dependencies=[require_super_admin()])
async def create_unit(
    data: CreateOrgUnitRequest,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    """Create an org unit. Super admin only."""
    parent_id = uuid_mod.UUID(data.parent_unit_id) if data.parent_unit_id else None
    try:
        unit = await create_org_unit(db, ctx.user.tenant_id, data.name, data.unit_type, parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=0,
        created_at=unit.created_at.isoformat(),
    )


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
    """Update an org unit. Super admin or Admin in unit."""
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == uid))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Org unit not found")

    try:
        unit = await update_org_unit(db, unit, data.name, data.unit_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=0,
        created_at=unit.created_at.isoformat(),
    )


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
