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
    http_client = httpx.AsyncClient(
        timeout=ai_config.request_timeout_seconds,
        event_hooks={
            "request": [_log_request],
            "response": [_log_response],
        },
    )
    raw = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
        max_retries=1,
        http_client=http_client,
    )
    return instructor.from_openai(
        raw,
        mode=instructor.Mode.TOOLS_STRICT,
    )


@lru_cache(maxsize=1)
def get_openai_raw_client() -> AsyncOpenAI:
    """Return a memoized async OpenAI client WITHOUT instructor wrapping.

    Used by the Phase C SpeechAgent for plain-text streaming chat completions.
    Evaluators (Phase D-H) continue to use ``get_openai_client()`` (instructor-
    wrapped). Same env vars, same timeout, same httpx event hooks — just
    no structured-output enforcement layer.

    The SpeechAgent owns its own retry policy in ``_drive``; this factory
    sets ``max_retries=0`` so SDK-level retries don't compound the per-attempt
    timeout (per Phase C spec §4.4).
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
        max_retries=0,
        http_client=http_client,
    )
