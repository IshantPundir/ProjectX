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
from collections.abc import AsyncGenerator, AsyncIterable, Callable

import structlog

from models import SteeringObservation

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def parse_interview_output(
    text: AsyncIterable[str],
    on_observation: Callable[[SteeringObservation], None] | None = None,
    on_complete: Callable[[bool], None] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream ``response`` tokens to TTS immediately.

    Fires *on_observation* when the full observation is available.
    Fires *on_complete(had_observation)* when the stream finishes,
    regardless of whether an observation was found -- this allows
    the caller to track when each LLM output is fully processed.

    If the LLM outputs malformed JSON (not valid structured output),
    yields all text as-is to TTS (graceful degradation -- the candidate
    still hears a response, we just lose the observation).
    """
    acc = ""
    last_yielded = 0
    observation_fired = False

    stream_id = id(text)  # unique identifier for this stream invocation

    try:
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

            # Log the full response text the TTS will speak
            logger.info(
                "stream.tts_text",
                stream_id=stream_id,
                text=full_response[:200] if full_response else "(empty)",
                text_length=len(full_response),
            )

            # Fire observation callback
            obs_data = parsed.get("observation")
            if obs_data and on_observation:
                try:
                    obs = SteeringObservation.model_validate(obs_data)
                    logger.debug(
                        "stream.observation_extracted",
                        stream_id=stream_id,
                        summary=obs.answer_summary[:100],
                        wants_probe=obs.wants_to_probe,
                        disengaged=obs.candidate_disengaged,
                    )
                    on_observation(obs)
                    observation_fired = True
                except Exception as exc:
                    logger.warning(
                        "observation.parse_failed",
                        stream_id=stream_id,
                        error=str(exc),
                    )
            else:
                logger.debug(
                    "stream.no_observation",
                    stream_id=stream_id,
                    obs_data_present=obs_data is not None,
                )

        except json.JSONDecodeError:
            # Graceful degradation -- yield entire accumulated text as speech
            # if nothing was yielded from incremental extraction.
            if last_yielded == 0:
                yield acc
            logger.warning(
                "structured_output.parse_failed",
                stream_id=stream_id,
                text_length=len(acc),
                raw_preview=acc[:200],
            )
    finally:
        # Always fire on_complete so the caller knows this stream is done
        if on_complete:
            try:
                on_complete(observation_fired)
            except Exception:
                pass


def create_output_processor(
    on_observation: Callable[[SteeringObservation], None] | None = None,
    on_complete: Callable[[bool], None] | None = None,
) -> Callable[[AsyncIterable[str]], AsyncIterable[str]]:
    """Create a bound output processor for use with LiveKit AgentSession.

    *on_complete* fires when each LLM output stream finishes, with a
    boolean indicating whether an observation was extracted.  This lets
    the caller gate observation processing per-stream (e.g. skip
    observations from agent-initiated ``generate_reply`` outputs).
    """

    async def processor(text: AsyncIterable[str]) -> AsyncIterable[str]:
        async for chunk in parse_interview_output(text, on_observation, on_complete):
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
