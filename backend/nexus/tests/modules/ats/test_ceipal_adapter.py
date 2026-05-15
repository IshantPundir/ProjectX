"""CeipalAdapter — field normalization, sentinel handling, timezone, PII strip.

All HTTP is mocked via ``httpx.MockTransport``. We're verifying the
adapter's vendor-quirk handling, not network behavior.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.constants import ATS_VENDOR_CEIPAL


def _state(tenant_tz: str | None = "Asia/Kolkata") -> ATSConnectionState:
    """Build an authenticated state — adapter skips auth in _request."""
    return ATSConnectionState(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        vendor=ATS_VENDOR_CEIPAL,
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token="valid",
        access_token_expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        tenant_timezone=tenant_tz,
    )


def _make_adapter(handler, tenant_tz: str | None = "Asia/Kolkata"):
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    return CeipalAdapter(_state(tenant_tz), _transport=httpx.MockTransport(handler))


# ─────────────────────────────── vendor constant ────────────────────────


def test_adapter_vendor_is_ats_ceipal():
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    assert CeipalAdapter.vendor == "ats_ceipal"


def test_adapter_capabilities_for_ceipal():
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    caps = CeipalAdapter.capabilities
    assert caps.supports_modified_after_cursor is True
    assert caps.job_detail_required_for_client_name is True
    assert caps.supports_client_search_by_name is False
    assert caps.rate_limit_qps == 0.5


# ─────────────────────────────── list_job_statuses ──────────────────────


@pytest.mark.asyncio
async def test_list_job_statuses_normalizes_ids_to_strings():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"id": 1, "name": "Active"},
            {"id": 8, "name": "Hold by Client"},
        ])

    adapter = _make_adapter(handler)
    statuses = await adapter.list_job_statuses()
    assert [s.external_id for s in statuses] == ["1", "8"]
    assert [s.name for s in statuses] == ["Active", "Hold by Client"]


# ─────────────────────────────── iter_jobs + enrich_job ─────────────────


@pytest.mark.asyncio
async def test_iter_jobs_html_unescapes_description():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 50,
            "next": "", "previous": "",
            "results": [{
                "id": "JID-1",
                "position_title": "SRE",
                "requisition_description": "Use &nbsp; and &#39;quotes&#39; <br />",
                "job_status": "Active",
                "job_status_id": "1",
                "primary_recruiter": "U1",
                "posted_by": "U2",
                "created_by": "U3",
                "assigned_recruiter": "U1,U2",
                "skills": "python, react,",
                "country": "India",
                "primary_city": "Bengaluru",
                "primary_state": "KA",
                "pay_rates": [],
                "secondary_cities": [],
                "secondary_states": [],
                "closing_date": "Open Until Filled",
                "created": "2026-05-01T00:00:00Z",
                "modified": "2026-05-10T00:00:00Z",
            }],
        })

    adapter = _make_adapter(handler)
    jobs = []
    async for j in adapter.iter_jobs(
        status_external_ids=["1"], modified_after=None,
    ):
        jobs.append(j)

    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "SRE"
    assert "&nbsp;" not in j.description_raw
    assert "&#39;" not in j.description_raw
    assert "'quotes'" in j.description_raw
    # Sentinel handling on closing_date
    assert j.deadline is None
    # CSV recruiter parsing — empty trailing entry dropped
    assert j.skills == ["python", "react"]
    assert j.assigned_recruiter_external_ids == ["U1", "U2"]
    assert j.posted_by_external_id == "U2"
    assert j.primary_recruiter_external_id == "U1"
    assert j.created_external_id == "U3"


@pytest.mark.asyncio
async def test_iter_jobs_does_not_populate_client_name_from_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 50,
            "next": "", "previous": "",
            "results": [{
                "id": "JID-X",
                "position_title": "Eng",
                "requisition_description": "",
                "job_status": "Active",
                "job_status_id": "1",
                "primary_recruiter": "",
                "posted_by": "",
                "created_by": "",
                "assigned_recruiter": "",
                "pay_rates": [],
                "secondary_cities": [],
                "secondary_states": [],
                "skills": "",
                "closing_date": "",
                "created": "2026-05-01T00:00:00Z",
                "modified": "2026-05-10T00:00:00Z",
            }],
        })

    adapter = _make_adapter(handler)
    jobs = [j async for j in adapter.iter_jobs(
        status_external_ids=["1"], modified_after=None,
    )]
    # client_external_name lives on the detail endpoint — list shouldn't set it.
    assert jobs[0].client_external_name is None
    assert jobs[0].client_external_id is None


@pytest.mark.asyncio
async def test_enrich_job_populates_client_external_name():
    from app.modules.ats.schemas import ATSJobPayload

    def handler(request: httpx.Request) -> httpx.Response:
        assert "getJobPostingDetails" in str(request.url)
        return httpx.Response(200, json={
            "id": "JID-1",
            "client": "Oracle",
            "extra_detail_field": "value",
        })

    adapter = _make_adapter(handler)
    base = ATSJobPayload(
        external_id="JID-1",
        title="SRE",
        description_raw="",
        external_status="Active",
        external_status_id="1",
        external_created_at=datetime.now(tz=UTC),
        external_modified_at=datetime.now(tz=UTC),
        raw={"id": "JID-1"},
    )
    enriched = await adapter.enrich_job(base)
    assert enriched.client_external_name == "Oracle"
    # Detail fields merged into raw
    assert enriched.raw["extra_detail_field"] == "value"


@pytest.mark.asyncio
async def test_enrich_job_url_encodes_opaque_id_with_slashes():
    """Opaque IDs may contain '/', '+', '=' — must be path-encoded."""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json={"id": "x", "client": "C"})

    from app.modules.ats.schemas import ATSJobPayload

    adapter = _make_adapter(handler)
    base = ATSJobPayload(
        external_id="ABC/def+xyz=",
        title="X",
        description_raw="",
        external_status="Active",
        external_status_id="1",
        external_created_at=datetime.now(tz=UTC),
        external_modified_at=datetime.now(tz=UTC),
        raw={"id": "ABC/def+xyz="},
    )
    await adapter.enrich_job(base)
    assert "ABC%2Fdef%2Bxyz%3D" in captured[0]
    assert "ABC/def+xyz=" not in captured[0]


# ─────────────────────────────── get_client ─────────────────────────────


@pytest.mark.asyncio
async def test_get_client_strips_industry_exp_zero_sentinel():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "C1",
            "name": "  Oracle  ",
            "website": "",
            "industry_exp": "0",
            "country": "India",
            "state": "KA",
            "city": "Bengaluru",
            "primary_business_unit": 42,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "",
            "contacts": [
                {
                    "id": "HR1",
                    "first_name": "Asha",
                    "last_name": "Kumar",
                    "email_id": "asha@oracle.com",
                    "designation": "VP HR",
                    "mobile_number": "+91-1",
                },
            ],
        })

    adapter = _make_adapter(handler)
    payload = await adapter.get_client(external_id="C1")
    assert payload.name == "Oracle"               # trimmed
    assert payload.industry is None                # "0" sentinel
    assert payload.website is None                 # empty string → None
    assert payload.external_modified_at is None    # empty → None
    assert len(payload.contacts) == 1
    c = payload.contacts[0]
    assert c.name == "Asha Kumar"
    assert c.email == "asha@oracle.com"
    assert c.phone == "+91-1"


# ─────────────────────────────── get_user ───────────────────────────────


@pytest.mark.asyncio
async def test_get_user_normalizes_email_id_and_first_last_name():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "U1",
            "email_id": "rec@oracle.com",
            "first_name": "Naresh",
            "last_name": "Reddy",
            "role": "Recruiter Lead",
            "business_unit_id": 42,
            "timezone": "Asia/Kolkata",
            "status": "Active",
        })

    adapter = _make_adapter(handler)
    user = await adapter.get_user(external_id="U1")
    assert user.email == "rec@oracle.com"
    assert user.full_name == "Naresh Reddy"
    assert user.role == "Recruiter Lead"
    assert user.timezone == "Asia/Kolkata"
    assert user.external_status == "Active"


# ─────────────────────────────── iter_submissions ───────────────────────


@pytest.mark.asyncio
async def test_iter_submissions_strips_resume_token_at_wire_boundary():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getSubmissionsList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 50,
            "next": "", "previous": "",
            "results": [{
                "id": "S1",
                "job_id": "J1",
                "job_seeker_id": "A1",
                "submitted_by": "U1",
                "submission_status": "L2 Rejected",
                "pipeline_status": "Internal Review",
                "source": "Naukri",
                "pay_rate": "1234.50",
                "currency_code": "INR",
                "resume_token": "SECRET-MUST-NOT-LEAK",
                "Documents": [{"resume_token": "ALSO-SECRET"}],
                "merged_pdf_document": "PDFBYTES",
                "merge_document_path": "/tmp/x",
                "submitted_on": "2026-05-10T00:00:00Z",
                "modified": "2026-05-12T00:00:00Z",
            }],
        })

    adapter = _make_adapter(handler)
    subs = [s async for s in adapter.iter_submissions(
        job_external_id="J1", modified_after=None,
    )]
    assert len(subs) == 1
    s = subs[0]
    assert s.external_id == "S1"
    assert s.external_status == "L2 Rejected"
    assert s.submission_channel == "Naukri"
    assert s.pay_rate == 1234.5
    # Hard-strip MUST keep these out of `raw`
    raw = s.raw
    assert "resume_token" not in raw
    assert "Documents" not in raw
    assert "merged_pdf_document" not in raw
    assert "merge_document_path" not in raw


# ─────────────────────────────── get_applicant ──────────────────────────


@pytest.mark.asyncio
async def test_get_applicant_strips_aadhar_from_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "A1",
            "firstname": "Asha",
            "lastname": "Kumar",
            "email": "asha@candidate.com",
            "email_address_1": "asha.alt@candidate.com",
            "mobile_number": "+91-99999",
            "city": "Bengaluru",
            "state": "KA",
            "country": "India",
            "source": "Naukri",
            "aadhar_number": "1234-5678-9012",
            "pan_number": "ABCDE1234F",
            "resume_token": "MUST-NOT-LEAK",
        })

    adapter = _make_adapter(handler)
    payload = await adapter.get_applicant(external_id="A1")
    assert payload.first_name == "Asha"
    assert payload.last_name == "Kumar"
    assert payload.email == "asha@candidate.com"
    assert payload.secondary_email == "asha.alt@candidate.com"
    assert payload.applicant_source == "Naukri"
    raw = payload.raw
    assert "aadhar_number" not in raw
    assert "pan_number" not in raw
    assert "resume_token" not in raw


# ─────────────────────────────── timezone normalization ─────────────────


@pytest.mark.asyncio
async def test_naive_timestamp_converted_to_utc_via_tenant_timezone():
    """A vendor-naive timestamp '2026-05-10 12:00:00' in Asia/Kolkata
    must be returned as 06:30 UTC (offset +05:30)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 50,
            "next": "", "previous": "",
            "results": [{
                "id": "J1",
                "position_title": "Eng",
                "requisition_description": "",
                "job_status": "Active",
                "job_status_id": "1",
                "primary_recruiter": "",
                "posted_by": "",
                "created_by": "",
                "assigned_recruiter": "",
                "pay_rates": [],
                "secondary_cities": [],
                "secondary_states": [],
                "skills": "",
                "closing_date": "",
                "created": "2026-05-10 12:00:00",
                "modified": "2026-05-10 12:00:00",
            }],
        })

    adapter = _make_adapter(handler, tenant_tz="Asia/Kolkata")
    job = [j async for j in adapter.iter_jobs(
        status_external_ids=["1"], modified_after=None,
    )][0]
    # 12:00 IST = 06:30 UTC
    assert job.external_created_at.tzinfo is not None
    assert job.external_created_at.utcoffset().total_seconds() == 0
    assert job.external_created_at.hour == 6
    assert job.external_created_at.minute == 30


@pytest.mark.asyncio
async def test_naive_timestamp_falls_back_to_utc_when_tenant_tz_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 50,
            "next": "", "previous": "",
            "results": [{
                "id": "J1",
                "position_title": "Eng",
                "requisition_description": "",
                "job_status": "Active",
                "job_status_id": "1",
                "primary_recruiter": "",
                "posted_by": "",
                "created_by": "",
                "assigned_recruiter": "",
                "pay_rates": [],
                "secondary_cities": [],
                "secondary_states": [],
                "skills": "",
                "closing_date": "",
                "created": "2026-05-10 12:00:00",
                "modified": "2026-05-10 12:00:00",
            }],
        })

    adapter = _make_adapter(handler, tenant_tz=None)
    job = [j async for j in adapter.iter_jobs(
        status_external_ids=["1"], modified_after=None,
    )][0]
    assert job.external_created_at.hour == 12
    assert job.external_created_at.minute == 0
    assert job.external_created_at.utcoffset().total_seconds() == 0
