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
