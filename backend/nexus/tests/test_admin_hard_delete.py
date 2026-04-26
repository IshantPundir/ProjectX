"""Tests for the tenant hard-delete operation."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.admin.service import _purge_auth_users
from app.modules.auth.admin.base import AuthProviderError


@pytest.mark.asyncio
async def test_purge_auth_users_partial_failure(monkeypatch):
    """One success, one failure — both must be reported, neither aborts the other."""
    success_id = "00000000-0000-0000-0000-000000000001"
    failure_id = "00000000-0000-0000-0000-000000000002"

    fake_provider = AsyncMock()

    async def fake_delete_user(uid: str) -> None:
        if uid == failure_id:
            raise AuthProviderError("HTTP 500: simulated supabase outage")
        # success path — return None (provider.delete_user returns None on success)

    fake_provider.delete_user = fake_delete_user

    monkeypatch.setattr(
        "app.modules.admin.service.get_auth_provider",
        lambda: fake_provider,
    )

    purged, failed = await _purge_auth_users([success_id, failure_id])

    assert purged == [success_id]
    assert len(failed) == 1
    assert failed[0][0] == failure_id
    assert "simulated supabase outage" in failed[0][1]
