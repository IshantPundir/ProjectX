"""Phase C — SpeechAgent class + SpeechRenderHandle Protocol + supporting types.

The SpeechAgent class itself + StreamingRenderHandle implementation is
filled in by Tasks 8-9. This file establishes the Protocol surface and
type contracts that StaticFallbackHandle (Task 6) and the orchestrator
wiring (Task 11) depend on.

Protocol structure: spec §2.2.
Two implementations:
    - StreamingRenderHandle (in this module, Task 8)
    - StaticFallbackHandle (in speech/fallbacks.py, Task 6)
Both satisfy the SpeechRenderHandle Protocol structurally.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class RenderMetadata:
    """Per-render metadata, resolved into handle.metadata Future at appropriate
    gate (live: stream-close + consumer-finish; fallback: pre-resolved at
    construction).

    For fallback handles, latency_first_token_ms / latency_last_token_ms /
    tokens_in / tokens_out are None (Pin 2 in spec §4.5) — analytics
    differentiate via was_fallback flag without floor-spike artifacts."""
    render_id: str
    template_name: str
    template_version: str
    model: str
    latency_first_token_ms: int | None
    latency_last_token_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    length_words: int
    playout_duration_ms: int | None
    was_fallback: bool
    retries: int


SpeechRenderErrorReason = Literal[
    "template_not_found",
    "placeholder_missing",
    "openai_timeout",
    "openai_5xx",
    "openai_connection_dropped_pre_first_token",
    "openai_429",
]


class SpeechRenderError(Exception):
    """Raised by SpeechAgent.render() synchronously for programmer errors,
    or by handle.ready_to_commit() for post-retry-exhaustion infrastructure
    errors. Caught only at StructuredInterviewAgent._consume_pending_or_render
    (spec §4.3)."""

    def __init__(
        self,
        *,
        reason: SpeechRenderErrorReason,
        render_id: str | None = None,
    ) -> None:
        super().__init__(f"SpeechRenderError(reason={reason})")
        self.reason: SpeechRenderErrorReason = reason
        self.render_id: str | None = render_id


@runtime_checkable
class SpeechRenderHandle(Protocol):
    """Single-use handle. Three terminal states: completed (committed and
    drained), cancelled, errored. Idempotent cancel(); commit() can only
    fire once. See spec §2.2 + §2.3."""

    async def ready_to_commit(self) -> None: ...
    def commit(self) -> AsyncIterable[str]: ...
    async def cancel(self) -> None: ...

    @property
    def is_committed(self) -> bool: ...
    @property
    def is_cancelled(self) -> bool: ...
    @property
    def metadata(self) -> asyncio.Future[RenderMetadata]: ...
    @property
    def completed_text(self) -> asyncio.Future[str]: ...


import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import openai
import structlog

from app.modules.interview_engine.event_kinds import (
    SPEECH_RENDERED,
    SPEECH_STREAM_INTERRUPTED,
)
from app.modules.interview_engine.event_log import EventCollector

log = structlog.get_logger("interview-engine.speech.agent")


_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+[A-Z]")
_MAX_PREFIX_TOKENS = 100


class _PreFirstTokenFailure(Exception):
    """Internal — used by _drive to signal pre-first-token failure for retry."""
    def __init__(self, *, reason: SpeechRenderErrorReason) -> None:
        self.reason = reason


_State = Literal[
    "opening",
    "buffering_prefix",
    "ready",
    "errored_pre_first_token",
    "committed",
    "cancelled",
    "completed",
]


class StreamingRenderHandle:
    """Live-LLM SpeechRenderHandle implementation (Option β: prefix-pipe).

    State machine per spec §2.5. Owns an internal asyncio.Task (`_drive`)
    that consumes the OpenAI stream, populates the prefix buffer, and
    resolves futures at the appropriate gates."""

    def __init__(
        self,
        *,
        client: openai.AsyncOpenAI,
        model: str,
        effort: str | None,
        prompt: str,
        template_name: str,
        template_version: str,
        render_id: str,
        collector: EventCollector,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._prompt = prompt
        self._template_name = template_name
        self._template_version = template_version
        self._render_id = render_id
        self._collector = collector

        self._state: _State = "opening"
        self._prefix_buffer: list[str] = []
        self._live_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._ready_event = asyncio.Event()
        self._cancel_event = asyncio.Event()
        self._error: SpeechRenderError | None = None

        # Prefer the currently running loop; fall back to creating a fresh
        # loop reference for sync construction (e.g. from synchronous test
        # contexts). asyncio.get_event_loop() is deprecated on 3.13 when no
        # loop is running, so we handle both paths explicitly. Same pattern
        # as StaticFallbackHandle (Task 6).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        self._metadata_fut: asyncio.Future[RenderMetadata] = loop.create_future()
        self._completed_text_fut: asyncio.Future[str] = loop.create_future()

        self._committed = False
        self._cancelled = False
        self._tokens_received = 0
        self._first_token_wall_ms: int | None = None
        self._stream_close_wall_ms: int | None = None
        self._consumer_finish_wall_ms: int | None = None
        self._tokens_in: int | None = None
        self._tokens_out: int | None = None
        self._completed_text_buf: list[str] = []
        self._retries_attempted: int = 0
        self._consumer_drained_naturally: bool = False

        self._task: asyncio.Task[None] = asyncio.create_task(self._drive())

    async def ready_to_commit(self) -> None:
        ready_task = asyncio.create_task(self._ready_event.wait())
        cancel_task = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            {ready_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()

        if self._cancel_event.is_set():
            raise asyncio.CancelledError()
        if self._error is not None:
            raise self._error
        # Otherwise: ready
        return

    def commit(self) -> AsyncIterable[str]:
        if self._cancelled:
            raise RuntimeError("Cannot commit a cancelled handle")
        if self._committed:
            raise RuntimeError("commit() may only be called once")
        if self._state not in ("ready",):
            raise RuntimeError(f"Cannot commit from state {self._state}")
        self._committed = True
        self._state = "committed"

        return self._joined_iterator()

    async def _joined_iterator(self) -> AsyncIterator[str]:
        # 1) Yield buffered prefix
        for chunk in self._prefix_buffer:
            yield chunk
        # 2) Pipe the live stream
        while True:
            chunk = await self._live_queue.get()
            if chunk is None:  # sentinel: stream closed or interrupted
                # Mark natural drain only if cancellation didn't trigger
                # this sentinel. If cancel_event is set when we hit the
                # sentinel, the iteration is being aborted (cancellation
                # pushed the sentinel to unblock us). This is what lets
                # _maybe_emit_rendered distinguish played (commit ran) vs
                # played_to_completion (commit ran AND drained naturally),
                # per spec §3.5 sub-case 3.
                if not self._cancel_event.is_set():
                    self._consumer_drained_naturally = True
                break
            yield chunk
        self._consumer_finish_wall_ms = _wall_ms()
        self._maybe_emit_rendered()

    async def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._cancel_event.set()
        self._task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

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

    async def _drive(self) -> None:
        """OpenAI streaming consumer. State machine per spec §2.5."""
        try:
            for attempt in range(2):
                self._retries_attempted = attempt  # 0 on first try, 1 on retry
                try:
                    await self._open_stream_and_buffer_prefix(attempt=attempt)
                    return
                except _PreFirstTokenFailure as exc:
                    if attempt == 0 and exc.reason != "openai_429":
                        log.warning(
                            "speech.render.retry",
                            template=self._template_name,
                            reason=exc.reason,
                            attempt=1,
                            render_id=self._render_id,
                        )
                        continue
                    self._fail_pre_first_token(reason=exc.reason)
                    return
        except asyncio.CancelledError:
            # Cancellation during _drive — close everything cleanly.
            # CancelledError MUST come before the broad except below so
            # that cooperative cancellation is propagated to the caller
            # rather than swallowed by the defensive openai_5xx bucket.
            self._state = "cancelled"
            self._live_queue.put_nowait(None)
            raise
        except Exception as exc:  # noqa: BLE001
            # Defensive bucket: any unexpected failure in _drive must NOT
            # leave the consumer hanging on _ready_event. Surface as an
            # openai_5xx-class error so ready_to_commit() raises and the
            # consumption helper falls back. Also push the live-queue
            # sentinel so any committed consumer that might already be
            # iterating gets unblocked. _fail_pre_first_token() is
            # idempotent in effect (re-setting an already-set Event is a
            # no-op), so the pre-ready path stays correct even if the
            # error happens after _ready_event was set (in which case
            # _error stays None for those callers — but ready_to_commit
            # already returned, so they won't observe inconsistency).
            log.error(
                "speech.render.drive_unexpected_error",
                render_id=self._render_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._fail_pre_first_token(reason="openai_5xx")
            self._live_queue.put_nowait(None)

    async def _open_stream_and_buffer_prefix(self, *, attempt: int) -> None:
        """Opens the stream, buffers the prefix, transitions to ready,
        then continues piping into the live queue until stream closes.

        IMPORTANT exception ordering: APITimeoutError must be caught before
        APIConnectionError because the former is a subclass of the latter
        (verified against openai-python source). Reordering for "alphabetical
        neatness" would silently route timeouts through the connection-error
        path."""
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": self._prompt}],
        }
        if self._effort:
            request_kwargs["reasoning_effort"] = self._effort

        try:
            stream = await self._client.chat.completions.create(**request_kwargs)
        except openai.APITimeoutError as e:
            raise _PreFirstTokenFailure(reason="openai_timeout") from e
        except openai.RateLimitError as e:
            raise _PreFirstTokenFailure(reason="openai_429") from e
        except (openai.APIConnectionError, openai.APIError) as e:
            raise _PreFirstTokenFailure(reason="openai_5xx") from e

        self._state = "buffering_prefix"
        prefix_text = ""
        try:
            async for chunk in stream:
                if self._cancel_event.is_set():
                    return
                # Track first-token timing
                if chunk.choices and chunk.choices[0].delta.content:
                    if self._first_token_wall_ms is None:
                        self._first_token_wall_ms = _wall_ms()
                    delta = chunk.choices[0].delta.content
                    self._tokens_received += 1
                    self._completed_text_buf.append(delta)

                    if self._state == "buffering_prefix":
                        prefix_text += delta
                        self._prefix_buffer.append(delta)
                        # Boundary detection: terminator + space + capital
                        m = _SENTENCE_BOUNDARY_RE.search(prefix_text)
                        if m or self._tokens_received >= _MAX_PREFIX_TOKENS:
                            self._state = "ready"
                            self._ready_event.set()
                    else:
                        # state is "ready" or "committed" — pipe live
                        self._live_queue.put_nowait(delta)

                # Capture usage on terminal chunk
                if chunk.usage:
                    self._tokens_in = chunk.usage.prompt_tokens
                    self._tokens_out = chunk.usage.completion_tokens
        except (openai.APIConnectionError, openai.APIError, asyncio.IncompleteReadError) as e:
            if self._first_token_wall_ms is None:
                raise _PreFirstTokenFailure(
                    reason="openai_connection_dropped_pre_first_token"
                ) from e
            # Post-first-token: non-recoverable, truncate
            log.warning(
                "speech.stream_interrupted",
                render_id=self._render_id,
                tokens_received=self._tokens_received,
                reason=str(type(e).__name__),
            )
            self._collector.append(
                kind=SPEECH_STREAM_INTERRUPTED,
                payload={
                    "render_id": self._render_id,
                    "tokens_received": self._tokens_received,
                    "reason": "openai_connection_dropped_post_first_token",
                },
                wall_ms=_wall_ms(),
            )
        finally:
            try:
                await stream.close()
            except Exception:  # noqa: BLE001
                pass

        self._stream_close_wall_ms = _wall_ms()
        # End-of-stream sentinel for the consumer
        self._live_queue.put_nowait(None)
        # If we never reached ready (e.g., empty stream), error out
        if not self._ready_event.is_set():
            self._fail_pre_first_token(reason="openai_connection_dropped_pre_first_token")
            return
        self._state = "completed"

    def _fail_pre_first_token(self, *, reason: SpeechRenderErrorReason) -> None:
        self._state = "errored_pre_first_token"
        self._error = SpeechRenderError(reason=reason, render_id=self._render_id)
        self._ready_event.set()  # unblock ready_to_commit so it raises

    def _maybe_emit_rendered(self) -> None:
        """Emit SPEECH_RENDERED at the LATER of stream-close + consumer-finish.
        Both must be set; we check this on every consumer-finish call (Add 2)."""
        if self._stream_close_wall_ms is None or self._consumer_finish_wall_ms is None:
            return
        if self._metadata_fut.done():
            return  # already emitted

        completed_text = "".join(self._completed_text_buf)
        latency_first = (
            self._first_token_wall_ms
            if self._first_token_wall_ms is not None
            else None
        )
        latency_last = self._stream_close_wall_ms
        playout_duration = self._consumer_finish_wall_ms - latency_last if latency_last else None

        # Spec §3.5 sub-case 3: distinguish `played` (commit ran — we
        # attempted playback) from `played_to_completion` (commit ran AND
        # the joined iterator drained naturally to its sentinel without
        # cancellation). A mid-PLAYOUT disconnect after commit yields
        # committed=true, played=true, played_to_completion=false.
        played = self._committed
        played_to_completion = self._committed and self._consumer_drained_naturally

        md = RenderMetadata(
            render_id=self._render_id,
            template_name=self._template_name,
            template_version=self._template_version,
            model=self._model,
            latency_first_token_ms=latency_first,
            latency_last_token_ms=latency_last,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            length_words=len(completed_text.split()),
            playout_duration_ms=playout_duration,
            was_fallback=False,
            retries=self._retries_attempted,
        )
        self._metadata_fut.set_result(md)
        self._completed_text_fut.set_result(completed_text)

        self._collector.append(
            kind=SPEECH_RENDERED,
            payload={
                "render_id": self._render_id,
                "template_name": self._template_name,
                "template_version": self._template_version,
                "model": self._model,
                "latency_first_token_ms": latency_first,
                "latency_last_token_ms": latency_last,
                "tokens_in": self._tokens_in,
                "tokens_out": self._tokens_out,
                "length_words": len(completed_text.split()),
                "playout_duration_ms": playout_duration,
                "committed": self._committed,
                "played": played,
                "played_to_completion": played_to_completion,
                "was_fallback": False,
                "retries": self._retries_attempted,
            },
            wall_ms=_wall_ms(),
        )


def _wall_ms() -> int:
    return int(time.time() * 1000)
