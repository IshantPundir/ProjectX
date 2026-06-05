"""Tests for the per-turn loop (Phase C3).

The loop runs one candidate turn:
  bridge ∥ brain → mouth real line + NoteLog append → BrainDecision returned.

All collaborators (Brain, Mouth, Voice) are duck-typed async fakes — no livekit.

Test coverage:
  1. Parallel launch — bridge and brain are BOTH entered before either returns.
  2. Bridge plays before the real line; real line's MouthTurnInput.just_said == bridge text.
  3. Notes appended — brain emits 2 observations → NoteLog has 2 entries with the ctx's
     turn_ref / from_question_id / via_probe.
  4. Bridge failure → canned fallback — voice.say still called with CANNED_BRIDGE_FALLBACK;
     turn completes (real line + notes).
  5. Barge-in cancels both — run_turn raises CancelledError; bridge_task and brain_task are
     both cancelled (no pending-task warnings).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.loop import (
    CANNED_BRIDGE_FALLBACK,
    TurnContext,
    run_turn,
)
from app.modules.interview_engine.contracts import (
    BrainDecision,
    BrainTurnInput,
    BrainTurnOutput,
    BrainSessionContext,
    BrainMove,
    BridgeRequest,
    Directive,
    DirectiveAct,
    DirectiveTone,
    MouthTurnInput,
    SignalObservation,
    ActiveQuestionRubric,
    BudgetPhase,
    CoverageState,
)
from app.modules.interview_engine.notes import NoteLog
from app.modules.interview_runtime.evidence import (
    EvidenceStance,
    EvidenceTexture,
    TimeSpan,
)


# ---------------------------------------------------------------------------
# Shared helpers / factories
# ---------------------------------------------------------------------------

def _directive(
    act: DirectiveAct = DirectiveAct.probe,
    say: str = "Tell me more.",
    is_terminal: bool = False,
) -> Directive:
    return Directive(act=act, say=say, tone=DirectiveTone.curious, is_terminal=is_terminal)


def _brain_session_context() -> BrainSessionContext:
    return BrainSessionContext(
        job_title="Software Engineer",
        company_name="ACME Corp",
        signals=[],
        questions=[],
        time_budget_s=1800.0,
        budget_phase=BudgetPhase.early,
    )


def _active_rubric() -> ActiveQuestionRubric:
    return ActiveQuestionRubric(
        question_id="q-1",
        question_text="Tell me about your Python experience.",
        primary_signal="python_experience",
        follow_ups=["How did you use async?", "Any ML work?"],
        difficulty="medium",
        advance_criteria="Candidate demonstrates 2+ years of production Python.",
    )


def _brain_turn_input() -> BrainTurnInput:
    return BrainTurnInput(
        session_context=_brain_session_context(),
        active_rubric=_active_rubric(),
        signal_reads=[],
        window=[],
        candidate_turn_ref="t-1",
        candidate_text="I've used Python for three years, mainly Django REST APIs.",
        elapsed_s=60.0,
        questions_asked=1,
        triage_intent="answering",
    )


def _bridge_request() -> BridgeRequest:
    return BridgeRequest(
        cue="Okay, go on...",
        triage_intent="answering",
    )


def _make_ctx(turn_ref: str = "t-1") -> TurnContext:
    return TurnContext(
        turn_ref=turn_ref,
        utterance="I've used Python for three years, mainly Django REST APIs.",
        utterance_span=TimeSpan(start_ms=1000, end_ms=6000),
        from_question_id="q-1",
        via_probe=False,
        brain_input=_brain_turn_input(),
        bridge_request=_bridge_request(),
        recent_openers=["so", "okay", "right"],
    )


def _make_obs(signal: str = "python_experience") -> SignalObservation:
    return SignalObservation(
        signal=signal,
        stance=EvidenceStance.supports,
        texture=EvidenceTexture.concrete,
        coverage_after=CoverageState.partial,
    )


def _brain_decision(
    observations: list[SignalObservation] | None = None,
    is_terminal: bool = False,
) -> BrainDecision:
    obs = observations or [_make_obs()]
    directive = _directive(
        act=DirectiveAct.close if is_terminal else DirectiveAct.probe,
        is_terminal=is_terminal,
    )
    return BrainDecision(
        directive=directive,
        observations=obs,
        reasoning="test reasoning",
        is_terminal=is_terminal,
    )


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------

class FakeVoice:
    """Records calls to .say() so tests can assert ordering."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, text: str) -> None:  # noqa: D401
        self.said.append(text)


class SimpleBrain:
    """Brain that always returns a pre-configured decision after an optional delay."""

    def __init__(self, decision: BrainDecision, delay: float = 0.0) -> None:
        self._decision = decision
        self._delay = delay
        self.called = False

    async def decide(self, turn_input: BrainTurnInput) -> BrainDecision:
        self.called = True
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._decision


class SimpleMouth:
    """Mouth that always returns pre-configured bridge / real-line strings."""

    def __init__(
        self,
        bridge_text: str = "Sure, go ahead...",
        real_line_text: str = "Interesting — and how did you handle async work?",
        bridge_delay: float = 0.0,
        bridge_error: Exception | None = None,
    ) -> None:
        self._bridge_text = bridge_text
        self._real_line_text = real_line_text
        self._bridge_delay = bridge_delay
        self._bridge_error = bridge_error
        self.real_line_inputs: list[MouthTurnInput] = []

    async def bridge(self, req: BridgeRequest) -> str:
        if self._bridge_delay:
            await asyncio.sleep(self._bridge_delay)
        if self._bridge_error:
            raise self._bridge_error
        return self._bridge_text

    async def real_line(self, mouth_input: MouthTurnInput) -> str:
        self.real_line_inputs.append(mouth_input)
        return self._real_line_text


# ---------------------------------------------------------------------------
# Test 1 — Parallel launch: both bridge and brain are entered before either returns
# ---------------------------------------------------------------------------

class ParallelCheckBrain:
    """Brain that sets an entry-event, then waits for a gate before returning."""

    def __init__(self, entry_event: asyncio.Event, gate: asyncio.Event, decision: BrainDecision) -> None:
        self._entry = entry_event
        self._gate = gate
        self._decision = decision

    async def decide(self, turn_input: BrainTurnInput) -> BrainDecision:
        self._entry.set()       # signal: brain has been entered
        await self._gate.wait() # wait until the test releases the gate
        return self._decision


class ParallelCheckMouth:
    """Mouth whose bridge sets an entry-event, then waits for a gate before returning."""

    def __init__(
        self,
        entry_event: asyncio.Event,
        gate: asyncio.Event,
        bridge_text: str = "Mm, okay...",
        real_line_text: str = "And how did that go?",
    ) -> None:
        self._entry = entry_event
        self._gate = gate
        self._bridge_text = bridge_text
        self._real_line_text = real_line_text

    async def bridge(self, req: BridgeRequest) -> str:
        self._entry.set()       # signal: bridge has been entered
        await self._gate.wait() # wait until the test releases the gate
        return self._bridge_text

    async def real_line(self, mouth_input: MouthTurnInput) -> str:
        return self._real_line_text


@pytest.mark.asyncio
async def test_parallel_launch():
    """Bridge and brain must BOTH be entered before either returns.

    Uses entry events + a shared gate to verify true parallel execution.
    The test holds the gate until BOTH entry events fire, proving the loop
    launched both tasks concurrently (not sequentially).
    """
    brain_entry = asyncio.Event()
    mouth_entry = asyncio.Event()
    gate = asyncio.Event()

    decision = _brain_decision()
    brain = ParallelCheckBrain(brain_entry, gate, decision)
    mouth = ParallelCheckMouth(mouth_entry, gate)
    voice = FakeVoice()
    notelog = NoteLog()
    ctx = _make_ctx()

    async def release_gate_when_both_entered() -> None:
        """Wait until both brain and mouth have signalled entry, then release."""
        await asyncio.gather(
            asyncio.wait_for(brain_entry.wait(), timeout=2.0),
            asyncio.wait_for(mouth_entry.wait(), timeout=2.0),
        )
        gate.set()

    run_task = asyncio.create_task(
        run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)
    )
    release_task = asyncio.create_task(release_gate_when_both_entered())

    await asyncio.wait_for(run_task, timeout=5.0)
    await release_task  # already done by now

    # If we got here without timeout, both were entered before either returned.
    assert brain_entry.is_set(), "brain.decide was never entered"
    assert mouth_entry.is_set(), "mouth.bridge was never entered"


# ---------------------------------------------------------------------------
# Test 2 — Bridge plays before real line; real line's just_said == bridge text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bridge_plays_before_real_line_and_just_said_is_bridge():
    """voice.say is called first with the bridge text, then with the real-line text.

    The MouthTurnInput passed to mouth.real_line must have just_said == bridge text.
    """
    bridge_text = "Sure, that's interesting..."
    real_line_text = "Could you walk me through a specific example?"

    brain = SimpleBrain(_brain_decision())
    mouth = SimpleMouth(bridge_text=bridge_text, real_line_text=real_line_text)
    voice = FakeVoice()
    notelog = NoteLog()
    ctx = _make_ctx()

    result = await run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)

    # voice.say called exactly twice, bridge first
    assert len(voice.said) == 2
    assert voice.said[0] == bridge_text, (
        f"Expected bridge text first, got: {voice.said[0]!r}"
    )
    assert voice.said[1] == real_line_text, (
        f"Expected real-line text second, got: {voice.said[1]!r}"
    )

    # real_line received just_said == bridge_text
    assert len(mouth.real_line_inputs) == 1
    mti = mouth.real_line_inputs[0]
    assert mti.just_said == bridge_text, (
        f"MouthTurnInput.just_said should be bridge text; got {mti.just_said!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Notes appended to NoteLog with correct metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notes_appended_with_ctx_metadata():
    """Brain emits 2 observations → NoteLog has 2 notes.

    Each note carries the ctx's turn_ref, from_question_id, and via_probe.
    """
    obs_1 = _make_obs("python_experience")
    obs_2 = _make_obs("django_experience")
    decision = _brain_decision(observations=[obs_1, obs_2])

    brain = SimpleBrain(decision)
    mouth = SimpleMouth()
    voice = FakeVoice()
    notelog = NoteLog()
    ctx = _make_ctx(turn_ref="t-7")
    # Override via_probe to True so we can assert it
    ctx = TurnContext(
        turn_ref="t-7",
        utterance=ctx.utterance,
        utterance_span=ctx.utterance_span,
        from_question_id="q-99",
        via_probe=True,
        brain_input=ctx.brain_input,
        bridge_request=ctx.bridge_request,
        recent_openers=ctx.recent_openers,
    )

    await run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)

    notes = notelog.notes
    assert len(notes) == 2, f"Expected 2 notes, got {len(notes)}"

    for note in notes:
        assert note.turn_ref == "t-7", f"Expected turn_ref='t-7', got {note.turn_ref!r}"
        assert note.from_question_id == "q-99", (
            f"Expected from_question_id='q-99', got {note.from_question_id!r}"
        )
        assert note.via_probe is True, f"Expected via_probe=True, got {note.via_probe}"

    # seq is monotonic
    assert notes[0].seq == 1
    assert notes[1].seq == 2


# ---------------------------------------------------------------------------
# Test 4 — Bridge failure → canned fallback; turn still completes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bridge_failure_falls_back_to_canned():
    """When mouth.bridge raises, voice.say is called with CANNED_BRIDGE_FALLBACK first.

    The turn still completes: real line is played and notes are appended.
    """
    obs = _make_obs("error_resilience")
    decision = _brain_decision(observations=[obs])

    brain = SimpleBrain(decision)
    mouth = SimpleMouth(
        bridge_error=RuntimeError("TTS upstream timeout"),
        real_line_text="Let's explore that further.",
    )
    voice = FakeVoice()
    notelog = NoteLog()
    ctx = _make_ctx()

    result = await run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)

    # First say must be the canned fallback
    assert len(voice.said) == 2, f"Expected 2 voice.say calls, got {voice.said}"
    assert voice.said[0] == CANNED_BRIDGE_FALLBACK, (
        f"Expected canned fallback first; got {voice.said[0]!r}"
    )

    # Turn must complete: real line played
    assert voice.said[1] == "Let's explore that further."

    # Notes still appended
    assert len(notelog.notes) == 1
    assert notelog.notes[0].signal == "error_resilience"

    # Real line's just_said should be the canned fallback (not the error text)
    mti = mouth.real_line_inputs[0]
    assert mti.just_said == CANNED_BRIDGE_FALLBACK, (
        f"just_said should be canned fallback; got {mti.just_said!r}"
    )

    # The returned decision is the brain's decision
    assert result is decision


# ---------------------------------------------------------------------------
# Test 5 — Barge-in cancels both: run_turn raises CancelledError,
#           bridge_task and brain_task are both cancelled.
# ---------------------------------------------------------------------------

class HangingBrain:
    """Brain that hangs forever (awaiting a never-set event).

    Exposes a `cancelled` flag so the test can verify cancellation propagated.
    """

    def __init__(self) -> None:
        self.entered = False
        self.cancelled = False
        self._never = asyncio.Event()  # never set

    async def decide(self, turn_input: BrainTurnInput) -> BrainDecision:
        self.entered = True
        try:
            await self._never.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        # unreachable
        return _brain_decision()  # pragma: no cover


class HangingMouth:
    """Mouth whose bridge hangs forever (awaiting a never-set event).

    Exposes a `cancelled` flag so the test can verify cancellation propagated.
    """

    def __init__(self) -> None:
        self.bridge_entered = False
        self.bridge_cancelled = False
        self._never = asyncio.Event()  # never set

    async def bridge(self, req: BridgeRequest) -> str:
        self.bridge_entered = True
        try:
            await self._never.wait()
        except asyncio.CancelledError:
            self.bridge_cancelled = True
            raise
        # unreachable
        return CANNED_BRIDGE_FALLBACK  # pragma: no cover

    async def real_line(self, mouth_input: MouthTurnInput) -> str:
        # Should never be reached when barge-in fires before bridge completes
        return "unreachable"  # pragma: no cover


@pytest.mark.asyncio
async def test_barge_in_cancels_both():
    """Cancelling the run_turn task propagates CancelledError to bridge_task and brain_task.

    Observable contract:
      - run_turn raises CancelledError.
      - Both HangingBrain and HangingMouth report their inner coroutines were cancelled.
    """
    brain = HangingBrain()
    mouth = HangingMouth()
    voice = FakeVoice()
    notelog = NoteLog()
    ctx = _make_ctx()

    run_task = asyncio.create_task(
        run_turn(ctx, brain=brain, mouth=mouth, voice=voice, notelog=notelog)
    )

    # Give both tasks a chance to start (enter their hanging waits)
    # We use a brief yield-loop rather than a fixed sleep for determinism.
    for _ in range(20):
        if brain.entered and mouth.bridge_entered:
            break
        await asyncio.sleep(0.01)

    assert brain.entered, "brain.decide was not entered before cancel"
    assert mouth.bridge_entered, "mouth.bridge was not entered before cancel"

    # Cancel the outer run_turn task — simulates barge-in
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run_task

    # Give the event loop one tick to propagate cancellation to child tasks
    await asyncio.sleep(0)

    # Both child coroutines must have been cancelled
    assert brain.cancelled, (
        "brain.decide should have received CancelledError after barge-in"
    )
    assert mouth.bridge_cancelled, (
        "mouth.bridge should have received CancelledError after barge-in"
    )
