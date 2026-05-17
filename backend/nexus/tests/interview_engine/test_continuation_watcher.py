"""Tests for the pre-Speaker cancellation watcher (2026-05-17 design).

Spec: docs/superpowers/specs/2026-05-17-conversational-continuation-design.md

The watcher mechanism makes the orchestrator refuse to commit a turn
until the agent has actually started speaking. While the Judge → State
Engine → Speaker pipeline is in flight, if the candidate's user_state
sustains "speaking" for >= engine_continuation_min_speech_duration_ms,
the pipeline is cancelled, the State Engine is rolled back to the
pre-turn snapshot, and the candidate's text is buffered for stitching
into the next turn.

We use small thresholds (20ms speech duration) so tests run quickly
and deterministically.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents import StopResponse

from app.modules.interview_engine.audit_events import (
    TurnAbortedForContinuationPayload,
    TurnStitchedContinuationPayload,
)
from app.modules.interview_engine.event_kinds import (
    STATE_SNAPSHOT_COMMITTED,
    STATE_SNAPSHOT_RESTORED,
    STATE_SNAPSHOT_TAKEN,
    TURN_ABORTED_FOR_CONTINUATION,
    TURN_COMPLETED,
    TURN_LOOP_GUARD_FIRED,
    TURN_STITCHED_CONTINUATION,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import AttributePublisher
from app.modules.interview_engine.models.judge import (
    CoverageQuality, CoverageTransition, NextAction, Observation,
)
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig,
)
from app.modules.interview_engine.state.engine import StateEngine


# --- Test fixtures -----------------------------------------------------------

class FakeAgentSession:
    """Minimal stand-in for AgentSession with event subscription + interrupt."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self.say = AsyncMock()
        self.interrupt = MagicMock()
        # Shutdown is a sync method on the real AgentSession (returns None).
        self.shutdown = MagicMock(return_value=None)

    def on(self, event: str, handler):
        self._handlers.setdefault(event, []).append(handler)
        return handler

    def off(self, event: str, handler):
        if event in self._handlers and handler in self._handlers[event]:
            self._handlers[event].remove(handler)

    def fire_user_state(self, new_state: str) -> None:
        ev = MagicMock(new_state=new_state, old_state="other")
        for h in list(self._handlers.get("user_state_changed", [])):
            h(ev)

    def fire_agent_state(self, new_state: str) -> None:
        ev = MagicMock(new_state=new_state, old_state="other")
        for h in list(self._handlers.get("agent_state_changed", [])):
            h(ev)

    def fire_user_input_transcribed(
        self, transcript: str, *, is_final: bool = True,
    ) -> None:
        ev = MagicMock(
            transcript=transcript, is_final=is_final,
            language="en", speaker_id=None,
        )
        for h in list(self._handlers.get("user_input_transcribed", [])):
            h(ev)


class _FakeSpeakerHandle:
    """Minimal stand-in for SpeakerStreamHandle that fires agent_state on stream."""

    def __init__(self, text: str, *, on_stream=None) -> None:
        self._text = text
        self._final = text
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.latency_ms_first_token = 10
        self.latency_ms_total = 20
        self.prompt_hash = "sha256:" + ("a" * 64)
        self.event_types_seen: list[str] = []
        self.refusal_text: str | None = None
        self.response_id: str | None = None
        self.finish_reason: str | None = None
        self._on_stream = on_stream

    def stream(self):
        async def gen():
            yield self._text
        return gen()

    async def final_text(self) -> str:
        return self._final


def _collector() -> EventCollector:
    return EventCollector(
        session_id="s", tenant_id="t", correlation_id="c",
        controller_prompt_hash="sha256:ctrl",
        model_versions={"judge": "m1", "speaker": "m1"},
        redaction_mode="metadata",
        task_prompt_hashes={"judge": "sha256:j", "speaker": "sha256:s"},
    )


def _build_orchestrator(
    *,
    cfg,
    fake_session: FakeAgentSession,
    speaker_handle: _FakeSpeakerHandle,
    judge_action: NextAction = NextAction.probe,
    judge_target: str = "q1",
    judge_observations: list[Observation] | None = None,
    judge_delay_s: float = 0.0,
    continuation_min_word_count: int = 2,
    continuation_cap: int = 3,
    continuation_enabled: bool = True,
) -> tuple[InterviewOrchestrator, EventCollector, StateEngine, MagicMock]:
    """Build an orchestrator with the new continuation settings.

    Returns (orchestrator, collector, state_engine, fake_agent) so tests
    can introspect state / events / agent.
    """
    from app.modules.interview_engine.models.judge import (
        AdvancePayload, JudgeOutput, ProbePayload, TurnMetadata,
    )

    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock(return_value=speaker_handle)

    if judge_action == NextAction.probe:
        payload = ProbePayload(probe_id="0")
    elif judge_action == NextAction.advance:
        payload = AdvancePayload(target_question_id=judge_target)
    else:
        raise ValueError(f"unsupported test action {judge_action!r}")
    judge_output = JudgeOutput(
        observations=judge_observations or [],
        candidate_claims=[],
        next_action=judge_action,
        next_action_payload=payload,
        turn_metadata=TurnMetadata(),
    )

    async def _judge_call(**_kwargs):
        if judge_delay_s > 0:
            await asyncio.sleep(judge_delay_s)
        return MagicMock(
            judge_output=judge_output,
            is_fallback=False, fallback_reason=None,
            original_failure_context=None, latency_ms=10,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            model_used="test",
        )

    judge_service = MagicMock()
    judge_service.call = AsyncMock(side_effect=_judge_call)

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)

    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()

    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge_service,
        speaker=speaker_service,
        attr_publisher=pub,
        event_collector=collector,
        correlation_id="c",
        config=OrchestratorConfig(
            continuation_enabled=continuation_enabled,
            continuation_min_word_count=continuation_min_word_count,
            continuation_consecutive_abort_cap=continuation_cap,
        ),
        tenant_id="t",
    )
    return orch, collector, state_engine, fake_agent


def _new_message(text: str):
    """Construct a mock new_message with text_content property."""
    msg = MagicMock()
    msg.text_content = text
    return msg


async def _prime_session_start(orch, fake_agent, fake_session):
    """Drive on_enter so q1 is active and the State Engine is past
    pre_start. Also wires _on_stream callback so agent_state speaking
    fires the moment Speaker.say runs."""
    async def _say_with_agent_state(*args, **kwargs):
        fake_session.fire_agent_state("speaking")

    fake_session.say.side_effect = _say_with_agent_state
    await orch.on_enter(fake_agent)
    # Reset side_effect for subsequent tests to set their own behavior.
    fake_session.say.side_effect = None
    fake_session.say.reset_mock()


# --- Watcher behavior --------------------------------------------------------


@pytest.mark.asyncio
async def test_watcher_does_not_fire_for_short_transcript(
    make_session_config, make_question,
):
    """An STT-final with fewer words than min_word_count must not abort.

    STT confirms the user said something, but a single filler word
    ("uh", "okay") is below the meaningful-speech threshold. The watcher
    must distinguish real content from such interjections.
    """
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
            make_question(qid="q2", position=1, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, _state, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="I did this.",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.05,           # 50ms judge — window for STT event
        continuation_min_word_count=3,  # require ≥3 words to abort
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    collector._events.clear()  # noqa: SLF001 — test-only; .events is a copying property

    async def _emit_short_final():
        await asyncio.sleep(0.005)
        # Only 2 words — below the 3-word threshold.
        fake_session.fire_user_input_transcribed("uh okay", is_final=True)

    short_task = asyncio.create_task(_emit_short_final())
    await orch.on_user_turn_completed(fake_agent, MagicMock(), _new_message("answer"))
    await short_task

    kinds = [e.kind for e in collector.events]
    assert TURN_ABORTED_FOR_CONTINUATION not in kinds
    assert TURN_COMPLETED in kinds
    assert STATE_SNAPSHOT_COMMITTED in kinds
    assert orch._pending_continuation_text is None


@pytest.mark.asyncio
async def test_watcher_does_not_fire_for_interim_transcripts(
    make_session_config, make_question,
):
    """Interim (is_final=False) STT events are noisy; only finals trigger."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", position=0, mandatory=True)],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, _state, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="ok",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.05,
        continuation_min_word_count=1,  # very permissive, but interim still ignored
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    collector._events.clear()  # noqa: SLF001

    async def _emit_interim_only():
        await asyncio.sleep(0.005)
        fake_session.fire_user_input_transcribed("interim long content", is_final=False)

    task = asyncio.create_task(_emit_interim_only())
    await orch.on_user_turn_completed(fake_agent, MagicMock(), _new_message("answer"))
    await task

    kinds = [e.kind for e in collector.events]
    assert TURN_ABORTED_FOR_CONTINUATION not in kinds
    assert TURN_COMPLETED in kinds


@pytest.mark.asyncio
async def test_watcher_fires_for_stt_final_with_real_content(
    make_session_config, make_question,
):
    """STT-final with >= min_word_count during Judge aborts the turn.

    The Judge sleep is long enough that the cancellation actually
    propagates: now that JudgeService.call no longer swallows
    CancelledError, turn_task.cancel() unwinds the Judge before the
    Speaker is invoked. No agent audio plays.
    """
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
            make_question(qid="q2", position=1, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, state_engine, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="I did this.",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.2,                  # 200ms judge — plenty of time to abort
        continuation_min_word_count=2,
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    # Snapshot state for comparison post-abort.
    pre_snapshot = state_engine.snapshot_full()
    collector._events.clear()  # noqa: SLF001

    async def _emit_substantive_final():
        await asyncio.sleep(0.005)
        fake_session.fire_user_input_transcribed(
            "I also wanted to add another point", is_final=True,
        )

    speech_task = asyncio.create_task(_emit_substantive_final())

    with pytest.raises(StopResponse):
        await orch.on_user_turn_completed(
            fake_agent, MagicMock(), _new_message("orphan fragment"),
        )
    await speech_task

    kinds = [e.kind for e in collector.events]
    assert TURN_ABORTED_FOR_CONTINUATION in kinds
    assert STATE_SNAPSHOT_RESTORED in kinds
    assert TURN_COMPLETED not in kinds
    assert STATE_SNAPSHOT_COMMITTED not in kinds

    # Pending buffer holds the candidate's text for stitching next turn.
    assert orch._pending_continuation_text == "orphan fragment"
    assert orch._consecutive_aborts == 1

    # State engine was restored — byte-identical to pre-turn snapshot.
    assert state_engine.ledger_snapshot() == pre_snapshot.ledger
    assert state_engine.queue_snapshot() == pre_snapshot.queue
    assert state_engine.turn_count_snapshot() == pre_snapshot.turn_count

    fake_session.interrupt.assert_called()


@pytest.mark.asyncio
async def test_stitch_prepends_pending_continuation(
    make_session_config, make_question,
):
    """Next on_user_turn_completed prepends _pending_continuation_text and clears it."""
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, state_engine, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="ok",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.0,
        continuation_min_word_count=2,
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    collector._events.clear()  # noqa: SLF001 — test-only; .events is a copying property

    # Pre-seed pending continuation text — simulates an earlier abort.
    orch._pending_continuation_text = "first half of answer"
    orch._consecutive_aborts = 1

    await orch.on_user_turn_completed(
        fake_agent, MagicMock(), _new_message("second half"),
    )

    # TURN_STITCHED_CONTINUATION emitted.
    stitch_events = [
        e for e in collector.events
        if e.kind == TURN_STITCHED_CONTINUATION
    ]
    assert len(stitch_events) == 1
    payload = stitch_events[0].payload
    assert payload["prior_chars"] == len("first half of answer")
    assert payload["current_chars"] == len("second half")
    # Pending buffer cleared on consumption.
    assert orch._pending_continuation_text is None
    # Counter reset on successful commit.
    assert orch._consecutive_aborts == 0

    # The Judge received the combined text — read the candidate utterance
    # captured in the transcript.
    transcript = state_engine.transcript_snapshot()
    candidate_entries = [t for t in transcript if t.role == "candidate"]
    assert candidate_entries
    assert candidate_entries[-1].text == "first half of answer second half"


@pytest.mark.asyncio
async def test_loop_guard_commits_after_cap_consecutive_aborts(
    make_session_config, make_question,
):
    """After ``continuation_consecutive_abort_cap`` consecutive aborts, the
    next turn skips the watcher and commits even with sustained speech."""
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, _state, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="ok",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.1,             # gives time for watcher to fire
        continuation_min_word_count=2,
        continuation_cap=3,
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say

    # Simulate 3 consecutive aborts arriving at this orchestrator.
    orch._consecutive_aborts = 3
    orch._pending_continuation_text = "prior fragments"
    collector._events.clear()  # noqa: SLF001 — test-only; .events is a copying property

    async def _emit_stt_final():
        await asyncio.sleep(0.005)
        fake_session.fire_user_input_transcribed(
            "I have more to say", is_final=True,
        )

    speech_task = asyncio.create_task(_emit_stt_final())
    # Loop guard fires; we commit instead of aborting.
    await orch.on_user_turn_completed(
        fake_agent, MagicMock(), _new_message("fourth try"),
    )
    await speech_task

    kinds = [e.kind for e in collector.events]
    assert TURN_LOOP_GUARD_FIRED in kinds
    assert TURN_COMPLETED in kinds
    assert TURN_ABORTED_FOR_CONTINUATION not in kinds
    # Counter reset on commit.
    assert orch._consecutive_aborts == 0
    # Text was stitched (loop guard does not skip stitching).
    assert orch._pending_continuation_text is None


@pytest.mark.asyncio
async def test_commit_after_tts_disengages_watcher(
    make_session_config, make_question,
):
    """User starts speaking AFTER agent_state==speaking → no abort,
    framework handles it as a new turn via adaptive interruption."""
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, _state, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="ok",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.0,
        continuation_min_word_count=2,
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
        # AFTER commit point, an STT-final arrives — must NOT abort.
        fake_session.fire_user_input_transcribed(
            "I have more to add", is_final=True,
        )
        await asyncio.sleep(0.02)
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    collector._events.clear()  # noqa: SLF001 — test-only; .events is a copying property

    # No abort — commit point already reached.
    await orch.on_user_turn_completed(
        fake_agent, MagicMock(), _new_message("answer"),
    )

    kinds = [e.kind for e in collector.events]
    assert TURN_COMPLETED in kinds
    assert TURN_ABORTED_FOR_CONTINUATION not in kinds
    assert STATE_SNAPSHOT_COMMITTED in kinds
    assert orch._pending_continuation_text is None


@pytest.mark.asyncio
async def test_snapshot_taken_audit_event_fires_each_turn(
    make_session_config, make_question,
):
    """STATE_SNAPSHOT_TAKEN must fire at the top of every turn, carrying
    forensic fields for replay tools."""
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, _state, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="ok",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.0,
        continuation_min_word_count=2,
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    collector._events.clear()  # noqa: SLF001 — test-only; .events is a copying property

    await orch.on_user_turn_completed(
        fake_agent, MagicMock(), _new_message("answer"),
    )

    snap_events = [e for e in collector.events if e.kind == STATE_SNAPSHOT_TAKEN]
    assert len(snap_events) == 1
    p = snap_events[0].payload
    assert "turn_id" in p
    assert p["transcript_entries"] >= 0
    # active_index can be 0 (q1 active after on_enter).
    assert p["queue_active_index"] is not None


@pytest.mark.asyncio
async def test_continuation_disabled_skips_watcher_entirely(
    make_session_config, make_question,
):
    """When ``continuation_enabled=False``, the orchestrator behaves
    exactly like the pre-2026-05-17 path: no snapshot, no watcher.
    Sustained user speech during the turn does NOT abort."""
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True),
        ],
        signals=["S1"],
    )
    fake_session = FakeAgentSession()
    handle = _FakeSpeakerHandle("ack")

    orch, collector, _state, fake_agent = _build_orchestrator(
        cfg=cfg, fake_session=fake_session, speaker_handle=handle,
        judge_action=NextAction.probe, judge_target="q1",
        judge_observations=[Observation(
            signal_value="S1", anchor_id=0, evidence_quote="ok",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=CoverageQuality.concrete,
        )],
        judge_delay_s=0.1,
        continuation_min_word_count=2,
        continuation_enabled=False,    # KILL SWITCH
    )

    async def _say(*args, **kwargs):
        fake_session.fire_agent_state("speaking")
    fake_session.say.side_effect = _say
    await _prime_session_start(orch, fake_agent, fake_session)
    fake_session.say.side_effect = _say
    collector._events.clear()  # noqa: SLF001 — test-only; .events is a copying property

    async def _emit_stt_final():
        await asyncio.sleep(0.005)
        fake_session.fire_user_input_transcribed(
            "I want to add another point", is_final=True,
        )

    speech_task = asyncio.create_task(_emit_stt_final())
    await orch.on_user_turn_completed(
        fake_agent, MagicMock(), _new_message("answer"),
    )
    await speech_task

    kinds = [e.kind for e in collector.events]
    assert TURN_ABORTED_FOR_CONTINUATION not in kinds
    assert STATE_SNAPSHOT_TAKEN not in kinds
    assert STATE_SNAPSHOT_COMMITTED not in kinds
    assert TURN_COMPLETED in kinds
