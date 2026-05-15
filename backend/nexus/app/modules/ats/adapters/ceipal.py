"""CeipalAdapter — Ceipal ATS v2 API implementation.

Owns every Ceipal-specific quirk. The orchestrator only sees the canonical
ProjectX shapes defined in `app.modules.ats.schemas`.

Quirks handled here:
  - HTML-encoded description bodies (``&nbsp;``, ``<br />``, ``&#39;``) →
    ``html.unescape`` before saving to ``description_raw``.
  - Timezone-naive vendor timestamps in the tenant's local time →
    converted to UTC using ``state.tenant_timezone`` (IANA name, e.g.
    ``Asia/Kolkata``). Fallback is UTC when ``tenant_timezone`` is None.
  - Opaque IDs contain ``/``, ``+``, ``=`` and must be URL-path-encoded
    via ``urllib.parse.quote(id, safe="")``.
  - Magic-string sentinels:
      * ``industry_exp == "0"`` → None
      * ``closing_date`` non-date strings (``"Open Until Filled"``,
        sometimes with leading space) → None
  - Field-name inconsistencies:
      * users carry ``email_id`` + ``first_name``
      * applicants carry ``email`` + ``firstname``
  - CSV recruiter parsing: ``assigned_recruiter`` is a CSV of opaque IDs;
    split on ``,``, strip whitespace, drop empties.
  - PII strip at the wire boundary on ``get_applicant``: drop
    ``aadhar_number``, ``ssn``, ``pan_number``, ``passport_number``,
    ``drivers_license``, ``tax_id``, ``nric``, ``emirates_id``, any
    ``*_token`` field, ``Documents``, ``merged_pdf_document``,
    ``merge_document_path`` from the raw payload before constructing
    the DTO.

Auth model:
  - createAuthtoken: email + password + apiKey → access_token (1h) +
    refresh_token (7d).
  - refreshToken: expired access_token in ``Token: Bearer …`` header
    → new access_token.
Refresh strategy: proactive at 80% of access_token lifetime. If refresh_token
has also expired, fall back to full re-auth from stored credentials.
"""
from __future__ import annotations

import asyncio
import html
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import structlog

from app.config import settings
from app.modules.ats.adapter import ATSAdapterCapabilities
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.constants import ATS_VENDOR_CEIPAL
from app.modules.ats.errors import (
    ATSAuthorizationError,
    ATSCredentialsInvalidError,
    ATSNetworkError,
    ATSRateLimitedError,
    ATSVendorContractError,
)
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientContact,
    ATSClientPayload,
    ATSJobPayload,
    ATSJobStatus,
    ATSSubmissionPayload,
    ATSUserPayload,
)
from app.modules.candidates import strip_sensitive_pii

logger = structlog.get_logger()

CEIPAL_BASE_URL = "https://api.ceipal.com/v2"
ACCESS_TOKEN_REFRESH_THRESHOLD = 0.20  # refresh when ≤20% of lifetime remains

# Magic strings Ceipal uses to signal "no value" in non-null columns.
_INDUSTRY_EMPTY_SENTINELS = frozenset({"", "0"})
_DEADLINE_NON_DATE_SENTINELS = frozenset(
    {"open until filled", "open until fill", "open until filed", "n/a", "na", "tbd", ""}
)

# Submission-payload keys to drop at the wire boundary — never persisted in
# the `raw` field of an ATSSubmissionPayload. The PII helper provides a
# second layer; this hard list is the first.
_SUBMISSION_DROPPED_KEYS = frozenset(
    {"resume_token", "Documents", "merged_pdf_document", "merge_document_path"}
)


class CeipalAdapter:
    """Concrete adapter for Ceipal v2 API."""

    vendor: ClassVar[str] = ATS_VENDOR_CEIPAL
    capabilities: ClassVar[ATSAdapterCapabilities] = ATSAdapterCapabilities(
        supports_modified_after_cursor=True,
        supports_per_job_submission_cursor=True,
        supports_client_search_by_name=False,
        job_detail_required_for_client_name=True,
        # Ceipal team confirmed (2026-05-14) a hard ceiling of 60
        # calls/min = 1.0 req/s. We advertise the real limit here; actual
        # in-flight pacing comes from settings.ats_default_request_pacing_seconds
        # (currently 1.1s = ~0.91 req/s, leaving a 10% safety margin).
        rate_limit_qps=1.0,
    )

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
        # state.rate_limit_qps overrides the global default; resolve the
        # minimum-gap-in-seconds once at init.
        if state.rate_limit_qps and float(state.rate_limit_qps) > 0:
            self._min_request_gap_s = 1.0 / float(state.rate_limit_qps)
        else:
            self._min_request_gap_s = float(
                settings.ats_default_request_pacing_seconds
            )
        self._last_request_at: float = 0.0  # monotonic timestamp

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- Timezone resolution ----------

    def _tenant_tz(self) -> ZoneInfo:
        """Resolve the connection's tenant_timezone to a ZoneInfo.

        Used to convert Ceipal's timezone-naive timestamps (which are in
        the tenant's local time) into UTC before persistence. Fallback is
        UTC when the connection has no timezone yet (fresh sync) or the
        stored value is unrecognised.
        """
        tz_name = self.state.tenant_timezone
        if not tz_name:
            return UTC  # type: ignore[return-value]
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "ats.ceipal.unknown_tenant_timezone",
                connection_id=str(self.state.id),
                tz_name=tz_name,
            )
            return UTC  # type: ignore[return-value]

    def _to_utc(self, value: Any) -> datetime | None:
        """Parse a Ceipal timestamp and return a tz-aware UTC datetime.

        Handles two formats Ceipal returns in the same payload:
          - ``"2026-05-12T06:38:35Z"`` (ISO 8601 UTC)
          - ``"2026-05-12 06:31:23"`` (space-separated, naive — assumed to
            be in the tenant's local timezone)

        Empty / None / unparseable input returns None.
        """
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        try:
            if "T" in text:
                # ISO format — fromisoformat handles the trailing Z replacement.
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return dt.astimezone(UTC)
            naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            return naive.replace(tzinfo=self._tenant_tz()).astimezone(UTC)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_deadline(value: Any) -> date | None:
        """Safe-parse ``closing_date``. Returns None for non-date sentinels."""
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or text.lower() in _DEADLINE_NON_DATE_SENTINELS:
            return None
        try:
            return date.fromisoformat(text)
        except (ValueError, TypeError):
            return None

    # ---------- Auth ----------

    async def ensure_authenticated(self) -> None:
        """Idempotent. Refresh tokens if expired or near-expiry."""
        now = datetime.now(tz=UTC)

        # Case 1: tokens still valid → no-op
        if self.state.access_token and self.state.access_token_expires_at:
            time_left = (
                self.state.access_token_expires_at - now
            ).total_seconds()
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
            except (
                ATSCredentialsInvalidError,
                ATSAuthorizationError,
                ATSVendorContractError,
            ) as exc:
                # Fall through to full reauth. All three error shapes are
                # valid "refresh slot is no longer usable" signals from
                # Ceipal. ATSNetworkError and ATSRateLimitedError are NOT
                # caught here — those reflect connectivity / quota problems
                # that full re-auth won't fix.
                logger.warning(
                    "ats.ceipal.refresh_failed_falling_back_to_reauth",
                    connection_id=str(self.state.id),
                    error_class=type(exc).__name__,
                )

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
            raise ATSNetworkError(
                f"createAuthtoken network error: {exc}"
            ) from exc
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

    def _handle_auth_response(
        self, response: httpx.Response, endpoint: str,
    ) -> None:
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

        body = (
            response.json()
            if response.headers.get("content-type", "").startswith(
                "application/json"
            )
            else {}
        )
        message = body.get("message", "")

        if response.status_code == 401:
            raise ATSCredentialsInvalidError(f"{endpoint} 401: {message}")
        if response.status_code == 403:
            raise ATSAuthorizationError(f"{endpoint} 403: {message}")
        if response.status_code == 429:
            raise ATSRateLimitedError(
                retry_after_seconds=settings.ats_default_retry_after_seconds,
                message=f"{endpoint} 429: {message}",
            )
        if response.status_code >= 500:
            raise ATSNetworkError(
                f"{endpoint} {response.status_code}: {message}"
            )
        raise ATSVendorContractError(
            f"{endpoint} unexpected {response.status_code}: {message}"
        )

    def _apply_auth_payload(self, payload: dict) -> None:
        now = datetime.now(tz=UTC)
        access = payload.get("access_token")
        if not access:
            raise ATSVendorContractError("Auth response missing access_token")
        self.state.access_token = access

        expires_in = int(payload.get("expires_in", 3600))
        self.state.access_token_expires_at = now + timedelta(seconds=expires_in)

        refresh = payload.get("refresh_token")
        if refresh:
            self.state.refresh_token = refresh
            self.state.refresh_token_expires_at = now + timedelta(days=7)

    # ---------- Shared HTTP plumbing ----------

    async def _wait_for_next_request(self) -> None:
        if self._min_request_gap_s <= 0:
            return
        now = time.monotonic()
        delay = (self._last_request_at + self._min_request_gap_s) - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request_at = time.monotonic()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
    ) -> httpx.Response:
        await self.ensure_authenticated()
        await self._wait_for_next_request()
        try:
            response = await self._client.request(
                method,
                path,
                params=params or {},
                headers={
                    "Authorization": f"Bearer {self.state.access_token}"
                },
            )
        except httpx.HTTPError as exc:
            raise ATSNetworkError(f"{path} network error: {exc}") from exc
        self._raise_for_envelope(response, path)
        return response

    def _raise_for_envelope(
        self, response: httpx.Response, path: str,
    ) -> None:
        """Translate Ceipal's HTTP-status + JSON error envelope.

        Ceipal envelope: ``{"message": "<human string>"}``. 404 on a LIST
        endpoint means 'no rows match the filter' — callers handle that
        by returning early; this helper returns without raising for 404.
        """
        if response.status_code == 200:
            return

        body: dict[str, Any] = {}
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = {"message": response.text[:200]}
        message = body.get("message", "")

        if response.status_code == 401:
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
            raise ATSNetworkError(
                f"{path} {response.status_code}: {message}"
            )
        if response.status_code == 404:
            return
        raise ATSVendorContractError(
            f"{path} unexpected {response.status_code}: {message}"
        )

    async def _paginate(
        self, path: str, params: dict,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield items from every page of a Ceipal list endpoint.

        Pagination envelope:
          ``{count, num_pages, page_number, limit, next, previous, results}``
        Walks until ``next`` is empty (or 404, which we treat as 'no more').
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

    # ---------- Modified-after formatting ----------

    def _format_modified_after(
        self, modified_after: datetime | None,
    ) -> dict[str, str]:
        """Format ``modified_after`` as Ceipal's ``modifiedAfter`` param.

        Ceipal accepts ``YYYY-MM-DD HH:MM:SS`` (no timezone). The value is
        interpreted in the tenant's local timezone (which is why we keep
        ``state.tenant_timezone``). For a tenant in Asia/Kolkata, an
        ``Asia/Kolkata`` wall-clock string is what Ceipal expects.
        """
        if modified_after is None:
            return {}
        local = modified_after.astimezone(self._tenant_tz()).replace(tzinfo=None)
        return {"modifiedAfter": local.strftime("%Y-%m-%d %H:%M:%S")}

    # ---------- ATSAdapter protocol implementation ----------

    async def list_job_statuses(self) -> list[ATSJobStatus]:
        """GET /getJobStatusList/

        Returns a bare JSON array (not the paginated envelope used elsewhere).
        Shape: ``[{"id": int, "name": str}, ...]``
        """
        response = await self._request("GET", "/getJobStatusList/")
        body = response.json()
        if not isinstance(body, list):
            raise ATSVendorContractError(
                f"/getJobStatusList/ returned {type(body).__name__}, "
                "expected list"
            )
        out: list[ATSJobStatus] = []
        for item in body:
            ext_id = item.get("id")
            name = item.get("name")
            if ext_id is None or name is None:
                continue
            out.append(ATSJobStatus(external_id=str(ext_id), name=str(name)))
        return out

    async def iter_jobs(  # type: ignore[override]
        self,
        *,
        status_external_ids: list[str],
        modified_after: datetime | None,
    ) -> AsyncIterator[ATSJobPayload]:
        """Walk /getJobPostingsList/.

        Only the list endpoint is called here. ``client_external_name`` is
        left None; the orchestrator calls ``enrich_job`` to populate it via
        getJobPostingDetails for each job that's new-or-changed.
        """
        params: dict[str, Any] = {"limit": 50, **self._format_modified_after(modified_after)}
        if status_external_ids:
            params["jobStatus"] = ",".join(status_external_ids)
        async for raw in self._paginate("/getJobPostingsList/", params):
            yield self._build_job_payload(raw)

    async def enrich_job(self, job: ATSJobPayload) -> ATSJobPayload:
        """Call getJobPostingDetails/{id} to fill in client_external_name.

        Opaque IDs may contain '/', '+', '=' — must be path-encoded.
        """
        encoded_id = quote(job.external_id, safe="")
        try:
            response = await self._request(
                "GET", f"/getJobPostingDetails/{encoded_id}",
            )
        except (ATSNetworkError, ATSVendorContractError) as exc:
            logger.warning(
                "ats.ceipal.job_details_fetch_failed",
                external_job_id=job.external_id,
                error=str(exc)[:200],
            )
            return job

        detail = response.json() or {}
        client_name = (detail.get("client") or "").strip() or None
        # Merge detail fields into raw for fuller audit trail.
        merged_raw = {**job.raw, **detail}
        return job.model_copy(update={
            "client_external_name": client_name,
            "raw": merged_raw,
        })

    async def iter_clients(  # type: ignore[override]
        self,
    ) -> AsyncIterator[ATSClientPayload]:
        """Walk getClientsList/ (paginated). No modified_after — the index
        is built once per sync and cached in-memory by the orchestrator."""
        params: dict[str, Any] = {"limit": 50}
        async for raw in self._paginate("/getClientsList/", params):
            yield self._build_client_payload(raw, contacts=[])

    async def get_client(
        self, *, external_id: str,
    ) -> ATSClientPayload:
        """GET /getClientDetails/{id} — source of truth for org_units."""
        encoded_id = quote(external_id, safe="")
        response = await self._request(
            "GET", f"/getClientDetails/{encoded_id}",
        )
        raw = response.json() or {}
        contacts = [
            self._build_client_contact(c)
            for c in (raw.get("contacts") or [])
            if isinstance(c, dict)
        ]
        return self._build_client_payload(raw, contacts=contacts)

    async def get_user(self, *, external_id: str) -> ATSUserPayload:
        """GET /getUserDetails/{id} — source of truth for users."""
        encoded_id = quote(external_id, safe="")
        response = await self._request(
            "GET", f"/getUserDetails/{encoded_id}",
        )
        raw = response.json() or {}
        return self._build_user_payload(raw)

    async def iter_submissions(  # type: ignore[override]
        self,
        *,
        job_external_id: str,
        modified_after: datetime | None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        """GET /getSubmissionsList?jobId=…

        modified_after is ignored — capabilities flag
        supports_per_job_submission_cursor is True at the contract level
        (we'd accept it if Ceipal supported it), but in practice the
        orchestrator drives per-job freshness via the parent job's modified
        timestamp, and we want all submissions whenever the parent is
        touched.
        """
        del modified_after  # noqa: F841 (declared in contract)
        params: dict[str, Any] = {"jobId": job_external_id, "limit": 50}
        async for raw in self._paginate("/getSubmissionsList/", params):
            yield self._build_submission_payload(raw, job_external_id)

    async def get_applicant(
        self, *, external_id: str,
    ) -> ATSApplicantPayload:
        """GET /getApplicantDetails/{id} — PII-bearing.

        Hard-strip the prohibited fields here at the wire boundary. The
        orchestrator applies a second strip via
        ``app.modules.candidates.pii.strip_sensitive_pii`` before persistence
        as defence-in-depth.
        """
        encoded_id = quote(external_id, safe="")
        response = await self._request(
            "GET", f"/getApplicantDetails/{encoded_id}",
        )
        raw = response.json() or {}
        sanitized = strip_sensitive_pii(raw)
        return self._build_applicant_payload(sanitized)

    # ---------- DTO builders ----------

    def _build_job_payload(self, raw: dict[str, Any]) -> ATSJobPayload:
        # The orchestrator filters in `status_external_ids` server-side via
        # the `jobStatus` param; the per-row `job_status` label is the
        # human-readable name. The integer ID is on `job_status_id` in some
        # payloads — fall back to the label if the ID isn't surfaced.
        external_status = (raw.get("job_status") or "").strip()
        external_status_id = str(
            raw.get("job_status_id") or raw.get("jobStatusId") or ""
        )

        description_raw = html.unescape(
            raw.get("requisition_description") or ""
        )
        description_enriched_raw = raw.get("public_job_desc")
        description_enriched = (
            html.unescape(description_enriched_raw)
            if description_enriched_raw
            else None
        )

        recruiter_csv = raw.get("assigned_recruiter") or ""
        assigned_recruiter_ids = [
            r.strip() for r in recruiter_csv.split(",") if r.strip()
        ]

        skills_csv = raw.get("skills") or ""
        skills = [s.strip() for s in skills_csv.split(",") if s.strip()]

        secondary_cities = raw.get("secondary_cities") or []
        secondary_states = raw.get("secondary_states") or []
        secondary_locations = _pair_secondary_locations(
            secondary_cities, secondary_states,
        )

        pay_rates = raw.get("pay_rates") or []

        return ATSJobPayload(
            external_id=str(raw["id"]),
            title=(raw.get("position_title") or "").strip(),
            description_raw=description_raw,
            description_enriched=description_enriched,
            external_status=external_status,
            external_status_id=external_status_id,
            client_external_name=None,        # filled by enrich_job
            client_external_id=None,           # filled by orchestrator
            created_external_id=_str_or_none(raw.get("created_by")),
            posted_by_external_id=_str_or_none(raw.get("posted_by")),
            primary_recruiter_external_id=_str_or_none(
                raw.get("primary_recruiter"),
            ),
            assigned_recruiter_external_ids=assigned_recruiter_ids,
            business_unit_id=_safe_int(raw.get("business_unit_id")),
            country=_str_or_none(raw.get("country")),
            primary_city=_str_or_none(raw.get("primary_city")),
            primary_state=_str_or_none(raw.get("primary_state")),
            secondary_locations=secondary_locations or None,
            skills=skills,
            pay_rates=pay_rates if isinstance(pay_rates, list) else [],
            deadline=self._parse_deadline(raw.get("closing_date")),
            external_created_at=self._to_utc(raw.get("created")) or datetime.now(tz=UTC),
            external_modified_at=self._to_utc(raw.get("modified")) or datetime.now(tz=UTC),
            raw=raw,
        )

    def _build_client_payload(
        self,
        raw: dict[str, Any],
        *,
        contacts: list[ATSClientContact],
    ) -> ATSClientPayload:
        industry_exp = (raw.get("industry_exp") or "").strip()
        industry = (
            None
            if industry_exp in _INDUSTRY_EMPTY_SENTINELS
            else industry_exp
        )
        return ATSClientPayload(
            external_id=str(raw["id"]),
            name=(raw.get("name") or "").strip(),
            website=_str_or_none(raw.get("website")),
            industry=industry,
            country=_str_or_none(raw.get("country")),
            state=_str_or_none(raw.get("state")),
            city=_str_or_none(raw.get("city")),
            business_unit_id=_safe_int(raw.get("primary_business_unit")),
            external_created_at=self._to_utc(raw.get("created_at")),
            external_modified_at=self._to_utc(raw.get("updated_at")),
            contacts=contacts,
            raw=raw,
        )

    def _build_client_contact(
        self, raw: dict[str, Any],
    ) -> ATSClientContact:
        name_parts = [
            (raw.get("first_name") or "").strip(),
            (raw.get("last_name") or "").strip(),
        ]
        name = " ".join(p for p in name_parts if p).strip() or None
        return ATSClientContact(
            external_id=str(raw.get("id") or raw.get("contact_id") or ""),
            name=name,
            email=_str_or_none(raw.get("email") or raw.get("email_id")),
            designation=_str_or_none(raw.get("designation")),
            phone=_str_or_none(
                raw.get("phone") or raw.get("mobile_number"),
            ),
        )

    def _build_user_payload(self, raw: dict[str, Any]) -> ATSUserPayload:
        # users use `first_name` + `last_name` (jet underscore) and
        # `email_id` (NOT `email`). Normalize.
        first = (raw.get("first_name") or "").strip()
        last = (raw.get("last_name") or "").strip()
        full_name = " ".join(p for p in [first, last] if p).strip() or "(unnamed)"
        email = (raw.get("email_id") or raw.get("email") or "").strip()
        return ATSUserPayload(
            external_id=str(raw["id"]),
            email=email,
            full_name=full_name,
            role=_str_or_none(raw.get("role")),
            business_unit_id=_safe_int(raw.get("business_unit_id")),
            timezone=_str_or_none(raw.get("timezone")),
            external_status=(raw.get("status") or "").strip(),
            raw=raw,
        )

    def _build_submission_payload(
        self, raw: dict[str, Any], job_external_id: str,
    ) -> ATSSubmissionPayload:
        # Hard-strip the resume-artifact keys at the wire boundary.
        sanitized = {
            k: v for k, v in raw.items()
            if k not in _SUBMISSION_DROPPED_KEYS
        }
        # PII helper catches the long-tail (e.g. nested *_token fields).
        sanitized = strip_sensitive_pii(sanitized)

        pay_rate = sanitized.get("pay_rate")
        try:
            pay_rate_float = (
                float(pay_rate) if pay_rate not in (None, "") else None
            )
        except (TypeError, ValueError):
            pay_rate_float = None

        return ATSSubmissionPayload(
            external_id=str(sanitized["id"]),
            job_external_id=str(
                sanitized.get("job_id") or job_external_id,
            ),
            applicant_external_id=str(
                sanitized.get("job_seeker_id")
                or sanitized.get("applicant_id") or "",
            ),
            submitted_by_external_id=_str_or_none(
                sanitized.get("submitted_by"),
            ),
            external_status=(sanitized.get("submission_status") or "").strip(),
            pipeline_status=_str_or_none(sanitized.get("pipeline_status")),
            submission_channel=_str_or_none(sanitized.get("source")),
            pay_rate=pay_rate_float,
            pay_currency=_str_or_none(sanitized.get("currency_code")),
            external_submitted_at=(
                self._to_utc(sanitized.get("submitted_on"))
                or datetime.now(tz=UTC)
            ),
            external_modified_at=(
                self._to_utc(sanitized.get("modified"))
                or datetime.now(tz=UTC)
            ),
            raw=sanitized,
        )

    def _build_applicant_payload(
        self, sanitized: dict[str, Any],
    ) -> ATSApplicantPayload:
        # applicants use `firstname` + `lastname` (no underscore) and
        # `email` (NOT `email_id`). Normalize.
        return ATSApplicantPayload(
            external_id=str(sanitized["id"]),
            first_name=_str_or_none(sanitized.get("firstname")),
            last_name=_str_or_none(sanitized.get("lastname")),
            email=_str_or_none(sanitized.get("email")),
            secondary_email=_str_or_none(
                sanitized.get("email_address_1"),
            ),
            mobile=_str_or_none(sanitized.get("mobile_number")),
            address=_str_or_none(sanitized.get("address")),
            city=_str_or_none(sanitized.get("city")),
            state=_str_or_none(sanitized.get("state")),
            country=_str_or_none(sanitized.get("country")),
            applicant_source=_str_or_none(sanitized.get("source")),
            raw=sanitized,
        )


# ---------- Module-level helpers ----------

def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    """Trim and convert empty strings to None. Non-strings pass through
    untouched (None on falsy, str otherwise)."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _pair_secondary_locations(
    cities: Any, states: Any,
) -> list[dict[str, str]]:
    """Zip Ceipal's parallel secondary_cities/secondary_states arrays.

    Ceipal returns these as two arrays of equal length where each index
    refers to the same secondary location. When the arrays are mismatched
    (rare), pair what we can and drop the rest.
    """
    if not isinstance(cities, list) or not isinstance(states, list):
        return []
    pairs: list[dict[str, str]] = []
    for city, state in zip(cities, states, strict=False):
        if not (city or state):
            continue
        pairs.append({"city": str(city or ""), "state": str(state or "")})
    return pairs
