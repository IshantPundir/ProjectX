"""Verify migration 0034 backfills column-level fields from JSONB and
strips obsolete metadata keys. Runs the migration's `upgrade()` function
manually against a connection that has been pre-seeded with legacy-shape
rows."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB


@pytest.mark.asyncio
async def test_migration_0034_backfills_about_hiring_bar_industry(db):
    """All three strict-profile fields move from JSONB to columns. Industry
    enum strings translate to human-readable labels."""
    # Pre-seed: tenant + root + one client_account with full legacy profile.
    tenant_id = uuid.uuid4()
    root_id = uuid.uuid4()
    unit_id = uuid.uuid4()

    await db.execute(
        text("INSERT INTO clients (id, name) VALUES (:t, 'Acme Inc')"),
        {"t": tenant_id},
    )
    await db.execute(
        text("INSERT INTO organizational_units (id, client_id, name, "
             "unit_type, is_root, company_profile, "
             "company_profile_completion_status) VALUES "
             "(:r, :t, 'Acme', 'company', true, "
             "'{\"about\":\"x\",\"industry\":\"saas_enterprise_software\","
             "\"company_stage\":\"series_a_b\","
             "\"hiring_bar\":\"high bar\"}', 'complete')"),
        {"r": root_id, "t": tenant_id},
    )
    # Pass JSON as bound params typed as JSONB to avoid two pitfalls:
    # (a) SQLAlchemy text() treating `:false`/`:true` inside JSON string
    #     literals as named bind parameters, and
    # (b) asyncpg rejecting `:name::jsonb` syntax (colon after param ref).
    profile_val = {
        "about": "oracle about",
        "industry": "fintech_financial_services",
        "company_stage": "large_enterprise",
        "hiring_bar": "oracle bar",
    }
    meta_val = {
        "website": "oracle.com",
        "default_timezone": "America/New_York",
        "default_currency": "USD",
        "default_locale": "en-US",
        "compliance_aivia_il": False,
        "compliance_gdpr_eu": True,
        "compliance_ccpa_ca": False,
        "short_name": "ORC",
        "focus": "banking-engineering",
    }
    await db.execute(
        text(
            "INSERT INTO organizational_units (id, client_id, parent_unit_id, "
            "name, unit_type, is_root, company_profile, "
            "company_profile_completion_status, metadata) VALUES "
            "(:u, :t, :r, 'Oracle', 'client_account', false, "
            ":profile, 'complete', :meta)"
        ).bindparams(
            bindparam("profile", type_=JSONB),
            bindparam("meta", type_=JSONB),
        ),
        {
            "u": unit_id,
            "t": tenant_id,
            "r": root_id,
            "profile": profile_val,
            "meta": meta_val,
        },
    )
    await db.flush()

    # Run the migration's upgrade SQL list directly against the test DB.
    # Production deploys still go through `alembic upgrade`.
    from migrations.versions import _0034_company_profile_columns as migration
    for stmt in migration._UPGRADE_SQL:
        await db.execute(text(stmt))
    await db.flush()

    row = await db.execute(
        text("SELECT about, industry, hiring_bar, website, country, state, "
             "city, metadata FROM organizational_units WHERE id = :u"),
        {"u": unit_id},
    )
    r = row.one()
    assert r.about == "oracle about"
    assert r.industry == "Fintech / Financial Services"
    assert r.hiring_bar == "oracle bar"
    assert r.website == "oracle.com"
    assert r.country is None
    assert r.state is None
    assert r.city is None
    # Stripped metadata keys are gone; unit-type-specific keys survive.
    assert r.metadata == {"focus": "banking-engineering"}

    # company_profile column was dropped from the table.
    columns = await db.execute(
        text("SELECT column_name FROM information_schema.columns "
             "WHERE table_name = 'organizational_units'")
    )
    column_names = {c.column_name for c in columns.all()}
    assert "company_profile" not in column_names
    assert {"about", "industry", "hiring_bar", "website",
            "country", "state", "city"}.issubset(column_names)


@pytest.mark.asyncio
async def test_migration_0034_handles_null_company_profile(db):
    """Units with company_profile=NULL pass through cleanly."""
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO clients (id, name) VALUES (:t, 'X')"),
        {"t": tenant_id},
    )
    await db.execute(
        text("INSERT INTO organizational_units (id, client_id, name, "
             "unit_type, is_root, company_profile, "
             "company_profile_completion_status) VALUES "
             "(:u, :t, 'Y', 'company', true, NULL, 'pending')"),
        {"u": unit_id, "t": tenant_id},
    )
    await db.flush()

    from migrations.versions import _0034_company_profile_columns as migration
    for stmt in migration._UPGRADE_SQL:
        await db.execute(text(stmt))
    await db.flush()

    row = await db.execute(
        text("SELECT about, industry, hiring_bar FROM organizational_units "
             "WHERE id = :u"),
        {"u": unit_id},
    )
    r = row.one()
    assert r.about is None
    assert r.industry is None
    assert r.hiring_bar is None
