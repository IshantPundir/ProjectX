"""Router-level test for GET /api/reports/session/{id}/proctoring.

Auth pattern mirrors tests/reporting/test_router.py (which in turn mirrors
tests/test_candidates_router.py):
  1. Patch app.middleware.auth.verify_access_token to accept a sentinel bearer.
  2. Override get_current_user_roles to return a pre-built UserContext.
  3. Override get_tenant_db to yield the test's own db session so rows
     flushed in the test are visible to router code.

The /proctoring endpoint shares the same RBAC gate (reports.view) as the
sibling /recording endpoint and the core report endpoints.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.main import app
from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
from app.modules.auth.models import User as UserModel
from app.modules.auth.schemas import TokenPayload
from tests.conftest import create_test_client, create_test_user

_TEST_BEARER = "test-proctoring-endpoint-token"


# ---------------------------------------------------------------------------
# Router registration shim (idempotent — no-op once main.py registers it)
# ---------------------------------------------------------------------------


def _ensure_router_registered() -> None:
    from app.modules.reporting.router import router as reporting_router

    existing = {
        getattr(r, "path_format", "") or getattr(r, "path", "")
        for r in app.routes
    }
    if not any(p.startswith("/api/reports") for p in existing):
        app.include_router(reporting_router)


_ensure_router_registered()


# ---------------------------------------------------------------------------
# Shared helpers (verbatim copy of the pattern from tests/reporting/test_router.py)
# ---------------------------------------------------------------------------


def _user_ctx(
    user: UserModel,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = ("reports.view",),
) -> UserContext:
    assignments: list[RoleAssignment] = []
    if permissions:
        assignments.append(
            RoleAssignment(
                org_unit_id=uuid.uuid4(),
                org_unit_name="Root",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=list(permissions),
            )
        )
    return UserContext(
        user=user,
        is_super_admin=is_super,
        assignments=assignments,
    )


def _setup_test_context(
    db: AsyncSession,
    user: UserModel,
    tenant_id: uuid.UUID,
    *,
    is_super: bool = False,
    permissions: tuple[str, ...] = ("reports.view",),
) -> tuple[dict[str, str], Any]:
    """Install overrides + patch verify_access_token; return (headers, restore_fn)."""
    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )
    ctx = _user_ctx(user, is_super=is_super, permissions=permissions)

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return fake_payload
        return None

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proctoring_endpoint_absent(db: AsyncSession):
    """GET /api/reports/session/{id}/proctoring with no analysis row → 200 absent."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    # A random session_id that has no proctoring row — service returns absent.
    session_id = uuid.uuid4()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/reports/session/{session_id}/proctoring",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "absent"


@pytest.mark.asyncio
async def test_proctoring_endpoint_forbidden_without_reports_view(db: AsyncSession):
    """GET /api/reports/session/{id}/proctoring without reports.view → 403."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    headers, restore = _setup_test_context(
        db, user, tenant.id, permissions=()  # no permissions at all
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/reports/session/{uuid.uuid4()}/proctoring",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 403, resp.text
