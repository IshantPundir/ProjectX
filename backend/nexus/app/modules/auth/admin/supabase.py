"""Supabase concrete implementation of AuthProvider.

Talks directly to Supabase's GoTrue REST API via httpx — no SDK. Same
transport pattern used elsewhere in the codebase (see the legacy
`_delete_auth_user` that this module subsumes).

Configuration:
    settings.supabase_url              — e.g. http://127.0.0.1:54321
    settings.supabase_service_role_key — Admin API bearer token

Both must be set. Calls silently no-op (log + return) if either is
missing — matching existing behavior so local `test` environments that
omit the keys don't crash on auth-touching code paths.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import settings
from app.modules.auth.admin.base import (
    AuthProviderError,
    InvalidCredentialsError,
    SessionTokens,
    UserAlreadyExistsError,
    UserIdentity,
    UserNotFoundError,
)

logger = structlog.get_logger()

_JSON = {"Content-Type": "application/json"}


def _missing_config() -> bool:
    return not (settings.supabase_url and settings.supabase_service_role_key)


def _admin_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        **_JSON,
    }


def _anon_headers() -> dict[str, str]:
    # Token endpoint uses service role key as apikey (same as admin surface).
    # The `grant_type=password` flow signs the user in and is the same path
    # the frontend would hit — we just call it server-side with admin key.
    key = settings.supabase_service_role_key
    return {"apikey": key, **_JSON}


def _identity_from_user_json(body: dict[str, Any]) -> UserIdentity:
    return UserIdentity(id=str(body["id"]), email=str(body["email"]))


class SupabaseAuthProvider:
    """AuthProvider backed by Supabase GoTrue REST."""

    async def create_user(
        self, email: str, password: str
    ) -> UserIdentity:
        if _missing_config():
            raise AuthProviderError(
                "Supabase URL or service_role_key not configured"
            )
        url = f"{settings.supabase_url}/auth/v1/admin/users"
        payload = {
            "email": email,
            "password": password,
            "email_confirm": True,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=_admin_headers(), json=payload)
        if resp.status_code == 200:
            return _identity_from_user_json(resp.json())
        if resp.status_code == 422:
            body = _safe_json(resp)
            code = body.get("code") or body.get("error_code") or ""
            msg = body.get("msg") or body.get("message") or ""
            if code == "email_exists" or "already" in msg.lower():
                raise UserAlreadyExistsError(email)
        logger.error(
            "auth.admin.create_user.failed",
            status=resp.status_code,
            body=_safe_json(resp),
        )
        raise AuthProviderError(
            f"Supabase create_user failed ({resp.status_code})"
        )

    async def find_user_by_email(
        self, email: str
    ) -> UserIdentity | None:
        if _missing_config():
            raise AuthProviderError(
                "Supabase URL or service_role_key not configured"
            )
        url = f"{settings.supabase_url}/auth/v1/admin/users"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers=_admin_headers(),
                params={"email": email},
            )
        if resp.status_code != 200:
            logger.error(
                "auth.admin.find_user.failed",
                status=resp.status_code,
                body=_safe_json(resp),
            )
            raise AuthProviderError(
                f"Supabase find_user_by_email failed ({resp.status_code})"
            )
        body = resp.json()
        users = body.get("users") or []
        if not users:
            return None
        # Defensive: Supabase may return users whose emails case-differ;
        # enforce exact-match.
        for user in users:
            if (user.get("email") or "").lower() == email.lower():
                return _identity_from_user_json(user)
        return None

    async def update_user_password(
        self, user_id: str, password: str
    ) -> None:
        if _missing_config():
            raise AuthProviderError(
                "Supabase URL or service_role_key not configured"
            )
        url = f"{settings.supabase_url}/auth/v1/admin/users/{user_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                url, headers=_admin_headers(), json={"password": password}
            )
        if resp.status_code == 200:
            return
        if resp.status_code == 404:
            raise UserNotFoundError(user_id)
        logger.error(
            "auth.admin.update_password.failed",
            status=resp.status_code,
            body=_safe_json(resp),
        )
        raise AuthProviderError(
            f"Supabase update_user_password failed ({resp.status_code})"
        )

    async def sign_in_with_password(
        self, email: str, password: str
    ) -> SessionTokens:
        if _missing_config():
            raise AuthProviderError(
                "Supabase URL or service_role_key not configured"
            )
        url = f"{settings.supabase_url}/auth/v1/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers=_anon_headers(),
                params={"grant_type": "password"},
                json={"email": email, "password": password},
            )
        if resp.status_code == 200:
            body = resp.json()
            return SessionTokens(
                access_token=str(body["access_token"]),
                refresh_token=str(body["refresh_token"]),
                expires_in=int(body.get("expires_in", 3600)),
            )
        if resp.status_code == 400:
            # GoTrue returns 400 with error=invalid_grant on wrong password.
            raise InvalidCredentialsError(email)
        logger.error(
            "auth.admin.sign_in.failed",
            status=resp.status_code,
            body=_safe_json(resp),
        )
        raise AuthProviderError(
            f"Supabase sign_in_with_password failed ({resp.status_code})"
        )

    async def sign_out(self, tokens: SessionTokens) -> None:
        if _missing_config():
            logger.warning(
                "auth.admin.sign_out.skipped",
                reason="supabase_url or service_role_key not configured",
            )
            return
        url = f"{settings.supabase_url}/auth/v1/logout"
        headers = {
            **_anon_headers(),
            "Authorization": f"Bearer {tokens.access_token}",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers)
        if resp.status_code in (200, 204, 404):
            if resp.status_code == 404:
                logger.info("auth.admin.sign_out.already_revoked")
            else:
                logger.info("auth.admin.sign_out.ok")
            return
        logger.error(
            "auth.admin.sign_out.failed",
            status=resp.status_code,
            body=_safe_json(resp),
        )
        raise AuthProviderError(
            f"Supabase sign_out failed ({resp.status_code})"
        )

    async def delete_user(self, user_id: str) -> None:
        if _missing_config():
            logger.warning(
                "auth.admin.delete_user.skipped",
                reason="supabase_url or service_role_key not configured",
                user_id=user_id,
            )
            return
        url = f"{settings.supabase_url}/auth/v1/admin/users/{user_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers=_admin_headers())
        if resp.status_code in (200, 204, 404):
            if resp.status_code == 404:
                # Idempotent — already absent.
                logger.info(
                    "auth.admin.delete_user.already_absent",
                    user_id=user_id,
                )
            else:
                logger.info(
                    "auth.admin.delete_user.ok",
                    user_id=user_id,
                )
            return
        logger.error(
            "auth.admin.delete_user.failed",
            user_id=user_id,
            status=resp.status_code,
            body=_safe_json(resp),
        )
        raise AuthProviderError(
            f"Supabase delete_user failed ({resp.status_code})"
        )


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}
