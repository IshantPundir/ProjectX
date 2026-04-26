"""Tests for the tenant hard-delete operation."""
from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Client
from app.modules.admin.service import (
    ConfirmationMismatchError,
    InvalidClientStateError,
    _purge_auth_users,
    delete_client,
    hard_delete_client,
)
from app.modules.auth.admin import AuthProviderError
from app.modules.auth.schemas import TokenPayload
from tests.conftest import create_test_client


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


@pytest.mark.asyncio
async def test_hard_delete_rejects_active_tenant(db: AsyncSession):
    client = await create_test_client(db)
    with pytest.raises(InvalidClientStateError):
        await hard_delete_client(
            db=db,
            client_id=client.id,
            admin_identity="admin@projectx.com",
            confirmation_name=client.name,
        )


@pytest.mark.asyncio
async def test_hard_delete_rejects_blocked_tenant(db: AsyncSession):
    client = await create_test_client(db)
    client.blocked_at = datetime.now(UTC)
    await db.flush()
    with pytest.raises(InvalidClientStateError):
        await hard_delete_client(
            db=db,
            client_id=client.id,
            admin_identity="admin@projectx.com",
            confirmation_name=client.name,
        )


@pytest.mark.asyncio
async def test_hard_delete_rejects_mismatched_name(db: AsyncSession):
    client = await create_test_client(db)
    await delete_client(
        db=db,
        client_id=client.id,
        admin_identity="admin@projectx.com",
    )
    with pytest.raises(ConfirmationMismatchError):
        await hard_delete_client(
            db=db,
            client_id=client.id,
            admin_identity="admin@projectx.com",
            confirmation_name=client.name + "_typo",
        )


@pytest.mark.asyncio
async def test_hard_delete_purges_users_and_invites_preserves_audit(
    db: AsyncSession,
):
    """Soft-delete then hard-delete; verify users + invites are gone,
    audit_log row for the hard-delete event survives."""
    from app.models import User, UserInvite

    client = await create_test_client(db)
    user = User(
        auth_user_id=uuid_mod.uuid4(),
        tenant_id=client.id,
        email=f"user-{uuid_mod.uuid4()}@example.com",
        full_name="Test User",
    )
    db.add(user)
    invite = UserInvite(
        tenant_id=client.id,
        email=f"pending-{uuid_mod.uuid4()}@example.com",
        token_hash="x" * 64,
        status="pending",
    )
    db.add(invite)
    await db.flush()

    # Step into "deleted" state via the regular soft-delete service.
    await delete_client(
        db=db, client_id=client.id, admin_identity="admin@projectx.com"
    )

    # Hard delete.
    result = await hard_delete_client(
        db=db,
        client_id=client.id,
        admin_identity="admin@projectx.com",
        confirmation_name=client.name,
    )
    assert result["client_id"] == str(client.id)

    # Cascade verification: clients, users, invites all gone for this tenant.
    for table in ("clients", "users", "user_invites"):
        col = "id" if table == "clients" else "tenant_id"
        result_count = await db.execute(
            sqlalchemy.text(
                f"SELECT count(*) FROM public.{table} WHERE {col} = :tid"
            ),
            {"tid": str(client.id)},
        )
        assert result_count.scalar() == 0, f"{table} still has rows for tenant"

    # Audit_log preservation: the hard-delete event row should survive.
    audit_count = await db.execute(
        sqlalchemy.text(
            "SELECT count(*) FROM public.audit_log "
            "WHERE tenant_id = :tid AND action = 'client.hard_deleted'"
        ),
        {"tid": str(client.id)},
    )
    assert audit_count.scalar() == 1


def _admin_token_payload(email: str = "admin@projectx.com") -> TokenPayload:
    return TokenPayload(
        sub=str(uuid_mod.uuid4()),
        tenant_id="",  # ProjectX admins have no tenant
        email=email,
        role="authenticated",
        is_projectx_admin=True,
        exp=2_000_000_000,
    )


@pytest.mark.asyncio
async def test_hard_delete_endpoint_returns_409_when_not_soft_deleted(
    client, db, monkeypatch
):
    """Active tenant must be rejected with 409 InvalidClientStateError."""
    from app.middleware.auth import AuthMiddleware
    from app.database import get_bypass_db

    test_client_obj = await create_test_client(db)
    await db.commit()  # endpoint uses its own session; need this row visible

    # Patch the auth middleware to inject the admin token payload.
    async def _fake_dispatch(self, request, call_next):
        request.state.token_payload = _admin_token_payload()
        request.state.user_id = request.state.token_payload.sub
        request.state.tenant_id = ""
        request.state.is_projectx_admin = True
        return await call_next(request)

    monkeypatch.setattr(AuthMiddleware, "dispatch", _fake_dispatch)

    # Override get_bypass_db to share the test transaction.
    from app.main import app as fastapi_app
    fastapi_app.dependency_overrides[get_bypass_db] = lambda: db

    try:
        resp = await client.post(
            f"/api/admin/clients/{test_client_obj.id}/hard-delete",
            json={"confirmation_name": test_client_obj.name},
        )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert resp.status_code == 409
