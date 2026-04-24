"""Lazy singleton factory for the configured AuthProvider."""
from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.modules.auth.admin.base import AuthProvider


@lru_cache(maxsize=1)
def _get_provider_singleton() -> AuthProvider:
    provider_name = settings.auth_provider.lower()
    if provider_name == "supabase":
        from app.modules.auth.admin.supabase import SupabaseAuthProvider

        return SupabaseAuthProvider()
    raise RuntimeError(
        f"Unknown auth_provider: {provider_name!r}. "
        f"Supported: 'supabase'. "
        f"Add a new class satisfying app.modules.auth.admin.AuthProvider "
        f"and wire it here."
    )
