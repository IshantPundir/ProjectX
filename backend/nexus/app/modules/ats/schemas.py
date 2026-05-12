"""Canonical vendor-agnostic DTOs returned by ATSAdapter implementations.

Every DTO carries a `raw: dict` of the verbatim vendor payload — this lets
us add field extractions later without re-syncing, and gives audit forensics
a complete picture. The `raw` field lives in DB columns (source_metadata),
NEVER in log fields.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _passthrough_raw(v: Any) -> dict[str, Any]:
    """Preserve the verbatim vendor payload by object identity.

    Pydantic v2's default `dict[str, Any]` validation rebuilds the dict (loses
    identity). The audit / re-extraction contract requires that `raw` is the
    *same object* the adapter handed in, so we short-circuit validation with a
    `mode="plain"` validator that only enforces the dict type.
    """
    if not isinstance(v, dict):
        raise TypeError("raw must be a dict")
    return v


class ATSClientPayload(BaseModel):
    external_id: str
    name: str
    website: str | None = None
    industry: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    address: str | None = None
    status: str | None = None
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any]
    fetched_at: datetime

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSUserPayload(BaseModel):
    external_id: str
    email: str
    display_name: str
    role: str | None = None
    status: str | None = None
    raw: dict[str, Any]
    fetched_at: datetime

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSJobPayload(BaseModel):
    external_id: str
    external_client_id: str
    title: str
    description: str | None = None
    status: str | None = None
    location: str | None = None
    skills: list[str] = Field(default_factory=list)
    employment_type: str | None = None
    work_arrangement: str | None = None
    salary_range_min: int | None = None
    salary_range_max: int | None = None
    salary_currency: str | None = None
    assigned_recruiter_external_ids: list[str] = Field(default_factory=list)
    raw: dict[str, Any]
    fetched_at: datetime

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSApplicantPayload(BaseModel):
    external_id: str
    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    current_title: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    raw: dict[str, Any]
    fetched_at: datetime

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSSubmissionPayload(BaseModel):
    external_id: str
    applicant_external_id: str
    job_external_id: str
    submission_status: str | None = None
    pipeline_status: str | None = None
    source: str | None = None                    # 'Naukri', 'LinkedIn', …
    submitted_on: datetime | None = None
    submitted_by_external_id: str | None = None
    pay_rate: Decimal | None = None
    employment_type: str | None = None
    raw: dict[str, Any]                          # carries resume_token, Documents[], etc.
    fetched_at: datetime

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)

    @field_validator("pay_rate", mode="before")
    @classmethod
    def _coerce_pay_rate(cls, v):
        """Ceipal returns pay_rate as int, float, or string across responses."""
        if v is None or v == "":
            return None
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))
