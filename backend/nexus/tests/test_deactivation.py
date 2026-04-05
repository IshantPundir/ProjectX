"""Tests for user deactivation — deletable_by nullification and auth deletion decoupling."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit, Role, User, UserRoleAssignment
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


@pytest.mark.asyncio
async def test_deactivate_team_user_sets_inactive_and_returns_auth_id(db: AsyncSession):
    """deactivate_team_user sets is_active=False and returns auth_user_id."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    caller = await create_test_user(db, client.id, email="admin@test.com")
    target = await create_test_user(db, client.id, email="target@test.com")

    auth_user_id = await deactivate_team_user(
        db, client.id, target.id, str(caller.auth_user_id),
    )

    assert auth_user_id == str(target.auth_user_id)

    result = await db.execute(select(User).where(User.id == target.id))
    user = result.scalar_one()
    assert user.is_active is False


@pytest.mark.asyncio
async def test_deactivate_team_user_nullifies_deletable_by(db: AsyncSession):
    """deactivate_team_user also nullifies deletable_by references."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    caller = await create_test_user(db, client.id, email="admin@test.com")
    target = await create_test_user(db, client.id, email="target@test.com")

    unit = await create_test_org_unit(db, client.id, deletable_by=target.id)

    await deactivate_team_user(db, client.id, target.id, str(caller.auth_user_id))

    result = await db.execute(select(OrganizationalUnit).where(OrganizationalUnit.id == unit.id))
    assert result.scalar_one().deletable_by is None


@pytest.mark.asyncio
async def test_deactivate_self_raises(db: AsyncSession):
    """Cannot deactivate your own account."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    with pytest.raises(ValueError, match="Cannot deactivate your own account"):
        await deactivate_team_user(db, client.id, user.id, str(user.auth_user_id))


@pytest.mark.asyncio
async def test_deactivate_team_user_removes_role_assignments(db: AsyncSession):
    """Deactivating a user removes all their role assignments across all org units."""
    from app.modules.settings.service import deactivate_team_user

    client = await create_test_client(db)
    caller = await create_test_user(db, client.id, email="admin@test.com")
    target = await create_test_user(db, client.id, email="target@test.com")

    unit1 = await create_test_org_unit(db, client.id, name="Engineering")
    unit2 = await create_test_org_unit(db, client.id, name="Marketing")

    # Create a role to assign
    role = Role(name="TestRole", is_system=False, tenant_id=client.id)
    db.add(role)
    await db.flush()

    # Assign target to both units
    for unit in [unit1, unit2]:
        assignment = UserRoleAssignment(
            user_id=target.id,
            org_unit_id=unit.id,
            role_id=role.id,
            tenant_id=client.id,
            assigned_by=caller.id,
        )
        db.add(assignment)
    await db.flush()

    # Verify assignments exist
    result = await db.execute(
        select(UserRoleAssignment).where(UserRoleAssignment.user_id == target.id)
    )
    assert len(result.scalars().all()) == 2

    await deactivate_team_user(db, client.id, target.id, str(caller.auth_user_id))

    # All assignments should be gone
    result = await db.execute(
        select(UserRoleAssignment).where(UserRoleAssignment.user_id == target.id)
    )
    assert len(result.scalars().all()) == 0
