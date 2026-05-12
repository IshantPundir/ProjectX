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
