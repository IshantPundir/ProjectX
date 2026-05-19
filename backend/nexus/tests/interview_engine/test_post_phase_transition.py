"""Verifies the orchestrator sets is_post_phase_transition on kind boundaries.

The detection runs after process_judge_output returns the SpeakerInput
for an advance/deliver_first_question turn. We test three cases:
- first question (flag stays False)
- within-kind advance (False)
- behavioral_star → technical_depth (True)

Spec: docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §4
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import AttributePublisher
from app.modules.interview_engine.models.judge import (
    AdvancePayload, CoverageQuality, CoverageTransition,
    JudgeOutput, NextAction, Observation, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig,
)
from app.modules.interview_engine.state.engine import StateEngine


# ---------------------------------------------------------------------------
# Minimal scaffolding mirroring test_intro_brief_turn.py (no shared fixture).
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
    """Minimal stand-in for SpeakerStreamHandle."""

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


def _build_orchestrator(
    cfg: Any, *, judge_output_for_advance: JudgeOutput | None = None,
) -> tuple[InterviewOrchestrator, Any]:
    """Construct an orchestrator + agent mock from a SessionConfig.

    Mirrors the helpers in test_intro_brief_turn.py.
    """
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("ok."))

    judge = MagicMock()
    if judge_output_for_advance is not None:
        judge.call = AsyncMock(return_value=MagicMock(
            judge_output=judge_output_for_advance,
            is_fallback=False, fallback_reason=None,
            original_failure_context=None, latency_ms=10,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            model_used="gpt-test",
        ))
    else:
        judge.call = AsyncMock()

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    publisher = AttributePublisher(room=room)

    fake_session = MagicMock()
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


def _make_advance_judge_output(target_qid: str) -> JudgeOutput:
    """Build a JudgeOutput that advances the queue to ``target_qid``."""
    return JudgeOutput(
        reasoning="Test reasoning for the unit fixture.",
        observations=[
            Observation(
                signal_value="S1", anchor_id=0,
                evidence_quote="I have a concrete example here from my work.",
                coverage_transition=CoverageTransition.none_to_sufficient,
                quality=CoverageQuality.concrete,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id=target_qid),
        turn_metadata=TurnMetadata(),
    )


# ---------------------------------------------------------------------------
# Factories per scenario
# ---------------------------------------------------------------------------


@pytest.fixture
def orchestrator_factory(make_session_config, make_question):
    """Legacy single-kind factory: 1 technical_depth question.

    Used for the first-question test where the flag should stay False
    because there is no prior kind to differ from.
    """

    def _make() -> tuple[InterviewOrchestrator, Any]:
        cfg = make_session_config(
            questions=[
                make_question(
                    qid="q1", position=0, mandatory=True,
                    text="Walk me through your first response.",
                    question_kind="technical_depth",
                ),
            ],
            signals=["S1"],
        )
        return _build_orchestrator(cfg)

    return _make


@pytest.fixture
def orchestrator_factory_two_behavioral(make_session_config, make_question):
    """Factory with 2 behavioral_star + 1 technical_depth questions.

    Advancing from Q1 (behavioral) to Q2 (behavioral) is a within-kind
    advance — the flag must stay False.
    """

    def _make() -> tuple[InterviewOrchestrator, Any]:
        cfg = make_session_config(
            questions=[
                make_question(
                    qid="q1", position=0, mandatory=True,
                    text="Tell me about a time you led a difficult migration.",
                    question_kind="behavioral_star",
                ),
                make_question(
                    qid="q2", position=1, mandatory=True,
                    text="Tell me about a time you mentored a junior engineer.",
                    question_kind="behavioral_star",
                ),
                make_question(
                    qid="q3", position=2, mandatory=True,
                    text="Walk me through how you debug a flaky integration test.",
                    question_kind="technical_depth",
                ),
            ],
            signals=["S1"],
        )
        return _build_orchestrator(
            cfg,
            judge_output_for_advance=_make_advance_judge_output("q2"),
        )

    return _make


@pytest.fixture
def orchestrator_factory_one_behavioral_one_technical(
    make_session_config, make_question,
):
    """Factory with 1 behavioral_star + 1 technical_depth question.

    Advancing from Q1 (behavioral) to Q2 (technical) crosses the
    behavioral_star → technical_depth boundary — the flag must fire.
    """

    def _make() -> tuple[InterviewOrchestrator, Any]:
        cfg = make_session_config(
            questions=[
                make_question(
                    qid="q1", position=0, mandatory=True,
                    text="Tell me about a time you led a difficult migration.",
                    question_kind="behavioral_star",
                ),
                make_question(
                    qid="q2", position=1, mandatory=True,
                    text="Walk me through how you debug a flaky integration test.",
                    question_kind="technical_depth",
                ),
            ],
            signals=["S1"],
        )
        return _build_orchestrator(
            cfg,
            judge_output_for_advance=_make_advance_judge_output("q2"),
        )

    return _make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_question_does_not_set_post_phase_flag(orchestrator_factory):
    """First question delivery: _prev_active_question_kind is None, flag stays False."""
    orch, agent = orchestrator_factory()
    await orch.on_enter(agent)

    speaker_events = [
        e for e in orch._collector.events
        if e.kind == "speaker.input"
        and e.payload["speaker_input"]["instruction_kind"]
        in (
            InstructionKind.deliver_first_question.value,
            InstructionKind.deliver_question.value,
        )
    ]
    # First emitted deliver_* is the first question
    assert len(speaker_events) >= 1
    flag = speaker_events[0].payload["speaker_input"].get(
        "is_post_phase_transition", False,
    )
    assert flag is False


@pytest.mark.asyncio
async def test_within_kind_advance_does_not_set_post_phase_flag(
    orchestrator_factory_two_behavioral,
):
    """Advance from behavioral_star Q1 to behavioral_star Q2: flag stays False."""
    orch, agent = orchestrator_factory_two_behavioral()
    await orch.on_enter(agent)

    msg = MagicMock()
    msg.text_content = "Eight years on MuleSoft at Workato."
    await orch.on_user_turn_completed(
        agent, turn_ctx=MagicMock(), new_message=msg,
    )

    speaker_events = [
        e for e in orch._collector.events
        if e.kind == "speaker.input"
        and e.payload["speaker_input"]["instruction_kind"]
        in (
            InstructionKind.deliver_first_question.value,
            InstructionKind.deliver_question.value,
        )
    ]
    assert len(speaker_events) >= 2
    flag = speaker_events[1].payload["speaker_input"].get(
        "is_post_phase_transition", False,
    )
    assert flag is False


@pytest.mark.asyncio
async def test_behavioral_to_technical_sets_post_phase_flag(
    orchestrator_factory_one_behavioral_one_technical,
):
    """Advance from behavioral_star to technical_depth: flag is True."""
    orch, agent = orchestrator_factory_one_behavioral_one_technical()
    await orch.on_enter(agent)

    msg = MagicMock()
    msg.text_content = "Eight years on MuleSoft, mostly at Workato."
    await orch.on_user_turn_completed(
        agent, turn_ctx=MagicMock(), new_message=msg,
    )

    speaker_events = [
        e for e in orch._collector.events
        if e.kind == "speaker.input"
        and e.payload["speaker_input"]["instruction_kind"]
        == InstructionKind.deliver_question.value
    ]
    assert len(speaker_events) >= 1
    flag = speaker_events[0].payload["speaker_input"].get(
        "is_post_phase_transition", False,
    )
    assert flag is True
