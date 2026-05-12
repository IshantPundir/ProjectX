"""Smoke tests for the canonical ATS DTOs — confirm fields, types, and
that the raw payload is preserved verbatim."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.modules.ats.schemas import (
    ATSClientPayload, ATSUserPayload, ATSJobPayload,
    ATSApplicantPayload, ATSSubmissionPayload,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_client_payload_minimal():
    p = ATSClientPayload(external_id="cid", name="Acme", raw={}, fetched_at=_now())
    assert p.contacts == []
    assert p.website is None


def test_client_payload_preserves_raw():
    raw = {"id": "cid", "name": "Acme", "weird_vendor_field": 1}
    p = ATSClientPayload(external_id="cid", name="Acme", raw=raw, fetched_at=_now())
    assert p.raw is raw  # same object preserved


def test_job_payload_recruiter_assignments_default_empty():
    p = ATSJobPayload(
        external_id="jid", external_client_id="cid", title="t",
        raw={}, fetched_at=_now(),
    )
    assert p.assigned_recruiter_external_ids == []
    assert p.skills == []


def test_submission_payload_pay_rate_coerces_numeric_and_string():
    """The Ceipal API has been observed returning pay_rate as int, float, or
    string. The DTO must coerce all three to Decimal."""
    for raw_val in (40, 40.0, "40.00"):
        p = ATSSubmissionPayload(
            external_id="sid", applicant_external_id="aid", job_external_id="jid",
            pay_rate=raw_val, raw={}, fetched_at=_now(),
        )
        assert isinstance(p.pay_rate, Decimal)
        assert p.pay_rate == Decimal("40.00") or p.pay_rate == Decimal("40")


def test_submission_payload_pay_rate_none_is_allowed():
    p = ATSSubmissionPayload(
        external_id="sid", applicant_external_id="aid", job_external_id="jid",
        raw={}, fetched_at=_now(),
    )
    assert p.pay_rate is None


def test_applicant_payload_required_fields():
    with pytest.raises(Exception):
        ATSApplicantPayload(raw={}, fetched_at=_now())  # missing external_id, name, email


def test_user_payload_required_fields():
    p = ATSUserPayload(
        external_id="uid", email="u@x.com", display_name="U One",
        raw={}, fetched_at=_now(),
    )
    assert p.role is None


def test_job_payload_external_client_name_defaults_to_none():
    """ATSJobPayload accepts external_client_name as optional; defaults to None."""
    from datetime import datetime, timezone
    from app.modules.ats.schemas import ATSJobPayload

    p = ATSJobPayload(
        external_id="jid",
        external_client_id="cid",
        title="x",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    assert p.external_client_name is None


def test_job_payload_external_client_name_carried_when_provided():
    """ATSJobPayload stores the client NAME when the vendor's API only
    exposes the linkage by name (Ceipal pattern)."""
    from datetime import datetime, timezone
    from app.modules.ats.schemas import ATSJobPayload

    p = ATSJobPayload(
        external_id="jid",
        external_client_id="",  # Ceipal list endpoint has no client id
        external_client_name="Oracle",
        title="Java AWS Developer",
        raw={"client": "Oracle"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    assert p.external_client_name == "Oracle"
    assert p.external_client_id == ""
