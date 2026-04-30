"""HTTP client for Nexus's /api/internal/sessions/{id}/{config,results} endpoints.

The engine signs ONE engine-dispatch JWT per session (received in the
LiveKit dispatch metadata) and uses it for both endpoints. Each endpoint
is single-use per (jti, endpoint) — replay returns 401.

Retry policy:
- 5xx + network/timeout errors: 3 attempts with exponential backoff
  (1s, 2s, 4s).
- 401 (auth): no retry — raise PermissionError immediately.
- 404/409/422: no retry — raise typed errors.
- POST /results 409: TREAT AS IDEMPOTENT SUCCESS. The endpoint is
  idempotent on the active->completed transition; a 409 here means
  the session is already completed (likely from a worker re-dispatch).

This module imports from the nexus package (path-installed in the engine
container — see backend/interview_engine/pyproject.toml). The two types
``SessionConfig`` and ``SessionResult`` are the wire contract — see
``app.modules.interview_runtime.schemas`` for the source of truth.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from app.modules.interview_runtime.schemas import SessionConfig, SessionResult

log = structlog.get_logger("nexus_client")


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class ConfigUnavailableError(RuntimeError):
    """409/422 from /config — bank not ready, stage not AI-driven, missing
    company profile. Engine should fail the session, not retry."""


class ConfigNotFoundError(RuntimeError):
    """404 from /config — session_id has no row in nexus."""


class ResultRejectedError(RuntimeError):
    """401 or 422 from /results — engine JWT rejected or payload invalid.
    Engine should NOT retry; falls back to local-disk write for forensics."""


class ResultPostFailedError(RuntimeError):
    """3 retries on 5xx/network exhausted, or non-204/409/401/422 status.
    Engine falls back to local-disk write."""


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


_BACKOFF = (1.0, 2.0, 4.0)  # seconds — 3 attempts means index 0 + 1 + 2.
_TIMEOUT_GET = 10.0
_TIMEOUT_POST = 15.0


async def fetch_session_config(
    *, session_id: str, jwt: str, base_url: str
) -> SessionConfig:
    """Fetch the SessionConfig for the dispatched session.

    Raises:
        PermissionError              — 401 (engine JWT rejected).
        ConfigNotFoundError          — 404 (session not found).
        ConfigUnavailableError       — 409 (bank not ready) / 422 (stage not
                                       AI-driven, company profile missing).
        httpx.HTTPError / TimeoutException — last-attempt 5xx or network
                                       failure exhausts retries.
    """
    headers = {
        "Authorization": f"Bearer {jwt}",
        "x-correlation-id": session_id,
    }
    url = f"{base_url}/api/internal/sessions/{session_id}/config"

    async with httpx.AsyncClient(timeout=_TIMEOUT_GET) as client:
        for attempt in range(len(_BACKOFF)):
            try:
                r = await client.get(url, headers=headers)
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                log.warning(
                    "nexus_client.config.network_error",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == len(_BACKOFF) - 1:
                    raise
                await asyncio.sleep(_BACKOFF[attempt])
                continue

            if r.status_code == 200:
                return SessionConfig.model_validate(r.json())

            if r.status_code == 401:
                raise PermissionError("Engine JWT rejected by /config")
            if r.status_code == 404:
                raise ConfigNotFoundError(_safe_body(r))
            if r.status_code in (409, 422):
                raise ConfigUnavailableError(_safe_body(r))

            if r.status_code >= 500:
                log.warning(
                    "nexus_client.config.5xx",
                    attempt=attempt,
                    status=r.status_code,
                )
                if attempt == len(_BACKOFF) - 1:
                    r.raise_for_status()
                await asyncio.sleep(_BACKOFF[attempt])
                continue

            r.raise_for_status()

    # Unreachable — the loop either returns or raises.
    raise RuntimeError("fetch_session_config: retry loop exited unexpectedly")


async def post_session_result(
    *, session_id: str, jwt: str, result: SessionResult, base_url: str
) -> None:
    """Post the engine's SessionResult.

    Treats 204 and 409 as success. 409 means the session was already
    completed by a previous successful POST (engine retry / re-dispatch).
    Both are end-states from the engine's perspective.

    Raises:
        ResultRejectedError    — 401 (auth) or 422 (validation). No retry.
                                 Caller should fall back to local-disk write.
        ResultPostFailedError  — retries exhausted on 5xx / network errors
                                 or status code outside 204/401/409/422/5xx.
                                 Caller should fall back to local-disk write.
    """
    headers = {
        "Authorization": f"Bearer {jwt}",
        "x-correlation-id": session_id,
    }
    url = f"{base_url}/api/internal/sessions/{session_id}/results"
    body = result.model_dump(mode="json")

    async with httpx.AsyncClient(timeout=_TIMEOUT_POST) as client:
        for attempt in range(len(_BACKOFF)):
            try:
                r = await client.post(url, headers=headers, json=body)
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                log.warning(
                    "nexus_client.result.network_error",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == len(_BACKOFF) - 1:
                    raise ResultPostFailedError(str(exc)) from exc
                await asyncio.sleep(_BACKOFF[attempt])
                continue

            if r.status_code in (204, 409):
                return  # 409 = already completed; idempotent success.

            if r.status_code in (401, 422):
                raise ResultRejectedError(_safe_body(r))

            if r.status_code >= 500:
                log.warning(
                    "nexus_client.result.5xx",
                    attempt=attempt,
                    status=r.status_code,
                )
                if attempt == len(_BACKOFF) - 1:
                    raise ResultPostFailedError(
                        f"5xx after {len(_BACKOFF)} attempts: {_safe_body(r)}"
                    )
                await asyncio.sleep(_BACKOFF[attempt])
                continue

            raise ResultPostFailedError(
                f"unexpected status={r.status_code} body={_safe_body(r)}"
            )

    raise RuntimeError("post_session_result: retry loop exited unexpectedly")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_body(r: httpx.Response) -> str:
    """Truncate the response body for inclusion in exception messages.

    Truncate at 200 chars so we don't put a giant stack trace into a log
    line. Per CLAUDE.md, no full transcripts in error messages.
    """
    try:
        return r.text[:200]
    except Exception:  # noqa: BLE001
        return "<body unreadable>"
