import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import Client, OrganizationalUnit, User
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
    if ctx.is_super_admin:
        return
    if ctx.has_role_in_unit(org_unit_id, "Admin"):
        return
    raise HTTPException(status_code=403, detail="Requires super admin or Admin role in this unit")


def _build_response(
    unit: OrganizationalUnit, member_count: int, email_map: dict
) -> OrgUnitResponse:
    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=member_count,
        is_root=unit.is_root,
        company_profile=unit.company_profile,
        created_at=unit.created_at.isoformat(),
        created_by=str(unit.created_by) if unit.created_by else None,
        created_by_email=email_map.get(unit.created_by) if unit.created_by else None,
        deletable_by=str(unit.deletable_by) if unit.deletable_by else None,
        deletable_by_email=email_map.get(unit.deletable_by) if unit.deletable_by else None,
        admin_delete_disabled=unit.admin_delete_disabled,
    )


async def _load_email_map(db: AsyncSession, *user_ids: uuid_mod.UUID | None) -> dict:
    ids = [uid for uid in user_ids if uid is not None]
    if not ids:
        return {}
    result = await db.execute(select(User.id, User.email).where(User.id.in_(ids)))
    return {row[0]: row[1] for row in result.all()}


@router.post("", response_model=OrgUnitResponse)
async def create_unit(
    data: CreateOrgUnitRequest,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    """Create an org unit.

    Top-level units (no parent): super admin only.
    Sub-units (has parent): super admin OR Admin in the parent unit.
    """
    parent_id = uuid_mod.UUID(data.parent_unit_id) if data.parent_unit_id else None

    if parent_id is not None:
        # Sub-unit: super admin or Admin in parent
        _require_unit_admin(ctx, parent_id)
    else:
        # Top-level: super admin only
        if not ctx.is_super_admin:
            raise HTTPException(
                status_code=403, detail="Only a super admin can create top-level units"
            )

    # Load workspace_mode for the tenant
    client_result = await db.execute(select(Client).where(Client.id == ctx.user.tenant_id))
    client = client_result.scalar_one()

    try:
        unit = await create_org_unit(
            db,
            ctx.user.tenant_id,
            data.name,
            data.unit_type,
            parent_id,
            created_by=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
            workspace_mode=client.workspace_mode,
            company_profile=data.company_profile,
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
    units = await list_org_units(db, ctx.user.tenant_id, ctx.user.id, ctx.is_super_admin)
    return [OrgUnitResponse(**u) for u in units]


@router.put("/{unit_id}", response_model=OrgUnitResponse)
async def update_unit(
    unit_id: str,
    data: UpdateOrgUnitRequest,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)

    raw_body = data.model_dump(exclude_unset=True)
    if (
        "deletable_by" in raw_body or "admin_delete_disabled" in raw_body
    ) and not ctx.is_super_admin:
        raise HTTPException(status_code=403, detail="Only a super admin can change delete settings")

    result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == uid))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Org unit not found")

    set_deletable_by = "deletable_by" in raw_body

    try:
        unit = await update_org_unit(
            db,
            unit,
            data.name,
            data.unit_type,
            deletable_by=data.deletable_by,
            set_deletable_by=set_deletable_by,
            admin_delete_disabled=data.admin_delete_disabled,
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
            company_profile=data.company_profile,
            set_company_profile=data.set_company_profile,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    email_map = await _load_email_map(db, unit.created_by, unit.deletable_by)
    return _build_response(unit, 0, email_map)


@router.delete("/{unit_id}")
async def delete_unit(
    unit_id: str,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    uid = uuid_mod.UUID(unit_id)
    try:
        await delete_org_unit(
            db,
            org_unit_id=uid,
            caller_user_id=ctx.user.id,
            is_super_admin=ctx.is_super_admin,
            caller_has_admin_role=ctx.has_role_in_unit(uid, "Admin"),
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
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
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)
    members = await list_unit_members(db, uid)
    return [OrgUnitMember(**m) for m in members]


@router.post("/{unit_id}/members")
async def assign_member_role(
    unit_id: str,
    data: AssignRoleRequest,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
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
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "assigned"}


@router.delete("/{unit_id}/members/{user_id}")
async def remove_member(
    unit_id: str,
    user_id: str,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)
    try:
        count = await remove_user_from_unit(
            db,
            uid,
            uuid_mod.UUID(user_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "removed", "count": str(count)}


@router.delete("/{unit_id}/members/{user_id}/roles/{role_id}")
async def remove_member_role(
    unit_id: str,
    user_id: str,
    role_id: str,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    uid = uuid_mod.UUID(unit_id)
    _require_unit_admin(ctx, uid)
    try:
        await remove_role_from_user(
            db,
            uid,
            uuid_mod.UUID(user_id),
            uuid_mod.UUID(role_id),
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "removed"}
