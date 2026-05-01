"""Auth module — provider-agnostic JWT verification + RBAC context.

Public surface for cross-module callers.
"""
from app.modules.auth.admin import AuthProviderError, get_auth_provider
from app.modules.auth.context import (
    UserContext,
    get_current_user_roles,
    require_super_admin,
)
from app.modules.auth.errors import AccountSuspendedError, suspended_response
from app.modules.auth.lifecycle import load_tenant_status
from app.modules.auth.models import User, UserInvite, UserRoleAssignment
from app.modules.auth.schemas import TokenPayload
from app.modules.auth.service import (
    create_candidate_token,
    require_projectx_admin,
    verify_access_token,
    verify_candidate_token,
)

__all__ = [
    "AccountSuspendedError",
    "AuthProviderError",
    "TokenPayload",
    "User",
    "UserContext",
    "UserInvite",
    "UserRoleAssignment",
    "create_candidate_token",
    "get_auth_provider",
    "get_current_user_roles",
    "load_tenant_status",
    "require_projectx_admin",
    "require_super_admin",
    "suspended_response",
    "verify_access_token",
    "verify_candidate_token",
]
