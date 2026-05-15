"""Ancestry walks for column-level profile fields."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


async def _seed_three_units(db):
    """grandparent -> parent -> child. Returns (tenant, grandparent_id,
    parent_id, child_id)."""
    tenant_id = uuid.uuid4()
    gp = uuid.uuid4()
    p = uuid.uuid4()
    c = uuid.uuid4()
    await db.execute(text("INSERT INTO clients (id, name) VALUES (:t, 'X')"),
                     {"t": tenant_id})
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, name, unit_type, "
        "is_root, company_profile_completion_status) VALUES "
        "(:gp, :t, 'GP', 'company', true, 'pending')"),
        {"gp": gp, "t": tenant_id})
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, parent_unit_id, "
        "name, unit_type, is_root, company_profile_completion_status) "
        "VALUES (:p, :t, :gp, 'P', 'client_account', false, 'pending')"),
        {"p": p, "t": tenant_id, "gp": gp})
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, parent_unit_id, "
        "name, unit_type, is_root, company_profile_completion_status) "
        "VALUES (:c, :t, :p, 'C', 'division', false, 'pending')"),
        {"c": c, "t": tenant_id, "p": p})
    await db.flush()
    return tenant_id, gp, p, c


@pytest.mark.asyncio
async def test_find_company_profile_walk_stops_at_owning_unit(db):
    """Walk stops at the first client_account / company. If THAT unit's
    profile is incomplete, return None — do NOT fall through to a higher
    ancestor. Otherwise jobs under a pending client_account would silently
    use the parent agency's profile, which doesn't match the client's
    hiring identity.

    Setup: company root (complete profile) → client_account (pending
    profile) → division. Walking from the division: closest owner is the
    client_account; its profile is incomplete; result is None.
    """
    from app.modules.org_units.service import find_company_profile_in_ancestry

    _, gp, p, c = await _seed_three_units(db)
    # Grandparent (company) has full triple. Parent (client_account) has
    # only about+industry (no hiring_bar). Child (division) has nothing.
    await db.execute(text(
        "UPDATE organizational_units SET about='gp_about', "
        "industry='gp_industry', hiring_bar='gp_bar' WHERE id = :u"),
        {"u": gp})
    await db.execute(text(
        "UPDATE organizational_units SET about='p_about', "
        "industry='p_industry' WHERE id = :u"),
        {"u": p})
    await db.flush()

    result = await find_company_profile_in_ancestry(db, c)
    assert result is None


@pytest.mark.asyncio
async def test_find_company_profile_owner_with_complete_profile(db):
    """When the closest client_account / company has its own complete
    profile, return it. Pass-through containers (division/region/team)
    above the owner are not consulted further."""
    from app.modules.org_units.service import find_company_profile_in_ancestry

    _, gp, p, c = await _seed_three_units(db)
    # Parent (client_account) has its OWN complete profile. Walk from
    # division stops at parent — returns parent's triple.
    await db.execute(text(
        "UPDATE organizational_units SET about='client_about', "
        "industry='client_industry', hiring_bar='client_bar' WHERE id = :u"),
        {"u": p})
    await db.flush()

    result = await find_company_profile_in_ancestry(db, c)
    assert result is not None
    assert result["about"] == "client_about"
    assert result["industry"] == "client_industry"
    assert result["hiring_bar"] == "client_bar"


@pytest.mark.asyncio
async def test_find_company_profile_company_root_when_no_client_account(db):
    """When no client_account sits between the job and the company root,
    the company's profile is the owner. Mirrors in-house hiring (no
    staffing-agency clients above the role)."""
    from app.modules.org_units.service import find_company_profile_in_ancestry

    tenant_id = uuid.uuid4()
    company_id = uuid.uuid4()
    div_id = uuid.uuid4()
    await db.execute(text("INSERT INTO clients (id, name) VALUES (:t, 'X')"),
                     {"t": tenant_id})
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, name, unit_type, "
        "is_root, company_profile_completion_status, about, industry, hiring_bar) "
        "VALUES (:c, :t, 'Co', 'company', true, 'complete', 'a', 'i', 'h')"),
        {"c": company_id, "t": tenant_id})
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, parent_unit_id, "
        "name, unit_type, is_root, company_profile_completion_status) VALUES "
        "(:d, :t, :c, 'D', 'division', false, 'pending')"),
        {"d": div_id, "t": tenant_id, "c": company_id})
    await db.flush()

    result = await find_company_profile_in_ancestry(db, div_id)
    assert result is not None
    assert result["about"] == "a"


@pytest.mark.asyncio
async def test_find_company_profile_in_ancestry_returns_none_when_no_complete(db):
    from app.modules.org_units.service import find_company_profile_in_ancestry

    _, gp, p, c = await _seed_three_units(db)
    # No unit has all three. Should return None.
    await db.execute(text("UPDATE organizational_units SET about='x' WHERE id = :u"),
                     {"u": gp})
    await db.flush()

    result = await find_company_profile_in_ancestry(db, c)
    assert result is None


@pytest.mark.asyncio
async def test_find_address_in_ancestry_per_field_walk(db):
    """country/state/city walked per-field: closest ancestor wins each.
    Source_unit_id points at the closest contributing ancestor."""
    from app.modules.org_units.service import find_address_in_ancestry

    _, gp, p, c = await _seed_three_units(db)
    # Grandparent has country only. Parent has state only. Child has nothing.
    await db.execute(text(
        "UPDATE organizational_units SET country='US' WHERE id = :u"),
        {"u": gp})
    await db.execute(text(
        "UPDATE organizational_units SET state='NY' WHERE id = :u"),
        {"u": p})
    await db.flush()

    result = await find_address_in_ancestry(db, c)
    assert result is not None
    assert result["values"]["country"] == "US"
    assert result["values"]["state"] == "NY"
    assert result["values"]["city"] is None
    # Closest contributor walked from `c`: parent (`p`) contributes state.
    assert result["source_unit_id"] == str(p)


@pytest.mark.asyncio
async def test_find_address_in_ancestry_returns_none_when_chain_empty(db):
    from app.modules.org_units.service import find_address_in_ancestry

    _, _, _, c = await _seed_three_units(db)
    # No country/state/city set anywhere in the chain.

    result = await find_address_in_ancestry(db, c)
    assert result is None
