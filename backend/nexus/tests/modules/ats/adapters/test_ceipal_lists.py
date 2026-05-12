"""Each list_* method: delta filter passes through, results parse into the
right canonical DTO with raw preserved verbatim."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState


def _adapter(handler):
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token="t", access_token_expires_at=future,
    )
    return CeipalAdapter(state, _transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_clients_parses_envelope_and_preserves_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getClientsList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "cid-hash",
                "name": "Oracle",
                "website": "www.oracle.com",
                "industry_exp": "Computer Software",
                "country": "India", "state": "Karnataka", "city": "",
                "address": "", "zipcode": "",
                "status": "Active",
                "vendor_quirk_field": "preserved",
            }],
        })

    a = _adapter(handler)
    payloads = []
    async for c in a.list_clients():
        payloads.append(c)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.external_id == "cid-hash"
    assert p.name == "Oracle"
    assert p.industry == "Computer Software"
    assert p.raw["vendor_quirk_field"] == "preserved"


@pytest.mark.asyncio
async def test_list_clients_passes_modifiedAfter_when_since_given():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "count": 0, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "", "results": [],
        })

    a = _adapter(handler)
    since = datetime(2026, 5, 12, 8, 30, 0, tzinfo=timezone.utc)
    async for _ in a.list_clients(since=since):
        pass
    assert captured["params"]["modifiedAfter"] == "2026-05-12 08:30:00"


@pytest.mark.asyncio
async def test_list_users_passes_through():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getUsersList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "uid",
                "first_name": "John", "last_name": "Doe",
                "display_name": "John Doe", "email_id": "j@x.com",
                "role": "Administrator", "status": "Active",
            }],
        })

    a = _adapter(handler)
    out = [u async for u in a.list_users()]
    assert out[0].external_id == "uid"
    assert out[0].email == "j@x.com"
    assert out[0].display_name == "John Doe"


@pytest.mark.asyncio
async def test_list_jobs_fetches_details_and_carries_client_name():
    """``list_jobs`` enriches each list item with /getJobPostingDetails/{id}
    to pick up the client NAME (Ceipal's list endpoint doesn't carry the
    job→client linkage)."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getJobPostingsList" in url:
            return httpx.Response(200, json={
                "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
                "next": "", "previous": "",
                "results": [{
                    "id": "jid",
                    "company": 43573,  # agency tenant id (NOT client id)
                    "position_title": "Java AWS Developer",
                    "public_job_desc": "<html>JD body</html>",
                    "job_status": "Active",
                    "primary_city": "Bangalore",
                    "employment_type": "Full Time",
                    "remote_opportunities": "Yes",
                    "skills": "Java, AWS, Python",
                    "assigned_recruiter": "rid-1,rid-2,rid-3",
                    "pay_rates": [{
                        "pay_rate_currency": "INR",
                        "min_pay_rate": "1000000",
                        "max_pay_rate": "2000000",
                    }],
                }],
            })
        if "getJobPostingDetails/jid" in url:
            return httpx.Response(200, json={
                "id": "jid",
                "client": "Oracle",  # ← the client NAME, our linkage key
                "position_title": "Java AWS Developer",
                "job_status": "Active",
                "experience": "8-10 years",
                "min_experience": "8",
                "number_of_positions": 1,
            })
        return httpx.Response(404, text="unmocked")

    a = _adapter(handler)
    out = [j async for j in a.list_jobs()]
    j = out[0]
    assert j.external_id == "jid"
    assert j.external_client_id == ""  # Ceipal: id-based linkage unavailable
    assert j.external_client_name == "Oracle"  # ← linked by NAME via details
    assert j.title == "Java AWS Developer"
    assert j.status == "Active"
    assert set(j.skills) == {"Java", "AWS", "Python"}
    assert j.assigned_recruiter_external_ids == ["rid-1", "rid-2", "rid-3"]


@pytest.mark.asyncio
async def test_list_jobs_skips_client_name_when_details_fails():
    """If /getJobPostingDetails/{id} fails (network / contract error),
    list_jobs should NOT fail the whole phase — yield the job with
    external_client_name=None so the importer can skip just this one."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "getJobPostingsList" in str(request.url):
            return httpx.Response(200, json={
                "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
                "next": "", "previous": "",
                "results": [{
                    "id": "jid",
                    "company": 43573,
                    "position_title": "Java AWS Developer",
                    "job_status": "Active",
                }],
            })
        if "getJobPostingDetails/jid" in str(request.url):
            # Simulate "details endpoint returned bad data" — vendor contract error.
            return httpx.Response(400, json={"message": "bad job id"})
        return httpx.Response(404, text="unmocked")

    a = _adapter(handler)
    out = [j async for j in a.list_jobs()]
    assert len(out) == 1  # job still yielded, not raised
    assert out[0].external_client_name is None  # details fetch failed → no link


@pytest.mark.asyncio
async def test_list_applicants_parses_minimal_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getApplicantsList" in str(request.url)
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "aid", "applicant_id": "9999",
                "firstname": "Jane", "lastname": "Doe",
                "email": "jane@x.com", "mobile_number": "555-0100",
                "city": "Bangalore", "state": "Karnataka",
                "job_title": "Senior Engineer",
            }],
        })

    a = _adapter(handler)
    out = [a_ async for a_ in a.list_applicants()]
    p = out[0]
    assert p.external_id == "aid"
    assert p.name == "Jane Doe"
    assert p.email == "jane@x.com"
    assert p.phone == "555-0100"
    assert p.location == "Bangalore, Karnataka"
    assert p.current_title == "Senior Engineer"


@pytest.mark.asyncio
async def test_list_submissions_requires_job_id_and_extracts_link():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getSubmissionsList" in str(request.url)
        assert request.url.params["jobId"] == "TVhUa2J3eDA"
        return httpx.Response(200, json={
            "count": 1, "num_pages": 1, "page_number": 1, "limit": 20,
            "next": "", "previous": "",
            "results": [{
                "id": "sid", "submission_id": 9061,
                "applicant_id": 9999, "job_seeker_id": "appl-hash",
                "job_id": "TVhUa2J3eDA",
                "submission_status": "Internal Interview Scheduled",
                "pipeline_status": "",
                "source": "Naukri",
                "submitted_on": "2026-05-12 06:31:23",
                "pay_rate": 40.0,
                "employment_type": "Full Time",
                "resume_token": "opaque-token-abc",
            }],
        })

    a = _adapter(handler)
    out = [s async for s in a.list_submissions(job_external_id="TVhUa2J3eDA")]
    s = out[0]
    assert s.external_id == "sid"
    assert s.applicant_external_id == "appl-hash"
    assert s.job_external_id == "TVhUa2J3eDA"
    assert s.submission_status == "Internal Interview Scheduled"
    assert s.source == "Naukri"
    assert s.raw["resume_token"] == "opaque-token-abc"   # preserved for future
