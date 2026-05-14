"""Independent-field save + completion-gate auto-flip tests.

The bug being fixed: today's update_org_unit refuses to persist any of the
strict company_profile fields unless all four validate. A recruiter edits
`about`, leaves industry blank, hits Save -> the about text is silently
dropped. The new column-level model persists each field independently and
re-derives completion_status on every save.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


async def _seed_unit_with_pending_profile(db, tenant_id, root_id, unit_id):
    """Seed a client_account with all profile fields NULL."""
    await db.execute(
        text("INSERT INTO clients (id, name) VALUES (:t, 'X')"),
        {"t": tenant_id},
    )
    await db.execute(
        text("INSERT INTO organizational_units (id, client_id, name, "
             "unit_type, is_root, company_profile_completion_status) "
             "VALUES (:r, :t, 'Root', 'company', true, 'complete')"),
        {"r": root_id, "t": tenant_id},
    )
    await db.execute(
        text("INSERT INTO organizational_units (id, client_id, parent_unit_id, "
             "name, unit_type, is_root, "
             "company_profile_completion_status) VALUES "
             "(:u, :t, :r, 'Acme', 'client_account', false, 'pending')"),
        {"u": unit_id, "t": tenant_id, "r": root_id},
    )
    await db.flush()


@pytest.mark.asyncio
async def test_update_org_unit_persists_about_when_industry_is_blank(db):
    """REGRESSION: edit `about` alone, leave industry NULL — `about` must
    persist; status stays pending."""
    from app.modules.org_units.models import OrganizationalUnit
    from app.modules.org_units.service import update_org_unit

    tenant_id = uuid.uuid4()
    root_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    await _seed_unit_with_pending_profile(db, tenant_id, root_id, unit_id)
    unit = await db.get(OrganizationalUnit, unit_id)
    assert unit is not None

    await update_org_unit(
        db, unit,
        name=None, unit_type=None,
        about="Some text the recruiter just typed", set_about=True,
        actor_id=None, actor_email=None,
    )
    await db.flush()

    row = await db.execute(
        text("SELECT about, industry, hiring_bar, "
             "company_profile_completion_status FROM organizational_units "
             "WHERE id = :u"),
        {"u": unit_id},
    )
    r = row.one()
    assert r.about == "Some text the recruiter just typed"
    assert r.industry is None
    assert r.hiring_bar is None
    assert r.company_profile_completion_status == "pending"


@pytest.mark.asyncio
async def test_update_completion_flips_pending_to_complete_when_all_three_filled(
    db,
):
    """All 3 strict fields non-empty -> status flips pending -> complete."""
    from app.modules.org_units.models import OrganizationalUnit
    from app.modules.org_units.service import update_org_unit

    tenant_id = uuid.uuid4()
    root_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    await _seed_unit_with_pending_profile(db, tenant_id, root_id, unit_id)
    unit = await db.get(OrganizationalUnit, unit_id)
    assert unit is not None

    await update_org_unit(
        db, unit,
        name=None, unit_type=None,
        about="Operational description here.", set_about=True,
        industry="Fintech / Financial Services", set_industry=True,
        hiring_bar="Strong engineers, polite humans.", set_hiring_bar=True,
        actor_id=None, actor_email=None,
    )
    await db.flush()

    row = await db.execute(
        text("SELECT company_profile_completion_status FROM "
             "organizational_units WHERE id = :u"),
        {"u": unit_id},
    )
    assert row.scalar_one() == "complete"


@pytest.mark.asyncio
async def test_update_completion_flips_complete_to_pending_when_cleared(db):
    """Clearing `about` flips status complete -> pending."""
    from app.modules.org_units.models import OrganizationalUnit
    from app.modules.org_units.service import update_org_unit

    tenant_id = uuid.uuid4()
    root_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    await _seed_unit_with_pending_profile(db, tenant_id, root_id, unit_id)
    # Pre-fill all 3 fields so the unit is currently complete.
    await db.execute(
        text("UPDATE organizational_units SET about = 'x', "
             "industry = 'y', hiring_bar = 'z', "
             "company_profile_completion_status = 'complete' WHERE id = :u"),
        {"u": unit_id},
    )
    await db.flush()
    unit = await db.get(OrganizationalUnit, unit_id)
    assert unit is not None

    await update_org_unit(
        db, unit,
        name=None, unit_type=None,
        about="", set_about=True,  # empty string = clear
        actor_id=None, actor_email=None,
    )
    await db.flush()

    row = await db.execute(
        text("SELECT about, company_profile_completion_status FROM "
             "organizational_units WHERE id = :u"),
        {"u": unit_id},
    )
    r = row.one()
    assert r.about is None  # empty string normalized to NULL after .strip()
    assert r.company_profile_completion_status == "pending"


@pytest.mark.asyncio
async def test_get_org_unit_response_shape_after_refactor(db):
    """GET /api/org-units/{id} response (via the service function that
    builds the dict) exposes column-level fields and inherited_address.
    Drops company_profile, inherited_locale, inherited_compliance keys.

    This is a service-level test rather than HTTP-level: it asserts the
    contract between get_org_unit and the router's _build_response. The
    HTTP layer is covered indirectly by the existing router test suite
    once those re-enable in subsequent work.
    """
    from app.modules.org_units.service import get_org_unit

    tenant_id = uuid.uuid4()
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()

    await db.execute(text("INSERT INTO clients (id, name) VALUES (:t, 'X')"),
                     {"t": tenant_id})
    # Parent with all three address columns set
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, name, unit_type, "
        "is_root, country, state, city, "
        "company_profile_completion_status) VALUES "
        "(:r, :t, 'Root', 'company', true, 'US', 'NY', 'NYC', 'complete')"),
        {"r": root_id, "t": tenant_id})
    # Child inherits address from parent
    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, parent_unit_id, "
        "name, unit_type, is_root, "
        "company_profile_completion_status) VALUES "
        "(:c, :t, :r, 'Child', 'division', false, 'pending')"),
        {"c": child_id, "t": tenant_id, "r": root_id})
    await db.flush()

    result = await get_org_unit(
        db, child_id,
        client_id=tenant_id,
        user_id=uuid.uuid4(),  # irrelevant when is_super_admin=True
        is_super_admin=True,
    )
    assert result is not None

    # 7 column-level fields are present (even if None on the child itself).
    assert "about" in result
    assert "industry" in result
    assert "hiring_bar" in result
    assert "website" in result
    assert "country" in result
    assert "state" in result
    assert "city" in result

    # inherited_address surfaces the parent's values via the ancestry walk.
    assert "inherited_address" in result
    assert result["inherited_address"] is not None
    assert result["inherited_address"]["values"]["country"] == "US"
    assert result["inherited_address"]["values"]["state"] == "NY"
    assert result["inherited_address"]["values"]["city"] == "NYC"
    assert result["inherited_address"]["source_unit_id"] == str(root_id)

    # Legacy keys are GONE.
    assert "company_profile" not in result
    assert "inherited_locale" not in result
    assert "inherited_compliance" not in result
