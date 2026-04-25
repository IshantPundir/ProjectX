"""Tests for SupabaseAuthProvider.sign_out.

Mirrors the httpx-stubbing pattern used by test_auth_admin_supabase.py:
patch.object(httpx.AsyncClient, "post", new=AsyncMock(...)).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.auth.admin.base import (
    AuthProviderError,
    SessionTokens,
)
from app.modules.auth.admin.supabase import SupabaseAuthProvider


def _make_response(
    status_code: int, json_body: dict | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body if json_body is not None else {},
        request=httpx.Request("POST", "http://test"),
    )


def _tokens() -> SessionTokens:
    return SessionTokens(
        access_token="atk-test-12345",
        refresh_token="rtk-test-12345",
        expires_in=3600,
    )


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(
        "app.modules.auth.admin.supabase.settings.supabase_url",
        "http://supabase.local",
    )
    monkeypatch.setattr(
        "app.modules.auth.admin.supabase.settings.supabase_service_role_key",
        "test-service-role-key",
    )
    return SupabaseAuthProvider()


class TestSignOut:
    @pytest.mark.asyncio
    async def test_calls_supabase_logout_with_bearer(self, provider):
        """sign_out POSTs to /auth/v1/logout with the access_token as Bearer."""
        resp = _make_response(204)
        post_mock = AsyncMock(return_value=resp)
        with patch.object(httpx.AsyncClient, "post", new=post_mock):
            await provider.sign_out(_tokens())

        assert post_mock.await_count == 1
        call_args = post_mock.await_args
        # First positional arg = url
        assert call_args.args[0] == "http://supabase.local/auth/v1/logout"
        headers = call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer atk-test-12345"
        # Anon-style apikey header (matches sign_in_with_password)
        assert headers["apikey"] == "test-service-role-key"

    @pytest.mark.asyncio
    async def test_idempotent_on_404(self, provider):
        """A 404 from Supabase means the session is already revoked."""
        resp = _make_response(404, {"msg": "not found"})
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
        ):
            # Must not raise.
            await provider.sign_out(_tokens())

    @pytest.mark.asyncio
    async def test_raises_on_other_errors(self, provider):
        """Any non-2xx-non-404 raises AuthProviderError."""
        resp = _make_response(500, {"msg": "internal error"})
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
        ):
            with pytest.raises(AuthProviderError):
                await provider.sign_out(_tokens())

    @pytest.mark.asyncio
    async def test_skipped_when_unconfigured(self, monkeypatch):
        """Missing supabase_url or service_role_key: log + return; no HTTP."""
        monkeypatch.setattr(
            "app.modules.auth.admin.supabase.settings.supabase_url", ""
        )
        monkeypatch.setattr(
            "app.modules.auth.admin.supabase.settings.supabase_service_role_key",
            "",
        )
        provider = SupabaseAuthProvider()
        post_mock = AsyncMock()
        with patch.object(httpx.AsyncClient, "post", new=post_mock):
            await provider.sign_out(_tokens())
        # No HTTP call attempted.
        assert post_mock.await_count == 0
