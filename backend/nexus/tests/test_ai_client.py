"""Tests for app.ai.client — OpenAI client factories.

Covers both factories:
  - get_openai_client() — instructor-wrapped (used by JD/question-bank evaluators)
  - get_openai_raw_client() — raw AsyncOpenAI (used by Phase C SpeechAgent
    for plain-text streaming chat completions, no structured-output layer)

Both must share the same httpx config (timeout, base URL, event hooks) so
operators don't have to maintain two parallel sets of env knobs.
"""

from __future__ import annotations


def test_get_openai_raw_client_returns_async_openai_instance():
    """SpeechAgent (Phase C) needs the raw AsyncOpenAI client for
    streaming chat completions — not the instructor.AsyncInstructor wrapper.
    Both factories must coexist; evaluators continue using
    get_openai_client() (instructor-wrapped)."""
    from openai import AsyncOpenAI
    from app.ai.client import get_openai_raw_client

    raw = get_openai_raw_client()
    assert isinstance(raw, AsyncOpenAI)


def test_get_openai_raw_and_instructor_share_httpx_config():
    """Both factories must use the same timeout + base URL config so
    operators don't have to maintain two parallel sets of env knobs."""
    from app.ai.client import get_openai_client, get_openai_raw_client
    from app.ai.config import ai_config

    raw = get_openai_raw_client()
    instructor_wrapped = get_openai_client()

    # Same timeout
    assert raw.timeout == ai_config.request_timeout_seconds
    # Both wrap an underlying AsyncOpenAI; instructor exposes .client
    assert instructor_wrapped.client.timeout == raw.timeout
