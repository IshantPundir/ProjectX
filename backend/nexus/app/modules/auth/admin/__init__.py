"""Auth provider factory + re-exports.

Business logic imports:

    from app.modules.auth.admin import get_auth_provider, AuthProvider
"""
from __future__ import annotations

from app.modules.auth.admin.base import (
    AuthProvider,
    AuthProviderError,
    InvalidCredentialsError,
    SessionTokens,
    UserAlreadyExistsError,
    UserIdentity,
    UserNotFoundError,
)

__all__ = [
    "AuthProvider",
    "AuthProviderError",
    "InvalidCredentialsError",
    "SessionTokens",
    "UserAlreadyExistsError",
    "UserIdentity",
    "UserNotFoundError",
    "get_auth_provider",
]


def get_auth_provider() -> AuthProvider:
    """Return the configured auth provider singleton.

    Reads `settings.auth_provider` (defaults to "supabase"). Instantiates
    once and caches for process lifetime.
    """
    # Lazy import avoids a circular: supabase.py imports config which may
    # trigger app.* imports that transitively want admin/__init__.
    from app.modules.auth.admin._factory import _get_provider_singleton

    return _get_provider_singleton()
