"""Authorization guards for /api/ats/* routes.

ATS connection management is a high-privilege operation (credential storage,
sync trigger). Restricted to super_admin per spec.

A future ``ats_admin`` permission can be added to roles/permissions.py to
allow Recruiting Operations to manage ATS without granting full super_admin.
For MVP, we delegate entirely to the super-admin check.

Exposed as a plain coroutine FastAPI dependency (callable directly via
``Depends(require_ats_admin)``) — distinct from the
``require_super_admin()`` factory in ``auth.context`` so route handlers can
extract the ``UserContext`` from the same dependency that gated access.
Tests can override this via ``app.dependency_overrides[require_ats_admin]``.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.modules.auth.context import UserContext, get_current_user_roles


async def require_ats_admin(
    ctx: UserContext = Depends(get_current_user_roles),
) -> UserContext:
    """Reject non-super-admins. Returns the UserContext on success."""
    if not ctx.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin required")
    return ctx
