"""Phase C handle Protocol + shape tests (per spec §5.4)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable

import pytest

from app.modules.interview_engine.speech.agent import (
    RenderMetadata,
    SpeechRenderError,
    SpeechRenderHandle,
)


def test_speech_render_error_carries_reason_and_render_id():
    """SpeechRenderError must expose reason + render_id (None for synchronous
    programmer errors, set for runtime errors)."""
    err = SpeechRenderError(reason="openai_timeout", render_id="abc-123")
    assert err.reason == "openai_timeout"
    assert err.render_id == "abc-123"

    sync_err = SpeechRenderError(reason="template_not_found")
    assert sync_err.reason == "template_not_found"
    assert sync_err.render_id is None


def test_render_metadata_fallback_fields_nullable():
    """Fallback handles populate metadata with null latency/token fields
    (Pin 2). Analytics differentiate via was_fallback."""
    md = RenderMetadata(
        render_id="abc",
        template_name="intro",
        template_version="v1",
        model="gpt-5-mini",
        latency_first_token_ms=None,
        latency_last_token_ms=None,
        tokens_in=None,
        tokens_out=None,
        length_words=12,
        playout_duration_ms=None,
        was_fallback=True,
        retries=1,
    )
    assert md.was_fallback is True
    assert md.latency_first_token_ms is None
    assert md.length_words == 12


def test_speech_render_handle_protocol_is_runtime_checkable():
    """The Protocol uses @runtime_checkable so isinstance() works for tests."""
    # A trivial stub class that matches the structural shape.
    class _StubHandle:
        async def ready_to_commit(self) -> None: ...
        def commit(self) -> AsyncIterable[str]: ...  # type: ignore[empty-body]
        async def cancel(self) -> None: ...
        is_committed = False
        is_cancelled = False

        @property
        def metadata(self) -> asyncio.Future[RenderMetadata]: ...  # type: ignore[empty-body]
        @property
        def completed_text(self) -> asyncio.Future[str]: ...  # type: ignore[empty-body]

    assert isinstance(_StubHandle(), SpeechRenderHandle)


def test_static_fallback_handle_satisfies_protocol():
    """StaticFallbackHandle must structurally satisfy SpeechRenderHandle."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="Hi there.",
        template_name="intro",
        template_version="v1",
        failure_reason="openai_timeout",
        retries_attempted=1,
        render_id="abc-123",
        collector=MagicMock(),
    )
    assert isinstance(h, SpeechRenderHandle)


@pytest.mark.asyncio
async def test_static_fallback_handle_pre_resolved_futures():
    """metadata + completed_text futures resolve immediately (no Task)."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="That's everything from my side.",
        template_name="wrap_normal",
        template_version="v1",
        failure_reason="openai_timeout",
        retries_attempted=1,
        render_id="abc",
        collector=MagicMock(),
    )
    assert h.metadata.done()
    assert h.completed_text.done()
    md = await h.metadata
    assert md.was_fallback is True
    assert md.length_words == 5
    assert md.tokens_in is None  # Pin 2: nullable for fallbacks
    assert (await h.completed_text) == "That's everything from my side."


@pytest.mark.asyncio
async def test_static_fallback_handle_commit_yields_one_chunk():
    """commit() returns an AsyncIterable yielding exactly one chunk = full text."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="Hi there, candidate.",
        template_name="intro",
        template_version="v1",
        failure_reason="openai_5xx",
        retries_attempted=1,
        render_id="abc",
        collector=MagicMock(),
    )
    chunks = [chunk async for chunk in h.commit()]
    assert chunks == ["Hi there, candidate."]
    assert h.is_committed
    # Re-committing must raise
    with pytest.raises(RuntimeError):
        h.commit()


@pytest.mark.asyncio
async def test_static_fallback_handle_cancel_is_idempotent_noop():
    """cancel() on a fallback handle is a no-op (no Task to cancel)."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="x",
        template_name="intro",
        template_version="v1",
        failure_reason="openai_timeout",
        retries_attempted=1,
        render_id="abc",
        collector=MagicMock(),
    )
    await h.cancel()
    await h.cancel()  # idempotent
    assert h.is_cancelled
    with pytest.raises(RuntimeError):
        h.commit()  # cannot commit after cancel


def test_static_fallback_handle_emits_fallback_used_on_construction():
    """Constructing the handle MUST emit speech.fallback_used (Pin 1).
    Render and consumer code never have to remember to fire this event."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.event_kinds import SPEECH_FALLBACK_USED
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    collector = MagicMock()
    StaticFallbackHandle(
        text="x",
        template_name="ask_question_standard",
        template_version="v1",
        failure_reason="openai_429",
        retries_attempted=1,
        render_id="render-abc",
        collector=collector,
    )
    collector.append.assert_called_once()
    call_kwargs = collector.append.call_args.kwargs
    assert call_kwargs["kind"] == SPEECH_FALLBACK_USED
    payload = call_kwargs["payload"]
    assert payload["render_id"] == "render-abc"
    assert payload["template_name"] == "ask_question_standard"
    assert payload["template_version"] == "v1"
    assert payload["reason"] == "openai_429"
    assert payload["retries_attempted"] == 1


@pytest.mark.asyncio
async def test_streaming_render_handle_satisfies_protocol():
    """StreamingRenderHandle must structurally satisfy SpeechRenderHandle."""
    from unittest.mock import MagicMock, AsyncMock
    from app.modules.interview_engine.speech.agent import StreamingRenderHandle

    h = StreamingRenderHandle(
        client=AsyncMock(),
        model="gpt-5-mini",
        effort=None,
        prompt="ignored",
        template_name="intro",
        template_version="v1",
        render_id="abc",
        collector=MagicMock(),
    )
    assert isinstance(h, SpeechRenderHandle)
    await h.cancel()  # ensure no leak


@pytest.mark.asyncio
async def test_streaming_render_handle_cancel_during_buffering(monkeypatch):
    """cancel() during buffering: ready_to_commit raises CancelledError
    (NOT SpeechRenderError); subsequent commit() raises RuntimeError."""
    from unittest.mock import MagicMock
    import openai
    from app.modules.interview_engine.speech.agent import StreamingRenderHandle

    # Mock client whose stream yields slowly; we cancel before completion.
    async def slow_stream(*_, **__):
        class _Stream:
            async def __aiter__(self):
                # Yield one delta then sleep forever
                from openai.types.chat import ChatCompletionChunk
                yield ChatCompletionChunk(
                    id="x", object="chat.completion.chunk", created=0, model="x",
                    choices=[{"index": 0, "delta": {"content": "Hi "}, "finish_reason": None}],
                )
                await asyncio.sleep(60)
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = slow_stream

    h = StreamingRenderHandle(
        client=client, model="gpt-5-mini", effort=None,
        prompt="x", template_name="intro", template_version="v1",
        render_id="abc", collector=MagicMock(),
    )

    # Spawn ready_to_commit and cancel before it resolves.
    r2c_task = asyncio.create_task(h.ready_to_commit())
    await asyncio.sleep(0.05)  # let _drive start
    await h.cancel()

    with pytest.raises(asyncio.CancelledError):
        await r2c_task

    # Subsequent commit() raises
    with pytest.raises(RuntimeError):
        h.commit()
    assert h.is_cancelled
