"""Gen-3 Ear — VAD pause gate + MultilingualModel text-EOU wrapper (B3).

Two public pieces:

1.  **SpeechActivity** — a pure, clock-agnostic pause detector.

    Tracks whether the candidate is currently speaking and how long they have
    been silent.  The caller (B4 Ear loop) feeds it LiveKit ``user_state``
    change events:

      * ``state == "speaking"`` → call ``on_speaking_started(t_ms)``
      * ``state == "listening"`` → call ``on_speaking_stopped(t_ms)``

    ``t_ms`` and ``now_ms`` are a monotonic session clock supplied by the
    caller — the class is fully clock-agnostic and therefore trivially
    unit-testable with a fake event stream.

    Interface::

        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        sa.on_speaking_stopped(t_ms=3000)
        sa.is_speaking          # → False
        sa.silence_ms(now_ms=3500)  # → 500

2.  **text_eou_probability** — a thin async wrapper around LiveKit's
    MultilingualModel (or any duck-typed equivalent exposing an async
    ``predict_end_of_turn``).

    Returns the float probability in ``[0, 1]`` on success, or ``None`` on
    **any** failure (including ``asyncio.TimeoutError`` and all other
    exceptions).  The ``None`` contract is load-bearing: the fusion ladder
    (``ear/ladder.py``) has an explicit Smart-Turn-only fallback for
    ``text_eou_prob=None``.

    **ChatContext construction** is the caller's responsibility.  The wrapper
    accepts a pre-built ``livekit.agents.llm.ChatContext`` so it stays thin
    and has no coupling to the ChatContext API.  B4 will construct the context
    from the session's conversation history before calling this wrapper.

    Interface::

        prob = await text_eou_probability(model, chat_ctx, timeout=1.0)
        # → float in [0, 1]  on success
        # → None             on any error / timeout

No livekit imports at module top level.  LiveKit types are only referenced
under ``TYPE_CHECKING`` so the FastAPI/nexus process (which does not load the
livekit engine plugins) can import this module without error.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    # Only for type annotations — never imported at runtime from this module.
    from livekit.agents import llm as lk_llm

log = structlog.get_logger("interview_engine.ear")


# ---------------------------------------------------------------------------
# SpeechActivity — pure pause detector
# ---------------------------------------------------------------------------


class SpeechActivity:
    """Clock-agnostic pause detector driven by LiveKit user-state events.

    The caller is responsible for mapping LiveKit ``VoiceActivityEvent``
    states to the two notification methods:

    * ``state == "speaking"``   → ``on_speaking_started(t_ms)``
    * ``state == "listening"``  → ``on_speaking_stopped(t_ms)``

    All time arguments are in **milliseconds** on a monotonic session clock
    supplied by the caller.  The class never reads the wall clock so it is
    deterministic and trivially testable.

    Initial state: **not speaking**, silence counter = 0 (no pause has
    occurred yet, so querying ``silence_ms`` before any speech event returns
    0 rather than an unbounded accumulation from session start).
    """

    __slots__ = ("_is_speaking", "_last_stopped_ms")

    def __init__(self) -> None:
        self._is_speaking: bool = False
        # None until the first speaking_stopped event.
        self._last_stopped_ms: int | None = None

    # ------------------------------------------------------------------
    # Event callbacks (B4 will wire these from LiveKit)
    # ------------------------------------------------------------------

    def on_speaking_started(self, t_ms: int) -> None:
        """Candidate began speaking at ``t_ms`` (session-monotonic ms).

        Resets the silence counter: while speaking ``silence_ms`` is 0.
        """
        self._is_speaking = True
        # Do NOT reset _last_stopped_ms here — the silence counter
        # is driven by _is_speaking, not by clearing the timestamp.

    def on_speaking_stopped(self, t_ms: int) -> None:
        """Candidate stopped speaking at ``t_ms`` (session-monotonic ms).

        Records the stop time so ``silence_ms`` can compute the elapsed
        pause duration.
        """
        self._is_speaking = False
        self._last_stopped_ms = t_ms

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def is_speaking(self) -> bool:
        """``True`` while the candidate is actively speaking."""
        return self._is_speaking

    def silence_ms(self, now_ms: int) -> int:
        """Milliseconds of silence since the last speaking stop.

        Returns:
            0   — while speaking OR before any speech has been detected.
            ≥0  — ``now_ms - last_stopped_ms`` (clamped to 0) after a pause.

        The result is always non-negative even if ``now_ms`` is slightly
        stale (caller clock anomalies are clamped to 0 rather than raising).
        """
        if self._is_speaking or self._last_stopped_ms is None:
            return 0
        return max(0, now_ms - self._last_stopped_ms)


# ---------------------------------------------------------------------------
# text_eou_probability — thin async wrapper
# ---------------------------------------------------------------------------


async def text_eou_probability(
    model: object,
    chat_ctx: lk_llm.ChatContext,
    *,
    timeout: float | None = 1.0,
) -> float | None:
    """Ask the MultilingualModel for the text end-of-turn probability.

    Accepts any object with an async ``predict_end_of_turn(chat_ctx, *,
    timeout)`` method (duck-typed — pass a fake for unit tests; pass the
    real ``livekit.plugins.turn_detector.multilingual.MultilingualModel``
    in production).

    The wrapper is intentionally thin:

    * It delegates ChatContext construction to the caller (B4).  Building a
      ``ChatContext`` from raw transcript turns is a session-state concern,
      not a wrapper concern.
    * It catches **every** exception (including ``asyncio.TimeoutError``)
      and returns ``None`` so the Ear loop can always pass the result
      directly to ``ladder.decide()`` without a try/except of its own.

    Args:
        model:    Duck-typed EOU model with ``predict_end_of_turn``.
        chat_ctx: Pre-built ``livekit.agents.llm.ChatContext`` carrying the
                  conversation history.  The last ``user`` message should be
                  the candidate's current in-progress utterance.
        timeout:  Seconds to wait for model inference (default 1.0 s).
                  Passed straight through to ``predict_end_of_turn``.

    Returns:
        ``float`` in ``[0, 1]`` on success, ``None`` on any failure.
    """
    try:
        prob: float = await model.predict_end_of_turn(  # type: ignore[union-attr]
            chat_ctx, timeout=timeout
        )
        return prob
    except Exception as exc:  # noqa: BLE001  (intentional broad catch)
        log.warning(
            "text_eou_probability failed — falling back to Smart-Turn-only",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        return None
