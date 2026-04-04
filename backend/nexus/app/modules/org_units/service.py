"""Org unit CRUD — all operations scoped to caller's tenant via get_tenant_db."""

import uuid as uuid_mod

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit

logger = structlog.get_logger()

ALLOWED_UNIT_TYPES = {"client_account", "department", "team", "branch", "region"}


async def create_org_unit(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
) -> OrganizationalUnit:
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
    logger.info("org_unit.created", unit_id=str(unit.id), name=name)
    return unit


async def list_org_units(db: AsyncSession, client_id: uuid_mod.UUID) -> list[OrganizationalUnit]:
    result = await db.execute(
        select(OrganizationalUnit)
        .where(OrganizationalUnit.client_id == client_id)
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
