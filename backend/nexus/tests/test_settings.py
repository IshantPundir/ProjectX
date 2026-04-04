"""Tests for settings/team endpoints — auth guard tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_invite_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/settings/team/invite", json={"email": "a@b.com"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_members_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/settings/team/members")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_deactivate_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/settings/team/deactivate/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 401
