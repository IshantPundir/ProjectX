"""Tests for the Ear's VAD pause gate + MultilingualModel text-EOU wrapper (B3).

Three test groups:
  1. SpeechActivity (pure pause detector) — clock-agnostic, no livekit.
  2. text_eou_probability wrapper — mock model, graceful-None contract.
  3. Ladder integration — None flows correctly into Smart-Turn-only path.

No I/O, no real LiveKit, no real ONNX — fully unit-testable via injected mocks.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.interview_engine.ear.vad_gate import (
    SpeechActivity,
    text_eou_probability,
)
from app.modules.interview_engine.ear.ladder import (
    EarDecision,
    EarLadderConfig,
    decide,
)


# ---------------------------------------------------------------------------
# Shared ladder config (same values as test_ear_ladder.py for consistency)
# ---------------------------------------------------------------------------

CFG = EarLadderConfig(
    smart_turn_commit_thr=0.5,
    text_commit_thr=0.02,
    min_silence_ms=300,
    hold_cue_ms=2500,
)


# ---------------------------------------------------------------------------
# Helpers — fake EOU model (duck-typed, duck-quacks-like MultilingualModel)
# ---------------------------------------------------------------------------


class _FakeEOUModel:
    """Fake text-EOU model for unit tests.

    Injects a fixed probability, an error type, or a timeout.
    Records the chat_ctx passed to predict_end_of_turn.
    """

    def __init__(
        self,
        *,
        returns: float | None = None,
        raises: type[Exception] | None = None,
        delay: float = 0.0,
    ) -> None:
        self._returns = returns
        self._raises = raises
        self._delay = delay
        self.call_count: int = 0
        self.last_chat_ctx = None

    async def predict_end_of_turn(self, chat_ctx, *, timeout: float | None = 3) -> float:
        self.call_count += 1
        self.last_chat_ctx = chat_ctx
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises("injected error")
        assert self._returns is not None
        return self._returns


# ---------------------------------------------------------------------------
# Group 1 — SpeechActivity (pure pause detector)
# ---------------------------------------------------------------------------


class TestSpeechActivity:
    """Pure pause detector — no livekit imports, clock-agnostic."""

    def test_initial_state_not_speaking_silence_zero(self) -> None:
        """Before any events: not speaking, silence_ms == 0 (no pause yet)."""
        sa = SpeechActivity()
        assert sa.is_speaking is False
        assert sa.silence_ms(now_ms=5000) == 0

    def test_speaking_started_sets_is_speaking(self) -> None:
        """on_speaking_started flips is_speaking → True."""
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        assert sa.is_speaking is True

    def test_silence_ms_zero_while_speaking(self) -> None:
        """While speaking, silence_ms is 0 regardless of now_ms."""
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        assert sa.silence_ms(now_ms=3500) == 0

    def test_speaking_stopped_flips_is_speaking(self) -> None:
        """on_speaking_stopped flips is_speaking → False."""
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        sa.on_speaking_stopped(t_ms=3000)
        assert sa.is_speaking is False

    def test_silence_ms_after_speech_stopped(self) -> None:
        """silence_ms == now_ms − last_stopped_ms after a pause.

        Scenario: speaking started t=1000ms, stopped t=3000ms,
        query at now=3500ms → silence = 500ms.
        """
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        sa.on_speaking_stopped(t_ms=3000)
        assert sa.silence_ms(now_ms=3500) == 500

    def test_new_speech_start_resets_silence_clock(self) -> None:
        """Starting speaking again makes silence_ms go back to 0.

        Scenario: speaking → stopped (t=3000) → speaking again (t=4000).
        After restart, silence_ms must be 0 (we're back to speaking).
        """
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        sa.on_speaking_stopped(t_ms=3000)

        # Before the restart silence accumulates.
        assert sa.silence_ms(now_ms=3500) == 500

        # Speaking again — silence clock resets.
        sa.on_speaking_started(t_ms=4000)
        assert sa.is_speaking is True
        assert sa.silence_ms(now_ms=4500) == 0

    def test_speaking_listening_speaking_tracks_latest_pause(self) -> None:
        """A full speaking→pause→speaking→pause sequence tracks the most recent pause.

        Sequence:
          t=1000  speaking started
          t=2000  speaking stopped  (first pause)
          t=3000  speaking started  (resumed)
          t=4500  speaking stopped  (second pause)
          t=5000  query → silence = 5000 - 4500 = 500ms
        """
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        sa.on_speaking_stopped(t_ms=2000)

        # Mid-first-pause query.
        assert sa.silence_ms(now_ms=2500) == 500

        sa.on_speaking_started(t_ms=3000)
        # Now speaking → silence resets.
        assert sa.silence_ms(now_ms=3500) == 0

        sa.on_speaking_stopped(t_ms=4500)
        # Second pause: tracks the newest stop event.
        assert sa.silence_ms(now_ms=5000) == 500

    def test_silence_ms_clamped_non_negative(self) -> None:
        """silence_ms never goes below 0 even if now_ms < last_stopped_ms.

        This guards against caller clock anomalies (e.g. a slightly stale
        now_ms passed by mistake).
        """
        sa = SpeechActivity()
        sa.on_speaking_started(t_ms=1000)
        sa.on_speaking_stopped(t_ms=3000)

        # now_ms < last_stopped_ms → clamp to 0.
        assert sa.silence_ms(now_ms=2000) == 0


# ---------------------------------------------------------------------------
# Group 2 — text_eou_probability wrapper
# ---------------------------------------------------------------------------


class TestTextEooProbability:
    """text_eou_probability is a thin async wrapper with graceful-None on failure."""

    async def test_returns_model_probability(self) -> None:
        """Happy path: wrapper returns the model's float unchanged."""
        from livekit.agents import llm

        model = _FakeEOUModel(returns=0.013)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="I have five years of Python experience.")

        result = await text_eou_probability(model, chat_ctx)
        assert result == pytest.approx(0.013)
        assert model.call_count == 1

    async def test_chat_ctx_carries_last_user_utterance(self) -> None:
        """The chat_ctx forwarded to the model contains the expected last user message."""
        from livekit.agents import llm

        model = _FakeEOUModel(returns=0.85)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="assistant", content="Tell me about your background.")
        chat_ctx.add_message(role="user", content="I spent three years at a startup.")

        await text_eou_probability(model, chat_ctx)

        assert model.last_chat_ctx is chat_ctx, (
            "Wrapper must forward the same chat_ctx object to the model."
        )
        msgs = model.last_chat_ctx.messages()
        user_msgs = [m for m in msgs if m.role == "user"]
        assert len(user_msgs) >= 1
        last_user = user_msgs[-1]
        assert "startup" in last_user.text_content

    async def test_returns_none_on_exception(self) -> None:
        """When the model raises, wrapper swallows the error and returns None."""
        from livekit.agents import llm

        model = _FakeEOUModel(raises=RuntimeError)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="...")

        result = await text_eou_probability(model, chat_ctx)
        assert result is None, (
            "Wrapper must return None, not propagate the exception."
        )
        assert model.call_count == 1

    async def test_returns_none_on_value_error(self) -> None:
        """ValueError (model failure variant) also returns None."""
        from livekit.agents import llm

        model = _FakeEOUModel(raises=ValueError)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="...")

        result = await text_eou_probability(model, chat_ctx)
        assert result is None

    async def test_returns_none_on_asyncio_timeout_error(self) -> None:
        """asyncio.TimeoutError (treated as Exception) returns None."""
        from livekit.agents import llm

        model = _FakeEOUModel(raises=asyncio.TimeoutError)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="...")

        result = await text_eou_probability(model, chat_ctx)
        assert result is None

    async def test_does_not_propagate_exception(self) -> None:
        """The wrapper must NOT propagate any exception from the model.

        This is the load-bearing contract: the caller (B4 Ear loop) receives
        a clean None and passes it to the ladder — it must never see an exception
        from the text-EOU path.
        """
        from livekit.agents import llm

        model = _FakeEOUModel(raises=Exception)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="...")

        # If this raises, the test fails — that's the assertion.
        result = await text_eou_probability(model, chat_ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Group 3 — Ladder integration (None flows end-to-end)
# ---------------------------------------------------------------------------


class TestLadderIntegration:
    """Verify that text_eou_prob=None from the wrapper flows into the ladder correctly.

    These tests prove the full contract without any live LiveKit connection:
    error → wrapper returns None → ladder takes the Smart-Turn-only branch.
    """

    async def test_none_from_failing_model_flows_to_ladder_commit(self) -> None:
        """Failing model → None → ladder Smart-Turn-only → COMMIT (voice complete).

        Setup:
          - Model raises → wrapper returns None.
          - VAD silence = 1000ms (> min_silence_ms=300), voice complete (0.9 > 0.5).
          - Expected: ladder § Smart-Turn-only, voice done → COMMIT.
        """
        from livekit.agents import llm

        model = _FakeEOUModel(raises=RuntimeError)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="I built a REST API in Django.")

        text_prob = await text_eou_probability(model, chat_ctx)
        assert text_prob is None  # pre-condition

        decision = decide(
            vad_silence_ms=1000,
            smart_turn_prob=0.9,    # voice complete
            text_eou_prob=text_prob,
            cfg=CFG,
        )
        assert decision == EarDecision.commit, (
            f"Expected COMMIT on voice-complete + None text, got {decision!r}"
        )

    async def test_none_from_failing_model_flows_to_ladder_hold_cue(self) -> None:
        """Failing model → None → ladder Smart-Turn-only → HOLD_CUE (long silence, voice incomplete).

        Setup:
          - Model raises → wrapper returns None.
          - VAD silence = 2500ms (>= hold_cue_ms=2500), voice incomplete (0.1 < 0.5).
          - Expected: ladder § Smart-Turn-only, mid-thought pause → HOLD_CUE.
        """
        from livekit.agents import llm

        model = _FakeEOUModel(raises=RuntimeError)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="Hmm, let me think...")

        text_prob = await text_eou_probability(model, chat_ctx)
        assert text_prob is None  # pre-condition

        decision = decide(
            vad_silence_ms=2500,
            smart_turn_prob=0.1,    # voice incomplete
            text_eou_prob=text_prob,
            cfg=CFG,
        )
        assert decision == EarDecision.hold_cue, (
            f"Expected HOLD_CUE on long-silence + None text, got {decision!r}"
        )

    async def test_none_from_failing_model_flows_to_ladder_wait(self) -> None:
        """Failing model → None → ladder Smart-Turn-only → WAIT (short silence, voice incomplete)."""
        from livekit.agents import llm

        model = _FakeEOUModel(raises=RuntimeError)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="...")

        text_prob = await text_eou_probability(model, chat_ctx)
        assert text_prob is None

        decision = decide(
            vad_silence_ms=800,
            smart_turn_prob=0.1,    # voice incomplete
            text_eou_prob=text_prob,
            cfg=CFG,
        )
        assert decision == EarDecision.wait, (
            f"Expected WAIT on short-silence + None text + voice incomplete, got {decision!r}"
        )

    async def test_valid_prob_from_model_flows_to_ladder(self) -> None:
        """When the model returns a valid prob it flows into the full ladder path."""
        from livekit.agents import llm

        model = _FakeEOUModel(returns=0.5)   # text complete (>= text_commit_thr=0.02)
        chat_ctx = llm.ChatContext()
        chat_ctx.add_message(role="user", content="I completed the project last year.")

        text_prob = await text_eou_probability(model, chat_ctx)
        assert text_prob == pytest.approx(0.5)   # happy path, no None

        # voice incomplete (0.1) + text complete (0.5 >= 0.02) → WAIT (protect mid-word).
        decision = decide(
            vad_silence_ms=1000,
            smart_turn_prob=0.1,
            text_eou_prob=text_prob,
            cfg=CFG,
        )
        assert decision == EarDecision.wait, (
            f"Expected WAIT on voice-incomplete + text-complete, got {decision!r}"
        )
