"""Provider-agnostic auth admin boundary.

Business logic imports only this module (or `app.modules.auth.admin`).
Swapping Supabase for Cognito/Keycloak is a single-file change: add a
new concrete provider class that satisfies the `AuthProvider` protocol,
switch `settings.auth_provider`.

Every method here is async. Errors that callers must handle raise typed
exceptions; transport errors bubble as `AuthProviderError`.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class UserIdentity(BaseModel):
    """A provider-created auth principal. `id` is opaque to business logic."""

    id: str
    email: str


class SessionTokens(BaseModel):
    """Tokens returned by `sign_in_with_password`. Provider-agnostic shape.

    `access_token` — short-lived JWT for API calls.
    `refresh_token` — long-lived token the browser client uses to rotate.
    `expires_in` — seconds until `access_token` expires.
    """

    access_token: str
    refresh_token: str
    expires_in: int


class AuthProviderError(Exception):
    """Base class for provider transport/protocol errors."""


class UserAlreadyExistsError(AuthProviderError):
    """Raised by `create_user` when the email is already registered."""


class UserNotFoundError(AuthProviderError):
    """Raised by `update_user_password` / `delete_user` / `find_user_by_email`
    when the target user does not exist."""


class InvalidCredentialsError(AuthProviderError):
    """Raised by `sign_in_with_password` on bad password."""


class AuthProvider(Protocol):
    """Minimal surface every auth provider must satisfy.

    Methods are narrowly scoped and map cleanly to Supabase / Cognito /
    Keycloak admin APIs. Do not add provider-specific concepts here; if
    a feature doesn't port across at least two providers, keep it in the
    concrete class as a private helper.
    """

    async def create_user(self, email: str, password: str) -> UserIdentity:
        """Create a new auth user. Raises `UserAlreadyExistsError` if
        the email is already registered."""
        ...

    async def find_user_by_email(self, email: str) -> UserIdentity | None:
        """Return the matching user, or None if not found. Used for the
        "already exists" fallback in the invite flow."""
        ...

    async def update_user_password(self, user_id: str, password: str) -> None:
        """Set a new password for an existing user. Raises
        `UserNotFoundError` if the user does not exist."""
        ...

    async def sign_in_with_password(
        self, email: str, password: str
    ) -> SessionTokens:
        """Exchange credentials for session tokens. Raises
        `InvalidCredentialsError` on wrong password, `UserNotFoundError`
        if no such user."""
        ...

    async def delete_user(self, user_id: str) -> None:
        """Delete an auth user. Idempotent — returns normally if the user
        is already absent. Raises `AuthProviderError` on transport failure."""
        ...
