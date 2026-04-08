"""Strict Company Profile schema and helpers.

The 4-field Phase 2A shape is the single source of truth for both the
JSONB validation on org units (when unit_type is company/client_account)
and the input the Call 1 prompt receives from
find_company_profile_in_ancestry().

Enum values are duplicated in the frontend Zod schema
(frontend/app/components/dashboard/company-profile-form.tsx). A test in
tests/test_company_profile_schema.py enforces parity with
tests/fixtures/company_profile_enums.json — the fixture is the canonical
definition; both sides import from it (backend via this module, frontend
via a constant that MUST match)."""

from typing import Final, Literal

from pydantic import BaseModel, Field


INDUSTRY_VALUES: Final[tuple[str, ...]] = (
    "fintech_financial_services",
    "healthcare_medtech",
    "ecommerce_retail",
    "ai_ml_products",
    "saas_enterprise_software",
    "developer_tools_infrastructure",
    "agency_consulting_staffing",
    "media_content",
    "logistics_supply_chain",
    "other",
)

COMPANY_STAGE_VALUES: Final[tuple[str, ...]] = (
    "pre_seed_seed",
    "series_a_b",
    "series_c_plus",
    "large_enterprise",
)

IndustryEnum = Literal[
    "fintech_financial_services",
    "healthcare_medtech",
    "ecommerce_retail",
    "ai_ml_products",
    "saas_enterprise_software",
    "developer_tools_infrastructure",
    "agency_consulting_staffing",
    "media_content",
    "logistics_supply_chain",
    "other",
]

CompanyStageEnum = Literal[
    "pre_seed_seed",
    "series_a_b",
    "series_c_plus",
    "large_enterprise",
]


class CompanyProfile(BaseModel):
    about: str = Field(
        min_length=30,
        max_length=500,
        description="Operational description of what the company builds. Not a mission statement.",
    )
    industry: IndustryEnum
    company_stage: CompanyStageEnum
    hiring_bar: str = Field(
        min_length=20,
        max_length=280,
        description="What a strong hire looks like at this company.",
    )
