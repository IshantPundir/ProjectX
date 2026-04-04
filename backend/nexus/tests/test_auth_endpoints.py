"""Tests for auth endpoints — error paths that don't require a live database."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_verify_invite_missing_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/verify-invite")
    assert resp.status_code == 422  # missing required query param


@pytest.mark.asyncio
async def test_complete_invite_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/complete-invite", json={"raw_token": "abc"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_onboarding_complete_no_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/onboarding/complete")
    assert resp.status_code == 401
