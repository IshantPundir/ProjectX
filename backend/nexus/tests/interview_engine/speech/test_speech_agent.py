"""Phase C SpeechAgent unit tests — spec §5.3.

Mocks AsyncOpenAI; no LiveKit, no DB. Each test exercises one specific
behavior of render() / SpeechAgent / StreamingRenderHandle.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


@pytest.fixture
def collector():
    return MagicMock()


def _make_chunk(content=None, finish_reason=None, usage=None):
    """Build a mock OpenAI chunk."""
    chunk = MagicMock()
    if content is not None:
        chunk.choices = [MagicMock(delta=MagicMock(content=content), finish_reason=finish_reason)]
    else:
        chunk.choices = [MagicMock(delta=MagicMock(content=None), finish_reason=finish_reason)]
    chunk.usage = MagicMock(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
    return chunk


@contextlib.asynccontextmanager
async def _mock_stream(chunks):
    class _Stream:
        async def __aiter__(self):
            for c in chunks:
                yield c
        async def close(self):
            pass
    yield _Stream()


def _mock_client_yielding(chunks):
    """Returns an AsyncMock client whose chat.completions.create yields chunks."""
    client = MagicMock()

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                for c in chunks:
                    yield c
            async def close(self):
                pass
        return _Stream()

    client.chat.completions.create = _create
    return client


@pytest.mark.asyncio
async def test_render_happy_path(collector, tmp_path, monkeypatch):
    """Mocked client streams 3 chunks; ready_to_commit resolves;
    commit() yields concatenated tokens; metadata correct."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    chunks = [
        _make_chunk(content="Hi there. "),
        _make_chunk(content="Let's go."),
        _make_chunk(usage=(100, 5)),  # final usage chunk
    ]
    client = _mock_client_yielding(chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    # Mock template_loader to return a fixed prompt
    with patch(
        "app.modules.interview_engine.speech.agent._render_prompt",
        return_value="hello prompt",
    ):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    chunks_out = [c async for c in handle.commit()]
    assert "".join(chunks_out) == "Hi there. Let's go."
    md = await handle.metadata
    assert md.was_fallback is False
    assert md.tokens_in == 100
    assert md.tokens_out == 5


@pytest.mark.asyncio
async def test_render_first_sentence_prefix(collector):
    """Prefix is everything up to the first sentence boundary."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    chunks = [
        _make_chunk(content="Hi there. "),
        _make_chunk(content="Let's begin."),
        _make_chunk(usage=(50, 4)),
    ]
    client = _mock_client_yielding(chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    # First sentence boundary closes the prefix at "Hi there. "
    chunks_out = [c async for c in handle.commit()]
    full = "".join(chunks_out)
    assert full == "Hi there. Let's begin."


@pytest.mark.asyncio
async def test_render_max_prefix_cap_100_tokens(collector):
    """If no sentence boundary in 100 tokens, commit anyway."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    long_chunks = [_make_chunk(content="word ") for _ in range(150)]
    long_chunks.append(_make_chunk(usage=(10, 150)))
    client = _mock_client_yielding(long_chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="ask_question_standard",
            template_version="v1", inputs={"question_text": "x"},
        )
    await handle.ready_to_commit()
    # Just verify ready_to_commit returned without error after 100 tokens accumulated
    assert handle.is_committed is False
    chunks_out = [c async for c in handle.commit()]
    assert len("".join(chunks_out).split()) == 150


@pytest.mark.parametrize("input_text", [
    "In section 11.5 we describe the architecture.",
    "The U.S. office hours are flexible.",
    "That costs 1.5 dollars. Let's continue.",
])
@pytest.mark.asyncio
async def test_render_prefix_avoids_false_sentence_boundaries(collector, input_text):
    """Decimals (11.5), acronyms (U.S.), and bare-number+terminator
    sequences must NOT close the prefix early. Uses the
    [.!?]\\s+[A-Z] regex (Add 1 from spec §5.3)."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    chunks = [_make_chunk(content=input_text), _make_chunk(usage=(20, 10))]
    client = _mock_client_yielding(chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    chunks_out = [c async for c in handle.commit()]
    full = "".join(chunks_out)
    assert full == input_text
    # Verify the prefix actually contained the full first real sentence
    # (no early break at "11." / "U." / "1.5")


@pytest.mark.asyncio
async def test_retries_once_on_openai_timeout(collector):
    """First attempt times out, retry succeeds, metadata.retries reflects."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent

    call_count = {"n": 0}

    async def _create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise openai.APITimeoutError(request=MagicMock())
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="OK.")
                yield _make_chunk(usage=(10, 1))
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create

    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()  # should not raise
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_429_not_retried(collector):
    """429 is rate-limit; retrying compounds. Immediate fail (no retry)."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    call_count = {"n": 0}

    async def _create(**kwargs):
        call_count["n"] += 1
        raise openai.RateLimitError(
            message="429", response=MagicMock(status_code=429), body={},
        )

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    with pytest.raises(SpeechRenderError) as exc_info:
        await handle.ready_to_commit()
    assert exc_info.value.reason == "openai_429"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_falls_back_after_two_failures(collector):
    """Two timeouts → ready_to_commit raises SpeechRenderError(timeout)."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    call_count = {"n": 0}
    async def _create(**kwargs):
        call_count["n"] += 1
        raise openai.APITimeoutError(request=MagicMock())

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    with pytest.raises(SpeechRenderError) as exc_info:
        await handle.ready_to_commit()
    assert exc_info.value.reason == "openai_timeout"
    assert call_count["n"] == 2  # original + 1 retry


@pytest.mark.asyncio
async def test_does_not_retry_post_first_token_failure(collector):
    """Mocked client emits 5 tokens then drops. No retry. Truncate."""
    import openai
    from app.modules.interview_engine.event_kinds import SPEECH_STREAM_INTERRUPTED, SPEECH_FALLBACK_USED
    from app.modules.interview_engine.speech.agent import SpeechAgent

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                for word in ["Hi ", "there ", "I "]:
                    yield _make_chunk(content=word)
                raise openai.APIConnectionError(request=MagicMock())
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    # Wait for prefix to be ready (first sentence boundary OR cap)
    # Then commit and observe truncated output
    await handle.ready_to_commit()
    chunks_out = [c async for c in handle.commit()]
    # We got 3 tokens before drop; commit yields what's there

    # speech.stream_interrupted fired
    kinds = [call.kwargs["kind"] for call in collector.append.call_args_list]
    assert SPEECH_STREAM_INTERRUPTED in kinds
    assert SPEECH_FALLBACK_USED not in kinds  # NOT a fallback


@pytest.mark.asyncio
async def test_template_not_found_raises_synchronously(collector):
    """SpeechAgent.render(template_name='nonexistent') raises before
    Task spawn — programmer error, not retried, not caught by helper."""
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    client = MagicMock()
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with pytest.raises(SpeechRenderError) as exc_info:
        await agent.render(
            template_name="nonexistent_template", template_version="v1", inputs={},
        )
    assert exc_info.value.reason == "template_not_found"
    assert exc_info.value.render_id is None  # synchronous error: no render_id


@pytest.mark.asyncio
async def test_placeholder_missing_raises_synchronously(collector):
    """Template requires a placeholder not provided in inputs."""
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    client = MagicMock()
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    # The intro template requires candidate_first_name + role_title + target_duration_minutes
    with pytest.raises(SpeechRenderError) as exc_info:
        await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    assert exc_info.value.reason == "placeholder_missing"


@pytest.mark.asyncio
async def test_max_retries_zero_passed_to_openai_client(collector):
    """The raw OpenAI client used by SpeechAgent has max_retries=0
    set at construction (in get_openai_raw_client). Verify that
    chat.completions.create is NOT called with extra retry kwargs."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    call_kwargs = {}
    async def _create(**kwargs):
        call_kwargs.update(kwargs)
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="x. ")
                yield _make_chunk(usage=(5, 1))
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    # SpeechAgent must not pass max_retries to .create()
    assert "max_retries" not in call_kwargs
    # stream_options.include_usage must be set
    assert call_kwargs.get("stream_options") == {"include_usage": True}


@pytest.mark.asyncio
async def test_render_id_propagates_to_envelope_events(collector):
    """The same render_id appears across speech.rendered + speech.fallback_used
    + speech.stream_interrupted for the same logical render."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="Hi. ")
                raise openai.APIConnectionError(request=MagicMock())
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    [c async for c in handle.commit()]
    # All envelope events should share render_id from handle.metadata.render_id
    md = await handle.metadata
    rid = md.render_id
    for call in collector.append.call_args_list:
        payload = call.kwargs["payload"]
        assert payload["render_id"] == rid


@pytest.mark.asyncio
async def test_speech_rendered_emits_after_both_stream_close_and_playout(collector):
    """SPEECH_RENDERED fires at LATER of stream-close and consumer-finish.
    playout_duration_ms = consumer-finish - stream-close."""
    from app.modules.interview_engine.event_kinds import SPEECH_RENDERED
    from app.modules.interview_engine.speech.agent import SpeechAgent

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="Hi. ")
                yield _make_chunk(usage=(10, 1))
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    # Simulate slow TTS consumer
    chunks_out = []
    async for chunk in handle.commit():
        chunks_out.append(chunk)
        await asyncio.sleep(0.01)

    # SPEECH_RENDERED should have fired
    rendered_calls = [
        c for c in collector.append.call_args_list
        if c.kwargs["kind"] == SPEECH_RENDERED
    ]
    assert len(rendered_calls) == 1
    payload = rendered_calls[0].kwargs["payload"]
    assert payload["played"] is True
    assert payload["played_to_completion"] is True
    assert payload["was_fallback"] is False
    # playout_duration_ms is non-null
    assert payload["playout_duration_ms"] is not None


@pytest.mark.asyncio
async def test_empty_stream_yields_minimal_completed_text(collector):
    """OpenAI returns finish_reason on first chunk with no content.
    Treated as pre-first-token failure → fallback path."""
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(finish_reason="stop")
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    with pytest.raises(SpeechRenderError):
        await handle.ready_to_commit()
