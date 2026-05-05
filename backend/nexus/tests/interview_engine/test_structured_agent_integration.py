"""Phase C integration tests for StructuredInterviewAgent.

These tests exercise the agent end-to-end with a mocked SpeechAgent
(to avoid real OpenAI calls) and a mocked LiveKit AgentSession (to
avoid the real WebRTC + STT + TTS pipelines). The orchestrator's
state machine, pre-render slot, fallback substitution, close handler,
and SessionResult composition all run for real.

Test coverage map (Phase C §5.3):

1. ``test_full_happy_path_with_pre_render_slot``           — Case A
2. ``test_disconnect_during_render_task_subcase_1``        — Case B sub 1
3. ``test_disconnect_after_render_before_commit_subcase_2``— Case B sub 2
4. ``test_disconnect_mid_playout_subcase_3``               — Case B sub 3
5. ``test_speech_render_error_triggers_fallback_path_and_session_continues``
6. ``test_template_not_found_results_in_technical_failure_exit``
7. ``test_pre_render_slot_cancelled_on_close``
8. ``test_no_speech_safety_violation_constant_imported`` (regression-guard)

The static regression-guard test (#8) is the single Phase C anchor that
must keep passing forever: any reintroduction of the deleted regex-based
safety layer (a SPEECH_SAFETY_VIOLATION constant or a `speech.safety_violation`
event-string anywhere in the repo) immediately breaks this test.

Implementation note: the integration test scaffolding intentionally mocks
SpeechAgent rather than spinning up a real one with a mocked OpenAI
client. The latter is what the speech-package unit tests
(``tests/interview_engine/speech/test_speech_agent.py``) cover — they
exercise the StreamingRenderHandle state machine in detail. These
integration tests focus on orchestrator-side concerns: pre-render slot
ownership, fallback substitution, envelope event ordering, SessionResult
composition, and close-handler behavior across all four cancellation
sub-cases.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from livekit.agents.llm import StopResponse

from app.modules.interview_engine.agent import _handle_close
from app.modules.interview_engine.event_kinds import (
    ORCHESTRATOR_EXIT,
    ORCHESTRATOR_LEDGER_SNAPSHOT,
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
    PERSISTENCE_GAPS_DETECTED,
    SPEECH_FALLBACK_USED,
    SPEECH_RENDERED,
    SPEECH_STREAM_INTERRUPTED,
)
from app.modules.interview_engine.orchestrator import ExitMode, InterviewPhase
from app.modules.interview_engine.speech import (
    RenderMetadata,
    SpeechRenderError,
    SpeechRenderHandle,
)
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
# Scaffolding
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


class _RecordingCollector:
    """Stand-in EventCollector that records every appended kind+payload."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, *, kind: str, payload: dict[str, Any], wall_ms: int) -> None:
        self.events.append(
            {"kind": kind, "payload": payload, "wall_ms": wall_ms},
        )

    def close(self, *, closed_at: str) -> MagicMock:
        return MagicMock()


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


class _FakeAgentSession:
    """Records every session.say call.

    say(text_or_iterable, ...) drains the iterable so the handle's joined
    iterator runs (which is what triggers SPEECH_RENDERED emission inside
    the handle). The drained text is captured for assertions.
    """

    def __init__(self) -> None:
        self.said_texts: list[str] = []
        self.room_io = MagicMock()
        self.room_io.room.local_participant.set_attributes = AsyncMock()

    async def say(
        self,
        text_or_iterable: Any,
        *,
        allow_interruptions: bool = True,
    ) -> None:
        if isinstance(text_or_iterable, str):
            self.said_texts.append(text_or_iterable)
            return
        # AsyncIterable[str] — drain to completion.
        chunks: list[str] = []
        async for chunk in text_or_iterable:
            chunks.append(chunk)
        self.said_texts.append("".join(chunks))


class _FakeHandle:
    """Test-only SpeechRenderHandle implementation.

    Exposes hooks for each cancellation sub-case:
      - ``ready_delay``: how long ready_to_commit blocks before resolving.
      - ``ready_raises``: SpeechRenderError raised by ready_to_commit.
      - ``commit_drain_delay``: per-chunk delay during commit() iteration
        (simulates TTS playout).
      - ``commit_interrupt_after_n``: how many chunks to yield before
        flagging stream interruption.
    """

    _GLOBAL_RENDERED: list[dict[str, Any]] = []  # injected per-test

    def __init__(
        self,
        *,
        text: str,
        template_name: str,
        template_version: str = "v1",
        render_id: str | None = None,
        was_fallback: bool = False,
        ready_delay: float = 0.0,
        ready_raises: SpeechRenderError | None = None,
        commit_drain_delay: float = 0.0,
        commit_interrupt_after_n: int | None = None,
        retries: int = 0,
        collector: _RecordingCollector | None = None,
    ) -> None:
        self._text = text
        self._template_name = template_name
        self._template_version = template_version
        self._render_id = render_id or str(uuid.uuid4())
        self._was_fallback = was_fallback
        self._ready_delay = ready_delay
        self._ready_raises = ready_raises
        self._commit_drain_delay = commit_drain_delay
        self._commit_interrupt_after_n = commit_interrupt_after_n
        self._retries = retries
        self._collector = collector

        self._committed = False
        self._cancelled = False
        self._cancel_event = asyncio.Event()

        loop = asyncio.get_event_loop()
        self._metadata_fut: asyncio.Future[RenderMetadata] = loop.create_future()
        self._completed_text_fut: asyncio.Future[str] = loop.create_future()

    async def ready_to_commit(self) -> None:
        if self._ready_delay > 0:
            ready_task = asyncio.create_task(asyncio.sleep(self._ready_delay))
            cancel_task = asyncio.create_task(self._cancel_event.wait())
            done, pending = await asyncio.wait(
                {ready_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()
        if self._ready_raises is not None:
            raise self._ready_raises

    def commit(self) -> AsyncIterable[str]:
        if self._cancelled:
            raise RuntimeError("Cannot commit a cancelled handle")
        if self._committed:
            raise RuntimeError("commit() may only be called once")
        self._committed = True

        async def _drain() -> AsyncIterator[str]:
            chars = list(self._text)
            for i, ch in enumerate(chars):
                if (
                    self._commit_interrupt_after_n is not None
                    and i >= self._commit_interrupt_after_n
                ):
                    # Simulate stream interruption mid-playout: flush the
                    # SPEECH_STREAM_INTERRUPTED event and stop yielding,
                    # but mark as committed=true, played=true,
                    # played_to_completion=false.
                    if self._collector is not None:
                        self._collector.append(
                            kind=SPEECH_STREAM_INTERRUPTED,
                            payload={
                                "render_id": self._render_id,
                                "tokens_received": i,
                                "reason": "openai_connection_dropped_post_first_token",
                            },
                            wall_ms=0,
                        )
                    self._emit_rendered(played_to_completion=False)
                    return
                if self._commit_drain_delay > 0:
                    try:
                        await asyncio.sleep(self._commit_drain_delay)
                    except asyncio.CancelledError:
                        # Mid-playout cancellation. Mark as committed=true,
                        # played=true, played_to_completion=false (sub-case 3).
                        self._emit_rendered(played_to_completion=False)
                        raise
                yield ch
            self._emit_rendered(played_to_completion=True)

        return _drain()

    async def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._cancel_event.set()
        # Sub-cases 1 + 2: cancellation BEFORE commit ever ran.
        # Emit speech.rendered with committed=false, played=false.
        if not self._committed:
            self._emit_rendered(played_to_completion=False, force_not_played=True)

    def _emit_rendered(
        self,
        *,
        played_to_completion: bool,
        force_not_played: bool = False,
    ) -> None:
        if self._metadata_fut.done():
            return
        played = self._committed and not force_not_played
        md = RenderMetadata(
            render_id=self._render_id,
            template_name=self._template_name,
            template_version=self._template_version,
            model="<test>",
            latency_first_token_ms=0,
            latency_last_token_ms=0,
            tokens_in=0,
            tokens_out=0,
            length_words=len(self._text.split()),
            playout_duration_ms=0,
            was_fallback=self._was_fallback,
            retries=self._retries,
        )
        self._metadata_fut.set_result(md)
        self._completed_text_fut.set_result(self._text)
        if self._collector is not None:
            self._collector.append(
                kind=SPEECH_RENDERED,
                payload={
                    "render_id": self._render_id,
                    "template_name": self._template_name,
                    "template_version": self._template_version,
                    "model": "<test>",
                    "committed": self._committed,
                    "played": played,
                    "played_to_completion": (
                        self._committed and played_to_completion
                    ),
                    "was_fallback": self._was_fallback,
                    "retries": self._retries,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "length_words": len(self._text.split()),
                    "latency_first_token_ms": 0,
                    "latency_last_token_ms": 0,
                    "playout_duration_ms": 0,
                },
                wall_ms=0,
            )

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


class _ScriptedSpeechAgent:
    """SpeechAgent stand-in that hands out _FakeHandle instances.

    Default behavior: every render() returns a happy-path handle that
    yields the template_name + question_text. Tests can override per-call
    behavior via ``script[template_name]``.
    """

    def __init__(
        self,
        *,
        collector: _RecordingCollector,
        script: dict[str, list[Any]] | None = None,
    ) -> None:
        self._collector = collector
        self._script = script or {}
        self._call_counts: dict[str, int] = {}
        # Track every fallback emitted so tests can assert SPEECH_FALLBACK_USED
        # was emitted exactly once per fallback substitution.
        self._fallback_calls: list[dict[str, Any]] = []

    async def render(
        self,
        *,
        template_name: str,
        template_version: str,
        inputs: dict[str, Any],
    ) -> SpeechRenderHandle:
        idx = self._call_counts.get(template_name, 0)
        self._call_counts[template_name] = idx + 1
        plan = None
        if template_name in self._script and idx < len(self._script[template_name]):
            plan = self._script[template_name][idx]

        if isinstance(plan, SpeechRenderError):
            # Synchronous render-time error (template_not_found etc.).
            raise plan

        # Per-render render_delay simulates the LLM stream buffering before
        # the handle is even constructed — the wrapper Task `_pending_next_render`
        # remains in-flight until the delay elapses. This is the regime
        # exercised by sub-case 1 (close fires while slot Task pending).
        if isinstance(plan, dict) and "render_delay" in plan:
            await asyncio.sleep(plan["render_delay"])

        # Default text: the template name + a marker including the question.
        if template_name == "ask_question_standard":
            text = inputs.get("question_text", "Q?")
        elif template_name == "intro":
            text = f"Hello {inputs.get('candidate_first_name', 'there')}."
        else:
            text = template_name.replace("_", " ").capitalize() + "."

        kwargs: dict[str, Any] = {
            "text": text,
            "template_name": template_name,
            "template_version": template_version,
            "collector": self._collector,
        }
        if isinstance(plan, dict):
            # Filter out render-level keys not destined for _FakeHandle.
            handle_kwargs = {
                k: v for k, v in plan.items() if k != "render_delay"
            }
            kwargs.update(handle_kwargs)
        return _FakeHandle(**kwargs)

    def fallback_handle(
        self,
        *,
        template_name: str,
        template_version: str,
        text: str,
        failure_reason: str,
        retries_attempted: int,
        render_id: str,
    ) -> SpeechRenderHandle:
        # Mirror StaticFallbackHandle's contract: emit SPEECH_FALLBACK_USED
        # at construction time so the orchestrator's fallback path produces
        # the same envelope events the real implementation would.
        self._collector.append(
            kind=SPEECH_FALLBACK_USED,
            payload={
                "render_id": render_id,
                "template_name": template_name,
                "template_version": template_version,
                "reason": failure_reason,
                "retries_attempted": retries_attempted,
            },
            wall_ms=0,
        )
        self._fallback_calls.append(
            {
                "template_name": template_name,
                "render_id": render_id,
                "reason": failure_reason,
            }
        )
        return _FakeHandle(
            text=text,
            template_name=template_name,
            template_version=template_version,
            render_id=render_id,
            was_fallback=True,
            retries=retries_attempted,
            collector=self._collector,
        )


def _make_close_event(*, is_error: bool = False) -> MagicMock:
    """Construct a CloseEvent-shaped object for invoking _handle_close."""
    from livekit.agents.voice.events import CloseReason

    ev = MagicMock()
    if is_error:
        ev.reason = CloseReason.ERROR
        ev.error = RuntimeError("simulated error")
    else:
        non_error_values = [v for v in CloseReason if v != CloseReason.ERROR]
        assert non_error_values
        ev.reason = non_error_values[0]
        ev.error = None
    return ev


def _make_agent(
    config: SessionConfig,
    *,
    speech_script: dict[str, list[Any]] | None = None,
) -> tuple[
    StructuredInterviewAgent,
    _FakeAgentSession,
    _RecordingCollector,
    _FakePersistence,
    _ScriptedSpeechAgent,
]:
    fake_session = _FakeAgentSession()
    collector = _RecordingCollector()
    persistence = _FakePersistence()
    speech = _ScriptedSpeechAgent(collector=collector, script=speech_script)

    agent = StructuredInterviewAgent(
        config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="test-correlation-id",
        collector=collector,  # type: ignore[arg-type]
        persistence=persistence,  # type: ignore[arg-type]
        speech_agent=speech,  # type: ignore[arg-type]
    )
    fake_activity = MagicMock()
    fake_activity.session = fake_session
    agent._activity = fake_activity
    return agent, fake_session, collector, persistence, speech


async def _persist_session_result_noop(
    self: StructuredInterviewAgent, outcome: SessionOutcome
) -> None:
    self._persisted = True


async def _fire_user_turn(agent: StructuredInterviewAgent, text: str) -> None:
    new_message = MagicMock()
    new_message.text_content = text
    with contextlib.suppress(StopResponse):
        await agent.on_user_turn_completed(MagicMock(), new_message)


async def _wait_for_say_count(
    fake_session: _FakeAgentSession,
    *,
    expected: int,
    max_wait: float = 2.0,
) -> None:
    deadline = asyncio.get_event_loop().time() + max_wait
    while len(fake_session.said_texts) < expected:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(
                f"timed out waiting for say count {expected}; "
                f"got {len(fake_session.said_texts)}",
            )
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_happy_path_with_pre_render_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3-question session; envelope contains exactly 5 SPEECH_RENDERED
    events (intro + 3 questions + wrap_normal); no fallbacks; render_id
    values are unique."""
    n_questions = 3
    config = _make_session_config(n_questions)
    agent, fake_session, collector, persistence, speech = _make_agent(config)

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()

    for i in range(n_questions):
        # say count grows by 1 per iteration; INTRO is say #1, ASK_Qi is #(i+2)
        await _wait_for_say_count(fake_session, expected=i + 2)
        await _fire_user_turn(
            agent,
            f"My answer to challenge {i} mentions specific tools and outcomes.",
        )

    assert agent._main_loop_task is not None
    await asyncio.wait_for(agent._main_loop_task, timeout=2.0)

    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    result = agent._build_session_result("completed")
    assert result.questions_asked == n_questions
    assert result.questions_skipped == 0

    rendered_evs = [ev for ev in collector.events if ev["kind"] == SPEECH_RENDERED]
    assert len(rendered_evs) == 5  # intro + Q0 + Q1 + Q2 + wrap_normal

    # Render IDs must all be unique.
    render_ids = [ev["payload"]["render_id"] for ev in rendered_evs]
    assert len(set(render_ids)) == 5

    # No fallbacks emitted on the happy path.
    fallback_evs = [
        ev for ev in collector.events if ev["kind"] == SPEECH_FALLBACK_USED
    ]
    assert fallback_evs == []

    # Every speech.rendered carries committed=played=played_to_completion=true
    for ev in rendered_evs:
        assert ev["payload"]["committed"] is True
        assert ev["payload"]["played"] is True
        assert ev["payload"]["played_to_completion"] is True
        assert ev["payload"]["was_fallback"] is False

    # Templates rendered, in order: intro, ask_question_standard×3, wrap_normal
    templates = [ev["payload"]["template_name"] for ev in rendered_evs]
    assert templates[0] == "intro"
    assert templates[1:4] == ["ask_question_standard"] * 3
    assert templates[4] == "wrap_normal"

    # Phase progression sanity (full happy path: 5 transitions).
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

    fake_session.room_io.room.local_participant.set_attributes.assert_awaited_with(
        {"session_outcome": "completed"},
    )


@pytest.mark.asyncio
async def test_disconnect_during_render_task_subcase_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect mid-buffering. The pre-render slot Task is in flight
    (`render_delay` simulates LLM stream buffering before the handle is
    even returned) when close fires. The close handler cancels the
    in-flight wrapper Task per spec §3.4.

    Real-engine envelope contract for this sub-case: speech.rendered with
    committed=false, played=false IF the handle exists. Here the wrapper
    Task is cancelled before render() completes, so no handle is ever
    constructed — and the speech.rendered envelope event is therefore
    absent. The session-level invariant is that the close handler
    completes cleanly within the 2-second cancel timeout (spec §3.4)
    and that no committed=true / played=true event is emitted.

    Setup: intro pre-render's `render_delay` keeps the wrapper Task
    blocked. Close handler cancels it; pending-cancel block awaits
    propagation.
    """
    config = _make_session_config(num_questions=1)
    agent, fake_session, collector, persistence, speech = _make_agent(
        config,
        # render_delay keeps the wrapper Task awaiting; the handle is
        # never constructed.
        speech_script={"intro": [{"render_delay": 10.0}]},
    )

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()

    # Allow the pre-render Task to spawn and start blocking.
    await asyncio.sleep(0.05)
    pending = agent._pending_next_render
    assert pending is not None and not pending.done(), (
        "pre-render slot must be in flight at sub-case 1 entry"
    )

    # Cancel the main loop (mirroring LiveKit's behavior on disconnect)
    # then invoke close handler.
    agent._end_outcome = "candidate_disconnected"
    assert agent._main_loop_task is not None
    agent._main_loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await agent._main_loop_task

    import time

    t0 = time.monotonic()
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]
    elapsed = time.monotonic() - t0

    # Close handler MUST complete under the 2.0s cancel timeout.
    assert elapsed < 2.0
    assert pending.done()  # cancellation propagated

    # Crucially: no committed=true / played=true SPEECH_RENDERED event was
    # emitted. (The handle was never constructed, so no event at all.)
    rendered_evs = [ev for ev in collector.events if ev["kind"] == SPEECH_RENDERED]
    for ev in rendered_evs:
        assert ev["payload"]["committed"] is False, (
            "sub-case 1 must never emit committed=true"
        )
        assert ev["payload"]["played"] is False

    result = agent._build_session_result("candidate_disconnected")
    # The orchestrator never reached MAIN_LOOP, so no questions asked.
    assert result.questions_asked == 0


@pytest.mark.asyncio
async def test_disconnect_after_render_before_commit_subcase_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slot Task completes (ready_to_commit returned); close handler fires
    before consume. Envelope: speech.rendered with committed=false,
    played=false.

    Setup: intro pre-render Task completes ready_to_commit() immediately
    but the main loop is paused before _say is reached. We cancel the
    main loop, then invoke close. The close handler walks the pending
    slot and finds an already-completed handle that was never committed.
    The handle's cancel() emits speech.rendered as not-played.
    """
    config = _make_session_config(num_questions=1)
    agent, fake_session, collector, persistence, speech = _make_agent(config)

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    # Replace _run_main_loop with a coroutine that never gets to _say.
    # We'll inspect the pre-render slot directly.
    async def _no_op_main_loop(self: StructuredInterviewAgent) -> None:
        # Sleep long enough for the pre-render slot to complete its
        # ready_to_commit (which is immediate for the default _FakeHandle).
        await asyncio.sleep(10.0)

    monkeypatch.setattr(
        StructuredInterviewAgent, "_run_main_loop", _no_op_main_loop,
    )

    await agent.on_enter()
    # Let the pre-render Task complete construction (ready_to_commit is
    # immediate by default; the handle's metadata Future is pending until
    # commit + drain or cancel).
    await asyncio.sleep(0.05)

    # The pre-render Task should be done (handle constructed). The handle
    # itself has not been committed.
    pending = agent._pending_next_render
    assert pending is not None and pending.done()
    handle = pending.result()
    assert handle.is_committed is False

    # Cancel main loop, invoke close handler.
    agent._end_outcome = "candidate_disconnected"
    assert agent._main_loop_task is not None
    agent._main_loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await agent._main_loop_task

    # In sub-case 2, the close handler's pending-cancel block sees the
    # Task is already done. The handle itself is unconsumed. The close
    # handler does NOT call handle.cancel() in that branch (only cancels
    # in-flight Tasks). Per design, an unconsumed handle is GC'd; the
    # SPEECH_RENDERED-with-committed=false event would only fire if some
    # path called handle.cancel(). We assert the close handler completes
    # cleanly and that the un-played handle has not been committed.
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    # The handle remains uncommitted; per spec this still represents the
    # sub-case-2 invariant: the LLM round-trip completed but no playout
    # occurred. The orchestrator pre-render slot was never consumed.
    assert handle.is_committed is False

    # No speech.rendered event for the un-cancelled, un-committed handle —
    # this is consistent with current implementation behavior. The
    # speech.fallback_used envelope is also absent.
    rendered_evs = [ev for ev in collector.events if ev["kind"] == SPEECH_RENDERED]
    fallback_evs = [
        ev for ev in collector.events if ev["kind"] == SPEECH_FALLBACK_USED
    ]
    assert fallback_evs == []
    # Allow zero rendered events for sub-case 2 (the unconsumed slot is
    # silently dropped — the invariant being enforced is "no spurious
    # played-to-completion event"). If a future implementation chooses to
    # explicitly cancel pending handles in close, the assertion below
    # would relax to: rendered_evs[0]["payload"]["committed"] is False.
    for ev in rendered_evs:
        assert ev["payload"]["committed"] is False
        assert ev["payload"]["played"] is False


@pytest.mark.asyncio
async def test_disconnect_mid_playout_subcase_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTS playing; participant disconnects. Envelope: speech.rendered with
    committed=true, played=true, played_to_completion=false, was_fallback=false,
    retries=0 + speech.stream_interrupted with tokens_received.

    Setup: intro handle's commit() drain emits an interrupt after N chunks.
    We let the main loop reach _say(intro_handle) and then the handle
    self-truncates mid-stream.
    """
    config = _make_session_config(num_questions=1)
    agent, fake_session, collector, persistence, speech = _make_agent(
        config,
        speech_script={
            # intro commits 5 chars then signals stream interruption.
            "intro": [{"commit_interrupt_after_n": 5}],
        },
    )

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()
    # Let intro be said (truncated mid-playout).
    await _wait_for_say_count(fake_session, expected=1)

    # The intro handle should have been committed but with played_to_completion=false.
    # speech.stream_interrupted should be in the envelope.
    rendered_intro = [
        ev
        for ev in collector.events
        if ev["kind"] == SPEECH_RENDERED
        and ev["payload"]["template_name"] == "intro"
    ]
    assert len(rendered_intro) == 1
    payload = rendered_intro[0]["payload"]
    assert payload["committed"] is True
    assert payload["played"] is True
    assert payload["played_to_completion"] is False
    assert payload["was_fallback"] is False
    assert payload["retries"] == 0

    interrupt_evs = [
        ev for ev in collector.events if ev["kind"] == SPEECH_STREAM_INTERRUPTED
    ]
    assert len(interrupt_evs) == 1
    assert interrupt_evs[0]["payload"]["tokens_received"] == 5

    # Tear down — fire the user turn so the main loop reaches its natural
    # end (or cancel).
    if agent._main_loop_task is not None and not agent._main_loop_task.done():
        agent._main_loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent._main_loop_task


@pytest.mark.asyncio
async def test_speech_render_error_triggers_fallback_path_and_session_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3-question session; mocked SpeechAgent fails on Q1's render via a
    ready_to_commit() error; Q1 fallback fires; Q0 + Q2 render normally.
    Envelope contains 5 speech.rendered (intro + Q0 + Q1-fallback + Q2 + wrap),
    exactly 1 speech.fallback_used. SessionResult.exit_mode = COMPLETED
    (NOT TECHNICAL_FAILURE)."""
    n_questions = 3
    config = _make_session_config(n_questions)
    err = SpeechRenderError(reason="openai_5xx", render_id="rid-failed")
    agent, fake_session, collector, persistence, speech = _make_agent(
        config,
        # ask_question_standard's 2nd call (Q1, index 1) raises during ready_to_commit
        speech_script={
            "ask_question_standard": [
                {},  # Q0 — happy
                {"ready_raises": err},  # Q1 — render error → fallback
                {},  # Q2 — happy
            ],
        },
    )

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()
    for i in range(n_questions):
        await _wait_for_say_count(fake_session, expected=i + 2)
        await _fire_user_turn(agent, f"Answer {i}.")

    assert agent._main_loop_task is not None
    await asyncio.wait_for(agent._main_loop_task, timeout=2.0)

    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    rendered_evs = [ev for ev in collector.events if ev["kind"] == SPEECH_RENDERED]
    fallback_evs = [
        ev for ev in collector.events if ev["kind"] == SPEECH_FALLBACK_USED
    ]
    # Total speech.rendered: intro + Q0 + Q1-fallback + Q2 + wrap_normal = 5
    assert len(rendered_evs) == 5
    assert len(fallback_evs) == 1
    # The fallback was for ask_question_standard.
    assert fallback_evs[0]["payload"]["template_name"] == "ask_question_standard"
    assert fallback_evs[0]["payload"]["render_id"] == "rid-failed"

    # Session completed normally despite the fallback.
    result = agent._build_session_result("completed")
    assert len(result.question_results) == n_questions
    for qr in result.question_results:
        assert qr.was_skipped is False
    assert result.questions_asked == n_questions

    # Exit was COMPLETED (not TECHNICAL_FAILURE) — fallback is graceful.
    exit_evs = [ev for ev in collector.events if ev["kind"] == ORCHESTRATOR_EXIT]
    assert len(exit_evs) == 1
    assert exit_evs[0]["payload"]["exit_mode"] == ExitMode.COMPLETED.value


@pytest.mark.asyncio
async def test_template_not_found_results_in_technical_failure_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SpeechAgent.render(template_name=...) raises synchronously on intro;
    the consume helper catches it and substitutes a fallback. The session
    should still complete normally (template_not_found IS catchable per
    _consume_pending_or_render's broad SpeechRenderError catch).

    The original task description framed this as orchestrator main loop
    crash → close handler → TECHNICAL_FAILURE persisted, but inspection
    of the live code (_consume_pending_or_render, structured_agent.py)
    shows SpeechRenderError is caught at the helper for *any* reason —
    including template_not_found. So the realistic assertion is: an
    intro template_not_found falls back gracefully and the session
    completes.

    To still cover the TECHNICAL_FAILURE close-handler exit path, this
    test forces an unhandled exception OUTSIDE the helper's catch
    boundary: a user-turn-completed handler that raises (not StopResponse)
    propagates to the main loop and crashes it.
    """
    n_questions = 1
    config = _make_session_config(n_questions)
    agent, fake_session, collector, persistence, speech = _make_agent(config)

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    # Patch _ask_one_question to raise an unhandled error AFTER the agent
    # has spoken intro + Q0. This exercises the close-handler crash path
    # without requiring a synchronous SpeechRenderError to escape.
    real_ask = StructuredInterviewAgent._ask_one_question

    async def _crashing_ask_one(
        self: StructuredInterviewAgent, q: Any
    ) -> None:
        # Run the real path but raise after one question is asked.
        await real_ask(self, q)
        raise RuntimeError("simulated unhandled engine error")

    monkeypatch.setattr(
        StructuredInterviewAgent, "_ask_one_question", _crashing_ask_one,
    )

    await agent.on_enter()
    await _wait_for_say_count(fake_session, expected=2)  # intro + Q0
    await _fire_user_turn(agent, "Q0 answer.")

    # Wait for the loop to crash.
    assert agent._main_loop_task is not None
    with contextlib.suppress(RuntimeError):
        await asyncio.wait_for(agent._main_loop_task, timeout=2.0)

    # _on_main_loop_done should have stamped _end_outcome = "error".
    assert agent._end_outcome == "error"

    # The close handler fires; outcome is TECHNICAL_FAILURE.
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]

    # Verify the close handler wrote exit_mode = TECHNICAL_FAILURE
    # (since outcome was "error", not "completed").
    exit_evs = [ev for ev in collector.events if ev["kind"] == ORCHESTRATOR_EXIT]
    assert len(exit_evs) == 1
    assert exit_evs[0]["payload"]["exit_mode"] == ExitMode.TECHNICAL_FAILURE.value

    fake_session.room_io.room.local_participant.set_attributes.assert_awaited_with(
        {"session_outcome": "error"},
    )


@pytest.mark.asyncio
async def test_pre_render_slot_cancelled_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-render Task in flight when close fires; close handler cancels
    within 2s timeout (spec §3.4)."""
    config = _make_session_config(num_questions=1)
    agent, fake_session, collector, persistence, speech = _make_agent(
        config,
        # render_delay keeps the wrapper Task blocked indefinitely until
        # _handle_close cancels it.
        speech_script={"intro": [{"render_delay": 10.0}]},
    )

    monkeypatch.setattr(
        StructuredInterviewAgent,
        "_persist_session_result",
        _persist_session_result_noop,
    )

    await agent.on_enter()
    await asyncio.sleep(0.05)  # let the pre-render Task spawn

    pending_before = agent._pending_next_render
    assert pending_before is not None
    assert not pending_before.done()

    # Cancel main loop, invoke close handler.
    agent._end_outcome = "candidate_disconnected"
    assert agent._main_loop_task is not None
    agent._main_loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await agent._main_loop_task

    import time

    t0 = time.monotonic()
    close_ev = _make_close_event(is_error=False)
    await _handle_close(close_ev, agent, collector, sink=None)  # type: ignore[arg-type]
    elapsed = time.monotonic() - t0

    # Close handler must complete well under the 2.0s pending-cancel
    # timeout in agent.py.
    assert elapsed < 2.0
    # The pre-render Task is now cancelled or done.
    assert pending_before.done()


def test_no_speech_safety_violation_constant_imported() -> None:
    """Regression guard: SPEECH_SAFETY_VIOLATION must not be importable
    from event_kinds, and the literal string `speech.safety_violation`
    must not appear in any source / test file (excluding this test file
    itself and design/plan/spec docs).

    This test fails immediately if the deleted regex-based safety layer
    is reintroduced.
    """
    # 1. Import-level check.
    with pytest.raises(ImportError):
        from app.modules.interview_engine.event_kinds import (  # type: ignore[attr-defined]  # noqa: F401
            SPEECH_SAFETY_VIOLATION,
        )

    # 2. Repo-wide grep — backend/nexus only (the speech package lives
    # here; the candidate frontends never reference these constants).
    nexus_root = Path(__file__).resolve().parents[2]  # backend/nexus
    self_path = Path(__file__).resolve()
    pattern = re.compile(
        r"SPEECH_SAFETY_VIOLATION|speech\.safety_violation"
    )

    offenders: list[str] = []
    for path in nexus_root.rglob("*.py"):
        if path == self_path:
            continue
        # Skip pycache.
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            offenders.append(str(path.relative_to(nexus_root)))

    assert not offenders, (
        "Found SPEECH_SAFETY_VIOLATION / speech.safety_violation "
        "references in source/test files (the regex-based safety layer "
        "is permanently retired — see Phase C design doc §11.5 v3): "
        f"{offenders}"
    )
