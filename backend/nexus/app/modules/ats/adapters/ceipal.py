"""CeipalAdapter — Ceipal ATS v2 API implementation.

Auth model is unusual:
  - createAuthtoken: email + password + apiKey → access_token (1h) + refresh_token (7d)
  - refreshToken:    expired access_token in `Token: Bearer ...` header → new access_token

Refresh strategy: proactive at 80% of access_token lifetime. If refresh_token
has also expired, fall back to full re-auth from stored credentials.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import structlog

from app.config import settings
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import (
    ATSAuthorizationError,
    ATSCredentialsInvalidError,
    ATSNetworkError,
    ATSRateLimitedError,
    ATSVendorContractError,
)
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientPayload,
    ATSJobPayload,
    ATSSubmissionPayload,
    ATSUserPayload,
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
        # Per-request pacing — see _wait_for_next_request. Per-connection
        # state.rate_limit_qps overrides the global default; we resolve
        # the minimum-gap-in-seconds once at init.
        if state.rate_limit_qps and float(state.rate_limit_qps) > 0:
            self._min_request_gap_s = 1.0 / float(state.rate_limit_qps)
        else:
            self._min_request_gap_s = float(settings.ats_default_request_pacing_seconds)
        self._last_request_at: float = 0.0  # monotonic timestamp

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- Auth ----------

    async def ensure_authenticated(self) -> None:
        """Idempotent. Refresh tokens if expired or near-expiry."""
        now = datetime.now(tz=UTC)

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
        now = datetime.now(tz=UTC)
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

    # ---------- Shared HTTP plumbing for list endpoints ----------

    async def _wait_for_next_request(self) -> None:
        """Sleep just long enough to honor the configured request pacing.

        The first call is free; every subsequent call waits until
        ``last_request_at + min_gap`` before proceeding. Uses
        ``time.monotonic`` so the gap is correct even if the system
        clock jumps. This is the single load-bearing rate-limit
        primitive — Ceipal's undocumented limit empirically triggers at
        ~1 req/s sustained, so the default 2.0s gap (0.5 req/s) gives
        margin without changing the per-tenant cadence.
        """
        if self._min_request_gap_s <= 0:
            return
        now = time.monotonic()
        delay = (self._last_request_at + self._min_request_gap_s) - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request_at = time.monotonic()

    async def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        await self.ensure_authenticated()
        await self._wait_for_next_request()
        try:
            response = await self._client.request(
                method, path, params=params or {},
                headers={"Authorization": f"Bearer {self.state.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"{path} network error: {exc}") from exc
        self._raise_for_envelope(response, path)
        return response

    def _raise_for_envelope(self, response: httpx.Response, path: str) -> None:
        """Translate Ceipal's HTTP-status + JSON error envelope into typed exceptions.

        Ceipal envelope: {"message": "<human string>"}. 404 on a LIST endpoint
        means 'no rows match the filter' (NOT an error) — callers handle that by
        treating the empty results array as the empty list.
        """
        if response.status_code == 200:
            return

        body = {}
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = {"message": response.text[:200]}
        message = body.get("message", "")

        if response.status_code == 401:
            # ensure_authenticated should have prevented this — if we hit a 401
            # mid-list, treat as credentials-invalid (likely revoked upstream).
            raise ATSCredentialsInvalidError(f"{path} 401: {message}")
        if response.status_code == 403:
            raise ATSAuthorizationError(f"{path} 403: {message}")
        if response.status_code == 429:
            raise ATSRateLimitedError(
                retry_after_seconds=settings.ats_default_retry_after_seconds,
                message=f"{path} 429: {message}",
            )
        if response.status_code == 400:
            raise ATSVendorContractError(f"{path} 400: {message}")
        if response.status_code >= 500:
            raise ATSNetworkError(f"{path} {response.status_code}: {message}")
        if response.status_code == 404:
            # For list endpoints we synthesize an empty page rather than raising.
            # Caller's `if not next` loop exits naturally; per-method coercion is
            # handled in _paginate via the result-array length.
            return
        raise ATSVendorContractError(
            f"{path} unexpected {response.status_code}: {message}"
        )

    async def _paginate(
        self, path: str, params: dict,
    ):
        """Yield items from every page of a Ceipal list endpoint.

        Pagination envelope: {count, num_pages, page_number, limit, next, previous, results}
        Walks until `next` is empty (or 404, which we treat as 'no more').
        """
        page = 1
        params = dict(params)
        while True:
            params["page"] = page
            response = await self._request("GET", path, params=params)
            if response.status_code == 404:
                return
            envelope = response.json()
            for item in envelope.get("results", []):
                yield item
            if not envelope.get("next"):
                return
            page += 1

    # ---------- List endpoints ----------

    @staticmethod
    def _format_since(since: datetime | None) -> dict:
        """Ceipal accepts modifiedAfter as 'YYYY-MM-DD HH:MM:SS' (no timezone)."""
        if since is None:
            return {}
        # Strip tzinfo; Ceipal docs use space-separated naive timestamps
        utc = since.astimezone(UTC).replace(tzinfo=None)
        return {"modifiedAfter": utc.strftime("%Y-%m-%d %H:%M:%S")}

    async def list_clients(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        now = datetime.now(tz=UTC)
        params = {"limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getClientsList/", params):
            yield ATSClientPayload(
                external_id=raw["id"],
                name=raw["name"],
                website=raw.get("website") or None,
                industry=raw.get("industry_exp") or None,
                country=raw.get("country") or None,
                state=raw.get("state") or None,
                city=raw.get("city") or None,
                address=raw.get("address") or None,
                status=raw.get("status") or None,
                contacts=raw.get("contacts", []),
                raw=raw,
                fetched_at=now,
            )

    async def list_users(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        now = datetime.now(tz=UTC)
        # getUsersList does NOT document modifiedAfter; full sync per run.
        async for raw in self._paginate("/getUsersList/", {}):
            display = raw.get("display_name") or (
                f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()
            )
            yield ATSUserPayload(
                external_id=raw["id"],
                email=raw.get("email_id") or raw.get("email", ""),
                display_name=display or "(unnamed)",
                role=raw.get("role") or None,
                status=raw.get("status") or None,
                raw=raw,
                fetched_at=now,
            )

    async def _fetch_job_details(self, job_id: str) -> dict:
        """Fetch the per-job details endpoint.

        Ceipal's ``getJobPostingsList`` response does NOT carry the
        job→client linkage (the ``company`` integer on list items is the
        agency tenant id, not a client id). The details endpoint, which
        takes the job hash directly in the URL path (NOT as a query
        param), returns ``client`` as the client name — which we match
        against ``ats_client_mappings.external_client_name``.

        This is per-job, so the jobs phase makes 1 + N HTTP calls instead
        of N/50. Configured pacing keeps the call rate under Ceipal's
        undocumented rate limit.
        """
        response = await self._request("GET", f"/getJobPostingDetails/{job_id}")
        return response.json()

    async def list_jobs(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        now = datetime.now(tz=UTC)
        params = {"limit": 50, **self._format_since(since)}
        async for list_raw in self._paginate("/getJobPostingsList/", params):
            # Fetch the per-job details endpoint to get the client name.
            # The list-item carries only ``company`` (an integer == agency
            # tenant id) which is not usable for client linkage. We MUST
            # follow up with the details endpoint to learn which Ceipal
            # client this job belongs to.
            try:
                detail = await self._fetch_job_details(list_raw["id"])
            except (ATSNetworkError, ATSVendorContractError) as exc:
                logger.warning(
                    "ats.ceipal.job_details_fetch_failed",
                    external_job_id=list_raw.get("id"),
                    error=str(exc)[:200],
                )
                # Yield with empty client linkage; importer will skip with
                # a warning. Failing the whole phase for one bad job is
                # worse than skipping the row.
                detail = {}

            # Merge: details fields override list fields where present,
            # so the consumer gets the most complete view in ``raw``.
            raw = {**list_raw, **detail}

            skills_str = raw.get("skills") or ""
            skills = [s.strip() for s in skills_str.split(",") if s.strip()]
            recruiter_str = raw.get("assigned_recruiter") or ""
            recruiter_ids = [r.strip() for r in recruiter_str.split(",") if r.strip()]
            pay_rates = raw.get("pay_rates") or []
            first_pay = pay_rates[0] if pay_rates else {}

            yield ATSJobPayload(
                external_id=raw["id"],
                # Ceipal jobs do NOT carry a stable client id — the
                # ``company`` integer on list items is the agency tenant
                # id, not the client. The details endpoint returns the
                # client by NAME ("Oracle"); we link by name in the
                # importer.
                external_client_id="",
                external_client_name=(detail.get("client") or "").strip() or None,
                title=raw.get("position_title") or raw.get("public_job_title") or "",
                description=raw.get("public_job_desc") or raw.get("requisition_description"),
                status=raw.get("job_status") or None,
                location=raw.get("primary_city") or raw.get("country") or None,
                skills=skills,
                employment_type=raw.get("employment_type") or None,
                work_arrangement=(
                    "remote" if raw.get("remote_opportunities") == "Yes" else None
                ),
                salary_range_min=_safe_int(first_pay.get("min_pay_rate")),
                salary_range_max=_safe_int(first_pay.get("max_pay_rate")),
                salary_currency=first_pay.get("pay_rate_currency") or None,
                assigned_recruiter_external_ids=recruiter_ids,
                raw=raw,
                fetched_at=now,
            )

    async def list_applicants(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        now = datetime.now(tz=UTC)
        params = {"limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getApplicantsList/", params):
            full_name = " ".join(filter(None, [
                raw.get("firstname"), raw.get("middlename"), raw.get("lastname"),
            ])).strip() or "(unknown)"
            location_parts = [p for p in [raw.get("city"), raw.get("state")] if p]
            location = ", ".join(location_parts) or None
            yield ATSApplicantPayload(
                external_id=raw["id"],
                name=full_name,
                email=raw.get("email") or raw.get("email_address_1") or "",
                phone=(
                    raw.get("mobile_number")
                    or raw.get("home_phone_number")
                    or raw.get("work_phone_number")
                    or None
                ),
                location=location,
                current_title=raw.get("job_title") or None,
                linkedin_url=None,           # not in standard payload
                notes=None,
                raw=raw,
                fetched_at=now,
            )

    async def list_submissions(  # type: ignore[override]
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        now = datetime.now(tz=UTC)
        params = {"jobId": job_external_id, "limit": 50, **self._format_since(since)}
        async for raw in self._paginate("/getSubmissionsList/", params):
            yield ATSSubmissionPayload(
                external_id=raw["id"],
                applicant_external_id=str(
                    raw.get("job_seeker_id")
                    or raw.get("applicant_id")
                    or ""
                ),
                job_external_id=raw.get("job_id") or job_external_id,
                submission_status=raw.get("submission_status") or None,
                pipeline_status=raw.get("pipeline_status") or None,
                source=raw.get("source") or None,
                submitted_on=_parse_ceipal_datetime(raw.get("submitted_on")),
                submitted_by_external_id=raw.get("submitted_by") or None,
                pay_rate=raw.get("pay_rate"),         # validator coerces
                employment_type=raw.get("employment_type") or None,
                raw=raw,
                fetched_at=now,
            )


# ---------- Module-level helpers ----------

def _safe_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_ceipal_datetime(value) -> datetime | None:
    """Ceipal returns two date formats in the same payload:
      - '2026-05-12T06:38:35Z' (ISO 8601 UTC)
      - '2026-05-12 06:31:23'  (space-separated, no timezone — assume UTC)
    """
    if value is None or value == "":
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
