"""Integration tests for POST /api/auth/login.

The AuthProvider is overridden by monkey-patching `get_auth_provider` in
`app.modules.auth.admin` so each test controls exactly what the provider
returns — happy path, InvalidCredentialsError, UserNotFoundError, etc.

Test strategy note:
  Rather than mint real ES256-signed tokens, we patch
  `app.modules.auth.router.verify_access_token` directly. The handler
  only consumes `payload.tenant_id` from the decoded token, so any
  `TokenPayload` with the desired tenant_id is sufficient — and this
  keeps the tests focused on the handler's branching rather than the
  provider-independent JWT plumbing (already covered by
  test_auth_service.py).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.main import app
from app.models import Client, User
from app.modules.auth.admin.base import (
    AuthProviderError,
    InvalidCredentialsError,
    SessionTokens,
    UserIdentity,
    UserNotFoundError,
)
from app.modules.auth.schemas import TokenPayload

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class SeededUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    access_token_stub: str


def _make_db_override(db: AsyncSession):
    """Install the test session as the get_bypass_db dependency."""

    async def _override():
        yield db

    return _override


class _FakeProvider:
    """Minimal AuthProvider stand-in. Each test swaps the method it
    cares about via kwargs to construct."""

    def __init__(
        self,
        *,
        sign_in_side_effect: Exception | None = None,
        sign_in_return: SessionTokens | None = None,
        sign_out_side_effect: Exception | None = None,
    ) -> None:
        self._sign_in_side_effect = sign_in_side_effect
        self._sign_in_return = sign_in_return
        self._sign_out_side_effect = sign_out_side_effect
        self.calls: list[tuple[str, tuple]] = []
        self.sign_out_calls: list[SessionTokens] = []

    async def sign_in_with_password(
        self, email: str, password: str
    ) -> SessionTokens:
        self.calls.append(("sign_in_with_password", (email, password)))
        if self._sign_in_side_effect is not None:
            raise self._sign_in_side_effect
        assert self._sign_in_return is not None
        return self._sign_in_return

    async def sign_out(self, tokens: SessionTokens) -> None:
        self.sign_out_calls.append(tokens)
        if self._sign_out_side_effect is not None:
            raise self._sign_out_side_effect

    # Unused in these tests but present so the provider satisfies the
    # Protocol in case the handler grows to call other methods.
    async def create_user(self, email: str, password: str) -> UserIdentity:
        raise NotImplementedError

    async def find_user_by_email(self, email: str) -> UserIdentity | None:
        return None

    async def update_user_password(self, user_id: str, password: str) -> None:
        raise NotImplementedError

    async def delete_user(self, user_id: str) -> None:
        raise NotImplementedError


async def _seed_active_user(
    db: AsyncSession,
    *,
    is_active: bool = True,
    is_super_admin: bool = True,
    onboarding_complete: bool = False,
) -> SeededUser:
    """Seed a Client + User row. If `is_super_admin`, wire
    `clients.super_admin_id` to the new user so the handler's
    `is_super_admin` check returns True."""
    client_obj = Client(
        name="Login Test Co",
        workspace_mode="enterprise",
        onboarding_complete=onboarding_complete,
    )
    db.add(client_obj)
    await db.flush()

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        auth_user_id=uuid.uuid4(),
        tenant_id=client_obj.id,
        email=email,
        is_active=is_active,
    )
    db.add(user)
    await db.flush()

    if is_super_admin:
        client_obj.super_admin_id = user.id
        await db.flush()

    # access_token_stub — a sentinel string. The handler pipes it into
    # `verify_access_token`, which we monkey-patch per-test to return a
    # TokenPayload with the tenant_id we want. So the string itself is
    # not parsed by anything real.
    return SeededUser(
        user_id=user.id,
        tenant_id=client_obj.id,
        email=email,
        access_token_stub=f"stub-token-{user.id}",
    )


def _patch_verify_token(tenant_id: str | None):
    """Patch `verify_access_token` as imported into the router module to
    return a TokenPayload with the given tenant_id, or None to simulate
    verification failure."""

    def _fake(token: str):
        if tenant_id is None:
            return None
        return TokenPayload(
            sub=str(uuid.uuid4()),
            tenant_id=tenant_id,
            email="ignored@example.com",
            exp=0,
        )

    return patch("app.modules.auth.router.verify_access_token", new=_fake)


# ---------------------------------------------------------------------------
# 422 — Pydantic validation (no provider interaction)
# ---------------------------------------------------------------------------


async def test_login_rejects_malformed_email() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "not-an-email", "password": "hunter2hunter2"},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], list)
    assert any(err["loc"][-1] == "email" for err in body["detail"])


async def test_login_rejects_missing_fields() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/auth/login", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 401 — invalid credentials / unknown user (no user enumeration)
# ---------------------------------------------------------------------------


async def test_login_invalid_credentials_returns_401_generic_message() -> None:
    provider = _FakeProvider(
        sign_in_side_effect=InvalidCredentialsError("bad pw"),
    )
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "user@example.com", "password": "wrongpass"},
            )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


async def test_login_user_not_found_returns_401_same_generic_message() -> None:
    provider = _FakeProvider(
        sign_in_side_effect=UserNotFoundError("no user"),
    )
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "nobody@example.com", "password": "whatever"},
            )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


# ---------------------------------------------------------------------------
# Happy paths — 200 with tokens + redirect_to
# ---------------------------------------------------------------------------


async def test_login_happy_path_returns_tokens_and_redirect(
    db: AsyncSession,
) -> None:
    """Super admin, onboarding NOT complete → /onboarding."""
    user = await _seed_active_user(
        db, is_active=True, is_super_admin=True, onboarding_complete=False
    )
    provider = _FakeProvider(
        sign_in_return=SessionTokens(
            access_token=user.access_token_stub,
            refresh_token="refresh-abc",
            expires_in=3600,
        ),
    )

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ), _patch_verify_token(str(user.tenant_id)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/auth/login",
                    json={"email": user.email, "password": "correctpass"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] == user.access_token_stub
    assert body["refresh_token"] == "refresh-abc"
    assert body["expires_in"] == 3600
    assert body["redirect_to"] == "/onboarding"


async def test_login_completed_onboarding_redirects_to_root(
    db: AsyncSession,
) -> None:
    user = await _seed_active_user(
        db, is_active=True, is_super_admin=True, onboarding_complete=True
    )
    provider = _FakeProvider(
        sign_in_return=SessionTokens(
            access_token=user.access_token_stub,
            refresh_token="r",
            expires_in=3600,
        ),
    )

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ), _patch_verify_token(str(user.tenant_id)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/auth/login",
                    json={"email": user.email, "password": "x"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 200, resp.text
    assert resp.json()["redirect_to"] == "/"


# ---------------------------------------------------------------------------
# 403 — deactivated accounts / missing tenant_id
# ---------------------------------------------------------------------------


async def test_login_deactivated_account_returns_403(
    db: AsyncSession,
) -> None:
    user = await _seed_active_user(db, is_active=False, is_super_admin=False)
    provider = _FakeProvider(
        sign_in_return=SessionTokens(
            access_token=user.access_token_stub,
            refresh_token="r",
            expires_in=3600,
        ),
    )

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ), _patch_verify_token(str(user.tenant_id)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/auth/login",
                    json={"email": user.email, "password": "x"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 403
    assert "deactivated" in resp.json()["detail"].lower()


async def test_login_missing_tenant_id_returns_403() -> None:
    """A token whose payload lacks tenant_id — e.g. a ProjectX-admin-only
    account. The handler must 403 before any DB lookup, so no DB seed
    is needed here."""
    provider = _FakeProvider(
        sign_in_return=SessionTokens(
            access_token="token.without.tenant",
            refresh_token="r",
            expires_in=3600,
        ),
    )
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ), _patch_verify_token(""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "admin@projectx.example", "password": "x"},
            )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Token revocation on auth-failure branches (C7)
#
# Each of the 4 minted-token branches must:
#   1. Call AuthProvider.sign_out with the tokens just minted
#   2. Still return the original auth-failure status (revocation tolerant)
#   3. Not unbreak the auth failure if sign_out itself raises
# ---------------------------------------------------------------------------


async def test_login_revokes_tokens_on_token_verify_failure() -> None:
    """Branch 1: provider returns tokens, verify_access_token returns None.
    Handler must sign_out before raising 401."""
    minted = SessionTokens(
        access_token="atk-verify-fail",
        refresh_token="rtk-verify-fail",
        expires_in=3600,
    )
    provider = _FakeProvider(sign_in_return=minted)
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ), _patch_verify_token(None):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "user@example.com", "password": "x"},
            )
    assert resp.status_code == 401
    assert len(provider.sign_out_calls) == 1
    assert provider.sign_out_calls[0] == minted


async def test_login_revokes_tokens_on_missing_tenant_id() -> None:
    """Branch 2: tenant_id empty → sign_out then 403."""
    minted = SessionTokens(
        access_token="atk-no-tenant",
        refresh_token="rtk-no-tenant",
        expires_in=3600,
    )
    provider = _FakeProvider(sign_in_return=minted)
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ), _patch_verify_token(""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "admin@projectx.example", "password": "x"},
            )
    assert resp.status_code == 403
    assert len(provider.sign_out_calls) == 1
    assert provider.sign_out_calls[0] == minted


async def test_login_revokes_tokens_on_no_app_user(db: AsyncSession) -> None:
    """Branch 3: token decodes to a tenant_id but no users row exists for
    that email. sign_out then 403."""
    # Seed a tenant only — no User row for the email we'll send.
    client_obj = Client(
        name="No User Co",
        workspace_mode="enterprise",
        onboarding_complete=False,
    )
    db.add(client_obj)
    await db.flush()

    minted = SessionTokens(
        access_token="atk-no-user",
        refresh_token="rtk-no-user",
        expires_in=3600,
    )
    provider = _FakeProvider(sign_in_return=minted)

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ), _patch_verify_token(str(client_obj.id)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http_client:
                resp = await http_client.post(
                    "/api/auth/login",
                    json={
                        "email": "ghost@example.com",
                        "password": "x",
                    },
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 403
    assert len(provider.sign_out_calls) == 1
    assert provider.sign_out_calls[0] == minted


async def test_login_revokes_tokens_on_deactivated_user(
    db: AsyncSession,
) -> None:
    """Branch 4: deactivated user → sign_out then 403."""
    user = await _seed_active_user(
        db, is_active=False, is_super_admin=False
    )
    minted = SessionTokens(
        access_token="atk-deactivated",
        refresh_token="rtk-deactivated",
        expires_in=3600,
    )
    provider = _FakeProvider(sign_in_return=minted)

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ), _patch_verify_token(str(user.tenant_id)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http_client:
                resp = await http_client.post(
                    "/api/auth/login",
                    json={"email": user.email, "password": "x"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 403
    assert "deactivated" in resp.json()["detail"].lower()
    assert len(provider.sign_out_calls) == 1
    assert provider.sign_out_calls[0] == minted


async def test_login_invalid_credentials_does_not_call_sign_out() -> None:
    """The InvalidCredentialsError branch raises BEFORE tokens are minted.
    sign_out must NOT be called — there is nothing to revoke."""
    provider = _FakeProvider(
        sign_in_side_effect=InvalidCredentialsError("bad pw"),
    )
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "user@example.com", "password": "wrongpass"},
            )
    assert resp.status_code == 401
    assert provider.sign_out_calls == []


async def test_login_revoke_failure_does_not_unbreak_auth_failure() -> None:
    """If sign_out itself raises AuthProviderError, the original
    auth-failure response status MUST stand. Revocation errors are
    logged but never propagated to the caller."""
    minted = SessionTokens(
        access_token="atk-revoke-fail",
        refresh_token="rtk-revoke-fail",
        expires_in=3600,
    )
    provider = _FakeProvider(
        sign_in_return=minted,
        sign_out_side_effect=AuthProviderError("revocation transport error"),
    )
    with patch(
        "app.modules.auth.admin.get_auth_provider", return_value=provider
    ), _patch_verify_token(""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/auth/login",
                json={"email": "admin@projectx.example", "password": "x"},
            )
    # Revocation failure must not change the original 403.
    assert resp.status_code == 403
    # But sign_out WAS attempted.
    assert len(provider.sign_out_calls) == 1


# ---------------------------------------------------------------------------
# Password length validation (Field(min_length=1, max_length=128))
# ---------------------------------------------------------------------------


async def test_login_rejects_empty_password() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": ""},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert any(err["loc"][-1] == "password" for err in body["detail"])


async def test_login_rejects_oversized_password() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": "user@example.com",
                "password": "x" * 129,
            },
        )
    assert resp.status_code == 422
    body = resp.json()
    assert any(err["loc"][-1] == "password" for err in body["detail"])
