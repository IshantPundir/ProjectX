"""CeipalAdapter authentication: createAuthtoken, refreshToken, ensure_authenticated.

Auth-token refresh in Ceipal is unusual: refresh requires the EXPIRED access
token in the Token header, not the refresh token in the body. Tests pin
that contract.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import ATSCredentialsInvalidError, ATSAuthorizationError


def _state(**overrides) -> ATSConnectionState:
    base = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={"email": "u@x.com", "password": "p", "api_key": "k"},
        access_token=None, refresh_token=None,
        access_token_expires_at=None, refresh_token_expires_at=None,
    )
    base.update(overrides)
    return ATSConnectionState(**base)


def _make_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_initial_auth_calls_createAuthtoken_with_credentials():
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
        })

    adapter = CeipalAdapter(_state(), _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert "/v2/createAuthtoken/" in captured["url"]
    assert captured["body"] == {"email": "u@x.com", "password": "p", "apiKey": "k"}
    assert adapter.state.access_token == "fresh-access"
    assert adapter.state.refresh_token == "fresh-refresh"
    assert adapter.state.access_token_expires_at is not None


@pytest.mark.asyncio
async def test_refresh_uses_expired_access_token_in_header():
    """Ceipal's quirk: refreshToken takes the EXPIRED access token in the
    `Token: Bearer <token>` header (not the refresh_token in body)."""
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["token_header"] = request.headers.get("Token")
        return httpx.Response(200, json={
            "access_token": "refreshed-access",
            "expires_in": 3600,
        })

    expired = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    state = _state(
        access_token="old-expired-token",
        access_token_expires_at=expired,
        refresh_token="rfr-tok",
        refresh_token_expires_at=datetime.now(tz=timezone.utc) + timedelta(days=5),
    )
    adapter = CeipalAdapter(state, _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert "/v2/refreshToken/" in captured["url"]
    assert captured["token_header"] == "Bearer old-expired-token"
    assert adapter.state.access_token == "refreshed-access"


@pytest.mark.asyncio
async def test_refresh_expired_falls_back_to_full_reauth():
    """When refresh_token has also expired, the adapter re-auths from
    stored credentials transparently — recruiter sees no disconnection."""
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "createAuthtoken" in str(request.url):
            return httpx.Response(200, json={
                "access_token": "reauth-access",
                "refresh_token": "reauth-refresh",
                "expires_in": 3600,
            })
        return httpx.Response(401, json={"message": "Please provide the access token."})

    expired = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    state = _state(
        access_token="old", access_token_expires_at=expired,
        refresh_token="r", refresh_token_expires_at=expired,
    )
    adapter = CeipalAdapter(state, _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert any("createAuthtoken" in u for u in calls)
    assert adapter.state.access_token == "reauth-access"


@pytest.mark.asyncio
async def test_invalid_credentials_raise_typed_error():
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Please provide the access token."})

    adapter = CeipalAdapter(_state(), _transport=_make_transport(handler))
    with pytest.raises(ATSCredentialsInvalidError):
        await adapter.ensure_authenticated()


@pytest.mark.asyncio
async def test_skip_refresh_when_token_still_valid():
    """Idempotency: calling ensure_authenticated when tokens are valid is a no-op."""
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"access_token": "x", "expires_in": 3600})

    far_future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
    state = _state(access_token="still-good", access_token_expires_at=far_future)
    adapter = CeipalAdapter(state, _transport=_make_transport(handler))
    await adapter.ensure_authenticated()

    assert call_count == 0
    assert adapter.state.access_token == "still-good"
