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
    """Build a CASE expression mapping enum strings <-> labels."""
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

    # 4. Strip obsolete keys from metadata.
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

    # 2. Reconstruct JSONB from columns. company_stage is permanently lost.
    f"UPDATE organizational_units SET company_profile = jsonb_build_object("
    f"    'about', about,"
    f"    'industry',\n{_build_industry_case_expr(reverse=True)},"
    f"    'hiring_bar', hiring_bar"
    f") WHERE about IS NOT NULL OR industry IS NOT NULL OR hiring_bar IS NOT NULL",

    # 3. Move website back into metadata (best-effort).
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
