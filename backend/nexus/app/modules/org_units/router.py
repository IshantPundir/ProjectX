import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.middleware.auth import require_roles
from app.models import User
from app.modules.org_units.schemas import (
    AssignUserRequest,
    CreateOrgUnitRequest,
    OrgUnitMember,
    OrgUnitResponse,
    UpdateOrgUnitRequest,
)
from app.modules.org_units.service import (
    assign_user_to_org_unit,
    create_org_unit,
    list_org_unit_members,
    list_org_units,
    unassign_user_from_org_unit,
    update_org_unit,
)

router = APIRouter(prefix="/api/org-units", tags=["org-units"])


@router.post(
    "",
    response_model=OrgUnitResponse,
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def create_unit(
    data: CreateOrgUnitRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    parent_id = uuid_mod.UUID(data.parent_unit_id) if data.parent_unit_id else None
    try:
        unit = await create_org_unit(
            db=db,
            client_id=tenant_id,
            name=data.name,
            unit_type=data.unit_type,
            parent_unit_id=parent_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        created_at=unit.created_at.isoformat(),
    )


@router.get(
    "",
    response_model=list[OrgUnitResponse],
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def list_units(
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrgUnitResponse]:
    token_payload = request.state.token_payload
    tenant_id = uuid_mod.UUID(token_payload.tenant_id)
    caller_org = uuid_mod.UUID(token_payload.org_unit_id) if token_payload.org_unit_id else None
    units = await list_org_units(db, tenant_id, caller_org_unit_id=caller_org)
    return [
        OrgUnitResponse(
            id=str(u.id),
            client_id=str(u.client_id),
            parent_unit_id=str(u.parent_unit_id) if u.parent_unit_id else None,
            name=u.name,
            unit_type=u.unit_type,
            created_at=u.created_at.isoformat(),
        )
        for u in units
    ]


@router.put(
    "/{unit_id}",
    response_model=OrgUnitResponse,
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def update_unit(
    unit_id: str,
    data: UpdateOrgUnitRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> OrgUnitResponse:
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    try:
        unit = await update_org_unit(
            db=db,
            unit_id=uuid_mod.UUID(unit_id),
            client_id=tenant_id,
            name=data.name,
            unit_type=data.unit_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        created_at=unit.created_at.isoformat(),
    )


@router.get(
    "/{unit_id}/members",
    response_model=list[OrgUnitMember],
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def list_unit_members(
    unit_id: str,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrgUnitMember]:
    """List all users in an org unit (primary + additional assignments)."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    try:
        members = await list_org_unit_members(db, uuid_mod.UUID(unit_id), tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [OrgUnitMember(**m) for m in members]


@router.post(
    "/{unit_id}/members",
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def assign_member(
    unit_id: str,
    data: AssignUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Assign an existing user to an org unit."""
    token_payload = request.state.token_payload
    tenant_id = uuid_mod.UUID(token_payload.tenant_id)

    result = await db.execute(
        select(User).where(User.auth_user_id == token_payload.sub)
    )
    caller = result.scalar_one_or_none()
    if not caller:
        raise HTTPException(status_code=404, detail="Caller user not found")

    try:
        await assign_user_to_org_unit(
            db=db,
            org_unit_id=uuid_mod.UUID(unit_id),
            user_id=uuid_mod.UUID(data.user_id),
            assigned_by=caller.id,
            client_id=tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "assigned"}


@router.delete(
    "/{unit_id}/members/{user_id}",
    dependencies=[require_roles("Company Admin", "Admin")],
)
async def unassign_member(
    unit_id: str,
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Remove a user's additional assignment from an org unit."""
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    try:
        await unassign_user_from_org_unit(
            db=db,
            org_unit_id=uuid_mod.UUID(unit_id),
            user_id=uuid_mod.UUID(user_id),
            client_id=tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "unassigned"}
