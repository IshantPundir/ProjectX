"""Integration test for StructuredInterviewAgent.

Mocked LiveKit transport (no real room, no real STT/TTS). The agent
class itself is exercised end-to-end: state machine traversal,
envelope event emission, SessionResult shape, ExitMode → SessionOutcome
mapping. Both happy-path and disconnect tests invoke
agent.py::_handle_close so the full close-path envelope sequence
(ledger.snapshot, gaps_detected, session.close) is covered.

Three sub-cases:
- Happy path: scripted candidate sends N transcribed utterances
  (UserInputTranscribedEvent fires N times); main loop completes;
  close handler runs; SessionResult.exit_mode maps to "completed",
  envelope events match the first/last/multiset contract from spec
  §5.3 Case A — first event is phase_changed CONNECTING→CONSENT,
  last event is session.close.
- Disconnect mid-session: cancel the orchestrator's main-loop task
  after Q1, set _end_outcome="candidate_disconnected", invoke the
  close handler; SessionResult exit_mode maps to "candidate_disconnected".
- Safety-fallback: inject a deliberately-unsafe string into _say(...);
  assert SPEECH_SAFETY_VIOLATION + SPEECH_FALLBACK_USED envelope
  events are emitted; the candidate hears the fallback text.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.agent import _handle_close
from app.modules.interview_engine.event_kinds import (
    ORCHESTRATOR_EXIT,
    ORCHESTRATOR_LEDGER_SNAPSHOT,
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
    PERSISTENCE_GAPS_DETECTED,
    SPEECH_FALLBACK_USED,
    SPEECH_SAFETY_VIOLATION,
)
from app.modules.interview_engine.orchestrator import ExitMode, InterviewPhase
from app.modules.interview_engine.structured_agent import (
    SessionOutcome,
    StructuredInterviewAgent,
)
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)

# ---------------------------------------------------------------------------
# Scaffolding helpers
# ---------------------------------------------------------------------------


def _make_session_config(num_questions: int) -> SessionConfig:
    """Build a minimal-but-valid SessionConfig with N questions."""
    questions = [
        QuestionConfig(
            id=f"q{i}",
            position=i,
            text=f"Tell me about challenge number {i} and how you resolved it.",
            signal_values=[f"signal_{i}"],
            estimated_minutes=2.0,
            is_mandatory=True,
            follow_ups=[],
            positive_evidence=[
                "Names specific tools",
                "Cites outcomes",
                "Step-by-step detail",
            ],
            red_flags=["Vague answer", "No specifics given"],
            rubric=QuestionRubric(
                excellent="excellent",
                meets_bar="meets",
                below_bar="below",
            ),
            evaluation_hint="Look for specificity in the response.",
        )
        for i in range(num_questions)
    ]
    return SessionConfig(
        session_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        candidate_id=str(uuid.uuid4()),
        job_title="Backend Engineer",
        role_summary="Backend engineering work",
        seniority_level="mid",
        company=CompanyContext(
            about="A test company building test things for testing purposes here.",
            industry="testing",
            company_stage="seed",
            hiring_bar="High bar text reaching the 20-character minimum here.",
        ),
        candidate=CandidateContext(name="Alex Test"),
        stage=StageConfig(
            stage_id=str(uuid.uuid4()),
            stage_type="ai_screening",
            name="Phone Screen",
            duration_minutes=15,
            difficulty="medium",
            questions=questions,
        ),
        signals=[],
        signal_metadata=[],
    )


class _FakeAgentSession:
    """Minimal AgentSession surface used by StructuredInterviewAgent.

    Records every session.say(text) call. Lets the test fire
    UserInputTranscribedEvent listeners on demand.
    """

    def __init__(self) -> None:
        self.said_texts: list[str] = []
        self._user_transcript_listeners: list[Callable[[Any], None]] = []
        # Mock room + room_io for set_attributes call from
        # _publish_session_outcome.
        self.room_io = MagicMock()
        self.room_io.room.local_participant.set_attributes = AsyncMock()

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        self.said_texts.append(text)

    def on(
        self,
        event_name: str,
        callback: Callable[[Any], None] | None = None,
    ) -> Callable[[Any], None]:
        """Register an event listener. Returns the callback for decorator use."""
        if callback is not None and event_name == "user_input_transcribed":
            self._user_transcript_listeners.append(callback)
            return callback
        # When used as a decorator factory (@session.on("event_name")),
        # callback is None — return a decorator that stores the listener.
        def _decorator(fn: Callable[[Any], None]) -> Callable[[Any], None]:
            if event_name == "user_input_transcribed":
                self._user_transcript_listeners.append(fn)
            return fn
        return _decorator  # type: ignore[return-value]

    def off(self, event_name: str, callback: Callable[[Any], None]) -> None:
        if event_name == "user_input_transcribed":
            with contextlib.suppress(ValueError):
                self._user_transcript_listeners.remove(callback)

    def fire_user_transcript(
        self, transcript: str, *, is_final: bool = True
    ) -> None:
        ev = MagicMock()
        ev.transcript = transcript
        ev.is_final = is_final
        for cb in list(self._user_transcript_listeners):
            cb(ev)


class _RecordingCollector:
    """Stand-in EventCollector that records every appended kind+payload.

    Real EventCollector requires controller_prompt_hash and other
    fields the tests don't need; this minimal recorder satisfies the
    .append(kind=..., payload=..., wall_ms=...) contract.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, *, kind: str, payload: dict[str, Any], wall_ms: int) -> None:
        self.events.append(
            {"kind": kind, "payload": payload, "wall_ms": wall_ms},
        )

    def close(self, *, closed_at: str) -> MagicMock:
        return MagicMock()  # not exercised in these tests


class _FakePersistence:
    """No-op LedgerPersistence stand-in."""

    def __init__(self) -> None:
        self.state_writes: int = 0
        self.ledger_writes: int = 0

    async def write_state(self, state: object) -> bool:
        self.state_writes += 1
        return True

    async def write_ledger(self, ledger: object) -> bool:
        self.ledger_writes += 1
        return True

    def detect_gaps(
        self, *, current_state_seq: int, current_ledger_seq: int
    ) -> dict[str, int]:
        return {"state_gap": 0, "ledger_gap": 0}


def _make_agent(
    config: SessionConfig,
) -> tuple[
    StructuredInterviewAgent, _FakeAgentSession, _RecordingCollector, _FakePersistence
]:
    """Construct a StructuredInterviewAgent with mocked surfaces.

    ``Agent.session`` is a read-only property that delegates to
    ``self._activity.session``. We inject a fake activity object whose
    ``.session`` attribute is our ``_FakeAgentSession``, bypassing the
    LiveKit framework's startup path entirely.
    """
    fake_session = _FakeAgentSession()
    collector = _RecordingCollector()
    persistence = _FakePersistence()
    agent = StructuredInterviewAgent(
        config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="test-correlation-id",
        collector=collector,  # type: ignore[arg-type]
        persistence=persistence,  # type: ignore[arg-type]
    )
    # Inject a fake AgentActivity so agent.session returns our fake.
    # Agent._activity is set to None in Agent.__init__; we replace it
    # with a MagicMock whose .session attribute is our _FakeAgentSession.
    fake_activity = MagicMock()
    fake_activity.session = fake_session
    agent._activity = fake_activity
    return agent, fake_session, collector, persistence


def _make_close_event(*, is_error: bool = False) -> MagicMock:
    """Construct a CloseEvent-shaped object for invoking _handle_close.

    The close handler reads ev.reason (compared against CloseReason.ERROR)
    and ev.error (truthy check) and ev.reason.value (string for envelope).
    """
    from livekit.agents.voice.events import CloseReason

    ev = MagicMock()
    if is_error:
        ev.reason = CloseReason.ERROR
        ev.error = RuntimeError("simulated error")
    else:
        # Use any non-ERROR enum value. The close handler only branches
        # on ERROR vs not-ERROR, so any non-ERROR enum is fine.
        non_error_values = [v for v in CloseReason if v != CloseReason.ERROR]
        assert non_error_values, "expected at least one non-ERROR CloseReason"
        ev.reason = non_error_values[0]
        ev.error = None
    return ev


async def _persist_session_result_noop(
    self: StructuredInterviewAgent, outcome: SessionOutcome
) -> None:
    """Patch target for _persist_session_result — DB writes are out of
    scope for this integration test."""
    self._persisted = True


async def _wait_for_say_count(
    fake_session: _FakeAgentSession,
    *,
    expected: int,
    max_wait: float = 2.0,
) -> None:
    """Spin briefly until len(said_texts) >= expected."""
    deadline = asyncio.get_event_loop().time() + max_wait
    while len(fake_session.said_texts) < expected:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"timed out waiting for say count {expected}; "
                f"got {len(fake_session.said_texts)}",
            )
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_produces_completed_session_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scripted candidate sends N transcribed utterances; main loop
    completes naturally; close handler runs; envelope events match
    spec §5.3 Case A first/last/multiset contract."""
    n_questions = 3
    config = _make_session_config(n_questions)
    agent, fake_session, collector, persistence = _make_agent(config)

    # Stub out _persist_session_result — DB writes are out of scope.
    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    # Launch on_enter — it kicks off the main loop as a background task.
    await agent.on_enter()

    # Drive the conversation: one transcribed utterance per question.
    # say count grows by 1 per iter: INTRO is say #1, ASK_Qi is say #(i+2).
    for i in range(n_questions):
        await _wait_for_say_count(fake_session, expected=i + 2)
        fake_session.fire_user_transcript(
            f"My answer to challenge {i} mentions specific tools and outcomes.",
        )

    # Wait for the main loop task to complete (after the WRAP_NORMAL utterance).
    assert agent._main_loop_task is not None
    await asyncio.wait_for(agent._main_loop_task, timeout=2.0)

    # Phase B has now reached state.phase == CLOSED via the natural main
    # loop completion path. Invoke the close handler to exercise the
    # close-path envelope events (ledger.snapshot, gaps_detected, session.close).
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    # Build the SessionResult to verify shape.
    result = agent._build_session_result("completed")

    # Question results
    assert len(result.question_results) == n_questions
    for qr in result.question_results:
        assert qr.was_skipped is False
        assert qr.probes_fired == 0
        assert qr.observations == []
        assert len(qr.transcript_entries) == 1

    assert result.questions_asked == n_questions
    assert result.questions_skipped == 0
    assert result.knockout_failures == []

    # Envelope events — first / last / multiset (spec §5.3 Case A).
    kinds = [ev["kind"] for ev in collector.events]

    # First envelope event: phase_changed CONNECTING→CONSENT (emitted by
    # the main loop's first _transition_with_persist call).
    assert kinds[0] == ORCHESTRATOR_PHASE_CHANGED
    assert collector.events[0]["payload"]["old_phase"] == "connecting"
    assert collector.events[0]["payload"]["new_phase"] == "consent"

    # _handle_close emits "session.close" first, then ledger.snapshot,
    # then gaps_detected. The last event in the full sequence is therefore
    # persistence.gaps_detected (the close handler's final append).
    assert kinds[-1] == PERSISTENCE_GAPS_DETECTED
    # session.close is present in the events (emitted at close handler entry).
    assert "session.close" in kinds

    # Multiset counts (no order constraint among these):
    # Happy path: the main loop itself emits ORCHESTRATOR_EXIT and drives
    # all 5 phase transitions (CONNECTING→CONSENT, CONSENT→INTRO,
    # INTRO→MAIN_LOOP, MAIN_LOOP→NORMAL_WRAP, NORMAL_WRAP→CLOSED).
    # _handle_close skips the transition block because phase is already CLOSED.
    assert kinds.count(ORCHESTRATOR_PHASE_CHANGED) == 5
    assert kinds.count(ORCHESTRATOR_QUESTION_ASKED) == n_questions
    assert kinds.count(ORCHESTRATOR_QUESTION_COMPLETED) == n_questions
    assert kinds.count(ORCHESTRATOR_EXIT) == 1
    assert kinds.count(ORCHESTRATOR_LEDGER_SNAPSHOT) == 1
    assert kinds.count(PERSISTENCE_GAPS_DETECTED) == 1

    # Phase progression sanity:
    phase_payloads = [
        ev["payload"]
        for ev in collector.events
        if ev["kind"] == ORCHESTRATOR_PHASE_CHANGED
    ]
    transitions = [(p["old_phase"], p["new_phase"]) for p in phase_payloads]
    assert transitions == [
        ("connecting", "consent"),
        ("consent", "intro"),
        ("intro", "main_loop"),
        ("main_loop", "normal_wrap"),
        ("normal_wrap", "closed"),
    ]

    # Persistence calls during the main loop:
    #   write_state: 5 transitions (CONSENT, INTRO, MAIN_LOOP, NORMAL_WRAP,
    #     CLOSED) + 1 per question (asked_at stamp in _ask_one_question)
    #   write_ledger: 1 per question completion
    assert persistence.state_writes == 5 + n_questions
    assert persistence.ledger_writes == n_questions

    # Outcome publishing reached the room mock.
    fake_session.room_io.room.local_participant.set_attributes.assert_awaited_with(
        {"session_outcome": "completed"},
    )


@pytest.mark.asyncio
async def test_disconnect_mid_session_produces_candidate_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scripted candidate answers Q1 then disconnects. Cancel the main
    loop task (test stand-in for LiveKit's participant-timeout-driven
    close); set _end_outcome="candidate_disconnected" (mirroring the
    real participant-disconnected callback); invoke _handle_close;
    assert it drives MAIN_LOOP→CLOSED and emits the full close-path
    envelope sequence."""
    n_questions = 3
    config = _make_session_config(n_questions)
    agent, fake_session, collector, persistence = _make_agent(config)

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()

    # Answer Q0 (the first question).
    await _wait_for_say_count(fake_session, expected=2)  # INTRO + ASK Q0
    fake_session.fire_user_transcript("My answer to Q0.")

    # Wait for ASK Q1 to be said — confirms Q0 completion was processed.
    await _wait_for_say_count(fake_session, expected=3)  # +ASK Q1

    # Simulate disconnect: state is MAIN_LOOP, the main loop is awaiting
    # Q1's transcript that never arrives. The real _wire_participant_disconnect
    # callback stamps _end_outcome but does NOT cancel the loop or
    # transition phase. The test simulates LiveKit's eventual session-
    # timeout-driven close by cancelling the loop and invoking
    # _handle_close, which is what _wire_close_handler does in production.
    assert agent._state.phase == InterviewPhase.MAIN_LOOP
    agent._end_outcome = "candidate_disconnected"
    assert agent._main_loop_task is not None
    agent._main_loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await agent._main_loop_task

    # Phase is still MAIN_LOOP — the close handler has not yet run.
    assert agent._state.phase == InterviewPhase.MAIN_LOOP

    # Invoke the close handler. It must drive MAIN_LOOP→CLOSED, set
    # ExitMode.TECHNICAL_FAILURE, emit ledger.snapshot, gaps_detected,
    # and session.close.
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    # Phase is now CLOSED, exit_mode is TECHNICAL_FAILURE.
    # Cast avoids mypy's non-overlapping-equality narrowing from the prior assert.
    final_phase: InterviewPhase = agent._state.phase
    assert final_phase == InterviewPhase.CLOSED
    assert agent._state.exit_mode == ExitMode.TECHNICAL_FAILURE

    # SessionResult shape.
    # Q0: asked + completed (transcript captured) → was_skipped=False, 1 entry.
    # Q1: asked (asked_at set before loop cancellation) but NOT completed
    #     (disconnect mid-wait) → was_skipped=False, 0 transcript entries.
    #     _build_session_result counts it as "asked" (asked_at is not None).
    # Q2: never asked → was_skipped=True, 0 transcript entries.
    result = agent._build_session_result("candidate_disconnected")
    assert len(result.question_results) == n_questions
    q0, q1, q2 = result.question_results
    assert q0.was_skipped is False
    assert len(q0.transcript_entries) == 1
    assert q1.was_skipped is False       # asked but no transcript
    assert q1.transcript_entries == []
    assert q2.was_skipped is True        # never asked
    assert q2.transcript_entries == []
    # questions_asked counts all questions with asked_at set (Q0 + Q1).
    assert result.questions_asked == 2
    assert result.questions_skipped == 1  # only Q2 was never asked

    # Envelope events: first event is phase_changed CONNECTING→CONSENT
    # (from main loop). _handle_close emits session.close first in its
    # sequence, followed by phase_changed (MAIN_LOOP→CLOSED), exit,
    # ledger.snapshot, then gaps_detected last.
    kinds = [ev["kind"] for ev in collector.events]
    assert kinds[0] == ORCHESTRATOR_PHASE_CHANGED
    assert collector.events[0]["payload"]["new_phase"] == "consent"
    # Last event: persistence.gaps_detected (final append in _handle_close).
    assert kinds[-1] == PERSISTENCE_GAPS_DETECTED
    assert "session.close" in kinds

    # 4 phase_changed events: CONNECTING→CONSENT, CONSENT→INTRO,
    # INTRO→MAIN_LOOP, MAIN_LOOP→CLOSED (no NORMAL_WRAP — close
    # handler took the direct edge).
    # Q1 was asked (asked_at set) before cancellation.
    assert kinds.count(ORCHESTRATOR_PHASE_CHANGED) == 4
    assert kinds.count(ORCHESTRATOR_QUESTION_ASKED) == 2   # Q0 + Q1 asked
    assert kinds.count(ORCHESTRATOR_QUESTION_COMPLETED) == 1  # only Q0 completed
    assert kinds.count(ORCHESTRATOR_EXIT) == 1
    assert kinds.count(ORCHESTRATOR_LEDGER_SNAPSHOT) == 1
    assert kinds.count(PERSISTENCE_GAPS_DETECTED) == 1

    # The exit-mode payload reflects TECHNICAL_FAILURE.
    exit_evs = [ev for ev in collector.events if ev["kind"] == ORCHESTRATOR_EXIT]
    assert len(exit_evs) == 1
    assert exit_evs[0]["payload"]["exit_mode"] == ExitMode.TECHNICAL_FAILURE.value

    # Outcome publishing reached the room mock with candidate_disconnected.
    fake_session.room_io.room.local_participant.set_attributes.assert_awaited_with(
        {"session_outcome": "candidate_disconnected"},
    )


@pytest.mark.asyncio
async def test_safety_fallback_emits_violation_and_fallback_events() -> None:
    """Inject a deliberately-unsafe string into _say(...). Verify both
    SPEECH_SAFETY_VIOLATION and SPEECH_FALLBACK_USED envelope events
    are emitted, and the candidate hears the fallback text instead."""
    config = _make_session_config(num_questions=1)
    agent, fake_session, collector, _persistence = _make_agent(config)

    # Bypass the orchestrator main loop — call _say directly with an
    # unsafe string containing outcome words that trigger safety violations.
    unsafe_text = "Unfortunately you have failed this screen."

    await agent._say(unsafe_text)

    # The candidate should have heard the fallback text, not the unsafe one.
    assert len(fake_session.said_texts) == 1
    assert fake_session.said_texts[0] != unsafe_text
    from app.modules.interview_engine._phase_b_utterances import (
        _PHASE_B_SAFETY_FALLBACK_TEXT,
    )
    assert fake_session.said_texts[0] == _PHASE_B_SAFETY_FALLBACK_TEXT

    # Envelope events
    kinds = [ev["kind"] for ev in collector.events]
    assert SPEECH_SAFETY_VIOLATION in kinds
    assert SPEECH_FALLBACK_USED in kinds

    # The violation payload carries category + pattern_name + matched_text_hash
    # (NOT raw matched_text).
    violation_evs = [
        ev for ev in collector.events if ev["kind"] == SPEECH_SAFETY_VIOLATION
    ]
    # The unsafe text "Unfortunately you have failed this screen." trips
    # exactly two outcome rules: outcome.unfortunately + outcome.failed.
    # Asserting the exact count guards against a regression where the
    # _say loop exits early after the first violation.
    assert len(violation_evs) == 2
    for ev in violation_evs:
        assert "category" in ev["payload"]
        assert "pattern_name" in ev["payload"]
        assert "matched_text_hash" in ev["payload"]
        assert "matched_text" not in ev["payload"]  # raw text never logged
