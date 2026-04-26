"""Tenant lifecycle gate — the single source of truth for whether a tenant
is allowed to be served.

`load_tenant_status` opens its own bypass-RLS DB session, runs a primary-key
lookup on `clients`, and returns `(blocked_at, deleted_at)`. Callers turn
that into either an `AccountSuspendedError` raise (FastAPI route / dependency
context) or a direct `suspended_response` return (middleware context, where
exceptions don't dispatch through @application.exception_handler).

Why a dedicated helper rather than inlining the query: this is the single
audit point for "what does suspension mean?" — defining it once means future
changes (e.g. adding a third state like `read_only`) don't require finding
every check across the codebase.
"""

from __future__ import annotations

from typing import Literal

import sqlalchemy

from app.database import get_bypass_session
from app.modules.auth.errors import AccountSuspendedError

TenantStatus = Literal["active", "blocked", "deleted"]


async def load_tenant_status(tenant_id: str) -> TenantStatus:
    """Fetch the tenant's lifecycle state. Returns 'active' if the tenant
    row is missing — that case is handled separately by callers (login
    rejects unknown users; middleware-time misses are typically a bug, not
    an attack, but the safe default of returning 'active' lets the rest of
    the request flow surface the real 404/403)."""
    async with get_bypass_session() as db:
        row = (
            await db.execute(
                sqlalchemy.text(
                    "SELECT blocked_at, deleted_at FROM public.clients "
                    "WHERE id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
        ).first()

    if row is None:
        return "active"
    blocked_at, deleted_at = row
    if deleted_at is not None:
        return "deleted"
    if blocked_at is not None:
        return "blocked"
    return "active"


async def assert_tenant_active(tenant_id: str) -> None:
    """Raise AccountSuspendedError if the tenant is blocked or deleted.

    Use from FastAPI route handlers and dependencies (where raised
    exceptions dispatch through `@application.exception_handler`).
    Middleware should call `load_tenant_status` directly and return
    `suspended_response(...)` instead — see `app/middleware/auth.py`.
    """
    status = await load_tenant_status(tenant_id)
    if status != "active":
        raise AccountSuspendedError(status=status)
