"""OpenAI client factory wrapped with instructor (structured output) and
langfuse (LLM observability).

Business logic imports get_openai_client() — never openai or langfuse.openai
directly. This is the single swap point for a future provider change.

Langfuse initialization:
  - The SDK can auto-read LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY,
    LANGFUSE_BASE_URL from env vars. However, our Settings also supports
    LANGFUSE_HOST (legacy). To avoid confusion when LANGFUSE_HOST="" is set
    in .env, we resolve the URL explicitly and call langfuse_context.configure()
    so both the @observe() decorator and the langfuse.openai.AsyncOpenAI
    drop-in share the same config.
  - When no URL + keys are configured, Langfuse is disabled — the OpenAI
    wrapper becomes a transparent passthrough.

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
from langfuse.decorators import langfuse_context
from langfuse.openai import AsyncOpenAI

from app.ai.config import ai_config
from app.config import settings

logger = structlog.get_logger()

_langfuse_configured = False


def _resolve_langfuse_url() -> str:
    """Return the Langfuse host URL, preferring base_url over host.
    Returns empty string when neither is configured."""
    return settings.langfuse_base_url or settings.langfuse_host


def langfuse_enabled() -> bool:
    """True when Langfuse is configured with URL + both keys."""
    return bool(
        _resolve_langfuse_url()
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def _is_langfuse_cloud_host(url: str) -> bool:
    """True if the Langfuse URL points at any cloud.langfuse.com subdomain.

    Matches both the US (us.cloud.langfuse.com) and EU (cloud.langfuse.com)
    managed instances, plus any future `*.langfuse.com` host. Self-hosted
    deployments use the operator's own domain and are not affected.
    """
    if not url:
        return False
    # Strip scheme and path; keep only the host portion.
    stripped = url
    for prefix in ("https://", "http://"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    host = stripped.split("/", 1)[0].split(":", 1)[0].lower()
    return host == "langfuse.com" or host.endswith(".langfuse.com")


def _ensure_langfuse_configured() -> None:
    """Configure the langfuse_context singleton (used by @observe() and the
    OpenAI wrapper) with explicit host/keys. Safe to call multiple times;
    the actual configure() only runs once.

    Cloud prevention: CLAUDE.md prohibits routing candidate evaluation data
    through managed Langfuse cloud (AIVIA + third-party sub-processor rules).
    If the configured host resolves to *.langfuse.com outside
    `ENVIRONMENT=development`, this raises RuntimeError on first call so the
    process fails closed at startup rather than silently leaking PII. In
    development we still allow it (for quick benchmarking) but log a loud
    warning.
    """
    global _langfuse_configured
    if _langfuse_configured:
        return
    _langfuse_configured = True

    url = _resolve_langfuse_url()
    if not langfuse_enabled():
        langfuse_context.configure(enabled=False)
        logger.info("langfuse.disabled", reason="missing url or keys")
        return

    if _is_langfuse_cloud_host(url):
        if settings.environment != "development":
            raise RuntimeError(
                "Langfuse cloud is prohibited in non-development environments "
                "— use self-hosted per CLAUDE.md. Set LANGFUSE_BASE_URL to a "
                "self-hosted instance, or leave it empty to disable tracing."
            )
        logger.warning(
            "langfuse.cloud_host_in_dev",
            host=url,
            message=(
                "Langfuse is pointed at *.langfuse.com. This is only allowed "
                "in ENVIRONMENT=development. Do NOT deploy this configuration "
                "to staging or production — candidate transcripts would leak "
                "to a third-party sub-processor."
            ),
        )

    langfuse_context.configure(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=url,
        enabled=True,
    )
    logger.info("langfuse.configured", host=url)


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

    The underlying langfuse.openai.AsyncOpenAI wrapper auto-traces every
    OpenAI call. When nested inside a @observe() decorated function, the
    generation is attached to the parent trace automatically.

    Calling _ensure_langfuse_configured() first so the wrapper and the
    decorator context share the same host/keys."""
    _ensure_langfuse_configured()
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


def flush_langfuse() -> None:
    """Flush pending Langfuse events. Call at process shutdown or after
    actor completion to avoid losing traces."""
    _ensure_langfuse_configured()
    if langfuse_enabled():
        langfuse_context.flush()
        logger.info("langfuse.flushed")


def shutdown_langfuse() -> None:
    """Flush pending events at process shutdown."""
    flush_langfuse()
