"""Tests for find_company_profile_in_ancestry() — walks up parent_unit_id
looking for the first ancestor where all three typed columns are non-empty."""

import pytest

from app.modules.org_units.service import find_company_profile_in_ancestry
from tests.conftest import create_test_client, create_test_org_unit


@pytest.mark.asyncio
async def test_returns_profile_from_direct_unit(db):
    tenant = await create_test_client(db)
    await db.flush()
    unit = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        about="We build real-time risk scoring infrastructure for mid-market lenders.",
        industry="Fintech / Financial Services",
        hiring_bar="Engineers who own problems end-to-end with high autonomy.",
    )
    await db.flush()

    result = await find_company_profile_in_ancestry(db, unit.id)
    assert result is not None
    assert result["about"] == "We build real-time risk scoring infrastructure for mid-market lenders."
    assert result["industry"] == "Fintech / Financial Services"
    assert result["hiring_bar"] == "Engineers who own problems end-to-end with high autonomy."


@pytest.mark.asyncio
async def test_returns_profile_from_ancestor(db):
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        about="Acme about",
        industry="SaaS / Enterprise Software",
        hiring_bar="High bar engineers.",
        name="Acme",
    )
    division = await create_test_org_unit(
        db, tenant.id, unit_type="division", parent_unit_id=company.id, name="Eng",
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=division.id, name="Platform",
    )
    await db.flush()

    result = await find_company_profile_in_ancestry(db, team.id)
    assert result is not None
    assert result["about"] == "Acme about"
    assert result["industry"] == "SaaS / Enterprise Software"
    assert result["hiring_bar"] == "High bar engineers."


@pytest.mark.asyncio
async def test_returns_none_when_no_ancestor_has_profile(db):
    tenant = await create_test_client(db)
    await db.flush()
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    await db.flush()

    result = await find_company_profile_in_ancestry(db, division.id)
    assert result is None
