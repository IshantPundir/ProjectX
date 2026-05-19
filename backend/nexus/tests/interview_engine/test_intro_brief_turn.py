"""Verifies on_enter fires intro_brief before the first question.

These are unit-flavored: the LiveKit agent + Speaker service are stubbed
and we assert the orchestrator's behavior on the deterministic state
mutations + audit-event sequence.

Spec: docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §2
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import AttributePublisher
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig,
)
from app.modules.interview_engine.state.engine import StateEngine


# ---------------------------------------------------------------------------
# Minimal scaffolding (reused across the four tests in this file)
# ---------------------------------------------------------------------------


def _collector() -> EventCollector:
    return EventCollector(
        session_id="s", tenant_id="t", correlation_id="c",
        controller_prompt_hash="sha256:ctrl",
        model_versions={"judge": "m1", "speaker": "m1"},
        redaction_mode="metadata",
        task_prompt_hashes={"judge": "sha256:j", "speaker": "sha256:s"},
    )


class _FakeSpeakerHandle:
    """Minimal stand-in for SpeakerStreamHandle.

    Returns the same canned text for any invocation. The orchestrator
    only reads ``final_text()`` / ``stream()`` + a few diagnostic
    attributes — everything else is shaped to satisfy the audit-payload
    Pydantic validators.
    """

    def __init__(self, text: str = "ok.") -> None:
        self._text = text
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.latency_ms_first_token = 10
        self.latency_ms_total = 20
        self.prompt_hash = "sha256:" + ("0" * 64)
        self.event_types_seen: list[str] = []
        self.refusal_text: str | None = None
        self.response_id: str | None = None
        self.finish_reason: str | None = None

    def stream(self) -> Any:
        async def gen():
            yield self._text
        return gen()

    async def final_text(self) -> str:
        return self._text


@pytest.fixture
def orchestrator_factory(make_session_config, make_question):
    """Build (orchestrator, agent) with mocked Speaker/Judge/AttributePublisher.

    Returned ``agent`` is a MagicMock with ``agent.session.say``
    awaitable and ``agent.session.shutdown`` callable. The Speaker stream
    always returns the same handle; the Judge is wired up but should
    not be called by the tests in this file (which only exercise
    on_enter + on_user_turn_completed top-of-body timer init).
    """

    def _make() -> tuple[InterviewOrchestrator, Any]:
        cfg = make_session_config(
            questions=[
                make_question(
                    qid="q1", position=0, mandatory=True,
                    text="Walk me through your first response.",
                ),
            ],
            signals=["S1"],
        )

        speaker = MagicMock()
        speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("ok."))

        judge = MagicMock()
        judge.call = AsyncMock()  # not invoked by these tests

        room = MagicMock()
        room.local_participant.set_attributes = AsyncMock()
        publisher = AttributePublisher(room=room)

        fake_session = MagicMock()
        # Phase 9.4: orchestrator reads SpeechHandle.interrupted; default to
        # not-interrupted so the happy path runs through.
        fake_session.say = AsyncMock(return_value=MagicMock(interrupted=False))
        fake_session.shutdown = MagicMock()
        fake_agent = MagicMock()
        fake_agent.session = fake_session

        state_engine = StateEngine(session_config=cfg)

        orch = InterviewOrchestrator(
            session_config=cfg,
            tenant_settings=MagicMock(engine_agent_name=None),
            state_engine=state_engine,
            judge=judge,
            speaker=speaker,
            attr_publisher=publisher,
            event_collector=_collector(),
            correlation_id="c",
            config=OrchestratorConfig(),
            tenant_id="t",
        )
        return orch, fake_agent

    return _make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_enter_fires_intro_brief_then_first_question(
    orchestrator_factory,
):
    """on_enter emits Phase A (intro_brief) then Phase B (first question).

    Expected audit sequence (subset, in order):
      TURN_STARTED → SPEAKER_INPUT(intro_brief) → TURN_COMPLETED →
      TURN_STARTED → JUDGE_SYNTHETIC → SPEAKER_INPUT(deliver_first_question) →
      TURN_COMPLETED.
    """
    orch, agent = orchestrator_factory()
    await orch.on_enter(agent)

    kinds = [e.kind for e in orch._collector.events]
    # First event is the intro_brief TURN_STARTED.
    assert kinds[0] == "turn.started"

    speaker_inputs = [
        e for e in orch._collector.events if e.kind == "speaker.input"
    ]
    assert len(speaker_inputs) == 2, (
        f"Expected exactly 2 speaker.input events (intro_brief + "
        f"deliver_first_question); got {len(speaker_inputs)}."
    )
    intro_kind = speaker_inputs[0].payload["speaker_input"]["instruction_kind"]
    assert intro_kind == InstructionKind.intro_brief.value
    first_q_kind = speaker_inputs[1].payload["speaker_input"]["instruction_kind"]
    assert first_q_kind == InstructionKind.deliver_first_question.value

    # Sequence sanity: TURN_STARTED appears twice (one per phase) and
    # TURN_COMPLETED appears twice.
    assert kinds.count("turn.started") == 2
    assert kinds.count("turn.completed") == 2
    # JUDGE_SYNTHETIC fires once, in Phase B (session_start reason).
    judge_syn = [
        e for e in orch._collector.events
        if e.kind == "judge.synthetic"
    ]
    assert len(judge_syn) == 1
    assert judge_syn[0].payload["reason"] == "session_start"


@pytest.mark.asyncio
async def test_on_enter_does_not_start_lifecycle_timer(orchestrator_factory):
    """``_session_started_monotonic`` stays None through on_enter.

    The lifecycle timer pause is the load-bearing invariant: candidate's
    full ``duration_minutes`` budget covers actual interview time, not
    intro pre-roll.
    """
    orch, agent = orchestrator_factory()
    assert orch._session_started_monotonic is None
    await orch.on_enter(agent)
    assert orch._session_started_monotonic is None, (
        "on_enter must NOT start the lifecycle timer — that happens on "
        "the first candidate utterance in on_user_turn_completed."
    )
    # No session.timer_started event should have been emitted during intro.
    timer_events = [
        e for e in orch._collector.events
        if e.kind == "session.timer_started"
    ]
    assert len(timer_events) == 0


@pytest.mark.asyncio
async def test_first_candidate_utterance_starts_lifecycle_timer(
    orchestrator_factory,
):
    """on_user_turn_completed sets ``_session_started_monotonic`` and
    emits ``session.timer_started`` exactly once on the first utterance.
    """
    orch, agent = orchestrator_factory()
    await orch.on_enter(agent)

    # Pre-condition: timer is paused after on_enter.
    assert orch._session_started_monotonic is None

    # Wire the Judge so the body of on_user_turn_completed can drive a
    # full turn. The body invokes the State Engine, which is fine — we
    # only assert the top-of-body timer initialization here.
    from app.modules.interview_engine.models.judge import (
        AdvancePayload, CoverageQuality, CoverageTransition,
        JudgeOutput, NextAction, Observation, TurnMetadata,
    )
    orch._judge.call = AsyncMock(return_value=MagicMock(
        judge_output=JudgeOutput(
            reasoning="Test reasoning for the unit fixture.",
            observations=[
                Observation(
                    signal_value="S1", anchor_id=0,
                    evidence_quote="I have 8 years of experience.",
                    coverage_transition=CoverageTransition.none_to_sufficient,
                    quality=CoverageQuality.concrete,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q1"),
            turn_metadata=TurnMetadata(),
        ),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        model_used="gpt-test",
    ))

    new_message = MagicMock()
    new_message.text_content = "I have 8 years of experience."
    await orch.on_user_turn_completed(
        agent, turn_ctx=MagicMock(), new_message=new_message,
    )

    # Timer is now set.
    assert orch._session_started_monotonic is not None, (
        "First candidate utterance must start the lifecycle timer."
    )

    timer_events = [
        e for e in orch._collector.events
        if e.kind == "session.timer_started"
    ]
    assert len(timer_events) == 1, (
        f"Expected exactly one session.timer_started event; "
        f"got {len(timer_events)}."
    )
    # Payload carries a positive wall_ms timestamp.
    assert timer_events[0].payload["wall_ms"] > 0


@pytest.mark.asyncio
async def test_session_timer_started_fires_exactly_once(orchestrator_factory):
    """Multiple utterances do NOT re-fire ``session.timer_started``.

    The init block at the top of on_user_turn_completed is gated on
    ``_session_started_monotonic is None``; once set, it never re-fires.
    """
    orch, agent = orchestrator_factory()
    await orch.on_enter(agent)

    # Wire a minimal Judge — same as the prior test.
    from app.modules.interview_engine.models.judge import (
        AdvancePayload, CoverageQuality, CoverageTransition,
        JudgeOutput, NextAction, Observation, TurnMetadata,
    )
    orch._judge.call = AsyncMock(return_value=MagicMock(
        judge_output=JudgeOutput(
            reasoning="Test reasoning for the unit fixture.",
            observations=[
                Observation(
                    signal_value="S1", anchor_id=0,
                    evidence_quote="evidence quote",
                    coverage_transition=CoverageTransition.none_to_sufficient,
                    quality=CoverageQuality.concrete,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q1"),
            turn_metadata=TurnMetadata(),
        ),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        model_used="gpt-test",
    ))

    for utterance in ("first", "second", "third"):
        msg = MagicMock()
        msg.text_content = utterance
        await orch.on_user_turn_completed(
            agent, turn_ctx=MagicMock(), new_message=msg,
        )

    timer_events = [
        e for e in orch._collector.events
        if e.kind == "session.timer_started"
    ]
    assert len(timer_events) == 1, (
        f"session.timer_started must fire exactly once per session; "
        f"got {len(timer_events)} after 3 candidate utterances."
    )
