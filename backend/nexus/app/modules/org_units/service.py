"""Org unit CRUD — scoped by caller's org_unit_id.

Super Admin (org_unit_id=NULL): sees all units in the tenant.
Admin (org_unit_id=<uuid>): sees their own unit + descendants only.
"""

import uuid as uuid_mod

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit

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
