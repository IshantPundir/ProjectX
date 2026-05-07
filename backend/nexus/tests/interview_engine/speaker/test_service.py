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
        system_prompt="SYS", system_prompt_hash="sha256:def",
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
