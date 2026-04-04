"""Tests for org units endpoints — auth guard tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_create_org_unit_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/org-units", json={"name": "Test", "unit_type": "team"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_org_units_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/org-units")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_roles_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/roles")
    assert resp.status_code == 401
