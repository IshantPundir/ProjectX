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


# StreamingRenderHandle and SpeechAgent classes — filled in Tasks 8-9.
