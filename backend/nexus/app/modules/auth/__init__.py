"""Auth module — provider-agnostic JWT verification + RBAC context.

Public surface for cross-module callers.

NOTE: service/context/admin function exports are DEFERRED to Stage E.2
(sub-commit 4d-2). They cannot be eagerly imported here while the
transitional ``app/models.py`` shim is still in place — the import chain
auth/__init__ → auth.context → app.models → ... → auth.* deadlocks at
"partially initialized module 'app.models'". Removing the shim in 4d-2
breaks the cycle and lets us add the missing exports in the same commit.
"""
from app.modules.auth.models import User, UserInvite, UserRoleAssignment
from app.modules.auth.schemas import TokenPayload

__all__ = [
    "TokenPayload",
    "User",
    "UserInvite",
    "UserRoleAssignment",
]
