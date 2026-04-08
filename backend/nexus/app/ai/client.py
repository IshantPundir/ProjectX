"""OpenAI client factory wrapped with instructor (structured output) and
langfuse (LLM observability).

Business logic imports get_openai_client() — never openai or langfuse.openai
directly. This is the single swap point for a future provider change.

Langfuse behavior:
  - When LANGFUSE_HOST is set and keys are configured, every call is traced.
  - When LANGFUSE_HOST is empty, the wrapper degrades to a transparent
    passthrough — no network calls, no state, no errors.

Instructor behavior:
  - mode=TOOLS_STRICT uses OpenAI function-calling with strict schema
    enforcement. If the model returns a malformed payload, instructor
    retries up to max_schema_retries times before raising
    InstructorRetryException (from instructor.core).
"""

from functools import lru_cache

import instructor
from langfuse.openai import AsyncOpenAI

from app.ai.config import ai_config
from app.config import settings


@lru_cache(maxsize=1)
def get_openai_client() -> instructor.AsyncInstructor:
    """Return a memoized async OpenAI client wrapped with instructor.

    Memoization is safe because the client is stateless across calls and
    the underlying httpx pool is managed by openai SDK internals.

    NOTE: max_retries is NOT a factory-level argument in instructor.
    Passing it to from_openai() stores it in a forwarded-kwargs bucket
    that leaks into every .create() call as an extra kwarg, producing
    `TypeError: got multiple values for keyword argument 'max_retries'`
    because instructor's per-call create() has its own internal default.
    If we ever need a non-default schema-retry count, pass it per-call
    in the actor via `max_retries=` on chat.completions.create()."""
    raw = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
    )
    return instructor.from_openai(
        raw,
        mode=instructor.Mode.TOOLS_STRICT,
    )
