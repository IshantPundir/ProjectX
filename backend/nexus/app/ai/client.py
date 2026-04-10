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


def _ensure_langfuse_configured() -> None:
    """Configure the langfuse_context singleton (used by @observe() and the
    OpenAI wrapper) with explicit host/keys. Safe to call multiple times;
    the actual configure() only runs once."""
    global _langfuse_configured
    if _langfuse_configured:
        return
    _langfuse_configured = True

    url = _resolve_langfuse_url()
    if not langfuse_enabled():
        langfuse_context.configure(enabled=False)
        logger.info("langfuse.disabled", reason="missing url or keys")
        return

    langfuse_context.configure(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=url,
        enabled=True,
    )
    logger.info("langfuse.configured", host=url)


@lru_cache(maxsize=1)
def get_openai_client() -> instructor.AsyncInstructor:
    """Return a memoized async OpenAI client wrapped with instructor.

    The underlying langfuse.openai.AsyncOpenAI wrapper auto-traces every
    OpenAI call. When nested inside a @observe() decorated function, the
    generation is attached to the parent trace automatically.

    Calling _ensure_langfuse_configured() first so the wrapper and the
    decorator context share the same host/keys."""
    _ensure_langfuse_configured()
    raw = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
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
