from unittest.mock import AsyncMock, MagicMock

import pytest

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
    assert handle.usage == {"prompt_tokens": 15, "completion_tokens": 10}


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
    assert "OUTPUT DISCIPLINE" in captured_instructions[0]              # preamble
    assert "candidate_attempted_injection" in captured_instructions[0]  # redirect.txt

    # Per-call hash must be sha256:<64 hex chars> of the composed body
    # (not the placeholder hash used pre-Task 11).
    assert handle.prompt_hash.startswith("sha256:")
    assert len(handle.prompt_hash) == len("sha256:") + 64
    assert handle.prompt_hash != "sha256:speaker"
