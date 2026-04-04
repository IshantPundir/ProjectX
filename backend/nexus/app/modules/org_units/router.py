import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.middleware.auth import require_roles
from app.modules.org_units.schemas import (
    CreateOrgUnitRequest,
    OrgUnitResponse,
    UpdateOrgUnitRequest,
)
from app.modules.org_units.service import (
    create_org_unit,
    list_org_units,
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
    tenant_id = uuid_mod.UUID(request.state.token_payload.tenant_id)
    units = await list_org_units(db, tenant_id)
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
