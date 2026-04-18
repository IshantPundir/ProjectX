"""Streaming parser for structured LLM output.

The interview agent produces JSON of the form:

    {
      "response": "spoken text streamed to TTS",
      "observation": { ... SteeringObservation fields ... }
    }

This module streams the ``response`` field to TTS token-by-token while
accumulating the full JSON.  When the stream completes, it validates the
``observation`` block into a :class:`SteeringObservation` and fires a
callback.

If the LLM produces malformed JSON (or plain text), all tokens are
forwarded to TTS as-is so the candidate always hears a response.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, Callable

import structlog

from models import SteeringObservation

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def parse_interview_output(
    text: AsyncIterable[str],
    on_observation: Callable[[SteeringObservation], None] | None = None,
) -> AsyncIterable[str]:
    """Stream ``response`` tokens to TTS immediately.

    Fires *on_observation* when the full observation is available.

    If the LLM outputs malformed JSON (not valid structured output),
    yields all text as-is to TTS (graceful degradation -- the candidate
    still hears a response, we just lose the observation).
    """
    acc = ""
    last_yielded = 0

    async for chunk in text:
        acc += chunk

        # Try to yield new response content incrementally
        new_text = _extract_response_delta(acc, last_yielded)
        if new_text:
            last_yielded += len(new_text)
            yield new_text

    # Stream complete -- try to parse the full JSON
    try:
        parsed = json.loads(acc)

        # Yield any remaining response text we didn't stream yet
        full_response = parsed.get("response", "")
        if len(full_response) > last_yielded:
            yield full_response[last_yielded:]

        # Fire observation callback
        obs_data = parsed.get("observation")
        if obs_data and on_observation:
            try:
                obs = SteeringObservation.model_validate(obs_data)
                on_observation(obs)
            except Exception as exc:
                logger.warning(
                    "observation.parse_failed",
                    error=str(exc),
                )

    except json.JSONDecodeError:
        # Graceful degradation -- yield entire accumulated text as speech
        # if nothing was yielded from incremental extraction.
        if last_yielded == 0:
            yield acc
        logger.warning(
            "structured_output.parse_failed",
            text_length=len(acc),
        )


def create_output_processor(
    on_observation: Callable[[SteeringObservation], None] | None = None,
) -> Callable[[AsyncIterable[str]], AsyncIterable[str]]:
    """Create a bound output processor for use with LiveKit AgentSession."""

    async def processor(text: AsyncIterable[str]) -> AsyncIterable[str]:
        async for chunk in parse_interview_output(text, on_observation):
            yield chunk

    return processor


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_response_delta(acc: str, already_yielded: int) -> str:
    """Return new ``response`` characters from partially-accumulated JSON.

    Scans for the ``"response": "..."`` field and returns characters
    beyond *already_yielded*.  Handles JSON string escapes so that an
    incomplete escape sequence at a chunk boundary never corrupts the
    output.
    """
    marker = '"response":'
    idx = acc.find(marker)
    if idx == -1:
        return ""

    # Locate the opening quote of the value
    rest = acc[idx + len(marker) :]
    rest = rest.lstrip()
    if not rest or rest[0] != '"':
        return ""

    # Walk the string content, respecting escape sequences
    content = ""
    i = 1  # skip opening quote
    while i < len(rest):
        ch = rest[i]
        if ch == "\\" and i + 1 < len(rest):
            # Complete escape sequence -- include both characters
            content += rest[i : i + 2]
            i += 2
        elif ch == "\\":
            # Lone backslash at the very end -- incomplete escape; stop here
            break
        elif ch == '"':
            # Closing quote -- response field complete
            break
        else:
            content += ch
            i += 1

    # Decode JSON string escapes (e.g. \n, \u0041) into real characters
    try:
        content = json.loads(f'"{content}"')
    except json.JSONDecodeError:
        # Partial escape at the tail -- trim the incomplete sequence
        if content.endswith("\\"):
            content = content[:-1]
        try:
            content = json.loads(f'"{content}"')
        except json.JSONDecodeError:
            # Still broken -- return what we have raw
            pass

    # Return only the characters the caller hasn't seen yet
    if len(content) > already_yielded:
        return content[already_yielded:]
    return ""
