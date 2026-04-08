"""Tests for the strict Company Profile Pydantic schema and enum parity
with the frontend Zod enum (via the canonical JSON fixture)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.org_units.company_profile import (
    COMPANY_STAGE_VALUES,
    INDUSTRY_VALUES,
    CompanyProfile,
)

FIXTURE = Path(__file__).parent / "fixtures" / "company_profile_enums.json"


def test_enum_parity_with_frontend_fixture():
    """The Python enum values must match the fixture exactly. The frontend
    Zod schema reads the same fixture (via a build-time check in 2B+ or
    manual sync in 2A). Drift here means the backend rejects values the
    frontend allows or vice versa."""
    with FIXTURE.open() as f:
        expected = json.load(f)
    assert list(INDUSTRY_VALUES) == expected["industry"]
    assert list(COMPANY_STAGE_VALUES) == expected["company_stage"]


def test_valid_profile():
    profile = CompanyProfile(
        about="We build real-time risk scoring infrastructure for mid-market lenders.",
        industry="fintech_financial_services",
        company_stage="series_a_b",
        hiring_bar="Engineers who own problems end-to-end.",
    )
    assert profile.industry == "fintech_financial_services"


def test_about_too_short():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="Too short",
            industry="fintech_financial_services",
            company_stage="series_a_b",
            hiring_bar="Strong engineers who own problems end-to-end.",
        )


def test_about_too_long():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="A" * 501,
            industry="fintech_financial_services",
            company_stage="series_a_b",
            hiring_bar="Strong engineers who own problems end-to-end.",
        )


def test_hiring_bar_too_long():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="A real description of what this fintech company builds and for whom.",
            industry="fintech_financial_services",
            company_stage="series_a_b",
            hiring_bar="H" * 281,
        )


def test_invalid_industry():
    with pytest.raises(ValidationError):
        CompanyProfile(
            about="A real description of what this fintech company builds and for whom.",
            industry="not_a_valid_industry",
            company_stage="series_a_b",
            hiring_bar="Strong engineers who own problems end-to-end.",
        )
