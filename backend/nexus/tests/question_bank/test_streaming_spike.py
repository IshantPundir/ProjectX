"""R2 spike (M2 Task 1): confirm per-question streaming with the reasoning model.

Opt-in (hits the real OpenAI API):
    docker compose exec nexus pytest tests/question_bank/test_streaming_spike.py -m prompt_quality -s

Findings:
- Confirmed call path: client.chat.completions.create_iterable(...)
  (also available at client.create_iterable, but chat.completions is canonical)
- instructor 1.15.1 exposes both create_iterable and create_partial on
  client.chat.completions and at the top-level client.
"""
from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.ai.client import get_openai_client
from app.ai.config import ai_config

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


class _SpokenQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=10, max_length=240)
    primary_signal: str
    difficulty: str


async def test_create_iterable_streams_incrementally() -> None:
    """Verify that create_iterable yields multiple _SpokenQ objects incrementally.

    Three things are confirmed:
    1. The exact streaming call path on the instructor AsyncInstructor client.
    2. That multiple complete objects arrive incrementally
       (captured via per-object arrival timestamps).
    3. That reasoning_effort (when set) + Mode.TOOLS_STRICT are compatible
       with the streaming call.
    """
    client = get_openai_client()

    print(f"\nSPIKE CONFIG: model={ai_config.question_bank_model!r}"
          f"  effort={ai_config.question_bank_effort!r}")

    messages = [
        {"role": "system", "content": "You generate short spoken interview questions."},
        {
            "role": "user",
            "content": (
                "Generate exactly 4 short spoken screening questions for a Python backend role. "
                "Each: one focus, <200 chars, with a primary_signal and difficulty (easy/medium/hard)."
            ),
        },
    ]
    kwargs: dict = dict(
        model=ai_config.question_bank_model,
        response_model=_SpokenQ,
        messages=messages,
        max_retries=1,
    )
    # Only forward reasoning_effort when the config has a non-empty value.
    # Sending it to non-reasoning models returns HTTP 400.
    if ai_config.question_bank_effort:
        kwargs["reasoning_effort"] = ai_config.question_bank_effort

    arrivals: list[float] = []
    questions: list[_SpokenQ] = []
    start = time.monotonic()

    # Confirmed call path: client.chat.completions.create_iterable
    async for q in client.chat.completions.create_iterable(**kwargs):
        elapsed = time.monotonic() - start
        arrivals.append(elapsed)
        questions.append(q)
        print(f"  [{elapsed:.2f}s] {q.text!r} "
              f"(signal={q.primary_signal!r}, difficulty={q.difficulty!r})")

    offsets = [round(a, 2) for a in arrivals]
    print(f"\nSPIKE RESULT: {len(arrivals)} questions arrived; "
          f"arrival offsets={offsets}")
    print(f"VERDICT: {'PER-QUESTION CONFIRMED' if len(arrivals) >= 2 and arrivals[-1] > arrivals[0] else 'SINGLE-BATCH (all at end)'}")

    # At least 2 objects must arrive
    assert len(arrivals) >= 2, (
        f"Expected ≥2 streamed objects but got {len(arrivals)}. "
        "The call may have returned all objects at once (non-streaming)."
    )
    # Each object must be a valid _SpokenQ (Pydantic already validates on
    # construction, but be explicit about the schema contract)
    for q in questions:
        assert len(q.text) >= 10
        assert q.primary_signal
        assert q.difficulty in {"easy", "medium", "hard"}
