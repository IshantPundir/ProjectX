"""Tests for find_company_profile_in_ancestry() — walks up parent_unit_id
looking for the first ancestor with a completed company_profile."""

import pytest

from app.modules.org_units.service import find_company_profile_in_ancestry
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


_VALID_PROFILE = {
    "about": "We build real-time risk scoring infrastructure for mid-market lenders.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


@pytest.mark.asyncio
async def test_returns_profile_from_direct_unit(db):
    tenant = await create_test_client(db)
    await db.flush()
    unit = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    await db.flush()

    result = await find_company_profile_in_ancestry(db, unit.id)
    assert result == _VALID_PROFILE


@pytest.mark.asyncio
async def test_returns_profile_from_ancestor(db):
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE, name="Acme",
    )
    division = await create_test_org_unit(
        db, tenant.id, unit_type="division", parent_unit_id=company.id, name="Eng",
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=division.id, name="Platform",
    )
    await db.flush()

    result = await find_company_profile_in_ancestry(db, team.id)
    assert result == _VALID_PROFILE


@pytest.mark.asyncio
async def test_returns_none_when_no_ancestor_has_profile(db):
    tenant = await create_test_client(db)
    await db.flush()
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    await db.flush()

    result = await find_company_profile_in_ancestry(db, division.id)
    assert result is None
