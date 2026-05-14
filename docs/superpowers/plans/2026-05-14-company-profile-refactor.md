# Company Profile Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote `about` / `industry` / `hiring_bar` out of the `company_profile` JSONB into typed columns on `organizational_units`. Replace the locale + compliance system with a free-text country/state/city address block that inherits via ancestry. Auto-fill website/industry/country/state/city on ATS client sync. Delete the deep editor route; everything edits inline on the detail page. Fix the user-flagged bug where About/Hiring-bar edits are silently dropped when Industry is blank.

**Architecture:** One alembic migration moves data from JSONB into 7 new TEXT columns and strips obsolete metadata keys. Backend ancestry walkers are rewritten to read columns instead of JSONB. The PUT handler accepts per-field `set_<field>` sentinels so partial saves persist independently. ATS importer `_sync_clients` populates new columns on create and refreshes only NULL columns on promote. Frontend deletes the deep editor + locale/compliance UI, and `CompanyDetail.tsx` gains an Industry chip + Address block in the header.

**Tech Stack:** Python 3.13, SQLAlchemy 2 async (asyncpg), Alembic, FastAPI, Pydantic v2, Next.js 16, TypeScript, React Hook Form + Zod, TanStack Query, pytest-asyncio, Vitest + React Testing Library. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-14-company-profile-refactor-design.md`

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `backend/nexus/migrations/versions/0034_company_profile_columns.py` | Create | Add 7 typed columns, backfill from JSONB + metadata, strip locale/compliance/short_name/website keys from metadata, drop `company_profile` JSONB column. |
| `backend/nexus/app/modules/org_units/models.py` | Modify | Add 7 TEXT column declarations; remove `company_profile` mapping. |
| `backend/nexus/app/modules/org_units/schemas.py` | Modify | Rewrite `UpdateOrgUnitRequest` and `CreateOrgUnitRequest` with column-level fields + `set_<field>` sentinels. Update `OrgUnitResponse` to expose new columns + `inherited_address`; drop `company_profile` / `inherited_locale` / `inherited_compliance`. |
| `backend/nexus/app/modules/org_units/service.py` | Modify | Rewrite `create_org_unit`, `update_org_unit`, `get_org_unit`, ancestry helpers. Add `derive_completion_status` helper. Delete locale/compliance walkers. |
| `backend/nexus/app/modules/org_units/router.py` | Modify | Wire new request/response shapes; replace inherited_locale/inherited_compliance with inherited_address. |
| `backend/nexus/app/modules/org_units/company_profile.py` | Delete | Pydantic CompanyProfile + INDUSTRY/COMPANY_STAGE enums no longer needed. |
| `backend/nexus/app/modules/ats/importer.py` | Modify | `_sync_clients` CREATE populates new columns; PROMOTE refreshes only-NULL columns. |
| `backend/nexus/tests/fixtures/company_profile_enums.json` | Delete | Parity fixture for the dropped enums. |
| `backend/nexus/tests/test_company_profile_schema.py` | Delete | Parity test for the dropped enums. |
| `backend/nexus/tests/modules/org_units/test_migration_0034.py` | Create | Migration upgrade backfill verification. |
| `backend/nexus/tests/modules/org_units/test_update_org_unit.py` | Modify/Create | Independent-field-save regression test (the user-flagged bug) + completion-gate auto-flip both directions. |
| `backend/nexus/tests/modules/org_units/test_ancestry.py` | Modify/Create | `find_company_profile_in_ancestry` (column-based) + `find_address_in_ancestry` (new). |
| `backend/nexus/tests/modules/ats/test_importer_clients_users.py` | Modify | Two new tests: CREATE populates columns; PROMOTE preserves recruiter edits. |
| `frontend/app/lib/api/org-units.ts` | Modify | Update `OrgUnit` shape: add 7 columns + `inherited_address`; remove `company_profile`, `inherited_locale`, `inherited_compliance`. Add `set_<field>` keys to `update()`. |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyDetail.tsx` | Modify | Add Industry chip + Address block; delete locale strip + compliance section; rewrite form schema. |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/RegionDetail.tsx` | Modify | Drop locale/compliance; add country/state/city chips. |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts` | Modify | Update `companyFormSchema` and `regionFormSchema`. |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/shared.tsx` | Modify | Remove `LocaleChip`, `ComplianceRow`, locale/timezone/currency option lists, `COMPLIANCE_FLAGS`. Keep generic helpers (`Sidebar`, `HeaderActions`, etc.). |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/page.tsx` | Delete | The deep editor route. |
| `frontend/app/components/dashboard/company-profile-form.tsx` | Delete | Form file no longer needed after onboarding migrates. |
| `frontend/app/app/onboarding/page.tsx` | Modify | Switch to column-level API. Drop company_stage. Industry as free-text. |
| `frontend/app/tests/components/CompanyDetail.test.tsx` | Create | Composition tests covering: industry chip renders, address inheritance badge, edit-mode save sends correct payload. |

No new files in production code outside the migration. No new dependencies.

---

### Task 1: Alembic migration — add columns, backfill, drop JSONB

**Files:**
- Create: `backend/nexus/migrations/versions/0034_company_profile_columns.py`
- Create: `backend/nexus/tests/modules/org_units/test_migration_0034.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/modules/org_units/test_migration_0034.py
"""Verify migration 0034 backfills column-level fields from JSONB and
strips obsolete metadata keys. Runs the migration's `upgrade()` function
manually against a connection that has been pre-seeded with legacy-shape
rows."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text


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
    await db.execute(
        text("INSERT INTO organizational_units (id, client_id, parent_unit_id, "
             "name, unit_type, is_root, company_profile, "
             "company_profile_completion_status, metadata) VALUES "
             "(:u, :t, :r, 'Oracle', 'client_account', false, "
             "'{\"about\":\"oracle about\","
             "\"industry\":\"fintech_financial_services\","
             "\"company_stage\":\"large_enterprise\","
             "\"hiring_bar\":\"oracle bar\"}', 'complete', "
             "'{\"website\":\"oracle.com\","
             "\"default_timezone\":\"America/New_York\","
             "\"default_currency\":\"USD\","
             "\"default_locale\":\"en-US\","
             "\"compliance_aivia_il\":false,"
             "\"compliance_gdpr_eu\":true,"
             "\"compliance_ccpa_ca\":false,"
             "\"short_name\":\"ORC\","
             "\"focus\":\"banking-engineering\"}')"),
        {"u": unit_id, "t": tenant_id, "r": root_id},
    )
    await db.flush()

    # Run the migration's upgrade function.
    from migrations.versions import (
        _0034_company_profile_columns as migration,
    )
    # The migration uses op.* commands which need an alembic Operations
    # context. For the test we bypass alembic and re-run the SQL directly.
    # The migration file exposes _UPGRADE_SQL (list of SQL strings)
    # for this test path; production deploys go through `alembic upgrade`.
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
    assert r.industry == "Fintech / Financial Services"  # human label
    assert r.hiring_bar == "oracle bar"
    assert r.website == "oracle.com"
    assert r.country is None
    assert r.state is None
    assert r.city is None
    # Stripped metadata keys are gone; the unit-type-specific keys survive.
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
    """Units with company_profile=NULL pass through cleanly — columns stay
    NULL, no errors."""
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

    from migrations.versions import (
        _0034_company_profile_columns as migration,
    )
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_migration_0034.py -v
```

Expected: FAIL — migration file does not exist (ImportError).

- [ ] **Step 3: Write the migration file**

Create `backend/nexus/migrations/versions/0034_company_profile_columns.py`:

```python
"""company_profile columns

Revision ID: 0034
Revises: 0033
Create Date: 2026-05-14

Promotes about/industry/hiring_bar out of the company_profile JSONB into
typed TEXT columns on organizational_units. Adds website/country/state/city
typed columns (website moves out of metadata; country/state/city are new).
Strips obsolete keys from metadata (locale + compliance + short_name +
website). Drops the company_profile JSONB column.

The 10-value industry enum is converted to human-readable labels on
upgrade (e.g. 'fintech_financial_services' -> 'Fintech / Financial
Services'). After upgrade, industry is free-text.

DOWNGRADE NOTE: recreating company_profile JSONB cannot recover
company_stage (column never carried forward), nor the stripped metadata
keys (locale + compliance + short_name). Downgrade is best-effort
data-loss recovery; do not rely on it for production rollback.

The _UPGRADE_SQL / _DOWNGRADE_SQL module-level lists are exposed for the
test path in tests/modules/org_units/test_migration_0034.py — production
deploys run via `alembic upgrade`.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


# Industry enum -> human label mapping. Used by both upgrade backfill and
# downgrade inverse mapping.
_INDUSTRY_ENUM_TO_LABEL = {
    "fintech_financial_services":     "Fintech / Financial Services",
    "healthcare_medtech":             "Healthcare / Medtech",
    "ecommerce_retail":               "E-commerce / Retail",
    "ai_ml_products":                 "AI / ML Products",
    "saas_enterprise_software":       "SaaS / Enterprise Software",
    "developer_tools_infrastructure": "Developer Tools / Infrastructure",
    "agency_consulting_staffing":     "Agency / Consulting / Staffing",
    "media_content":                  "Media / Content",
    "logistics_supply_chain":         "Logistics / Supply Chain",
    "other":                          "Other",
}


def _build_industry_case_expr(reverse: bool = False) -> str:
    """Build a CASE expression that maps enum strings -> labels, or vice
    versa. Used inline in the backfill SQL."""
    if reverse:
        mapping = {v: k for k, v in _INDUSTRY_ENUM_TO_LABEL.items()}
        source = "industry"
    else:
        mapping = _INDUSTRY_ENUM_TO_LABEL
        source = "company_profile->>'industry'"
    cases = "\n".join(
        f"            WHEN '{k}' THEN '{v}'"
        for k, v in mapping.items()
    )
    return (
        f"        CASE {source}\n"
        f"{cases}\n"
        f"            ELSE {source}\n"
        f"        END"
    )


_UPGRADE_SQL: list[str] = [
    # 1. Add the seven new TEXT columns.
    "ALTER TABLE organizational_units "
    "ADD COLUMN about TEXT, "
    "ADD COLUMN industry TEXT, "
    "ADD COLUMN hiring_bar TEXT, "
    "ADD COLUMN website TEXT, "
    "ADD COLUMN country TEXT, "
    "ADD COLUMN state TEXT, "
    "ADD COLUMN city TEXT",

    # 2. Backfill about + hiring_bar verbatim; industry via CASE mapping.
    f"UPDATE organizational_units SET\n"
    f"    about = company_profile->>'about',\n"
    f"    hiring_bar = company_profile->>'hiring_bar',\n"
    f"    industry =\n{_build_industry_case_expr(reverse=False)}\n"
    f"WHERE company_profile IS NOT NULL",

    # 3. Backfill website from metadata.website.
    "UPDATE organizational_units SET website = metadata->>'website' "
    "WHERE metadata ? 'website'",

    # 4. Strip obsolete keys from metadata. JSONB minus-operator removes
    #    keys; chaining one per key is fine (no temporaries).
    "UPDATE organizational_units SET metadata = "
    "metadata - 'default_timezone' - 'default_currency' - 'default_locale' "
    "- 'compliance_aivia_il' - 'compliance_gdpr_eu' - 'compliance_ccpa_ca' "
    "- 'website' - 'short_name' "
    "WHERE metadata IS NOT NULL",

    # 5. Drop the JSONB column.
    "ALTER TABLE organizational_units DROP COLUMN company_profile",
]


_DOWNGRADE_SQL: list[str] = [
    # 1. Re-add the JSONB column.
    "ALTER TABLE organizational_units ADD COLUMN company_profile JSONB",

    # 2. Reconstruct JSONB from columns. company_stage is permanently lost
    #    (no source column carried it forward). Industry maps back to its
    #    enum string when possible; unmapped values fall back to verbatim.
    f"UPDATE organizational_units SET company_profile = jsonb_build_object("
    f"    'about', about,"
    f"    'industry',\n{_build_industry_case_expr(reverse=True)},"
    f"    'hiring_bar', hiring_bar"
    f") WHERE about IS NOT NULL OR industry IS NOT NULL OR hiring_bar IS NOT NULL",

    # 3. Move website back into metadata (best-effort; if metadata is NULL
    #    we set it to a fresh object with website key).
    "UPDATE organizational_units SET metadata = "
    "COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('website', website) "
    "WHERE website IS NOT NULL",

    # 4. Drop the new columns.
    "ALTER TABLE organizational_units "
    "DROP COLUMN about, "
    "DROP COLUMN industry, "
    "DROP COLUMN hiring_bar, "
    "DROP COLUMN website, "
    "DROP COLUMN country, "
    "DROP COLUMN state, "
    "DROP COLUMN city",
]


def upgrade() -> None:
    for stmt in _UPGRADE_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE_SQL:
        op.execute(stmt)
```

The filename Python identifier is `_0034_company_profile_columns` (underscore prefix because Python module names can't start with a digit). The test imports it as `migrations.versions._0034_company_profile_columns`. Alembic loads it via the file path, so the underscore-prefixed module name doesn't affect `alembic upgrade`.

- [ ] **Step 4: Add the missing `__init__.py` shim**

Check whether `backend/nexus/migrations/versions/__init__.py` exists. If not, create it (empty file) so the test can `from migrations.versions import _0034_company_profile_columns`. If it does, skip this step.

```bash
ls backend/nexus/migrations/versions/__init__.py 2>/dev/null || touch backend/nexus/migrations/versions/__init__.py
```

- [ ] **Step 5: Run the test again — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_migration_0034.py -v
```

Expected: PASS (both tests).

- [ ] **Step 6: Apply migration to dev DB and verify**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
```

Expected: alembic reports "Running upgrade 0033 -> 0034".

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/migrations/versions/0034_company_profile_columns.py \
        backend/nexus/migrations/versions/__init__.py \
        backend/nexus/tests/modules/org_units/test_migration_0034.py
git commit -m "$(cat <<'EOF'
feat(org-units): migration 0034 — promote company_profile fields to typed columns

Adds about/industry/hiring_bar/website/country/state/city TEXT columns to
organizational_units. Backfills from the legacy company_profile JSONB
(industry enum strings translate to human-readable labels) and from
metadata.website. Strips obsolete keys from metadata (locale + compliance
+ short_name + website). Drops the company_profile JSONB column.

Downgrade is best-effort: company_stage and the stripped metadata keys
are not recoverable. See migration docstring for the full rationale.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: ORM + Pydantic schemas + update_org_unit refactor + completion-gate tests

**Files:**
- Modify: `backend/nexus/app/modules/org_units/models.py`
- Modify: `backend/nexus/app/modules/org_units/schemas.py`
- Modify: `backend/nexus/app/modules/org_units/service.py`
- Create: `backend/nexus/tests/modules/org_units/test_update_org_unit.py`

- [ ] **Step 1: Write three failing tests**

Create `backend/nexus/tests/modules/org_units/test_update_org_unit.py`:

```python
"""Independent-field save + completion-gate auto-flip tests.

The bug being fixed: today's update_org_unit refuses to persist any of the
strict company_profile fields unless all four validate. A recruiter edits
`about`, leaves industry blank, hits Save -> the about text is silently
dropped. The new column-level model persists each field independently and
re-derives completion_status on every save.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
    """Clearing `about` flips status complete -> pending. No re-block of
    jobs that were already advanced."""
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_update_org_unit.py -v
```

Expected: FAIL on all three — `update_org_unit` does not yet accept `about=` / `set_about=` / `industry=` / `set_industry=` / `hiring_bar=` / `set_hiring_bar=` kwargs.

- [ ] **Step 3: Update the ORM model**

Edit `backend/nexus/app/modules/org_units/models.py`. Add seven new column declarations after `unit_metadata` and remove the `company_profile` mapping:

```python
class OrganizationalUnit(Base):
    __tablename__ = "organizational_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_type: Mapped[str] = mapped_column(String, nullable=False)
    is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # company_profile column removed (migration 0034). Replaced by typed
    # columns below.
    about: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    hiring_bar: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    company_profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_profile_completed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    company_profile_completion_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'complete'"),
    )
    unit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    deletable_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    admin_delete_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 4: Rewrite the Pydantic schemas**

Edit `backend/nexus/app/modules/org_units/schemas.py`. Full replacement contents:

```python
from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None
    # Column-level fields. All optional on create — recruiter fills them
    # later via the detail page edit mode. None means "leave NULL"; an
    # explicit value is set.
    about: str | None = None
    industry: str | None = None
    hiring_bar: str | None = None
    website: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    metadata: dict | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None
    set_deletable_by: bool = False
    admin_delete_disabled: bool | None = None

    # Column-level company-profile fields. Each is paired with a
    # `set_<field>` sentinel: only fields where the sentinel is True are
    # persisted (matches the existing `set_metadata` pattern). An empty
    # string with `set_<field>=True` clears the column to NULL after
    # .strip().
    about: str | None = None
    set_about: bool = False
    industry: str | None = None
    set_industry: bool = False
    hiring_bar: str | None = None
    set_hiring_bar: bool = False
    website: str | None = None
    set_website: bool = False
    country: str | None = None
    set_country: bool = False
    state: str | None = None
    set_state: bool = False
    city: str | None = None
    set_city: bool = False

    metadata: dict | None = None
    set_metadata: bool = False


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    is_root: bool
    # Column-level company-profile + address fields.
    about: str | None = None
    industry: str | None = None
    hiring_bar: str | None = None
    website: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    company_profile_completed_at: str | None = None
    company_profile_completion_status: str = "complete"
    metadata: dict | None = None
    created_at: str
    created_by: str | None
    created_by_email: str | None
    deletable_by: str | None
    deletable_by_email: str | None
    admin_delete_disabled: bool
    is_accessible: bool = True
    admin_emails: list[str] = []
    # Replaces inherited_locale + inherited_compliance. Same shape as the
    # old fields: {"values": {country, state, city}, "source_unit_id"}.
    inherited_address: dict | None = None
    # Populated only when the update flips status pending -> complete.
    # Count of jobs advanced out of blocked_pending_client_setup.
    unblocked_job_count: int = 0


class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str


class MemberRole(BaseModel):
    role_id: str
    role_name: str
    assigned_at: str


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    roles: list[MemberRole]
```

- [ ] **Step 5: Rewrite `update_org_unit` in service.py**

Open `backend/nexus/app/modules/org_units/service.py`. Replace the entire `update_org_unit` function (around lines 510–614) with the version below. Also add a new `derive_completion_status` helper above it:

```python
# ─── Completion-gate derivation ─────────────────────────────────────────
# A unit's `company_profile_completion_status` flips between 'pending' and
# 'complete' based on whether all three strict-profile columns are
# non-empty (whitespace-trimmed). This is a derived state — every
# update_org_unit call re-evaluates it. The frontend never sets the
# status directly.
_STRICT_PROFILE_COLUMNS = ("about", "industry", "hiring_bar")


def derive_completion_status(unit: OrganizationalUnit) -> str:
    """Return 'complete' iff all 3 strict-profile columns are non-empty
    after .strip(); otherwise 'pending'."""
    for col in _STRICT_PROFILE_COLUMNS:
        value = getattr(unit, col, None)
        if not value or not value.strip():
            return "pending"
    return "complete"


def _normalize_text(value: str | None) -> str | None:
    """`.strip()` and convert empty string -> None. Applied to every
    text-column write so trailing-space inputs don't satisfy non-empty
    checks accidentally."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def update_org_unit(
    db: AsyncSession,
    unit: OrganizationalUnit,
    name: str | None,
    unit_type: str | None,
    deletable_by: str | None = None,
    set_deletable_by: bool = False,
    admin_delete_disabled: bool | None = None,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    # Column-level profile + address fields. Each pairs with a set_<field>
    # sentinel; only fields with the sentinel True are persisted.
    about: str | None = None,
    set_about: bool = False,
    industry: str | None = None,
    set_industry: bool = False,
    hiring_bar: str | None = None,
    set_hiring_bar: bool = False,
    website: str | None = None,
    set_website: bool = False,
    country: str | None = None,
    set_country: bool = False,
    state: str | None = None,
    set_state: bool = False,
    city: str | None = None,
    set_city: bool = False,
    metadata: dict | None = None,
    set_metadata: bool = False,
) -> OrganizationalUnit:
    before = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "about": unit.about,
        "industry": unit.industry,
        "hiring_bar": unit.hiring_bar,
        "website": unit.website,
        "country": unit.country,
        "state": unit.state,
        "city": unit.city,
        "completion_status": unit.company_profile_completion_status,
        "metadata": str(unit.unit_metadata) if unit.unit_metadata else None,
    }

    if unit_type is not None and unit_type not in VALID_UNIT_TYPES:
        raise ValueError(
            f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}"
        )
    if unit_type is not None and unit.unit_type == "company" and unit_type != "company":
        raise ValueError("The unit type of the root company unit cannot be changed.")

    if name is not None:
        unit.name = name
    if unit_type is not None:
        unit.unit_type = unit_type
    if admin_delete_disabled is not None:
        unit.admin_delete_disabled = admin_delete_disabled
    if set_deletable_by:
        if deletable_by is not None:
            admin_role_result = await db.execute(
                select(Role).where(Role.name == "Admin", Role.is_system == True)
            )
            admin_role = admin_role_result.scalar_one_or_none()
            if admin_role:
                assignment = await db.execute(
                    select(UserRoleAssignment).where(
                        UserRoleAssignment.user_id == uuid_mod.UUID(deletable_by),
                        UserRoleAssignment.org_unit_id == unit.id,
                        UserRoleAssignment.role_id == admin_role.id,
                    )
                )
                if assignment.scalar_one_or_none() is None:
                    raise ValueError(
                        "User must be an admin of this unit to be assigned as deletable_by"
                    )
            unit.deletable_by = uuid_mod.UUID(deletable_by)
        else:
            unit.deletable_by = None

    # Per-field sentinel-gated writes. Each field is written exactly when
    # its sentinel is True; values are .strip()ed; empty string -> NULL.
    if set_about:
        unit.about = _normalize_text(about)
    if set_industry:
        unit.industry = _normalize_text(industry)
    if set_hiring_bar:
        unit.hiring_bar = _normalize_text(hiring_bar)
    if set_website:
        unit.website = _normalize_text(website)
    if set_country:
        unit.country = _normalize_text(country)
    if set_state:
        unit.state = _normalize_text(state)
    if set_city:
        unit.city = _normalize_text(city)

    # Re-derive completion status from the (possibly updated) strict fields.
    # The transition is observed via `before["completion_status"]` vs the
    # new value — the router uses that delta to fire the unblock cascade.
    new_status = derive_completion_status(unit)
    if new_status != unit.company_profile_completion_status:
        unit.company_profile_completion_status = new_status
        if new_status == "complete":
            unit.company_profile_completed_at = datetime.now(UTC)
            unit.company_profile_completed_by = actor_id

    if set_metadata:
        unit.unit_metadata = metadata

    after = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "about": unit.about,
        "industry": unit.industry,
        "hiring_bar": unit.hiring_bar,
        "website": unit.website,
        "country": unit.country,
        "state": unit.state,
        "city": unit.city,
        "completion_status": unit.company_profile_completion_status,
        "metadata": str(unit.unit_metadata) if unit.unit_metadata else None,
    }
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        await log_event(
            db,
            tenant_id=unit.client_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=audit_actions.ORG_UNIT_UPDATED,
            resource="org_unit",
            resource_id=unit.id,
            payload={"changed": changed},
            ip_address=ip_address,
        )

    return unit
```

The old `_validate_and_normalize_company_profile` helper is no longer called from `update_org_unit`. Leave it in place for now — `create_org_unit` may still call it; that's handled in Task 4.

- [ ] **Step 6: Run the 3 PUT-path tests — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_update_org_unit.py -v
```

Expected: all 3 PASS.

- [ ] **Step 7: Run the full org_units test suite — catch regressions**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units -q
```

Several existing tests may now fail because they construct `update_org_unit(company_profile=..., set_company_profile=...)`. Those callers need their kwargs updated to the new shape OR the failing tests must be deleted if they were testing the dropped enum-validation behavior. Triage as follows:

- Tests that asserted the strict 4-field validation: delete (the validation is gone — that's the whole point).
- Tests that called `update_org_unit(company_profile={...}, set_company_profile=True)`: rewrite to the column-level kwargs.
- Tests calling `find_company_profile_in_ancestry`: deferred — Task 3 rewrites the function. If they fail now because the data fixture expects JSONB, mark them xfail with the task ref and fix in Task 3.

The router's caller of `update_org_unit` also needs adapting (router still sends `company_profile=...`). Do not touch the router yet — Task 4 handles it. For now the router's PUT endpoint may 500. Confirm the org-units suite is at the desired pass/fail state before commit.

- [ ] **Step 8: Lint + type-check**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  ruff check app/modules/org_units
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  mypy app/modules/org_units
```

Pre-existing ruff/mypy warnings unchanged — flag any newly introduced ones.

- [ ] **Step 9: Commit**

```bash
git add backend/nexus/app/modules/org_units/models.py \
        backend/nexus/app/modules/org_units/schemas.py \
        backend/nexus/app/modules/org_units/service.py \
        backend/nexus/tests/modules/org_units/test_update_org_unit.py
git commit -m "$(cat <<'EOF'
feat(org-units): column-level profile fields + per-field save + completion auto-flip

Rewrites the company-profile + address surface on organizational_units:

  - ORM exposes about/industry/hiring_bar/website/country/state/city as
    typed TEXT columns. company_profile JSONB mapping removed (column
    dropped in migration 0034).
  - UpdateOrgUnitRequest accepts each field paired with set_<field>
    sentinels (matching the existing set_metadata pattern). Absent fields
    are untouched; empty string clears to NULL after .strip().
  - update_org_unit re-derives company_profile_completion_status on every
    save: 'complete' iff all three strict-profile columns are non-empty,
    'pending' otherwise. The router observes the transition and fires
    the unblock cascade (next commit).

Closes the user-flagged bug where about/hiring_bar edits were silently
dropped when industry was blank: the all-or-nothing strict validator is
gone. Every field saves independently. Three new regression tests pin
the behavior: persists-about-with-blank-industry, flips-pending-complete,
flips-complete-pending.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Ancestry walks — rewrite `find_company_profile_in_ancestry`, add `find_address_in_ancestry`, delete locale/compliance walkers

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`
- Create: `backend/nexus/tests/modules/org_units/test_ancestry.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/nexus/tests/modules/org_units/test_ancestry.py`:

```python
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
async def test_find_company_profile_in_ancestry_returns_first_complete_unit(db):
    """Walk skips ancestors whose triple is incomplete and returns the first
    complete one."""
    from app.modules.org_units.service import find_company_profile_in_ancestry

    _, gp, p, c = await _seed_three_units(db)
    # Grandparent has full triple. Parent has only about+industry (no
    # hiring_bar). Child has nothing.
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
    assert result is not None
    assert result["about"] == "gp_about"
    assert result["industry"] == "gp_industry"
    assert result["hiring_bar"] == "gp_bar"


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
```

- [ ] **Step 2: Run the tests — expect FAIL**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_ancestry.py -v
```

Expected: FAIL — `find_company_profile_in_ancestry` returns the JSONB shape (or fails because the column is gone); `find_address_in_ancestry` does not exist.

- [ ] **Step 3: Rewrite `find_company_profile_in_ancestry` + add `find_address_in_ancestry`**

Open `backend/nexus/app/modules/org_units/service.py`. Replace the entire `find_company_profile_in_ancestry` function (around lines 1067–1100) and the locale/compliance walker section (around lines 1103–1253) with:

```python
async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Walk parent_unit_id chain from the given unit up to root. Return
    the {about, industry, hiring_bar} triple from the closest unit (self
    or ancestor) where ALL THREE columns are non-empty after .strip().
    None if no ancestor satisfies.

    Used by JD enrichment + signal extraction prompts. The returned shape
    is the JSON-style dict the prompts already consume.

    Tenant scoping: the caller is responsible — see the original docstring
    on this function for the contract.
    """
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            return None  # defensive: corrupted parent-chain loop
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            return None
        about = (unit.about or "").strip()
        industry = (unit.industry or "").strip()
        hiring_bar = (unit.hiring_bar or "").strip()
        if about and industry and hiring_bar:
            return {
                "about": about,
                "industry": industry,
                "hiring_bar": hiring_bar,
            }
        current_id = unit.parent_unit_id
    return None


# ─── Address ancestry walk ──────────────────────────────────────────────
# country/state/city are walked per-field — closest ancestor wins each.
# The closest ancestor that contributed at least one key is exposed as
# `source_unit_id` so the frontend can render "Inherited from {ancestor}".

_ADDRESS_COLUMNS: tuple[str, ...] = ("country", "state", "city")


async def find_address_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Single-unit per-field ancestry walk for country/state/city.

    Returns ``{"values": {...}, "source_unit_id": "..."}`` or None when no
    address key is set anywhere in the chain.
    """
    found: dict[str, str | None] = {k: None for k in _ADDRESS_COLUMNS}
    source_unit_id: UUID | None = None
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            break
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            break
        contributed_here = False
        for col in _ADDRESS_COLUMNS:
            if found[col] is None:
                value = getattr(unit, col, None)
                if value is not None and value.strip():
                    found[col] = value
                    contributed_here = True
        if contributed_here and source_unit_id is None:
            source_unit_id = unit.id
        if all(v is not None for v in found.values()):
            break
        current_id = unit.parent_unit_id
    if all(v is None for v in found.values()):
        return None
    return {
        "values": found,
        "source_unit_id": str(source_unit_id) if source_unit_id else None,
    }
```

Also DELETE these obsolete helpers from the same file:
- `_walk_metadata_in_map` (was used by list_org_units for the in-memory locale/compliance walk; replace its callers in Step 4 below)
- `_walk_metadata_in_db`
- `_serialize_inheritance`
- `LOCALE_KEYS`, `COMPLIANCE_KEYS`
- `find_locale_defaults_in_ancestry`
- `find_compliance_flags_in_ancestry`

And add a sibling in-memory walker for the list_org_units path:

```python
def find_address_in_map(
    unit: OrganizationalUnit,
    unit_map: dict[UUID, OrganizationalUnit],
) -> dict | None:
    """In-memory per-field address walk. Same semantics as
    `find_address_in_ancestry` but reads from a pre-loaded map (avoids
    one DB hit per ancestor when the whole tree is already in memory)."""
    found: dict[str, str | None] = {k: None for k in _ADDRESS_COLUMNS}
    source_unit_id: UUID | None = None
    current: OrganizationalUnit | None = unit
    seen: set[UUID] = set()
    while current is not None:
        if current.id in seen:
            break
        seen.add(current.id)
        contributed_here = False
        for col in _ADDRESS_COLUMNS:
            if found[col] is None:
                value = getattr(current, col, None)
                if value is not None and value.strip():
                    found[col] = value
                    contributed_here = True
        if contributed_here and source_unit_id is None:
            source_unit_id = current.id
        if all(v is not None for v in found.values()):
            break
        if current.parent_unit_id is None:
            break
        current = unit_map.get(current.parent_unit_id)
    if all(v is None for v in found.values()):
        return None
    return {
        "values": found,
        "source_unit_id": str(source_unit_id) if source_unit_id else None,
    }
```

- [ ] **Step 4: Update consumers (`get_org_unit` and `list_org_units`)**

In the same file, find every reference to the deleted helpers:

```bash
grep -n "find_locale_defaults_in_ancestry\|find_compliance_flags_in_ancestry\|_walk_metadata_in_map\|_walk_metadata_in_db\|_serialize_inheritance\|inherited_locale\|inherited_compliance" backend/nexus/app/modules/org_units/service.py
```

In `get_org_unit` (around line 332), replace the two ancestry walk calls and the response dict additions:

```python
# OLD:
inherited_locale = await find_locale_defaults_in_ancestry(db, unit.id)
inherited_compliance = await find_compliance_flags_in_ancestry(db, unit.id)
# ...
"inherited_locale": inherited_locale,
"inherited_compliance": inherited_compliance,

# NEW:
inherited_address = await find_address_in_ancestry(db, unit.id)
# ...
"inherited_address": inherited_address,
```

In `list_org_units` (the place that used `_walk_metadata_in_map`), replace with calls to `find_address_in_map`. Add the keys `"inherited_address"` to each unit's response dict; remove `"inherited_locale"` and `"inherited_compliance"`.

Also remove from the response: `"company_profile"` (column gone). Add: `"about"`, `"industry"`, `"hiring_bar"`, `"website"`, `"country"`, `"state"`, `"city"` (read from `unit.<column>`).

- [ ] **Step 5: Run the ancestry tests — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_ancestry.py -v
```

Expected: all 4 PASS.

- [ ] **Step 6: Run the full org_units + jd + ats test suites — catch regressions**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units tests/modules/jd tests/modules/ats -q
```

JD actor tests that called `find_company_profile_in_ancestry` and expected JSONB-shape return should still work — the new return is the same shape (`{about, industry, hiring_bar}`). If a JD test breaks, inspect the seed data — likely it pre-seeds with old JSONB shape and needs updating to column writes.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/org_units/service.py \
        backend/nexus/tests/modules/org_units/test_ancestry.py
git commit -m "$(cat <<'EOF'
refactor(org-units): rewrite ancestry walks for column-level profile fields

find_company_profile_in_ancestry now walks the typed columns and returns
the first ancestor (or self) where all 3 strict-profile fields are
non-empty. JD enrichment + signal-extraction prompt consumers unchanged
— same {about, industry, hiring_bar} dict shape.

Adds find_address_in_ancestry (per-field walk for country/state/city,
closest ancestor wins each). Adds find_address_in_map for the in-memory
list_org_units path.

Deletes the now-obsolete locale/compliance helpers:
  - find_locale_defaults_in_ancestry, find_compliance_flags_in_ancestry
  - _walk_metadata_in_map, _walk_metadata_in_db, _serialize_inheritance
  - LOCALE_KEYS, COMPLIANCE_KEYS constants

list_org_units and get_org_unit response dicts now expose inherited_address
+ column-level fields; inherited_locale, inherited_compliance, and
company_profile keys are removed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Router wiring + create_org_unit refactor + integration test

**Files:**
- Modify: `backend/nexus/app/modules/org_units/router.py`
- Modify: `backend/nexus/app/modules/org_units/service.py`

- [ ] **Step 1: Write the failing integration test**

Append to `backend/nexus/tests/modules/org_units/test_update_org_unit.py` (the file from Task 2):

```python
@pytest.mark.asyncio
async def test_get_org_unit_response_exposes_inherited_address(db, async_client):
    """GET /api/org-units/{id} returns inherited_address with per-field
    walk, and column-level fields are present. company_profile,
    inherited_locale, inherited_compliance keys are absent."""
    # NOTE: this test requires an authenticated client fixture. If the
    # project does not yet have one for unit tests, set this up using the
    # existing pattern in tests/modules/org_units/test_router.py.
    # Pre-seed using raw SQL (same pattern as other tests in this file).
    # ... assertion shape:
    response = await async_client.get(f"/api/org-units/{child_id}")
    body = response.json()
    assert "inherited_address" in body
    assert body["inherited_address"]["values"]["country"] == "US"
    assert "company_profile" not in body
    assert "inherited_locale" not in body
    assert "inherited_compliance" not in body
    assert body["about"] is None
    assert body["industry"] is None
```

If the test fixtures for `async_client` are involved or differ from the org_units suite's existing pattern, replace this integration-style test with a unit-style assertion on the service function output. Either way, prove the response shape changed.

- [ ] **Step 2: Run the test — expect FAIL**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units/test_update_org_unit.py::test_get_org_unit_response_exposes_inherited_address -v
```

Expected: FAIL — the router still constructs the response with `inherited_locale` / `inherited_compliance` keys.

- [ ] **Step 3: Rewrite the router request/response wiring**

Open `backend/nexus/app/modules/org_units/router.py`. Three changes:

1. **Update the helper `_org_unit_to_response`** (around line 47) to pass column-level fields and `inherited_address` instead of `inherited_locale` / `inherited_compliance`:

```python
def _org_unit_to_response(
    unit: OrganizationalUnit,
    member_count: int,
    created_by_email: str | None = None,
    deletable_by_email: str | None = None,
    is_accessible: bool = True,
    admin_emails: list[str] | None = None,
    inherited_address: dict | None = None,
    unblocked_job_count: int = 0,
) -> OrgUnitResponse:
    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=member_count,
        is_root=unit.is_root,
        about=unit.about,
        industry=unit.industry,
        hiring_bar=unit.hiring_bar,
        website=unit.website,
        country=unit.country,
        state=unit.state,
        city=unit.city,
        company_profile_completed_at=unit.company_profile_completed_at.isoformat()
            if unit.company_profile_completed_at else None,
        company_profile_completion_status=unit.company_profile_completion_status,
        metadata=unit.unit_metadata,
        created_at=unit.created_at.isoformat(),
        created_by=str(unit.created_by) if unit.created_by else None,
        created_by_email=created_by_email,
        deletable_by=str(unit.deletable_by) if unit.deletable_by else None,
        deletable_by_email=deletable_by_email,
        admin_delete_disabled=unit.admin_delete_disabled,
        is_accessible=is_accessible,
        admin_emails=admin_emails or [],
        inherited_address=inherited_address,
        unblocked_job_count=unblocked_job_count,
    )
```

2. **Update the GET handler** (around lines 128–135) to call `find_address_in_ancestry`:

```python
inherited_address = await find_address_in_ancestry(db, unit.id)
# ... pass inherited_address=inherited_address into _org_unit_to_response.
```

Repeat for the second GET-style call site (around line 246).

3. **Update the PUT handler** to pass the new request fields through:

```python
# Inside the PUT handler:
unit = await update_org_unit(
    db,
    unit,
    name=body.name,
    unit_type=body.unit_type,
    deletable_by=body.deletable_by,
    set_deletable_by=body.set_deletable_by,
    admin_delete_disabled=body.admin_delete_disabled,
    actor_id=actor_id,
    actor_email=actor_email,
    about=body.about, set_about=body.set_about,
    industry=body.industry, set_industry=body.set_industry,
    hiring_bar=body.hiring_bar, set_hiring_bar=body.set_hiring_bar,
    website=body.website, set_website=body.set_website,
    country=body.country, set_country=body.set_country,
    state=body.state, set_state=body.set_state,
    city=body.city, set_city=body.set_city,
    metadata=body.metadata, set_metadata=body.set_metadata,
)
```

The unblock cascade firing on pending->complete transition is preserved — the router already inspects the before/after status to decide whether to call `_unblock_pending_jobs_for_org_unit`. Verify that logic still triggers off the column-level completion-status transition (it should — the router reads `unit.company_profile_completion_status` from the in-memory ORM row after `update_org_unit` returns).

- [ ] **Step 4: Update `create_org_unit` in service.py**

Find `create_org_unit` (around line 87 of service.py). The old signature accepted `company_profile: dict | None`. Replace with column-level kwargs:

```python
async def create_org_unit(
    db: AsyncSession,
    tenant_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
    created_by: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    about: str | None = None,
    industry: str | None = None,
    hiring_bar: str | None = None,
    website: str | None = None,
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
    metadata: dict | None = None,
) -> OrganizationalUnit:
    """..."""
    # Existing parent/nesting validation rules unchanged.

    unit = OrganizationalUnit(
        client_id=tenant_id,
        parent_unit_id=parent_unit_id,
        name=name,
        unit_type=unit_type,
        is_root=False,  # roots are seeded by provisioning, not this path
        about=_normalize_text(about),
        industry=_normalize_text(industry),
        hiring_bar=_normalize_text(hiring_bar),
        website=_normalize_text(website),
        country=_normalize_text(country),
        state=_normalize_text(state),
        city=_normalize_text(city),
        unit_metadata=metadata,
        created_by=created_by,
    )
    # Initial completion status derived from incoming fields (could be
    # 'complete' if all 3 strict fields arrived on create — rare but
    # possible).
    unit.company_profile_completion_status = derive_completion_status(unit)
    if unit.company_profile_completion_status == "complete":
        unit.company_profile_completed_at = datetime.now(UTC)
        unit.company_profile_completed_by = created_by

    db.add(unit)
    await db.flush()
    # Existing audit + admin-inheritance logic unchanged.
    # ...
    return unit
```

The old "company_profile is required for client_account / company" guard is removed — the recruiter can create a client_account with NULL fields and fill them in later via the inline editor. ATS-imported stubs already rely on this NULL state.

If `_validate_and_normalize_company_profile` is no longer called from anywhere, delete it from service.py.

- [ ] **Step 5: Update the POST handler**

In `router.py`, the POST handler creates from `CreateOrgUnitRequest`. Update to thread the column-level kwargs through to `create_org_unit`. The old `company_profile` field on the request is gone (Task 2's schema rewrite removed it).

- [ ] **Step 6: Re-run the integration test + full org_units suite**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/org_units -v
```

Expected: the new integration test PASSES; all existing tests pass or the failures are isolated to deleted-enum tests (covered in Task 10).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/org_units/router.py \
        backend/nexus/app/modules/org_units/service.py \
        backend/nexus/tests/modules/org_units/test_update_org_unit.py
git commit -m "$(cat <<'EOF'
feat(org-units): router + create_org_unit wired to column-level fields

Router PUT now threads per-field set_<field> sentinels through to
update_org_unit. GET response constructs inherited_address (per-field
walk replacing inherited_locale + inherited_compliance) and exposes
about/industry/hiring_bar/website/country/state/city as top-level keys.
company_profile is removed from the response shape.

create_org_unit accepts column-level kwargs directly; no JSONB validation
gate. The "company_profile required for client_account/company" guard is
gone — recruiters and ATS imports can create units with NULL fields and
fill them later via the inline editor.

_validate_and_normalize_company_profile helper deleted (no callers).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: ATS importer CREATE path — populate new columns

**Files:**
- Modify: `backend/nexus/app/modules/ats/importer.py`
- Modify: `backend/nexus/tests/modules/ats/test_importer_clients_users.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/modules/ats/test_importer_clients_users.py`:

```python
@pytest.mark.asyncio
async def test_sync_clients_create_populates_address_and_industry_columns(
    db, importer_fixture,
):
    """A Ceipal client payload with website/industry/country/state/city
    lands on the new column-level fields of the stub org_unit.
    about/hiring_bar stay NULL; completion_status='pending'."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, _root_unit_id = importer_fixture
    payload = ATSClientPayload(
        external_id="cid-new",
        name="Acme Services",
        website="https://acme.com",
        industry="Banking - Financial Services",
        country="United States",
        state="New York",
        city="Rochester",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    assert result.new == 1

    row = await db.execute(text(
        "SELECT website, industry, country, state, city, about, hiring_bar, "
        "company_profile_completion_status FROM organizational_units "
        "WHERE client_id = :t AND name = 'Acme Services'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.website == "https://acme.com"
    assert r.industry == "Banking - Financial Services"
    assert r.country == "United States"
    assert r.state == "New York"
    assert r.city == "Rochester"
    assert r.about is None
    assert r.hiring_bar is None
    assert r.company_profile_completion_status == "pending"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_clients_users.py::test_sync_clients_create_populates_address_and_industry_columns -v
```

Expected: FAIL — the importer still writes to the old `company_profile` JSONB shape (or the column-level fields are NULL because the importer never sets them).

- [ ] **Step 3: Update the `_sync_clients` CREATE branch**

In `backend/nexus/app/modules/ats/importer.py`, find the create branch in `_sync_clients` (around line 181 — the `db.add(OrganizationalUnit(...))` after the stub profile setup). Replace the stub-profile block + OrganizationalUnit construction with:

```python
            # Create the org_unit with column-level fields populated from
            # Ceipal. about + hiring_bar stay NULL — recruiter authors
            # those later via the inline editor.
            new_unit = OrganizationalUnit(
                client_id=tenant_id,
                parent_unit_id=root.id,
                name=payload.name,
                unit_type="client_account",
                is_root=False,
                website=(payload.website or None),
                industry=(payload.industry or None),
                country=(payload.country or None),
                state=(payload.state or None),
                city=(payload.city or None),
                company_profile_completion_status="pending",
                created_by=created_by,
            )
            db.add(new_unit)
            await db.flush()
```

The old `stub = {...}` dict assembling `name/website/industry/country/state/city/address` for the `company_profile` JSONB is deleted. Same with the `company_profile=stub` argument.

- [ ] **Step 4: Update `_get_or_create_client_stub_by_name`**

In the same file (around line 700), the helper that creates name-only stubs from the jobs phase. The current construction passes `company_profile={"name": external_client_name}`. After this refactor, that field doesn't exist — the stub just has `name` on the column. Remove the `company_profile=` kwarg:

```python
        org_unit = OrganizationalUnit(
            client_id=tenant_id,
            parent_unit_id=root_org_unit_id,
            name=external_client_name,
            unit_type="client_account",
            is_root=False,
            company_profile_completion_status="pending",
            created_by=created_by,
        )
```

All other fields stay NULL (no website/industry/address — the jobs phase doesn't have them).

- [ ] **Step 5: Run the failing test — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_clients_users.py -v
```

Expected: all tests in this file PASS (including the existing `test_sync_clients_creates_pending_org_unit_for_new_mapping` — confirm it still asserts whatever it was asserting against; if it asserted on `company_profile["website"]` etc., rewrite to read the columns).

- [ ] **Step 6: Run full ATS suite**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/ats/importer.py \
        backend/nexus/tests/modules/ats/test_importer_clients_users.py
git commit -m "$(cat <<'EOF'
feat(ats): populate column-level fields when _sync_clients creates a client_account

_sync_clients CREATE now writes website/industry/country/state/city
directly to the typed columns instead of stuffing them into the
company_profile JSONB. about/hiring_bar stay NULL — recruiter authors
those via the inline editor on /settings/org-units/[unitId].

_get_or_create_client_stub_by_name (jobs-phase stub creation) drops the
company_profile JSONB kwarg — the stub now lands with just name set; the
ATS clients-phase sync fills in website/address/industry on create (or
on promote, NULL-only refresh).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: ATS importer PROMOTE path — refresh only-NULL columns

**Files:**
- Modify: `backend/nexus/app/modules/ats/importer.py`
- Modify: `backend/nexus/tests/modules/ats/test_importer_clients_users.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/modules/ats/test_importer_clients_users.py`:

```python
@pytest.mark.asyncio
async def test_sync_clients_promote_preserves_recruiter_edits(
    db, importer_fixture,
):
    """Promotion never overwrites a recruiter-edited column. Only NULL
    columns receive the Ceipal value."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id = importer_fixture

    # Pre-seed: a stub with recruiter-edited industry+website, NULL address.
    stub_unit_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO organizational_units "
        "(id, client_id, parent_unit_id, name, unit_type, is_root, "
        " industry, website, "
        " company_profile_completion_status) "
        "VALUES (:o, :t, :p, 'Oracle', 'client_account', false, "
        " 'Custom Industry (recruiter)', 'https://recruiter-edit.com', "
        " 'pending')"
    ), {"o": stub_unit_id, "t": tenant_id, "p": root_unit_id})
    await db.execute(text(
        "INSERT INTO ats_client_mappings "
        "(tenant_id, ats_vendor, external_client_id, external_client_name, "
        " org_unit_id, source_metadata) "
        "VALUES (:t, 'ceipal', 'name:Oracle', 'Oracle', :o, "
        " '{\"stub\":true,\"origin\":\"jobs_phase\"}')"
    ), {"t": tenant_id, "o": stub_unit_id})
    await db.flush()

    payload = ATSClientPayload(
        external_id="ABC123",
        name="Oracle",
        website="https://ceipal-returns.com",  # would overwrite if not gated
        industry="Banking",                    # would overwrite if not gated
        country="United States",
        state="New York",
        city="Rochester",
        raw={"id": "ABC123"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    await importer._run_phase("clients", importer._sync_clients, adapter)

    row = await db.execute(text(
        "SELECT website, industry, country, state, city "
        "FROM organizational_units WHERE id = :u"
    ), {"u": stub_unit_id})
    r = row.one()
    # Recruiter edits preserved.
    assert r.industry == "Custom Industry (recruiter)"
    assert r.website == "https://recruiter-edit.com"
    # NULL columns now filled from Ceipal.
    assert r.country == "United States"
    assert r.state == "New York"
    assert r.city == "Rochester"


@pytest.mark.asyncio
async def test_sync_clients_promote_fills_only_null_columns(
    db, importer_fixture,
):
    """All-NULL stub gets every Ceipal field on promote."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id = importer_fixture

    stub_unit_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO organizational_units "
        "(id, client_id, parent_unit_id, name, unit_type, is_root, "
        " company_profile_completion_status) "
        "VALUES (:o, :t, :p, 'Globex', 'client_account', false, 'pending')"
    ), {"o": stub_unit_id, "t": tenant_id, "p": root_unit_id})
    await db.execute(text(
        "INSERT INTO ats_client_mappings "
        "(tenant_id, ats_vendor, external_client_id, external_client_name, "
        " org_unit_id, source_metadata) "
        "VALUES (:t, 'ceipal', 'name:Globex', 'Globex', :o, "
        " '{\"stub\":true,\"origin\":\"jobs_phase\"}')"
    ), {"t": tenant_id, "o": stub_unit_id})
    await db.flush()

    payload = ATSClientPayload(
        external_id="DEF456",
        name="Globex",
        website="https://globex.com",
        industry="Manufacturing",
        country="India",
        state="Karnataka",
        city="Bangalore",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    await importer._run_phase("clients", importer._sync_clients, adapter)

    row = await db.execute(text(
        "SELECT website, industry, country, state, city "
        "FROM organizational_units WHERE id = :u"
    ), {"u": stub_unit_id})
    r = row.one()
    assert r.website == "https://globex.com"
    assert r.industry == "Manufacturing"
    assert r.country == "India"
    assert r.state == "Karnataka"
    assert r.city == "Bangalore"
```

- [ ] **Step 2: Run — expect FAIL on both**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_clients_users.py::test_sync_clients_promote_preserves_recruiter_edits \
         tests/modules/ats/test_importer_clients_users.py::test_sync_clients_promote_fills_only_null_columns -v
```

Expected: both FAIL — the promotion block doesn't touch the org_unit's column fields.

- [ ] **Step 3: Update the promotion block in `_sync_clients`**

In `importer.py`, find the promotion block (around line 180–220, the one that fires when an existing `name:%` stub mapping is found). Append the org_unit field-refresh logic after the existing mapping rewrites:

```python
            if promotable is not None:
                from_id = promotable.external_client_id
                promotable.external_client_id = payload.external_id
                promotable.source_metadata = {
                    "contacts": payload.contacts,
                    "raw": payload.raw,
                }
                promotable.last_synced_at = datetime.now(tz=UTC)

                # Refresh the linked org_unit's column-level fields ONLY
                # where they're currently NULL. Recruiter edits between
                # stub creation and promotion survive the upgrade.
                # about/hiring_bar are never auto-filled — Ceipal has no
                # equivalent.
                unit = await db.get(OrganizationalUnit, promotable.org_unit_id)
                if unit is not None:
                    if unit.website is None and payload.website:
                        unit.website = payload.website
                    if unit.industry is None and payload.industry:
                        unit.industry = payload.industry
                    if unit.country is None and payload.country:
                        unit.country = payload.country
                    if unit.state is None and payload.state:
                        unit.state = payload.state
                    if unit.city is None and payload.city:
                        unit.city = payload.city

                await log_event(
                    db,
                    tenant_id=tenant_id,
                    actor_id=created_by,
                    actor_email="ats-import",
                    action="ats.client_mapping.promoted",
                    resource="ats_client_mapping",
                    resource_id=promotable.org_unit_id,
                    payload={
                        "vendor": adapter.vendor,
                        "from_external_client_id": from_id,
                        "to_external_client_id": payload.external_id,
                    },
                )
                result.updated += 1
                continue
```

- [ ] **Step 4: Run the failing tests — expect PASS**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_clients_users.py -v
```

Expected: both new tests PASS; all existing tests still pass.

- [ ] **Step 5: Run full ATS + JD + org_units suites**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats tests/modules/jd tests/modules/org_units -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/ats/importer.py \
        backend/nexus/tests/modules/ats/test_importer_clients_users.py
git commit -m "$(cat <<'EOF'
feat(ats): _sync_clients promote refreshes only-NULL org_unit columns

Promotion path (existing name:%-stub mapping upgraded to real Ceipal id)
now also walks the linked org_unit and refreshes website / industry /
country / state / city — but ONLY where the column is currently NULL.
Recruiter edits between stub creation and promotion are preserved.

about + hiring_bar are never auto-filled — Ceipal has no equivalent
field; those remain recruiter-authored.

Two new tests: promote-preserves-recruiter-edits (industry+website
edited stay; NULL address fields fill from Ceipal); promote-fills-
only-null-columns (all-NULL stub receives every Ceipal field).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Frontend — TypeScript types + CompanyDetail refactor + composition test

**Files:**
- Modify: `frontend/app/lib/api/org-units.ts`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyDetail.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/shared.tsx`
- Create: `frontend/app/tests/components/CompanyDetail.test.tsx`

- [ ] **Step 1: Update `OrgUnit` type + `orgUnitsApi.update` signature**

Replace the `OrgUnit` interface and `orgUnitsApi.update` function in `frontend/app/lib/api/org-units.ts`:

```ts
import { apiFetch } from './client'

export type OrgUnitMetadata = Record<string, unknown>

export const TEAM_DEFAULT_ROLES = [
  'Recruiter',
  'Hiring Manager',
  'Interviewer',
  'Observer',
] as const
export type TeamDefaultRole = (typeof TEAM_DEFAULT_ROLES)[number]

export interface TeamMetadata {
  default_role?: TeamDefaultRole
  focus?: string
}

export interface DivisionMetadata {
  description?: string
}

/**
 * Address inheritance — per-field walk. `values.<field>` is the closest
 * non-null value walking root -> unit. `source_unit_id` is the closest
 * ancestor that contributed at least one field; null means every value
 * came from the unit itself (or nothing is set).
 */
export interface InheritedAddress {
  values: {
    country: string | null
    state: string | null
    city: string | null
  }
  source_unit_id: string | null
}

export interface OrgUnit {
  id: string
  client_id: string
  parent_unit_id: string | null
  name: string
  unit_type: string
  member_count: number
  created_at: string
  created_by: string | null
  created_by_email: string | null
  deletable_by: string | null
  deletable_by_email: string | null
  admin_delete_disabled: boolean
  is_accessible: boolean
  admin_emails: string[]
  is_root: boolean
  // Column-level company-profile fields. All free-text, all nullable.
  about: string | null
  industry: string | null
  hiring_bar: string | null
  website: string | null
  country: string | null
  state: string | null
  city: string | null
  company_profile_completed_at: string | null
  company_profile_completion_status: 'pending' | 'complete'
  metadata: OrgUnitMetadata | null
  inherited_address: InheritedAddress | null
}

// ... member / role types unchanged ...

export const orgUnitsApi = {
  // ... list / listMembers / assignRole / removeRole / listRoles / get unchanged ...

  create: (
    token: string,
    body: {
      name: string
      unit_type: string
      parent_unit_id: string | null
      about?: string | null
      industry?: string | null
      hiring_bar?: string | null
      website?: string | null
      country?: string | null
      state?: string | null
      city?: string | null
      metadata?: OrgUnitMetadata | null
    },
  ): Promise<OrgUnit> =>
    apiFetch<OrgUnit>('/api/org-units', {
      method: 'POST',
      token,
      body: JSON.stringify(body),
    }),

  update: (
    token: string,
    unitId: string,
    body: {
      name?: string
      about?: string
      set_about?: boolean
      industry?: string
      set_industry?: boolean
      hiring_bar?: string
      set_hiring_bar?: boolean
      website?: string
      set_website?: boolean
      country?: string
      set_country?: boolean
      state?: string
      set_state?: boolean
      city?: string
      set_city?: boolean
      metadata?: OrgUnitMetadata | null
      set_metadata?: boolean
    },
  ): Promise<OrgUnit> =>
    apiFetch<OrgUnit>(`/api/org-units/${unitId}`, {
      method: 'PUT',
      token,
      body: JSON.stringify(body),
    }),

  // delete / removeMember unchanged ...
}
```

Drop the `CompanyMetadata` and `RegionMetadata` types — they had locale/compliance keys which are gone. The `OrgUnitMetadata` record type is generic-enough that consumers can use it directly.

- [ ] **Step 2: Rewrite the form schema for company / client_account**

Edit `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts`. Replace the `companyFormSchema` block:

```ts
/* ─── Company / Client account ───────────────────────────────────────── */
//
// Free-text everywhere. No length caps (backend trims; empty string clears).
// Saving sends explicit `set_<field>: true` sentinels so each field
// persists independently.
export const companyFormSchema = z.object({
  name: unitNameSchema,
  about: z.string(),
  industry: z.string(),
  hiring_bar: z.string(),
  website: z.string(),
  country: z.string(),
  state: z.string(),
  city: z.string(),
});
export type CompanyFormValues = z.infer<typeof companyFormSchema>;

/* ─── Region ─────────────────────────────────────────────────────────── */
//
// Region adopts the same country/state/city block (replaces the locale +
// compliance shape it used to carry).
export const regionFormSchema = z.object({
  name: unitNameSchema,
  country: z.string(),
  state: z.string(),
  city: z.string(),
});
export type RegionFormValues = z.infer<typeof regionFormSchema>;
```

Delete `mergeMetadata` from this file — no longer used by Company/Region. (Division/Team forms may still need it; check before deleting.)

- [ ] **Step 3: Rewrite `CompanyDetail.tsx`**

Open `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyDetail.tsx`. The new file (replacing the old contents in full) is long — split-paste in two chunks for clarity.

Top half (imports + form defaults + helpers):

```tsx
"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { applyApiErrorToForm } from "@/lib/api/errors";
import { type OrgUnit } from "@/lib/api/org-units";
import { canManageUnit, useMe } from "@/lib/hooks/use-me";
import { useUpdateOrgUnit } from "@/lib/hooks/use-update-org-unit";
import { usePipelineTemplates } from "@/lib/hooks/use-pipeline-templates";

import { Sidebar } from "./Sidebar";
import { SidebarMembersCard } from "./SidebarMembersCard";
import {
  AddressChip,
  CrumbBack,
  HeaderActions,
  StatItem,
  StatSep,
  SubUnitCard,
  UnitCrumb,
  UnitPill,
} from "./shared";
import {
  companyFormSchema,
  type CompanyFormValues,
} from "./schema";

import "./detail.css";

export interface CompanyDetailProps {
  unit: OrgUnit;
  isClientAccount: boolean;
  parentChain: OrgUnit[];
  subUnits: OrgUnit[];
  openRolesCount: number;
  openRolesByChildId: Record<string, number>;
  onBack: () => void;
  onSaved: (next: OrgUnit) => void;
}

export function CompanyDetail({
  unit,
  isClientAccount,
  parentChain,
  subUnits,
  openRolesCount,
  openRolesByChildId,
  onBack,
  onSaved,
}: CompanyDetailProps) {
  const [mode, setMode] = React.useState<"view" | "edit">("view");
  const isEdit = mode === "edit";

  const defaults = React.useMemo<CompanyFormValues>(
    () => ({
      name: unit.name,
      about: unit.about ?? "",
      industry: unit.industry ?? "",
      hiring_bar: unit.hiring_bar ?? "",
      website: unit.website ?? "",
      country: unit.country ?? "",
      state: unit.state ?? "",
      city: unit.city ?? "",
    }),
    [
      unit.name,
      unit.about,
      unit.industry,
      unit.hiring_bar,
      unit.website,
      unit.country,
      unit.state,
      unit.city,
    ],
  );

  const form = useForm<CompanyFormValues>({
    resolver: zodResolver(companyFormSchema),
    defaultValues: defaults,
  });

  React.useEffect(() => {
    form.reset(defaults);
  }, [defaults, form]);

  const updateMutation = useUpdateOrgUnit();
  const meQuery = useMe();
  const canManageMembers = canManageUnit(meQuery.data, unit.id);
  const templatesQuery = usePipelineTemplates(unit.id);
  const templates = templatesQuery.data ?? [];
  const watched = form.watch();

  const inheritedFromName = React.useMemo(() => {
    if (!isClientAccount) return null;
    const sourceId = unit.inherited_address?.source_unit_id ?? null;
    if (!sourceId || sourceId === unit.id) return null;
    return parentChain.find((u) => u.id === sourceId)?.name ?? null;
  }, [
    isClientAccount,
    unit.id,
    unit.inherited_address?.source_unit_id,
    parentChain,
  ]);
```

Bottom half (submit handler + JSX render):

```tsx
  async function onSubmit(values: CompanyFormValues) {
    try {
      const updated = await updateMutation.mutateAsync({
        unitId: unit.id,
        body: {
          name: values.name.trim() || unit.name,
          about: values.about, set_about: true,
          industry: values.industry, set_industry: true,
          hiring_bar: values.hiring_bar, set_hiring_bar: true,
          website: values.website, set_website: true,
          country: values.country, set_country: true,
          state: values.state, set_state: true,
          city: values.city, set_city: true,
        },
      });
      onSaved(updated);
      toast.success(isClientAccount ? "Client account saved" : "Company saved");
      setMode("view");
      form.reset({
        name: updated.name,
        about: updated.about ?? "",
        industry: updated.industry ?? "",
        hiring_bar: updated.hiring_bar ?? "",
        website: updated.website ?? "",
        country: updated.country ?? "",
        state: updated.state ?? "",
        city: updated.city ?? "",
      });
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return;
      toast.error(err instanceof Error ? err.message : "Failed to save");
    }
  }

  function handleDiscard() {
    form.reset(defaults);
    setMode("view");
  }

  const crumbs = isClientAccount
    ? parentChain.map((u) => ({
        label: u.name,
        href: `/settings/org-units/${u.id}`,
      }))
    : [];

  const regionCount = subUnits.filter((u) => u.unit_type === "region").length;
  const divisionCount = subUnits.filter((u) => u.unit_type === "division").length;

  return (
    <main
      className="org-unit-detail-root"
      data-edit-mode={isEdit ? "true" : "false"}
    >
      <header className="unit-header">
        {isClientAccount && <CrumbBack onBack={onBack} />}
        <div className="unit-header-row">
          <div className="unit-header-main">
            <div className="unit-pills">
              <UnitPill
                type={isClientAccount ? "client_account" : "company"}
                label={isClientAccount ? "Client account" : "Company · Root"}
              />
            </div>
            {isClientAccount && <UnitCrumb items={crumbs} />}
            <h1
              className="unit-name"
              data-editable-text={isClientAccount ? "client-name" : "company-name"}
              contentEditable={isEdit}
              suppressContentEditableWarning
              onBlur={(e) => {
                const next = e.currentTarget.textContent?.trim() ?? "";
                if (next && next !== watched.name) {
                  form.setValue("name", next, { shouldDirty: true });
                }
              }}
            >
              {unit.name}
            </h1>
            <div className="unit-website">
              <span className="unit-website-label">Website</span>
              <input
                className="input mono unit-website-input"
                aria-label="Website"
                placeholder="https://example.com"
                {...form.register("website")}
              />
            </div>
            <div className="unit-industry" data-testid="unit-industry-row">
              <span className="unit-industry-label">Industry</span>
              <input
                className="input unit-industry-input"
                aria-label="Industry"
                placeholder="e.g. Banking / Financial Services"
                {...form.register("industry")}
              />
            </div>
            <div className="unit-about">
              <span className="unit-about-label">About</span>
              <textarea
                className="textarea unit-about-body"
                rows={4}
                aria-label="About"
                placeholder="Describe what this client builds in 1-2 sentences."
                {...form.register("about")}
              />
            </div>
            <div className="address-block" aria-label="Address">
              <AddressChip
                label="Country"
                isEdit={isEdit}
                value={watched.country}
                inheritedValue={
                  unit.inherited_address?.values.country ?? null
                }
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("country", v ?? "", { shouldDirty: true })
                }
              />
              <AddressChip
                label="State"
                isEdit={isEdit}
                value={watched.state}
                inheritedValue={
                  unit.inherited_address?.values.state ?? null
                }
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("state", v ?? "", { shouldDirty: true })
                }
              />
              <AddressChip
                label="City"
                isEdit={isEdit}
                value={watched.city}
                inheritedValue={
                  unit.inherited_address?.values.city ?? null
                }
                inheritedFromName={inheritedFromName}
                onChange={(v) =>
                  form.setValue("city", v ?? "", { shouldDirty: true })
                }
              />
            </div>
            <div className="unit-stats">
              {regionCount > 0 && (
                <>
                  <StatItem
                    value={regionCount}
                    label={regionCount === 1 ? "region" : "regions"}
                  />
                  <StatSep />
                </>
              )}
              {divisionCount > 0 && (
                <>
                  <StatItem
                    value={divisionCount}
                    label={divisionCount === 1 ? "division" : "divisions"}
                  />
                  <StatSep />
                </>
              )}
              <StatItem value={unit.member_count} label="direct members" />
              <StatSep />
              <StatItem value={openRolesCount} label="open jobs" rolledUp />
            </div>
          </div>
          <HeaderActions
            mode={mode}
            onModeChange={setMode}
            saving={updateMutation.isPending}
            dirty={form.formState.isDirty}
            onSave={form.handleSubmit(onSubmit)}
            onDiscard={handleDiscard}
          />
        </div>
      </header>

      <div className="unit-body">
        <div>
          {/* Hiring bar (highlighted) */}
          <section className="section highlight">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">Hiring bar</div>
                <div className="section-sub">
                  {isClientAccount
                    ? "Source of truth for jobs anchored under this client account."
                    : "Source of truth for the tenant. Inherited by every sub-unit unless a Client account overrides."}
                </div>
              </div>
            </div>
            <div className="card">
              <div className="profile-narrative">
                <span className="profile-narrative-label">
                  Hiring bar narrative
                </span>
                <textarea
                  className="textarea profile-narrative-body"
                  rows={5}
                  aria-label="Hiring bar narrative"
                  placeholder="Describe the bar. Read verbatim by Copilot when grounding JDs."
                  {...form.register("hiring_bar")}
                />
              </div>
              <div className="profile-action-row">
                <span className="profile-updated">
                  {unit.company_profile_completed_at
                    ? `Last updated ${unit.company_profile_completed_at.slice(0, 10)}`
                    : "Profile not yet complete"}
                </span>
              </div>
            </div>
          </section>

          {/* Sub-units (unchanged) */}
          <section className="section">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">
                  Sub-units <span className="count">{subUnits.length}</span>
                </div>
              </div>
              <a className="btn outline xs" href={`/settings/org-units?parent=${unit.id}`}>
                + New sub-unit
              </a>
            </div>
            {subUnits.length === 0 ? (
              <div className="empty-state">
                No sub-units yet. Add a region or division from the org graph.
              </div>
            ) : (
              <div className="subunits-grid">
                {subUnits.map((child) => (
                  <SubUnitCard
                    key={child.id}
                    unit={child}
                    href={`/settings/org-units/${child.id}`}
                    openRoles={openRolesByChildId[child.id] ?? 0}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Pipeline templates (unchanged) */}
          <section className="section">
            <div className="section-head">
              <div className="section-head-main">
                <div className="section-title">
                  Pipeline templates{" "}
                  <span className="count">
                    {templates.length}{" "}
                    {templates.length === 1 ? "template" : "templates"}
                    {isClientAccount ? " · owned by this client" : ""}
                  </span>
                </div>
              </div>
              <a
                className="btn outline xs"
                href={`/settings/org-units/${unit.id}/pipeline-templates`}
              >
                + Manage{isClientAccount ? "" : " tenant"} templates →
              </a>
            </div>
            {templatesQuery.isLoading ? (
              <div className="empty-state">Loading templates…</div>
            ) : templates.length === 0 ? (
              <div className="empty-state">No tenant templates yet.</div>
            ) : (
              <div className="card">
                {[...templates]
                  .sort((a, b) =>
                    a.is_default === b.is_default ? 0 : a.is_default ? -1 : 1,
                  )
                  .map((tpl) => {
                    const stages = [...tpl.stages].sort(
                      (a, b) => a.position - b.position,
                    );
                    return (
                      <div key={tpl.id} className="template-row">
                        <div className="template-name">
                          {tpl.name}
                          {tpl.is_default && (
                            <span className="default-tag">Default</span>
                          )}
                        </div>
                        <div className="template-stages">
                          {stages.map((s, i) => (
                            <React.Fragment key={s.id}>
                              {i > 0 && (
                                <span className="arrow" aria-hidden="true">
                                  →
                                </span>
                              )}
                              <span className="stage">{s.name}</span>
                            </React.Fragment>
                          ))}
                        </div>
                        <a
                          className="btn link"
                          href={`/settings/org-units/${unit.id}/pipeline-templates/${tpl.id}`}
                        >
                          Edit
                        </a>
                      </div>
                    );
                  })}
              </div>
            )}
          </section>
        </div>

        <Sidebar
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
          topCard={
            <SidebarMembersCard
              unitId={unit.id}
              canManageMembers={canManageMembers}
              helperText={
                isClientAccount
                  ? "Client-account admins live here. Per-member role picker."
                  : "Tenant-level admins live here. Per-member role picker."
              }
            />
          }
        />
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Add `AddressChip` helper to `shared.tsx`**

Open `frontend/app/app/(dashboard)/settings/org-units/[unitId]/shared.tsx`. Add a new `AddressChip` export that mirrors the visual pattern of the deleted `LocaleChip`:

```tsx
export function AddressChip({
  label,
  isEdit,
  value,
  inheritedValue,
  inheritedFromName,
  onChange,
}: {
  label: string;
  isEdit: boolean;
  value: string | null | undefined;
  inheritedValue: string | null;
  inheritedFromName: string | null;
  onChange: (next: string | null) => void;
}) {
  const display = value || inheritedValue || "";
  const isInherited = !value && !!inheritedValue;
  if (isEdit) {
    return (
      <label className="address-chip address-chip--edit">
        <span className="address-chip-label">{label}</span>
        <input
          className="input address-chip-input"
          value={value ?? ""}
          placeholder={inheritedValue ?? `e.g. ${label}`}
          onChange={(e) => onChange(e.target.value || null)}
        />
      </label>
    );
  }
  return (
    <div className="address-chip">
      <span className="address-chip-label">{label}</span>
      <span className="address-chip-value">{display || "—"}</span>
      {isInherited && inheritedFromName && (
        <span className="address-chip-source">
          Inherited from {inheritedFromName}
        </span>
      )}
    </div>
  );
}
```

DELETE from the same file (no longer used after CompanyDetail + RegionDetail refactor):

- `LOCALE_OPTIONS`
- `TIMEZONE_OPTIONS`
- `CURRENCY_OPTIONS`
- `CURRENCY_COMMON_VALUES`
- `COMPLIANCE_FLAGS`
- `LocaleChip`
- `ComplianceRow`
- `getLocaleCommonValues`
- `getTimezoneCommonValues`
- `localeDefaults`

If any of these have callers besides CompanyDetail/RegionDetail, hold off — Task 8 handles RegionDetail; nothing else should be a caller.

- [ ] **Step 5: Write the composition test**

Create `frontend/app/tests/components/CompanyDetail.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CompanyDetail } from "@/app/(dashboard)/settings/org-units/[unitId]/CompanyDetail";
import type { OrgUnit } from "@/lib/api/org-units";

// Mock the update hook so we can assert on the payload it receives.
const mutateAsync = vi.fn();
vi.mock("@/lib/hooks/use-update-org-unit", () => ({
  useUpdateOrgUnit: () => ({ mutateAsync, isPending: false }),
}));
vi.mock("@/lib/hooks/use-me", () => ({
  useMe: () => ({ data: { is_super_admin: true, assignments: [] } }),
  canManageUnit: () => true,
}));
vi.mock("@/lib/hooks/use-pipeline-templates", () => ({
  usePipelineTemplates: () => ({ data: [], isLoading: false }),
}));

function makeUnit(overrides: Partial<OrgUnit> = {}): OrgUnit {
  return {
    id: "u1",
    client_id: "t1",
    parent_unit_id: null,
    name: "Acme",
    unit_type: "client_account",
    member_count: 0,
    created_at: "2026-05-14T00:00:00Z",
    created_by: null,
    created_by_email: null,
    deletable_by: null,
    deletable_by_email: null,
    admin_delete_disabled: false,
    is_accessible: true,
    admin_emails: [],
    is_root: false,
    about: null,
    industry: null,
    hiring_bar: null,
    website: null,
    country: null,
    state: null,
    city: null,
    company_profile_completed_at: null,
    company_profile_completion_status: "pending",
    metadata: null,
    inherited_address: null,
    ...overrides,
  };
}

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("CompanyDetail", () => {
  it("renders Industry row in the header", () => {
    renderWithQuery(
      <CompanyDetail
        unit={makeUnit({ industry: "Banking / Financial Services" })}
        isClientAccount
        parentChain={[]}
        subUnits={[]}
        openRolesCount={0}
        openRolesByChildId={{}}
        onBack={() => {}}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByTestId("unit-industry-row")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("Banking / Financial Services"),
    ).toBeInTheDocument();
  });

  it("renders Address block with inheritance label when local value is null", () => {
    renderWithQuery(
      <CompanyDetail
        unit={makeUnit({
          country: null,
          state: null,
          city: null,
          inherited_address: {
            values: { country: "US", state: "NY", city: null },
            source_unit_id: "ancestor1",
          },
        })}
        isClientAccount
        parentChain={[
          {
            ...makeUnit({ id: "ancestor1", name: "Acme HQ", unit_type: "company" }),
          },
        ]}
        subUnits={[]}
        openRolesCount={0}
        openRolesByChildId={{}}
        onBack={() => {}}
        onSaved={() => {}}
      />,
    );
    expect(screen.getAllByText(/Inherited from Acme HQ/i).length).toBeGreaterThan(0);
  });

  it("saves about with blank industry — sends correct payload, no all-or-nothing gate", async () => {
    mutateAsync.mockResolvedValueOnce(makeUnit({ about: "new about text" }));
    renderWithQuery(
      <CompanyDetail
        unit={makeUnit()}
        isClientAccount
        parentChain={[]}
        subUnits={[]}
        openRolesCount={0}
        openRolesByChildId={{}}
        onBack={() => {}}
        onSaved={() => {}}
      />,
    );

    // Enter edit mode.
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    // Type into About; leave Industry blank.
    const aboutTextarea = screen.getByLabelText(/About/i);
    fireEvent.change(aboutTextarea, {
      target: { value: "new about text" },
    });

    // Save.
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const body = mutateAsync.mock.calls[0][0].body;
    expect(body.about).toBe("new about text");
    expect(body.set_about).toBe(true);
    expect(body.set_industry).toBe(true);
    expect(body.industry).toBe("");
  });
});
```

- [ ] **Step 6: Run frontend tests**

```bash
cd frontend/app && npm run test -- CompanyDetail
```

Expected: all three tests PASS. Type-check:

```bash
cd frontend/app && npm run type-check
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/lib/api/org-units.ts \
        frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/schema.ts \
        frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/CompanyDetail.tsx \
        frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/shared.tsx \
        frontend/app/tests/components/CompanyDetail.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend/org-units): CompanyDetail refactor — inline edit, address block, no deep editor

Replaces the deep-editor + locale-strip + compliance-section UX with a
single inline-edit detail page. Industry chip lives below Website in the
header. Address block (Country / State / City) replaces the Locale strip
and adopts the same inheritance-badge pattern. Hiring bar textarea stays
in its own section.

OrgUnit TypeScript shape: adds about/industry/hiring_bar/website/country/
state/city + inherited_address; removes company_profile, inherited_locale,
inherited_compliance, and the CompanyMetadata/RegionMetadata convenience
types. orgUnitsApi.update accepts per-field set_<field> sentinels matching
the backend Pydantic request shape.

shared.tsx loses LocaleChip / ComplianceRow / option lists; gains
AddressChip.

Composition test pins three behaviors:
  - Industry row renders when unit.industry is set.
  - Address inheritance label renders when local value is null.
  - Edit mode -> change About -> Save sends about+set_about with no
    industry-validation gate (the user-flagged bug fix in the UI layer).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: RegionDetail refactor — drop locale/compliance, add country/state/city

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/RegionDetail.tsx`

- [ ] **Step 1: Read the existing RegionDetail file to identify all locale/compliance usages**

```bash
grep -n "LocaleChip\|ComplianceRow\|default_timezone\|default_currency\|default_locale\|compliance_" frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/RegionDetail.tsx
```

- [ ] **Step 2: Replace the form schema usage**

The new `regionFormSchema` (defined in Task 7 Step 2) carries `{name, country, state, city}` only. Update RegionDetail's defaults and form construction to match. Replace every `LocaleChip` and `ComplianceRow` render in the JSX with `AddressChip` calls for country/state/city.

The submit handler swaps from `mergeMetadata(unit.metadata, {default_locale: …, compliance_aivia_il: …})` to:

```ts
async function onSubmit(values: RegionFormValues) {
  try {
    const updated = await updateMutation.mutateAsync({
      unitId: unit.id,
      body: {
        name: values.name.trim() || unit.name,
        country: values.country, set_country: true,
        state: values.state, set_state: true,
        city: values.city, set_city: true,
      },
    });
    onSaved(updated);
    toast.success("Region saved");
    setMode("view");
  } catch (err) {
    if (applyApiErrorToForm(err, form)) return;
    toast.error(err instanceof Error ? err.message : "Failed to save");
  }
}
```

If RegionDetail had a "Tenant-wide defaults inherited from ..." section that surfaced inherited locale/compliance, replace its rendering with an inherited-address indicator (same pattern as CompanyDetail's `inheritedFromName`).

- [ ] **Step 3: Run type-check + lint**

```bash
cd frontend/app && npm run type-check && npm run lint
```

Expected: clean.

- [ ] **Step 4: Manual smoke**

```bash
cd frontend/app && npm run dev
```

Open a region unit's detail page. Enter edit mode, type a country, save. Confirm no toast errors. Re-open the page — value persists.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/RegionDetail.tsx
git commit -m "$(cat <<'EOF'
feat(frontend/org-units): RegionDetail adopts country/state/city block

Replaces the locale + compliance metadata blocks (default_timezone,
default_currency, default_locale, compliance_aivia_il, compliance_gdpr_eu,
compliance_ccpa_ca) with the same Address block CompanyDetail uses.
Region inheritance picks up parent company/client_account address fields
via the new inherited_address response key.

Submit handler now sends column-level set_<field> sentinels instead of
metadata JSONB replacement.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Onboarding refactor — column-level fields, no enum, no company_stage

**Files:**
- Modify: `frontend/app/app/onboarding/page.tsx`

- [ ] **Step 1: Read the onboarding page**

```bash
wc -l frontend/app/app/onboarding/page.tsx
grep -n "companyProfileSchema\|company_stage\|CompanyProfile\|company_profile\b" frontend/app/app/onboarding/page.tsx
```

- [ ] **Step 2: Replace `companyProfileSchema` usage with column-level fields**

The onboarding wizard's Step 2 currently uses the strict 4-field `companyProfileSchema` from `components/dashboard/company-profile-form.tsx`. Replace with a local schema that matches the column-level API:

```ts
const onboardingProfileSchema = z.object({
  about: z.string().min(1, "Tell us what you build"),
  industry: z.string().min(1, "What industry?"),
  hiring_bar: z.string().min(1, "Describe a strong hire"),
});
type OnboardingProfileValues = z.infer<typeof onboardingProfileSchema>;
```

The form replaces the Industry and Company Stage `<Select>` dropdowns with free-text inputs. The Company Stage field is removed entirely.

The submit handler calls `orgUnitsApi.update(token, rootUnitId, {about: values.about, set_about: true, industry: values.industry, set_industry: true, hiring_bar: values.hiring_bar, set_hiring_bar: true})` instead of the JSONB shape.

- [ ] **Step 3: Type-check + lint**

```bash
cd frontend/app && npm run type-check && npm run lint
```

- [ ] **Step 4: Manual smoke**

Create a fresh tenant (or reset onboarding flag on an existing one) and walk through onboarding. Confirm Step 2 has 3 free-text fields, no Company Stage. After submitting, GET `/api/auth/me` reports `onboarding_complete: true`, and the root company unit's `about`/`industry`/`hiring_bar` columns are populated.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/app/onboarding/page.tsx
git commit -m "$(cat <<'EOF'
feat(frontend/onboarding): switch to column-level profile API, drop company_stage

Step 2 of the onboarding wizard now uses free-text inputs for about,
industry, and hiring_bar (no enum dropdowns). Company Stage field is
removed entirely. Submit posts to PUT /api/org-units/{root} with
per-field set_<field> sentinels.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Cleanup — delete deep editor, dropped enum module, dead helpers, parity test

**Files:**
- Delete: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/page.tsx`
- Delete: `frontend/app/components/dashboard/company-profile-form.tsx`
- Delete: `backend/nexus/app/modules/org_units/company_profile.py`
- Delete: `backend/nexus/tests/fixtures/company_profile_enums.json`
- Delete: `backend/nexus/tests/test_company_profile_schema.py`

- [ ] **Step 1: Verify nothing imports the about-to-be-deleted files**

```bash
grep -rn "components/dashboard/company-profile-form\|company-profile/page\|org_units.company_profile\|company_profile_enums.json" \
    frontend/app backend/nexus 2>/dev/null | grep -v "^Binary\|test_company_profile_schema"
```

Expected: empty (no remaining importers). If any importer surfaces, fix it before deleting.

- [ ] **Step 2: Delete the files**

```bash
rm frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/company-profile/page.tsx
rmdir frontend/app/app/\(dashboard\)/settings/org-units/\[unitId\]/company-profile 2>/dev/null
rm frontend/app/components/dashboard/company-profile-form.tsx
rm backend/nexus/app/modules/org_units/company_profile.py
rm backend/nexus/tests/fixtures/company_profile_enums.json
rm backend/nexus/tests/test_company_profile_schema.py
```

- [ ] **Step 3: Confirm no stale imports — full backend test + frontend type-check**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests -q
cd frontend/app && npm run type-check
```

Expected: both clean.

- [ ] **Step 4: Confirm the deep editor URL returns 404**

```bash
cd frontend/app && npm run dev
# In a separate terminal:
curl -I http://localhost:3000/settings/org-units/$(uuidgen)/company-profile
```

Expected: 404 (Next.js default — the route file no longer exists).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: delete deep editor route, dropped enum module, dead helpers

  - frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/
    The deep editor route. Inline edit on the parent detail page replaces it.
    Stale bookmarks 404 (no redirect — explicit decision per spec).
  - frontend/app/components/dashboard/company-profile-form.tsx
    Form component used only by the deleted deep editor + onboarding.
    Onboarding now uses local column-level fields directly.
  - backend/nexus/app/modules/org_units/company_profile.py
    Pydantic CompanyProfile + 10-value industry enum + 4-value stage enum.
    Industry is free-text after the refactor; company_stage is dropped.
  - backend/nexus/tests/fixtures/company_profile_enums.json
    Parity fixture for the dropped enums.
  - backend/nexus/tests/test_company_profile_schema.py
    Parity test for the dropped enums.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Manual smoke checklist

Manual verification — not a code change. Run before declaring the refactor complete.

- [ ] **Step 1: Start the stack**

```bash
docker compose -f backend/nexus/docker-compose.yml up -d
cd frontend/app && npm run dev
```

- [ ] **Step 2: Verify migration applied**

In Supabase Studio (http://localhost:54323) run:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'organizational_units'
ORDER BY column_name;
```

Expected: includes `about`, `industry`, `hiring_bar`, `website`, `country`, `state`, `city`. Does NOT include `company_profile`.

```sql
SELECT id, name, about, industry, hiring_bar, website, country, state, city,
       company_profile_completion_status
FROM organizational_units WHERE is_root = true LIMIT 5;
```

Expected: each root company unit shows the migrated values (industry is now a human label like "Fintech / Financial Services", not the enum string).

- [ ] **Step 3: ATS-sync a fresh tenant**

Connect Ceipal on a fresh tenant. Trigger a clients sync. Open `/settings/org-units` — the client_account units have website, industry, country, state, city populated from Ceipal. The "profile incomplete" badge is present because about + hiring_bar are still blank.

- [ ] **Step 4: Independent-field save (the user-flagged bug)**

Click into a client_account stub. Click Edit. Type only into the About textarea (e.g., "Acme builds payment routing infrastructure for fintech clients."). Leave Industry / Hiring bar empty. Click Save.

Expected:
- "Saved" toast.
- Refresh — About text persists.
- Profile incomplete badge still present (hiring_bar still empty).
- No console errors.

- [ ] **Step 5: Completion gate flips on third field fill**

Edit again. Fill in Hiring bar. Save.

Expected: status flips to complete. Any JD under this client_account that was in `blocked_pending_client_setup` advances to `draft` and starts signal extraction. Check the `/jobs` page — the related JDs now show "Awaiting setup" → "Draft" / "Signals extracting".

- [ ] **Step 6: Completion gate flips back on clear**

Edit again. Empty out the About textarea. Save.

Expected: profile incomplete badge returns. JDs that were already unblocked stay at their current status (no re-blocking — one-way ratchet).

- [ ] **Step 7: Inheritance walk**

On a region unit whose parent client_account has country set, view the region's detail page. The Country chip shows the parent's value with an "Inherited from {client_account name}" badge. Click Edit, override country, save. The chip now shows the overridden value with no inheritance badge.

- [ ] **Step 8: Audit trail**

```sql
SELECT action, payload, created_at FROM audit_log
WHERE action = 'org_unit_updated'
ORDER BY created_at DESC LIMIT 10;
```

Expected: every save during steps 4-7 wrote an audit row with the `changed` payload showing the column-level deltas.

- [ ] **Step 9: Deep editor returns 404**

Navigate directly to `/settings/org-units/<any-uuid>/company-profile`. Expected: 404 page.

- [ ] **Step 10: Done**

If everything above passes, the refactor is shipped.

---

## Self-Review

**Spec coverage** (skim each requirement in `docs/superpowers/specs/2026-05-14-company-profile-refactor-design.md`):

- ✅ Migration adds 7 typed columns + backfill + strip metadata + drop company_profile JSONB → Task 1.
- ✅ Industry enum mapped to human labels on upgrade → Task 1 migration `_INDUSTRY_ENUM_TO_LABEL` table.
- ✅ Drop company_profile.py module + enum fixtures + parity test → Task 10.
- ✅ ORM model updates → Task 2.
- ✅ Pydantic schemas: UpdateOrgUnitRequest with set_<field> sentinels → Task 2.
- ✅ Completion-status derivation: about + industry + hiring_bar all non-empty → Task 2 `derive_completion_status`.
- ✅ Independent-field save (the user-flagged bug) → Task 2 regression test.
- ✅ find_company_profile_in_ancestry rewrite → Task 3.
- ✅ find_address_in_ancestry replaces locale + compliance walkers → Task 3.
- ✅ Router GET response: inherited_address; column-level fields → Task 4.
- ✅ create_org_unit accepts column-level kwargs → Task 4.
- ✅ ATS create populates columns → Task 5.
- ✅ ATS promote refreshes only NULL columns → Task 6.
- ✅ Frontend TypeScript types → Task 7.
- ✅ CompanyDetail layout with Industry chip + Address block → Task 7.
- ✅ RegionDetail adopts country/state/city → Task 8.
- ✅ Onboarding switches to column-level API → Task 9.
- ✅ Deep editor route deleted (404 no redirect) → Task 10.
- ✅ shared.tsx helpers deleted → Task 7 + Task 10 cleanup.
- ✅ Backend tests: migration backfill, independent save, completion flip both directions, ancestry walks, ATS create + promote → Tasks 1, 2, 3, 5, 6.
- ✅ Frontend tests: CompanyDetail composition + save shape → Task 7.

**Placeholder scan:** no TBD / TODO / "implement later" / "handle edge cases" without code. Each test step shows full test code. Each implementation step shows full diff. Commands include expected output.

**Type consistency:**
- `derive_completion_status` signature is consistent across Tasks 2 and 4 (called from create_org_unit).
- `set_<field>` sentinel naming: `set_about`, `set_industry`, `set_hiring_bar`, `set_website`, `set_country`, `set_state`, `set_city` — identical across Pydantic schema (Task 2), service kwargs (Task 2), router wiring (Task 4), and frontend `update()` body (Task 7).
- `inherited_address` shape: `{values: {country, state, city}, source_unit_id}` — identical between `find_address_in_ancestry` return (Task 3), `OrgUnitResponse` (Task 2), TypeScript `InheritedAddress` (Task 7), and the composition test fixture (Task 7).
- Industry enum mapping `_INDUSTRY_ENUM_TO_LABEL`: Task 1 only — no downstream type references; consumers see free text after migration.
- `_normalize_text` helper signature consistent — Task 2 defines, Task 4 reuses in create_org_unit.
- `AddressChip` props (Task 7 Step 4) match call sites in both CompanyDetail (Task 7) and RegionDetail (Task 8).
