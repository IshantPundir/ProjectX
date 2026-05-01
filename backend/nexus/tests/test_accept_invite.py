"""Integration tests for POST /api/auth/accept-invite.

AuthProvider is mocked via a fake that records calls. The DB is real
(via the standard `db` fixture + get_bypass_db override) so the handler's
DB writes and compensation paths are exercised end-to-end.

Five paths covered:
  1. Happy path — new super-admin user, new auth user, tokens returned.
  2. Already-existing auth user fallback — update_user_password, no delete.
  3. Bad token (not in DB) → 401.
  4. Expired token → 401.
  5. DB failure after auth user created → compensation delete fires.
  6. Provider hard failure (AuthProviderError on create_user) → 502.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid as uuid_mod
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.main import app
from app.modules.auth.models import (
    User,
    UserInvite,
)
from app.modules.org_units.models import (
    Client,
    OrganizationalUnit,
)
from app.modules.auth.admin.base import (
    AuthProviderError,
    SessionTokens,
    UserAlreadyExistsError,
    UserIdentity,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_client_and_invite(
    db: AsyncSession,
    *,
    email: str,
    expires_in_hours: float = 48,
    is_super_admin: bool = False,
) -> tuple[uuid_mod.UUID, str]:
    """Seed a Client + pending UserInvite; return (tenant_id, raw_token).

    - invited_by is nullable (see models.py) — we pass None.
    - projectx_admin_id is a Text column (not UUID FK) — a non-None string
      triggers the super-admin path inside the handler.
    """
    client_obj = Client(name="Test Co", domain="testco.com")
    db.add(client_obj)
    await db.flush()
    tenant_id: uuid_mod.UUID = client_obj.id

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    invite = UserInvite(
        tenant_id=tenant_id,
        email=email,
        token_hash=token_hash,
        status="pending",
        invited_by=None,
        projectx_admin_id=str(uuid_mod.uuid4()) if is_super_admin else None,
    )
    db.add(invite)
    await db.flush()

    # expires_in_hours < 0 → already expired; adjust expires_at directly.
    if expires_in_hours < 0:
        from sqlalchemy import text

        await db.execute(
            text(
                "UPDATE user_invites SET expires_at = NOW() - INTERVAL '1 hour' "
                "WHERE id = :id"
            ),
            {"id": str(invite.id)},
        )
        await db.flush()

    return tenant_id, raw_token


def _make_db_override(db: AsyncSession):
    """Return an async generator function that yields the test session.

    This is installed as app.dependency_overrides[get_bypass_db] so the
    handler reads from and writes to the same rolled-back test connection.
    """

    async def _override():
        yield db

    return _override


class _FakeProvider:
    """In-memory stand-in for SupabaseAuthProvider.

    Records every call so tests can assert on call sequence and detect
    unexpected compensation (delete_user) calls.
    """

    def __init__(
        self,
        *,
        existing_users: dict[str, UserIdentity] | None = None,
        sign_in_tokens: SessionTokens | None = None,
    ) -> None:
        self.existing_users: dict[str, UserIdentity] = existing_users or {}
        self.sign_in_tokens: SessionTokens = sign_in_tokens or SessionTokens(
            access_token="at-new", refresh_token="rt-new", expires_in=3600
        )
        self.calls: list[tuple[str, tuple]] = []

    async def create_user(self, email: str, password: str) -> UserIdentity:
        self.calls.append(("create_user", (email, password)))
        if email in self.existing_users:
            raise UserAlreadyExistsError(email)
        return UserIdentity(id=str(uuid_mod.uuid4()), email=email)

    async def find_user_by_email(self, email: str) -> UserIdentity | None:
        self.calls.append(("find_user_by_email", (email,)))
        return self.existing_users.get(email)

    async def update_user_password(self, user_id: str, password: str) -> None:
        self.calls.append(("update_user_password", (user_id, password)))

    async def sign_in_with_password(self, email: str, password: str) -> SessionTokens:
        self.calls.append(("sign_in_with_password", (email, password)))
        return self.sign_in_tokens

    async def delete_user(self, user_id: str) -> None:
        self.calls.append(("delete_user", (user_id,)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_invite_happy_path_creates_super_admin(db: AsyncSession):
    """Full super-admin invite flow: new auth user, tokens returned, invite
    accepted in DB, root org unit created, clients.super_admin_id set."""
    tenant_id, raw_token = await _seed_client_and_invite(
        db, email="new@co.com", is_super_admin=True
    )
    provider = _FakeProvider()

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/auth/accept-invite",
                    json={"raw_token": raw_token, "password": "hunter2hunter2"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] == "at-new"
    assert body["refresh_token"] == "rt-new"
    assert body["expires_in"] == 3600
    assert body["redirect_to"] == "/onboarding"

    # Provider call ordering: create_user → sign_in_with_password.
    # No update_user_password (new user). No delete_user (no compensation).
    methods = [c[0] for c in provider.calls]
    assert methods == ["create_user", "sign_in_with_password"], methods

    # DB state: invite accepted.
    invite_row = (
        await db.execute(
            select(UserInvite).where(UserInvite.tenant_id == tenant_id)
        )
    ).scalar_one()
    assert invite_row.status == "accepted"

    # User row created.
    user_row = (
        await db.execute(select(User).where(User.tenant_id == tenant_id))
    ).scalar_one()
    assert user_row.email == "new@co.com"

    # clients.super_admin_id wired up.
    client_row = (
        await db.execute(select(Client).where(Client.id == tenant_id))
    ).scalar_one()
    assert client_row.super_admin_id == user_row.id

    # Root org unit takes its name from clients.name — not a hardcoded
    # "Company". Without this, the user lands on the company settings page
    # with the wrong name pre-filled in every form.
    root_unit = (
        await db.execute(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == tenant_id,
                OrganizationalUnit.is_root.is_(True),
            )
        )
    ).scalar_one()
    assert root_unit.name == client_row.name
    # Admin-provisioned `clients.domain` seeds the unit's metadata.website
    # so the form doesn't show an empty Website field for a value that was
    # already collected at provisioning time.
    assert root_unit.unit_metadata == {"website": client_row.domain}


@pytest.mark.asyncio
async def test_accept_invite_already_existing_auth_user_updates_password(db: AsyncSession):
    """Fallback path: UserAlreadyExistsError → find_user_by_email →
    update_user_password → sign_in.

    CRITICAL: delete_user must NOT be called — this path reuses a
    pre-existing auth user; deleting it would break the legitimate
    owner's session. This is the race-condition bug that was fixed in
    Cluster B.
    """
    _, raw_token = await _seed_client_and_invite(
        db, email="existing@co.com"
    )
    existing_id = str(uuid_mod.uuid4())
    provider = _FakeProvider(
        existing_users={
            "existing@co.com": UserIdentity(id=existing_id, email="existing@co.com")
        }
    )

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/auth/accept-invite",
                    json={"raw_token": raw_token, "password": "hunter2hunter2"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 200, resp.text

    methods = [c[0] for c in provider.calls]
    assert methods == [
        "create_user",
        "find_user_by_email",
        "update_user_password",
        "sign_in_with_password",
    ], methods

    # CRITICAL: must never compensation-delete a pre-existing auth user.
    assert not any(c[0] == "delete_user" for c in provider.calls), (
        "delete_user must not be called when the auth user was pre-existing"
    )


@pytest.mark.asyncio
async def test_accept_invite_bad_token_returns_401():
    """Token not present in DB → 401 before any provider call."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/api/auth/accept-invite",
            json={"raw_token": "does-not-exist-at-all", "password": "hunter2hunter2"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired invite"


@pytest.mark.asyncio
async def test_accept_invite_expired_token_returns_401(db: AsyncSession):
    """Token exists but has already expired → 401."""
    _, raw_token = await _seed_client_and_invite(
        db,
        email="expired@co.com",
        expires_in_hours=-1,  # one hour in the past
    )

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/auth/accept-invite",
                json={"raw_token": raw_token, "password": "hunter2hunter2"},
            )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired invite"


@pytest.mark.asyncio
async def test_accept_invite_db_failure_triggers_compensation(db: AsyncSession):
    """Force root-unit creation to raise after auth user is provisioned.

    Compensation MUST call delete_user because the request itself created
    the auth user (auth_user_created_here=True). This contrasts with the
    already-existing-user path (test 2) where compensation is skipped.
    """
    _, raw_token = await _seed_client_and_invite(
        db, email="db-fail@co.com", is_super_admin=True
    )
    provider = _FakeProvider()

    async def _raise(*args, **kwargs):
        raise RuntimeError("simulated DB failure in create_org_unit")

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ), patch(
            "app.modules.org_units.create_org_unit",
            new=AsyncMock(side_effect=_raise),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/auth/accept-invite",
                    json={"raw_token": raw_token, "password": "hunter2hunter2"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 500

    # Compensation must have fired because this was a newly-created user.
    assert any(c[0] == "delete_user" for c in provider.calls), (
        "Compensation delete_user must be called when a newly-created "
        "auth user is orphaned by a DB failure"
    )


@pytest.mark.asyncio
async def test_accept_invite_provider_exhaustion_propagates_502(db: AsyncSession):
    """When create_user raises AuthProviderError (not UserAlreadyExistsError)
    the handler returns 502 immediately. No DB writes, no compensation delete.
    """
    _, raw_token = await _seed_client_and_invite(
        db, email="provider-err@co.com"
    )

    class _FailingProvider(_FakeProvider):
        async def create_user(self, email: str, password: str) -> UserIdentity:
            self.calls.append(("create_user", (email, password)))
            raise AuthProviderError("upstream 503")

    provider = _FailingProvider()

    app.dependency_overrides[get_bypass_db] = _make_db_override(db)
    try:
        with patch(
            "app.modules.auth.admin.get_auth_provider", return_value=provider
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/auth/accept-invite",
                    json={"raw_token": raw_token, "password": "hunter2hunter2"},
                )
    finally:
        app.dependency_overrides.pop(get_bypass_db, None)

    assert resp.status_code == 502

    # Provider hard-errored before any user was created — no delete expected.
    assert not any(c[0] == "delete_user" for c in provider.calls)
