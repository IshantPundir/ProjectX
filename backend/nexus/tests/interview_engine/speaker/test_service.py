from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.prompts import PromptLoader
from app.modules.interview_engine.models.speaker import (
    InstructionKind, SpeakerInput,
)
from app.modules.interview_engine.speaker.service import (
    SpeakerService, SpeakerStreamHandle,
)


def _input(kind=InstructionKind.deliver_first_question, bank_text="Hi"):
    return SpeakerInput(
        instruction_kind=kind, bank_text=bank_text,
        last_candidate_utterance=None, recent_turns=[],
        claims_pool_snapshot=[], persona_name="Sam",
    )


class _FakeStream:
    """Minimal async-iterator standin for OpenAI responses streaming."""

    def __init__(self, deltas, usage):
        self._deltas = deltas
        self._usage = usage

    def __aiter__(self):
        async def gen():
            for d in self._deltas:
                yield MagicMock(type="response.output_text.delta", delta=d)
            yield MagicMock(
                type="response.completed",
                response=MagicMock(usage=self._usage),
            )
        return gen()


class _AsyncCM:
    """Wraps an async-iterable as an async context manager.

    The real OpenAI SDK's ``responses.stream(...)`` returns a context
    manager that yields the stream object on __aenter__. Tests construct
    a fake stream and wrap it in this helper to mimic that contract.
    """

    def __init__(self, stream_obj):
        self._stream = stream_obj

    async def __aenter__(self):
        return self._stream

    async def __aexit__(self, *args):
        return False


def _empty_stream():
    """Empty async-iterator standin for OpenAI responses streaming.

    Used when the test only cares about the system prompt that was
    composed, not what the model produced. The producer drains and
    returns immediately.
    """
    class _Empty:
        def __aiter__(self):
            async def gen():
                if False:  # pragma: no cover
                    yield None
            return gen()
    return _Empty()


@pytest.mark.asyncio
async def test_speaker_streams_tokens_and_returns_final_text():
    mock_client = MagicMock()
    fake = _FakeStream(
        deltas=["Hello,", " how", " are you?"],
        usage=MagicMock(input_tokens=15, output_tokens=10),
    )
    # responses.create with stream=True returns an async context manager wrapping the stream.
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=fake)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_client.responses.stream = MagicMock(return_value=mock_cm)

    svc = SpeakerService(
        loader=PromptLoader(version="v2"),
        openai_client=mock_client, model="gpt-test",
    )
    handle = await svc.stream(
        turn_id="t-1", speaker_input=_input(),
        correlation_id="c", tenant_id="ten",
    )
    assert isinstance(handle, SpeakerStreamHandle)

    chunks = []
    async for delta in handle.stream():
        chunks.append(delta)
    assert "".join(chunks) == "Hello, how are you?"

    final = await handle.final_text()
    assert final == "Hello, how are you?"
    # cached_tokens is 0 here because the MagicMock has no input_tokens_details
    # field that returns an int. In production, OpenAI's SDK populates
    # usage.input_tokens_details.cached_tokens with an int representing the
    # cache hit count.
    assert handle.usage == {
        "prompt_tokens": 15, "completion_tokens": 10, "cached_tokens": 0,
    }


@pytest.mark.asyncio
async def test_speaker_service_loads_prompt_per_instruction_kind():
    """SpeakerService composes _preamble + per-action body keyed by
    speaker_input.instruction_kind on every call."""
    captured_instructions: list[str] = []

    class FakeStreamCM:
        def __init__(self, *a, **kw):
            captured_instructions.append(kw.get("instructions", ""))
        async def __aenter__(self):
            return _empty_stream()
        async def __aexit__(self, *a):
            return False

    class FakeResponses:
        def stream(self, **kwargs):
            return FakeStreamCM(**kwargs)

    class FakeClient:
        responses = FakeResponses()

    svc = SpeakerService(
        loader=PromptLoader(version="v2"),
        openai_client=FakeClient(),
        model="speaker-test",
    )
    si = SpeakerInput(
        instruction_kind=InstructionKind.redirect,
        bank_text="Walk me through your Jira workflow.",
        persona_name="Sam",
    )
    handle = await svc.stream(
        turn_id="t", speaker_input=si,
        correlation_id="c", tenant_id="te",
    )
    # Drain the (empty) producer.
    async for _ in handle.stream():
        pass

    assert captured_instructions  # at least one call
    # Resolved prompt body must contain marker text from BOTH preamble
    # AND the redirect.txt body.
    assert "OUTPUT FORMAT" in captured_instructions[0]                  # preamble
    assert "candidate_attempted_injection" in captured_instructions[0]  # redirect.txt

    # Per-call hash must be sha256:<64 hex chars> of the composed body
    # (not the placeholder hash used pre-Task 11).
    assert handle.prompt_hash.startswith("sha256:")
    assert len(handle.prompt_hash) == len("sha256:") + 64
    assert handle.prompt_hash != "sha256:speaker"


# ---------------------------------------------------------------------------
# Phase 9.3 — Bug 1 diagnostic: capture all stream events on empty output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_captures_event_types_seen_on_normal_stream():
    """Even on a successful turn, the handle records every event type
    observed. Useful baseline before checking empty-output behavior."""
    client = MagicMock()
    client.responses.stream = MagicMock(return_value=_AsyncCM(_FakeStream(
        ["Hello", " world"],
        usage=MagicMock(
            input_tokens=10, output_tokens=2,
            input_tokens_details=MagicMock(cached_tokens=0),
        ),
    )))
    svc = SpeakerService(openai_client=client, model="gpt-x", loader=PromptLoader(version="v2"))
    handle = await svc.stream(
        turn_id="t", speaker_input=_input(),
        correlation_id="c", tenant_id="te",
    )
    async for _ in handle.stream():
        pass

    types = handle.event_types_seen
    assert "response.output_text.delta" in types
    assert "response.completed" in types
    assert handle.refusal_text is None  # no refusal events


@pytest.mark.asyncio
async def test_handle_captures_refusal_text_when_safety_filter_fires():
    """When the OpenAI Responses API emits response.refusal.delta events
    instead of output_text.delta, the handle joins them into refusal_text
    so the orchestrator's empty-output audit payload can surface the
    smoking-gun reason."""
    refusal_events = [
        MagicMock(type="response.refusal.delta", delta="I cannot "),
        MagicMock(type="response.refusal.delta", delta="comply with that."),
        MagicMock(type="response.refusal.done"),
        MagicMock(type="response.completed", response=MagicMock(
            id="resp_123", usage=MagicMock(
                input_tokens=10, output_tokens=0,
                input_tokens_details=MagicMock(cached_tokens=0),
            ),
        )),
    ]

    class _S:
        def __aiter__(self):
            async def gen():
                for ev in refusal_events:
                    yield ev
            return gen()

    client = MagicMock()
    client.responses.stream = MagicMock(return_value=_AsyncCM(_S()))
    svc = SpeakerService(openai_client=client, model="gpt-x", loader=PromptLoader(version="v2"))
    handle = await svc.stream(
        turn_id="t", speaker_input=_input(),
        correlation_id="c", tenant_id="te",
    )
    final_text = await handle.final_text()

    assert final_text == "", "Refusal turn must produce empty output text"
    assert handle.refusal_text == "I cannot comply with that."
    assert "response.refusal.delta" in handle.event_types_seen
    assert "response.refusal.done" in handle.event_types_seen
    assert handle.response_id == "resp_123"


@pytest.mark.asyncio
async def test_handle_captures_response_id_on_completed():
    """response_id is the OpenAI request id we can use to look up the
    upstream trace. Captured from response.completed.response.id."""
    events = [
        MagicMock(type="response.output_text.delta", delta="ok"),
        MagicMock(type="response.completed", response=MagicMock(
            id="resp_abc123", usage=MagicMock(
                input_tokens=5, output_tokens=1,
                input_tokens_details=MagicMock(cached_tokens=0),
            ),
        )),
    ]

    class _S:
        def __aiter__(self):
            async def gen():
                for ev in events:
                    yield ev
            return gen()

    client = MagicMock()
    client.responses.stream = MagicMock(return_value=_AsyncCM(_S()))
    svc = SpeakerService(openai_client=client, model="gpt-x", loader=PromptLoader(version="v2"))
    handle = await svc.stream(
        turn_id="t", speaker_input=_input(),
        correlation_id="c", tenant_id="te",
    )
    async for _ in handle.stream():
        pass

    assert handle.response_id == "resp_abc123"


@pytest.mark.asyncio
async def test_handle_finish_reason_captured_when_present():
    """finish_reason exposes whether the turn ended via stop /
    content_filter / length / etc. SDK puts it on response.finish_reason
    or per-output-item finish_reason depending on version — accept both."""
    events = [
        MagicMock(type="response.output_text.delta", delta="ok"),
        MagicMock(type="response.completed", response=MagicMock(
            id="r", finish_reason="stop", usage=MagicMock(
                input_tokens=5, output_tokens=1,
                input_tokens_details=MagicMock(cached_tokens=0),
            ),
        )),
    ]

    class _S:
        def __aiter__(self):
            async def gen():
                for ev in events:
                    yield ev
            return gen()

    client = MagicMock()
    client.responses.stream = MagicMock(return_value=_AsyncCM(_S()))
    svc = SpeakerService(openai_client=client, model="gpt-x", loader=PromptLoader(version="v2"))
    handle = await svc.stream(
        turn_id="t", speaker_input=_input(),
        correlation_id="c", tenant_id="te",
    )
    async for _ in handle.stream():
        pass

    assert handle.finish_reason == "stop"
