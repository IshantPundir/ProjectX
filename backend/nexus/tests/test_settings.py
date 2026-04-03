"""Tests for settings/team endpoints — auth guard tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class TestTeamInvite:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/settings/team/invite",
                json={"email": "test@test.com", "role": "Recruiter"},
            )
        assert resp.status_code == 401


class TestTeamMembers:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/settings/team/members")
        assert resp.status_code == 401


class TestRevokeInvite:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/settings/team/revoke/some-id")
        assert resp.status_code == 401


class TestDeactivateUser:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/settings/team/deactivate/some-id")
        assert resp.status_code == 401
