"""OpenAI client factory wrapped with instructor (structured output).

Business logic imports get_openai_client() — never openai directly.
This is the single swap point for a future provider change.

OpenTelemetry tracing is wired automatically via the OpenAI auto-instrumentor
registered in app.ai.otel.instrument_openai(). Every chat.completions.create()
call produces a span; app.ai.tracing.set_llm_span_attributes() adds
prompt-name + version + tenant + correlation metadata.

Instructor behavior:
  - mode=TOOLS_STRICT uses OpenAI function-calling with strict schema
    enforcement. If the model returns a malformed payload, instructor
    retries up to max_schema_retries times before raising
    InstructorRetryException (from instructor.core).

NOTE: max_retries is NOT a factory-level argument in instructor.
Passing it to from_openai() stores it in a forwarded-kwargs bucket
that leaks into every .create() call as an extra kwarg, producing
`TypeError: got multiple values for keyword argument 'max_retries'`
because instructor's per-call create() has its own internal default.
If we ever need a non-default schema-retry count, pass it per-call
in the actor via `max_retries=` on chat.completions.create()."""

from functools import lru_cache

import httpx
import instructor
import structlog
from openai import AsyncOpenAI

from app.ai.config import ai_config
from app.config import settings

logger = structlog.get_logger()


async def _log_request(request: "httpx.Request") -> None:
    """httpx event hook: log every outbound OpenAI HTTP request.

    This fires on every attempt — including SDK-level retries — so we get
    visibility into silent retry cascades that instructor-level logging can't
    see (e.g., the SDK retries a 429 or 503 before handing control back).
    """
    logger.info(
        "llm.http_request",
        method=request.method,
        url=str(request.url),
        body_bytes=len(request.content) if request.content else 0,
    )


async def _log_response(response: "httpx.Response") -> None:
    """httpx event hook: log every response received from OpenAI.

    Includes status code, reason, and selected rate-limit headers so we can
    diagnose throttling. On non-2xx, logs at warning level.
    """
    rate_remaining = response.headers.get("x-ratelimit-remaining-tokens")
    rate_reset = response.headers.get("x-ratelimit-reset-tokens")
    request_id = response.headers.get("x-request-id")
    level_fn = logger.info if response.is_success else logger.warning
    level_fn(
        "llm.http_response",
        status_code=response.status_code,
        url=str(response.request.url),
        request_id=request_id,
        rate_limit_remaining_tokens=rate_remaining,
        rate_limit_reset_tokens=rate_reset,
    )


@lru_cache(maxsize=1)
def _build_raw_openai_client() -> AsyncOpenAI:
    """Build the underlying AsyncOpenAI instance (memoized, shared).

    Both get_openai_client() (instructor-wrapped) and get_raw_openai_client()
    (bare, for the Responses API) reuse the same AsyncOpenAI instance so the
    httpx connection pool, event hooks, and SDK-level retry policy are
    identical for all callers.
    """
    http_client = httpx.AsyncClient(
        timeout=ai_config.request_timeout_seconds,
        event_hooks={
            "request": [_log_request],
            "response": [_log_response],
        },
    )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
        max_retries=1,
        http_client=http_client,
    )


@lru_cache(maxsize=1)
def get_openai_client() -> instructor.AsyncInstructor:
    """Return a memoized async OpenAI client wrapped with instructor.

    Configuration:
      - Timeout from ai_config.request_timeout_seconds.
      - max_retries=1 (OpenAI SDK-level auto-retry). Default is 2 which
        cascades badly when combined with reasoning models — a single
        retry on a 4-minute call burns 8 minutes silently. One retry
        covers spurious network blips; anything worse should surface.
      - httpx event hooks log every request attempt (including retries)
        and response status + rate-limit headers.

    OpenTelemetry's OpenAI auto-instrumentor (registered at app startup
    via app.ai.otel.instrument_openai()) wraps every chat.completions.create
    call into a span. Prompt metadata is added by callers via
    app.ai.tracing.set_llm_span_attributes()."""
    return instructor.from_openai(
        _build_raw_openai_client(),
        mode=instructor.Mode.TOOLS_STRICT,
    )


@lru_cache(maxsize=1)
def get_raw_openai_client() -> AsyncOpenAI:
    """Return the memoized raw AsyncOpenAI client (no instructor wrapper).

    Use this for Responses API calls (``client.responses.parse``) which do
    not go through instructor's tool-call shim.  The underlying client is
    the SAME instance as the one wrapped by ``get_openai_client()`` — same
    httpx pool, same event hooks, same SDK-level retry policy.

    Callers MUST stay within ``app/ai/`` or be explicitly documented
    exceptions (currently: ``app/modules/reporting/scoring/judge.py``).
    """
    return _build_raw_openai_client()
