"""Tests for the locale + compliance ancestry walks.

Mirror the shape of `test_find_company_profile_in_ancestry.py`. The walks
are used by the org-unit redesign to surface inherited values on the GET
response so the frontend can render override toggles without re-walking
the tree client-side.
"""

import pytest

from app.modules.org_units.service import (
    find_compliance_flags_in_ancestry,
    find_locale_defaults_in_ancestry,
)
from tests.conftest import create_test_client, create_test_org_unit

_LOCALE = {
    "default_timezone": "America/Los_Angeles",
    "default_currency": "USD",
    "default_locale": "en-US",
}

_COMPLIANCE = {
    "compliance_aivia_il": True,
    "compliance_gdpr_eu": False,
    "compliance_ccpa_ca": True,
}


@pytest.mark.asyncio
async def test_locale_returns_own_values_for_source_unit(db):
    tenant = await create_test_client(db)
    await db.flush()
    unit = await create_test_org_unit(
        db, tenant.id, unit_type="company", unit_metadata=_LOCALE,
    )
    await db.flush()

    result = await find_locale_defaults_in_ancestry(db, unit.id)

    assert result is not None
    assert result["values"] == _LOCALE
    assert result["source_unit_id"] == str(unit.id)


@pytest.mark.asyncio
async def test_locale_inherits_from_company_through_division_to_team(db):
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", unit_metadata=_LOCALE, name="Acme",
    )
    division = await create_test_org_unit(
        db, tenant.id, unit_type="division", parent_unit_id=company.id, name="Eng",
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=division.id, name="Platform",
    )
    await db.flush()

    result = await find_locale_defaults_in_ancestry(db, team.id)

    assert result is not None
    assert result["values"] == _LOCALE
    assert result["source_unit_id"] == str(company.id)


@pytest.mark.asyncio
async def test_locale_partial_override_uses_closest_ancestor_per_key(db):
    """Region overrides timezone but inherits currency + locale.

    The returned `source_unit_id` is the CLOSEST ancestor that contributed
    at least one key (the region itself, since it provides timezone).
    """
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", unit_metadata=_LOCALE, name="Acme",
    )
    region = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="region",
        parent_unit_id=company.id,
        unit_metadata={"default_timezone": "Europe/London"},
        name="EMEA",
    )
    await db.flush()

    result = await find_locale_defaults_in_ancestry(db, region.id)

    assert result is not None
    assert result["values"]["default_timezone"] == "Europe/London"
    assert result["values"]["default_currency"] == "USD"
    assert result["values"]["default_locale"] == "en-US"
    assert result["source_unit_id"] == str(region.id)


@pytest.mark.asyncio
async def test_locale_returns_none_when_unset_anywhere(db):
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", name="Acme",
    )
    division = await create_test_org_unit(
        db, tenant.id, unit_type="division", parent_unit_id=company.id,
    )
    await db.flush()

    result = await find_locale_defaults_in_ancestry(db, division.id)
    assert result is None


@pytest.mark.asyncio
async def test_compliance_treats_false_as_set_value(db):
    """`False` is a meaningful set value, not "missing"."""
    tenant = await create_test_client(db)
    await db.flush()
    company = await create_test_org_unit(
        db,
        tenant.id,
        unit_type="company",
        unit_metadata={"compliance_gdpr_eu": False, "compliance_ccpa_ca": True},
        name="Acme",
    )
    region = await create_test_org_unit(
        db, tenant.id, unit_type="region", parent_unit_id=company.id,
    )
    await db.flush()

    result = await find_compliance_flags_in_ancestry(db, region.id)

    assert result is not None
    assert result["values"]["compliance_gdpr_eu"] is False
    assert result["values"]["compliance_ccpa_ca"] is True
    assert result["values"]["compliance_aivia_il"] is None
    assert result["source_unit_id"] == str(company.id)


@pytest.mark.asyncio
async def test_compliance_returns_none_when_no_flag_anywhere(db):
    tenant = await create_test_client(db)
    await db.flush()
    unit = await create_test_org_unit(db, tenant.id, unit_type="region")
    await db.flush()

    result = await find_compliance_flags_in_ancestry(db, unit.id)
    assert result is None
