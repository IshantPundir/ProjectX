"""Pagination walks pages until `next` is empty; error envelope maps correctly."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import (
    ATSAuthorizationError, ATSRateLimitedError,
    ATSNetworkError, ATSVendorContractError,
)


def _adapter(handler):
    return _adapter_with_transport(handler)


def _adapter_with_transport(handler):
    from app.modules.ats.adapters.ceipal import CeipalAdapter
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token="valid", access_token_expires_at=future,
    )
    return CeipalAdapter(state, _transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_paginate_walks_two_pages_until_next_empty():
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    pages = {
        1: {
            "count": 3, "num_pages": 2, "page_number": 1, "limit": 2,
            "next": "https://api.ceipal.com/v2/getThings/?page=2",
            "previous": "",
            "results": [{"id": "a"}, {"id": "b"}],
        },
        2: {
            "count": 3, "num_pages": 2, "page_number": 2, "limit": 2,
            "next": "",
            "previous": "https://api.ceipal.com/v2/getThings/?page=1",
            "results": [{"id": "c"}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages[page])

    adapter = _adapter_with_transport(handler)
    all_ids = []
    async for item in adapter._paginate("/getThings/", {}):
        all_ids.append(item["id"])
    assert all_ids == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_429_raises_rate_limited_with_default_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Request limit exceeded. Please try again later."})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSRateLimitedError) as exc_info:
        async for _ in adapter._paginate("/getThings/", {}):
            pass
    assert exc_info.value.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_403_raises_authorization_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Your company access is temporarily disabled."})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSAuthorizationError):
        async for _ in adapter._paginate("/getThings/", {}):
            pass


@pytest.mark.asyncio
async def test_400_raises_vendor_contract_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Invalid parameters or filters."})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSVendorContractError):
        async for _ in adapter._paginate("/getThings/", {}):
            pass


@pytest.mark.asyncio
async def test_500_raises_network_error_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal"})

    adapter = _adapter_with_transport(handler)
    with pytest.raises(ATSNetworkError):
        async for _ in adapter._paginate("/getThings/", {}):
            pass


@pytest.mark.asyncio
async def test_paginate_enforces_request_pacing(monkeypatch):
    """Consecutive HTTP requests must be spaced by at least the configured
    pacing (1 / rate_limit_qps if set, else settings.ats_default_request_pacing_seconds).

    Pins the contract that prevents Ceipal's undocumented 429 storm
    (empirically observed at ~1 req/s sustained over 30 pages). Without
    pacing, this test makes 3 back-to-back requests in <50ms; with pacing,
    elapsed time is bounded by (N-1) * gap.
    """
    import time as _time
    from app.modules.ats.connection import ATSConnectionState
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    pages = {
        1: {"count": 3, "num_pages": 3, "page_number": 1, "limit": 1,
            "next": "https://api.ceipal.com/v2/getThings/?page=2",
            "previous": "", "results": [{"id": "a"}]},
        2: {"count": 3, "num_pages": 3, "page_number": 2, "limit": 1,
            "next": "https://api.ceipal.com/v2/getThings/?page=3",
            "previous": "", "results": [{"id": "b"}]},
        3: {"count": 3, "num_pages": 3, "page_number": 3, "limit": 1,
            "next": "", "previous": "", "results": [{"id": "c"}]},
    }

    def handler(request):
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages[page])

    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token="valid", access_token_expires_at=future,
    )
    adapter = CeipalAdapter(state, _transport=httpx.MockTransport(handler))

    # Pacing tight enough to keep the test fast (~100ms total) but
    # bigger than the bare HTTP latency so the assertion is meaningful.
    adapter._min_request_gap_s = 0.05

    start = _time.monotonic()
    ids = []
    async for item in adapter._paginate("/getThings/", {}):
        ids.append(item["id"])
    elapsed = _time.monotonic() - start

    assert ids == ["a", "b", "c"]
    # 3 requests → 2 gaps → at minimum 2 * 0.05s = 0.10s elapsed.
    # First request is free; subsequent waits add up.
    assert elapsed >= 0.10, f"elapsed {elapsed:.3f}s — pacing did not fire"


@pytest.mark.asyncio
async def test_pacing_respects_per_connection_rate_limit_qps():
    """When ATSConnectionState.rate_limit_qps is set, pacing = 1/qps
    overrides the default. Verifies per-tenant tuning (e.g., enterprise
    Ceipal accounts with higher rate limits)."""
    from decimal import Decimal
    from app.modules.ats.connection import ATSConnectionState
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u", "password": "p", "api_key": "k"},
        access_token="t",
        access_token_expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )
    # asyncpg returns Numeric as Decimal; the adapter must coerce to float.
    state.rate_limit_qps = Decimal("4.0")  # 4 req/s → 0.25s gap

    adapter = CeipalAdapter(state, _transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={})
    ))
    assert abs(adapter._min_request_gap_s - 0.25) < 0.001


@pytest.mark.asyncio
async def test_list_jobs_passes_jobStatus_when_filter_set():
    """When job_status_ids is provided, list_jobs forwards a comma-joined
    ``jobStatus`` query param on every page request."""
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getJobPostingsList" in url:
            captured.append(dict(request.url.params))
            return httpx.Response(200, json={
                "count": 0, "num_pages": 1, "page_number": 1, "limit": 50,
                "next": "", "previous": "", "results": [],
            })
        return httpx.Response(404, text="unmocked")

    a = _adapter(handler)
    async for _ in a.list_jobs(job_status_ids=[1, 8]):
        pass
    assert captured, "no list calls made"
    assert captured[0]["jobStatus"] == "1,8"


@pytest.mark.asyncio
async def test_list_jobs_omits_jobStatus_when_filter_none():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "getJobPostingsList" in str(request.url):
            captured.append(dict(request.url.params))
            return httpx.Response(200, json={
                "count": 0, "num_pages": 1, "page_number": 1, "limit": 50,
                "next": "", "previous": "", "results": [],
            })
        return httpx.Response(404, text="unmocked")

    a = _adapter(handler)
    async for _ in a.list_jobs():
        pass
    assert "jobStatus" not in captured[0]
