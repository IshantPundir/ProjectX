"""Canonical vendor-agnostic DTOs returned by ATSAdapter implementations.

Every DTO carries a `raw: dict` of the vendor payload (already stripped of
the sensitive token + PII fields listed in `app.modules.candidates.pii`).
The `raw` field lives in DB columns (`external_source_metadata.raw` /
`candidates.source_metadata`), NEVER in log fields.

Datetime contract: every `datetime` value is timezone-aware UTC. Adapters
normalize vendor timestamps to UTC before constructing these DTOs — see
`app/modules/ats/adapters/ceipal.py` for the Ceipal-side conversion.

Optional-field contract: empty strings from vendors are returned as None.
Adapters strip whitespace and convert ``""`` to ``None`` before constructing.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _passthrough_raw(v: Any) -> dict[str, Any]:
    """Preserve the vendor payload by object identity.

    Pydantic v2's default `dict[str, Any]` validation rebuilds the dict, which
    breaks our audit / re-extraction contract (the persisted blob must be
    the same object the adapter handed in, modulo PII strip). A `mode="plain"`
    validator short-circuits validation and only enforces the dict type.
    """
    if not isinstance(v, dict):
        raise TypeError("raw must be a dict")
    return v


class ATSJobStatus(BaseModel):
    """A job-status option as offered by the vendor.

    Used to populate the recruiter-facing filter-config UI. `external_id` is
    whatever the vendor uses to identify the status in subsequent filter
    queries (Ceipal: an integer-as-string).
    """

    external_id: str
    name: str


class ATSJobPayload(BaseModel):
    """Job posting as seen at the vendor's list-endpoint moment.

    `client_external_name` and `client_external_id` are set lazily:
      - `client_external_id` is populated by the orchestrator after
        resolving the name via the in-memory client index (or via
        adapter.get_client when capabilities allow it).
      - `client_external_name` is populated by `adapter.enrich_job` for
        vendors where the list endpoint omits it (Ceipal).

    `external_status_id` is the integer-as-string ID we used to filter
    server-side; `external_status` is the human-readable label
    (e.g. "Active", "Hold by Client").
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str
    title: str
    description_raw: str
    description_enriched: str | None = None
    external_status: str
    external_status_id: str
    client_external_name: str | None = None
    client_external_id: str | None = None
    created_external_id: str | None = None
    posted_by_external_id: str | None = None
    primary_recruiter_external_id: str | None = None
    assigned_recruiter_external_ids: list[str] = Field(default_factory=list)
    business_unit_id: int | None = None
    country: str | None = None
    primary_city: str | None = None
    primary_state: str | None = None
    secondary_locations: list[dict[str, Any]] | None = None
    skills: list[str] = Field(default_factory=list)
    pay_rates: list[dict[str, Any]] = Field(default_factory=list)
    deadline: date | None = None
    external_created_at: datetime
    external_modified_at: datetime
    raw: dict[str, Any]

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSClientContact(BaseModel):
    """Client-side HR personnel attached to a vendor client record.

    These are NOT vendor users. They are not auto-invited to ProjectX; they
    surface in the org-unit detail UI so a recruiter can manually invite a
    contact as a Hiring Manager on that client unit if appropriate.
    """

    external_id: str
    name: str | None = None
    email: str | None = None
    designation: str | None = None
    phone: str | None = None


class ATSClientPayload(BaseModel):
    """Source-of-truth client record from the vendor's detail endpoint.

    The orchestrator inserts an `organizational_units` row when this payload
    has no corresponding (tenant_id, source, external_id) row in DB. The
    column-level fields (website, industry, country, …) are backfilled into
    the org_unit ONLY when the column is currently NULL — recruiter-edited
    data is never overwritten.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str
    name: str
    website: str | None = None
    industry: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    business_unit_id: int | None = None
    external_created_at: datetime | None = None
    external_modified_at: datetime | None = None
    contacts: list[ATSClientContact] = Field(default_factory=list)
    raw: dict[str, Any]

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSUserPayload(BaseModel):
    """Source-of-truth user (recruiter/admin) record from the vendor.

    Inserted into the `users` table tagged with source='ats_<vendor>' and
    auth_user_id=NULL. The user is invitable to ProjectX via the team page
    — the invite-accept flow binds auth_user_id and flips is_active.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str
    email: str
    full_name: str
    role: str | None = None
    business_unit_id: int | None = None
    timezone: str | None = None
    external_status: str
    raw: dict[str, Any]

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSSubmissionPayload(BaseModel):
    """Candidate↔job submission record from the vendor.

    `external_status` is the free-form Ceipal label (e.g. "L2 Rejected").
    `submission_channel` is the source-of-application label (e.g. "Naukri",
    "Career Portal") — NOT our `source` provenance string. The `raw` field
    MUST NOT contain `resume_token`, `Documents`, `merged_pdf_document`, or
    `merge_document_path` — the Ceipal adapter strips these at the wire
    boundary; the candidates PII helper is the second defence layer.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str
    job_external_id: str
    applicant_external_id: str
    submitted_by_external_id: str | None = None
    external_status: str
    pipeline_status: str | None = None
    submission_channel: str | None = None
    pay_rate: float | None = None
    pay_currency: str | None = None
    external_submitted_at: datetime
    external_modified_at: datetime
    raw: dict[str, Any]

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)


class ATSApplicantPayload(BaseModel):
    """PII-bearing applicant record from the vendor.

    The `raw` field MUST NOT contain `aadhar_number` (Indian Aadhaar
    biometric ID), `ssn`, `pan_number`, `passport_number`,
    `drivers_license`, `tax_id`, `nric`, `emirates_id`, or any field
    matching ``*_token``. The Ceipal adapter strips these at the wire
    boundary; `app.modules.candidates.pii.strip_sensitive_pii` is the
    second defence layer applied by the orchestrator before persistence.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_id: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    secondary_email: str | None = None
    mobile: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    applicant_source: str | None = None
    raw: dict[str, Any]

    _preserve_raw = field_validator("raw", mode="plain")(_passthrough_raw)
