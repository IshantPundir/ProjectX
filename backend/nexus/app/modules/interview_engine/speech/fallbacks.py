"""Phase C — Static fallback handle + per-template fallback builders.

Triggered ONLY by OpenAI infrastructure errors (timeout, 5xx, pre-first-
token disconnect, 429). Hand-reviewed strings; ship in code, not data;
no runtime regex check (spec §0, design doc §11.5 v3).

The StaticFallbackHandle implements the SpeechRenderHandle Protocol with
pre-resolved futures and a single-chunk commit() iterator — indistinguishable
from a live LLM-rendered handle to consumers.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable
from typing import TYPE_CHECKING

from app.modules.interview_engine.event_kinds import SPEECH_FALLBACK_USED
from app.modules.interview_engine.speech.agent import (
    RenderMetadata,
    SpeechRenderErrorReason,
)

if TYPE_CHECKING:
    from app.modules.interview_engine.event_log import EventCollector


def _wall_ms() -> int:
    return int(time.time() * 1000)


class StaticFallbackHandle:
    """SpeechRenderHandle Protocol implementation backed by a static string.

    All futures are pre-resolved at construction time. commit() returns an
    AsyncIterable that yields exactly one chunk (the entire text) and stops.
    cancel() is a no-op (nothing to cancel — no Task running).

    SPEECH_FALLBACK_USED envelope event is emitted at construction time
    (Pin 1 — caller doesn't have to remember).
    """

    def __init__(
        self,
        *,
        text: str,
        template_name: str,
        template_version: str,
        failure_reason: SpeechRenderErrorReason,
        retries_attempted: int,
        render_id: str,
        collector: "EventCollector",
        model: str = "<fallback-no-llm-call>",
    ) -> None:
        self._text = text
        self._committed = False
        self._cancelled = False

        # Prefer the currently running loop; fall back to creating a fresh
        # loop reference for sync construction (e.g. from synchronous test
        # contexts). asyncio.get_event_loop() is deprecated on 3.13 when no
        # loop is running, so we handle both paths explicitly.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        self._metadata_fut: asyncio.Future[RenderMetadata] = loop.create_future()
        self._completed_text_fut: asyncio.Future[str] = loop.create_future()

        self._metadata_fut.set_result(
            RenderMetadata(
                render_id=render_id,
                template_name=template_name,
                template_version=template_version,
                model=model,
                latency_first_token_ms=None,
                latency_last_token_ms=None,
                tokens_in=None,
                tokens_out=None,
                length_words=len(text.split()),
                playout_duration_ms=None,
                was_fallback=True,
                retries=retries_attempted,
            )
        )
        self._completed_text_fut.set_result(text)

        # Pin 1: emit SPEECH_FALLBACK_USED at construction time.
        collector.append(
            kind=SPEECH_FALLBACK_USED,
            payload={
                "render_id": render_id,
                "template_name": template_name,
                "template_version": template_version,
                "reason": failure_reason,
                "retries_attempted": retries_attempted,
            },
            wall_ms=_wall_ms(),
        )

    async def ready_to_commit(self) -> None:
        return  # immediate

    def commit(self) -> AsyncIterable[str]:
        if self._cancelled:
            raise RuntimeError("Cannot commit a cancelled handle")
        if self._committed:
            raise RuntimeError("commit() may only be called once")
        self._committed = True

        async def _yield_once() -> AsyncIterator[str]:
            yield self._text

        return _yield_once()

    async def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_committed(self) -> bool:
        return self._committed

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def metadata(self) -> asyncio.Future[RenderMetadata]:
        return self._metadata_fut

    @property
    def completed_text(self) -> asyncio.Future[str]:
        return self._completed_text_fut


# ---------------------------------------------------------------------------
# Per-template fallback string builders. Hand-reviewed for outcome-neutrality.
# Code review enforces; no runtime regex check (spec §0, design doc §11.5 v3).
# ---------------------------------------------------------------------------


def _intro_fallback(*, target_duration_minutes: int, **_) -> str:
    """Parameterized — NEVER hardcode the duration (spec §4.1 Bug 2)."""
    return (
        f"Hi, I'll be running a short technical screen with you today. "
        f"We'll be about {target_duration_minutes} minutes. "
        f"Take your time. Let's get started."
    )


def _ask_question_standard_fallback(*, question_text: str, **_) -> str:
    """QuestionConfig.text is recruiter-validated; fallback asks verbatim."""
    return question_text


_WRAP_NORMAL_FALLBACK: str = (
    "That's everything from my side. The recruiting team will be "
    "in touch with next steps."
)


_FALLBACK_BUILDERS: dict[str, Callable[..., str]] = {
    "intro": _intro_fallback,
    "ask_question_standard": _ask_question_standard_fallback,
    "wrap_normal": lambda **_: _WRAP_NORMAL_FALLBACK,
}


def build_fallback_text(*, template_name: str, **inputs) -> str:
    """Returns the fallback string for a given template.

    Raises KeyError on unknown template_name (programmer error — fail loud)."""
    return _FALLBACK_BUILDERS[template_name](**inputs)
