"""CeipalAdapter — Ceipal ATS v2 API implementation.

Auth model is unusual:
  - createAuthtoken: email + password + apiKey → access_token (1h) + refresh_token (7d)
  - refreshToken:    expired access_token in `Token: Bearer ...` header → new access_token

Refresh strategy: proactive at 80% of access_token lifetime. If refresh_token
has also expired, fall back to full re-auth from stored credentials.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, ClassVar

import httpx
import structlog

from app.config import settings
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import (
    ATSAuthorizationError, ATSCredentialsInvalidError,
    ATSNetworkError, ATSRateLimitedError, ATSVendorContractError,
)
from app.modules.ats.schemas import (
    ATSApplicantPayload, ATSClientPayload, ATSJobPayload,
    ATSSubmissionPayload, ATSUserPayload,
)


logger = structlog.get_logger()

CEIPAL_BASE_URL = "https://api.ceipal.com/v2"
ACCESS_TOKEN_REFRESH_THRESHOLD = 0.20  # refresh when ≤20% of lifetime remains


class CeipalAdapter:
    vendor: ClassVar[str] = "ceipal"

    def __init__(
        self,
        state: ATSConnectionState,
        *,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.state = state
        # _transport is for tests via httpx.MockTransport; production calls
        # pass None and httpx uses its default async transport.
        self._client = httpx.AsyncClient(
            base_url=CEIPAL_BASE_URL,
            timeout=httpx.Timeout(30.0),
            transport=_transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- Auth ----------

    async def ensure_authenticated(self) -> None:
        """Idempotent. Refresh tokens if expired or near-expiry."""
        now = datetime.now(tz=timezone.utc)

        # Case 1: tokens still valid → no-op
        if self.state.access_token and self.state.access_token_expires_at:
            time_left = (self.state.access_token_expires_at - now).total_seconds()
            # access_token typically lives 3600s; refresh when ≤720s remain
            if time_left > 3600 * ACCESS_TOKEN_REFRESH_THRESHOLD:
                return

        # Case 2: refresh_token still valid → use refresh endpoint
        if (
            self.state.access_token
            and self.state.refresh_token_expires_at
            and self.state.refresh_token_expires_at > now
        ):
            try:
                await self._refresh_via_token_header()
                return
            except ATSCredentialsInvalidError:
                # Fall through to full reauth — refresh_token may be invalid
                # despite our expiry tracker
                logger.warning(
                    "ats.ceipal.refresh_failed_falling_back_to_reauth",
                    connection_id=str(self.state.id),
                )

        # Case 3: full re-auth from stored credentials
        await self._authenticate_with_credentials()

    async def _authenticate_with_credentials(self) -> None:
        creds = self.state.credentials
        body = {
            "email": creds["email"],
            "password": creds["password"],
            "apiKey": creds["api_key"],
        }
        try:
            response = await self._client.post("/createAuthtoken/", json=body)
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"createAuthtoken network error: {exc}") from exc

        self._handle_auth_response(response, "createAuthtoken")

    async def _refresh_via_token_header(self) -> None:
        try:
            response = await self._client.post(
                "/refreshToken/",
                headers={"Token": f"Bearer {self.state.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"refreshToken network error: {exc}") from exc

        self._handle_auth_response(response, "refreshToken")

    def _handle_auth_response(self, response: httpx.Response, endpoint: str) -> None:
        if response.status_code == 200:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise ATSVendorContractError(
                    f"{endpoint} returned 200 with non-JSON body"
                ) from exc
            self._apply_auth_payload(payload)
            logger.info(
                "ats.ceipal.auth.ok",
                connection_id=str(self.state.id),
                endpoint=endpoint,
            )
            return

        # Error envelope: 401 → invalid creds; 403 → scope; 429 → rate; 5xx → transient
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        message = body.get("message", "")

        if response.status_code == 401:
            raise ATSCredentialsInvalidError(
                f"{endpoint} 401: {message}"
            )
        if response.status_code == 403:
            raise ATSAuthorizationError(f"{endpoint} 403: {message}")
        if response.status_code == 429:
            raise ATSRateLimitedError(
                retry_after_seconds=settings.ats_default_retry_after_seconds,
                message=f"{endpoint} 429: {message}",
            )
        if response.status_code >= 500:
            raise ATSNetworkError(f"{endpoint} {response.status_code}: {message}")
        raise ATSVendorContractError(
            f"{endpoint} unexpected {response.status_code}: {message}"
        )

    def _apply_auth_payload(self, payload: dict) -> None:
        now = datetime.now(tz=timezone.utc)
        access = payload.get("access_token")
        if not access:
            raise ATSVendorContractError("Auth response missing access_token")
        self.state.access_token = access

        # Ceipal returns expires_in (seconds) per the docs; default to 3600
        # if missing (1h is the documented lifetime).
        expires_in = int(payload.get("expires_in", 3600))
        self.state.access_token_expires_at = now + timedelta(seconds=expires_in)

        # refreshToken endpoint may not return a new refresh_token; only set if present.
        refresh = payload.get("refresh_token")
        if refresh:
            self.state.refresh_token = refresh
            # refresh_token lifetime is 7d per docs
            self.state.refresh_token_expires_at = now + timedelta(days=7)

    # ---------- List endpoints (implemented in Task 15) ----------

    async def list_clients(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_users(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_jobs(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_applicants(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover

    async def list_submissions(  # type: ignore[override]
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        raise NotImplementedError("Task 15")
        yield  # pragma: no cover
