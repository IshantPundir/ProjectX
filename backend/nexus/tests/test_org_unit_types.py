"""Tests for unit type v2 — behavioural rules, nesting constraints, and migration verification."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit
from app.modules.org_units.service import create_org_unit, delete_org_unit, update_org_unit
from tests.conftest import create_test_client, create_test_user

PLACEHOLDER_PROFILE = {
    "about": "We build real-time risk scoring infrastructure for mid-market lenders.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


async def _create_root(db: AsyncSession, client_id: uuid.UUID, user_id: uuid.UUID | None = None):
    return await create_org_unit(
        db=db,
        client_id=client_id,
        name="Root",
        unit_type="company",
        parent_unit_id=None,
        created_by=user_id,
        workspace_mode="enterprise",
        company_profile=PLACEHOLDER_PROFILE,
    )


# ===== Company type rules (5) =============================================


@pytest.mark.asyncio
async def test_company_with_parent_raises(db: AsyncSession):
    """Creating a company unit with a parent must raise ValueError."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    with pytest.raises(ValueError, match="cannot have a parent"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Bad",
            unit_type="company",
            parent_unit_id=root.id,
            workspace_mode="enterprise",
            company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_second_company_in_same_tenant_raises(db: AsyncSession):
    """Only one company root per tenant."""
    client = await create_test_client(db)
    await _create_root(db, client.id)
    with pytest.raises(ValueError, match="already exists"):
        await _create_root(db, client.id)


@pytest.mark.asyncio
async def test_company_without_profile_raises(db: AsyncSession):
    """Company unit requires company_profile."""
    client = await create_test_client(db)
    with pytest.raises(ValueError, match="company_profile is required"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Root",
            unit_type="company",
            parent_unit_id=None,
            workspace_mode="enterprise",
            company_profile=None,
        )


@pytest.mark.asyncio
async def test_delete_root_unit_raises(db: AsyncSession):
    """Deleting a root unit (is_root=True) must raise ValueError."""
    client = await create_test_client(db)
    user = await create_test_user(db, client.id)
    root = await _create_root(db, client.id, user.id)
    with pytest.raises(ValueError, match="cannot be deleted"):
        await delete_org_unit(
            db=db,
            org_unit_id=root.id,
            caller_user_id=user.id,
            is_super_admin=True,
            caller_has_admin_role=True,
        )


@pytest.mark.asyncio
async def test_change_type_of_root_company_raises(db: AsyncSession):
    """Changing unit_type of a root company unit must raise ValueError."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    with pytest.raises(ValueError, match="cannot be changed"):
        await update_org_unit(db, root, name=None, unit_type="division")


# ===== Client account rules (7) ===========================================


@pytest.mark.asyncio
async def test_client_account_without_profile_raises(db: AsyncSession):
    """client_account requires company_profile."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    with pytest.raises(ValueError, match="company_profile is required"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Acme",
            unit_type="client_account",
            parent_unit_id=root.id,
            workspace_mode="agency",
            company_profile=None,
        )


@pytest.mark.asyncio
async def test_client_account_in_enterprise_raises(db: AsyncSession):
    """client_account only available in agency workspaces."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    with pytest.raises(ValueError, match="agency workspaces"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Acme",
            unit_type="client_account",
            parent_unit_id=root.id,
            workspace_mode="enterprise",
            company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_client_account_under_client_account_raises(db: AsyncSession):
    """client_account cannot nest under another client_account."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca1 = await create_org_unit(
        db=db,
        client_id=client.id,
        name="CA1",
        unit_type="client_account",
        parent_unit_id=root.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    with pytest.raises(ValueError, match="cannot be nested under another client account"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="CA2",
            unit_type="client_account",
            parent_unit_id=ca1.id,
            workspace_mode="agency",
            company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_client_account_under_team_raises(db: AsyncSession):
    """client_account cannot nest under a team."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Team",
        unit_type="team",
        parent_unit_id=root.id,
        workspace_mode="agency",
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Acme",
            unit_type="client_account",
            parent_unit_id=team.id,
            workspace_mode="agency",
            company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_client_account_under_company_success(db: AsyncSession):
    """client_account under company (agency) should succeed."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Acme",
        unit_type="client_account",
        parent_unit_id=root.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    assert ca.unit_type == "client_account"


@pytest.mark.asyncio
async def test_client_account_under_division_success(db: AsyncSession):
    """client_account under division (agency) should succeed."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    div = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Div",
        unit_type="division",
        parent_unit_id=root.id,
        workspace_mode="agency",
    )
    ca = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Acme",
        unit_type="client_account",
        parent_unit_id=div.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    assert ca.unit_type == "client_account"


@pytest.mark.asyncio
async def test_client_account_under_region_success(db: AsyncSession):
    """client_account under region (agency) should succeed."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    region = await create_org_unit(
        db=db,
        client_id=client.id,
        name="APAC",
        unit_type="region",
        parent_unit_id=root.id,
        workspace_mode="agency",
    )
    ca = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Acme",
        unit_type="client_account",
        parent_unit_id=region.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    assert ca.unit_type == "client_account"


# ===== Team leaf node rules (4) ===========================================


@pytest.mark.asyncio
async def test_division_under_team_raises(db: AsyncSession):
    """division under team must raise."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Team",
        unit_type="team",
        parent_unit_id=root.id,
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Div",
            unit_type="division",
            parent_unit_id=team.id,
        )


@pytest.mark.asyncio
async def test_region_under_team_raises(db: AsyncSession):
    """region under team must raise."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Team",
        unit_type="team",
        parent_unit_id=root.id,
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="R",
            unit_type="region",
            parent_unit_id=team.id,
        )


@pytest.mark.asyncio
async def test_team_under_team_raises(db: AsyncSession):
    """team under team must raise."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db,
        client_id=client.id,
        name="T1",
        unit_type="team",
        parent_unit_id=root.id,
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="T2",
            unit_type="team",
            parent_unit_id=team.id,
        )


@pytest.mark.asyncio
async def test_client_account_under_team_via_team_parent_path(db: AsyncSession):
    """client_account under team rejected via the team-parent check."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db,
        client_id=client.id,
        name="Team",
        unit_type="team",
        parent_unit_id=root.id,
        workspace_mode="agency",
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Acme",
            unit_type="client_account",
            parent_unit_id=team.id,
            workspace_mode="agency",
            company_profile=PLACEHOLDER_PROFILE,
        )


# ===== Valid nesting (12) ==================================================


@pytest.mark.asyncio
async def test_division_under_company(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    u = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D",
        unit_type="division",
        parent_unit_id=root.id,
    )
    assert u.unit_type == "division"


@pytest.mark.asyncio
async def test_division_under_client_account(db: AsyncSession):
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(
        db=db,
        client_id=client.id,
        name="CA",
        unit_type="client_account",
        parent_unit_id=root.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    u = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D",
        unit_type="division",
        parent_unit_id=ca.id,
    )
    assert u.unit_type == "division"


@pytest.mark.asyncio
async def test_division_under_division(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    d1 = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D1",
        unit_type="division",
        parent_unit_id=root.id,
    )
    d2 = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D2",
        unit_type="division",
        parent_unit_id=d1.id,
    )
    assert d2.unit_type == "division"


@pytest.mark.asyncio
async def test_division_under_region(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    r = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R",
        unit_type="region",
        parent_unit_id=root.id,
    )
    d = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D",
        unit_type="division",
        parent_unit_id=r.id,
    )
    assert d.unit_type == "division"


@pytest.mark.asyncio
async def test_region_under_company(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    u = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R",
        unit_type="region",
        parent_unit_id=root.id,
    )
    assert u.unit_type == "region"


@pytest.mark.asyncio
async def test_region_under_client_account(db: AsyncSession):
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(
        db=db,
        client_id=client.id,
        name="CA",
        unit_type="client_account",
        parent_unit_id=root.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    u = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R",
        unit_type="region",
        parent_unit_id=ca.id,
    )
    assert u.unit_type == "region"


@pytest.mark.asyncio
async def test_region_under_division(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    d = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D",
        unit_type="division",
        parent_unit_id=root.id,
    )
    r = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R",
        unit_type="region",
        parent_unit_id=d.id,
    )
    assert r.unit_type == "region"


@pytest.mark.asyncio
async def test_region_under_region(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    r1 = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R1",
        unit_type="region",
        parent_unit_id=root.id,
    )
    r2 = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R2",
        unit_type="region",
        parent_unit_id=r1.id,
    )
    assert r2.unit_type == "region"


@pytest.mark.asyncio
async def test_team_under_company(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    u = await create_org_unit(
        db=db,
        client_id=client.id,
        name="T",
        unit_type="team",
        parent_unit_id=root.id,
    )
    assert u.unit_type == "team"


@pytest.mark.asyncio
async def test_team_under_division(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    d = await create_org_unit(
        db=db,
        client_id=client.id,
        name="D",
        unit_type="division",
        parent_unit_id=root.id,
    )
    t = await create_org_unit(
        db=db,
        client_id=client.id,
        name="T",
        unit_type="team",
        parent_unit_id=d.id,
    )
    assert t.unit_type == "team"


@pytest.mark.asyncio
async def test_team_under_client_account(db: AsyncSession):
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(
        db=db,
        client_id=client.id,
        name="CA",
        unit_type="client_account",
        parent_unit_id=root.id,
        workspace_mode="agency",
        company_profile=PLACEHOLDER_PROFILE,
    )
    t = await create_org_unit(
        db=db,
        client_id=client.id,
        name="T",
        unit_type="team",
        parent_unit_id=ca.id,
        workspace_mode="agency",
    )
    assert t.unit_type == "team"


@pytest.mark.asyncio
async def test_team_under_region(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    r = await create_org_unit(
        db=db,
        client_id=client.id,
        name="R",
        unit_type="region",
        parent_unit_id=root.id,
    )
    t = await create_org_unit(
        db=db,
        client_id=client.id,
        name="T",
        unit_type="team",
        parent_unit_id=r.id,
    )
    assert t.unit_type == "team"


# ===== Migration verification (1) =========================================


@pytest.mark.asyncio
async def test_no_branch_or_department_rows_exist(db: AsyncSession):
    """After migration, no rows should have unit_type 'branch' or 'department'."""
    result = await db.execute(
        select(OrganizationalUnit).where(OrganizationalUnit.unit_type.in_(["branch", "department"]))
    )
    rows = result.scalars().all()
    assert len(rows) == 0, f"Found {len(rows)} rows with deprecated unit_type values"
