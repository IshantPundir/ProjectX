"""Unit tests for SupabaseAuthProvider. httpx is mocked — no network calls."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.modules.auth.admin.base import (
    InvalidCredentialsError,
    SessionTokens,
    UserAlreadyExistsError,
    UserIdentity,
    UserNotFoundError,
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


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_happy_path_returns_identity(self, provider):
        resp = _make_response(
            200,
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "email": "new@example.com",
            },
        )
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
        ):
            identity = await provider.create_user(
                "new@example.com", "hunter2hunter2"
            )
        assert identity == UserIdentity(
            id="11111111-1111-1111-1111-111111111111",
            email="new@example.com",
        )

    @pytest.mark.asyncio
    async def test_email_exists_raises_user_already_exists(self, provider):
        resp = _make_response(
            422,
            {
                "code": "email_exists",
                "msg": "A user with this email already exists",
            },
        )
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
        ):
            with pytest.raises(UserAlreadyExistsError):
                await provider.create_user(
                    "existing@example.com", "hunter2hunter2"
                )

    @pytest.mark.asyncio
    async def test_transport_error_bubbles(self, provider):
        with patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(side_effect=httpx.ConnectError("boom")),
        ):
            with pytest.raises(httpx.ConnectError):
                await provider.create_user("x@example.com", "hunter2hunter2")


class TestFindUserByEmail:
    @pytest.mark.asyncio
    async def test_returns_identity_when_found(self, provider):
        resp = _make_response(
            200,
            {
                "users": [
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "email": "found@example.com",
                    }
                ]
            },
        )
        with patch.object(
            httpx.AsyncClient, "get", new=AsyncMock(return_value=resp)
        ):
            identity = await provider.find_user_by_email("found@example.com")
        assert identity == UserIdentity(
            id="22222222-2222-2222-2222-222222222222",
            email="found@example.com",
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, provider):
        resp = _make_response(200, {"users": []})
        with patch.object(
            httpx.AsyncClient, "get", new=AsyncMock(return_value=resp)
        ):
            identity = await provider.find_user_by_email("nope@example.com")
        assert identity is None


class TestUpdateUserPassword:
    @pytest.mark.asyncio
    async def test_happy_path_returns_none(self, provider):
        resp = _make_response(200, {"id": "33333333-3333-3333-3333-333333333333"})
        with patch.object(
            httpx.AsyncClient, "put", new=AsyncMock(return_value=resp)
        ):
            await provider.update_user_password(
                "33333333-3333-3333-3333-333333333333", "newpass123"
            )

    @pytest.mark.asyncio
    async def test_missing_user_raises_not_found(self, provider):
        resp = _make_response(404, {"msg": "user not found"})
        with patch.object(
            httpx.AsyncClient, "put", new=AsyncMock(return_value=resp)
        ):
            with pytest.raises(UserNotFoundError):
                await provider.update_user_password("missing-id", "newpass123")


class TestSignInWithPassword:
    @pytest.mark.asyncio
    async def test_happy_path_returns_session_tokens(self, provider):
        resp = _make_response(
            200,
            {
                "access_token": "at-123",
                "refresh_token": "rt-456",
                "expires_in": 3600,
            },
        )
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
        ):
            tokens = await provider.sign_in_with_password(
                "user@example.com", "hunter2hunter2"
            )
        assert tokens == SessionTokens(
            access_token="at-123",
            refresh_token="rt-456",
            expires_in=3600,
        )

    @pytest.mark.asyncio
    async def test_bad_credentials_raises_invalid_credentials(self, provider):
        resp = _make_response(
            400, {"error": "invalid_grant", "error_description": "Invalid login"}
        )
        with patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=resp)
        ):
            with pytest.raises(InvalidCredentialsError):
                await provider.sign_in_with_password(
                    "user@example.com", "wrong"
                )


class TestDeleteUser:
    @pytest.mark.asyncio
    async def test_happy_path_returns_none(self, provider):
        resp = _make_response(204)
        with patch.object(
            httpx.AsyncClient, "delete", new=AsyncMock(return_value=resp)
        ):
            await provider.delete_user("some-id")

    @pytest.mark.asyncio
    async def test_missing_user_is_idempotent(self, provider):
        """404 on delete is treated as idempotent success."""
        resp = _make_response(404)
        with patch.object(
            httpx.AsyncClient, "delete", new=AsyncMock(return_value=resp)
        ):
            # Must not raise
            await provider.delete_user("already-gone")
