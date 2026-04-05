"""Tests for user deactivation — deletable_by nullification and auth deletion decoupling."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_nullify_deletable_by_for_user_clears_references(db: AsyncSession):
    """Deactivating a user should nullify their deletable_by on all org units in the same tenant."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    unit1 = await create_test_org_unit(db, client.id, deletable_by=user.id)
    unit2 = await create_test_org_unit(db, client.id, deletable_by=user.id)
    unit3 = await create_test_org_unit(db, client.id, deletable_by=None)

    count = await nullify_deletable_by_for_user(db, client.id, user.id)

    assert count == 2

    await db.flush()
    for uid in [unit1.id, unit2.id, unit3.id]:
        result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == uid))
        unit = result.scalar_one()
        assert unit.deletable_by is None


@pytest.mark.asyncio
async def test_nullify_deletable_by_does_not_affect_other_tenants(db: AsyncSession):
    """Nullification must be tenant-scoped — other tenants' units are untouched."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    client_a = await create_test_client(db, name="Tenant A")
    client_b = await create_test_client(db, name="Tenant B")

    user_a = await create_test_user(db, client_a.id)

    unit_a = await create_test_org_unit(db, client_a.id, deletable_by=user_a.id)
    unit_b = await create_test_org_unit(db, client_b.id, deletable_by=user_a.id)

    count = await nullify_deletable_by_for_user(db, client_a.id, user_a.id)

    assert count == 1

    result_a = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.id == unit_a.id)
    )
    assert result_a.scalar_one().deletable_by is None

    result_b = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.id == unit_b.id)
    )
    assert result_b.scalar_one().deletable_by == user_a.id


@pytest.mark.asyncio
async def test_nullify_deletable_by_returns_zero_when_no_matches(db: AsyncSession):
    """Returns 0 when user has no deletable_by references."""
    from app.modules.org_units.service import nullify_deletable_by_for_user

    client = await create_test_client(db)
    user = await create_test_user(db, client.id)
    await create_test_org_unit(db, client.id, deletable_by=None)

    count = await nullify_deletable_by_for_user(db, client.id, user.id)
    assert count == 0
