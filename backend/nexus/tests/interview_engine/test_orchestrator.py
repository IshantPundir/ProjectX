import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import (
    ATTR_CURRENT_QUESTION_INDEX, ATTR_TIME_REMAINING_SECONDS,
    ATTR_TOTAL_QUESTIONS, AttributePublisher,
)
from app.modules.interview_engine.models.speaker import (
    InstructionKind, SpeakerInput,
)
from app.modules.interview_engine.state.engine import StateEngine
from app.modules.interview_engine.event_kinds import (
    JUDGE_SYNTHETIC, SPEAKER_CALL, SPEAKER_OUTPUT, TURN_COMPLETED, TURN_STARTED,
)


def _collector() -> EventCollector:
    return EventCollector(
        session_id="s", tenant_id="t", correlation_id="c",
        controller_prompt_hash="sha256:ctrl",
        model_versions={"judge": "m1", "speaker": "m1"},
        redaction_mode="metadata",
        task_prompt_hashes={"judge": "sha256:j", "speaker": "sha256:s"},
    )


class _FakeSpeakerHandle:
    def __init__(self, text: str):
        self._text = text
        self._final = text
        self.usage = {"prompt_tokens": 5, "completion_tokens": 5}
        self.latency_ms_first_token = 100
        self.latency_ms_total = 250

    def stream(self):
        async def gen():
            yield self._text
        return gen()

    async def final_text(self):
        return self._final


@pytest.mark.asyncio
async def test_on_enter_delivers_first_question(make_session_config, make_question):
    cfg = make_session_config(
        questions=[
            make_question(
                qid="q1", position=0, mandatory=True,
                text="What is your first question response?",
                follow_ups=["fu0"],
            ),
            make_question(
                qid="q2", position=1, mandatory=True,
                text="What is your second question response?",
                follow_ups=[],
            ),
        ],
        signals=["S1"],
    )

    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock(return_value=_FakeSpeakerHandle("Hello — first Q rephrased."))

    judge_service = MagicMock()  # not invoked on session start

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)

    fake_session = MagicMock()
    fake_session.say = AsyncMock()

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
        config=OrchestratorConfig(),
        tenant_id="t",
    )

    await orch.on_enter(fake_agent)

    # Assert speaker called once with deliver_first_question.
    speaker_service.stream.assert_awaited_once()
    args, kwargs = speaker_service.stream.call_args
    sinput: SpeakerInput = kwargs["speaker_input"]
    assert sinput.instruction_kind == InstructionKind.deliver_first_question
    assert sinput.bank_text == "What is your first question response?"

    # Assert session.say was called.
    fake_session.say.assert_awaited_once()

    # Assert frontend attributes pushed.
    push_args = room.local_participant.set_attributes.await_args_list
    pushed = {}
    for a in push_args:
        pushed.update(a.args[0])
    assert pushed[ATTR_TOTAL_QUESTIONS] == "2"
    assert pushed[ATTR_CURRENT_QUESTION_INDEX] == "0"
    assert ATTR_TIME_REMAINING_SECONDS in pushed

    # Assert audit envelope contains the expected events.
    kinds = [e.kind for e in collector.events]
    assert JUDGE_SYNTHETIC in kinds
    assert SPEAKER_CALL in kinds
    assert SPEAKER_OUTPUT in kinds
    assert TURN_STARTED in kinds
    assert TURN_COMPLETED in kinds


@pytest.mark.asyncio
async def test_on_user_turn_completed_happy_path(make_session_config, make_question, make_judge_output):
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True,
                          text="What is your first question response?", follow_ups=["fu0"]),
            make_question(qid="q2", position=1, mandatory=True,
                          text="What is your second question response?", follow_ups=[]),
        ],
        signals=["S1"],
    )

    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock(return_value=_FakeSpeakerHandle("rephrased."))

    judge_service = MagicMock()
    judge_service.call = AsyncMock(return_value=MagicMock(
        judge_output=make_judge_output(
            action=__import__(
                "app.modules.interview_engine.models.judge", fromlist=["NextAction"],
            ).NextAction.advance,
            target="q2",
        ),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=120, usage={"prompt_tokens": 8, "completion_tokens": 4},
        model_used="gpt-test",
    ))

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)

    fake_session = MagicMock()
    fake_session.say = AsyncMock()

    fake_agent = MagicMock()
    fake_agent.session = fake_session

    from app.modules.interview_engine.event_kinds import JUDGE_CALL
    from livekit.agents.llm import ChatMessage
    from livekit.agents.llm import StopResponse

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
        config=OrchestratorConfig(),
        tenant_id="t",
    )
    await orch.on_enter(fake_agent)
    speaker_service.stream.reset_mock()

    msg = ChatMessage(role="user", content=["I have 5 years of JQL experience."])
    with pytest.raises(StopResponse):
        await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    judge_service.call.assert_awaited_once()
    speaker_service.stream.assert_awaited_once()
    kinds = [e.kind for e in collector.events]
    assert JUDGE_CALL in kinds

    # Frontend index moved to q2 (index 1).
    pushed = {}
    for a in room.local_participant.set_attributes.await_args_list:
        pushed.update(a.args[0])
    assert pushed.get(ATTR_CURRENT_QUESTION_INDEX) == "1"


@pytest.mark.asyncio
async def test_speaker_error_triggers_canned_recovery(make_session_config, make_question, make_judge_output):
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )

    raising_speaker = MagicMock()
    call_count = {"n": 0}

    async def _stream(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeSpeakerHandle("first question")
        raise RuntimeError("simulated streaming failure")

    raising_speaker.stream = AsyncMock(side_effect=_stream)

    judge_service = MagicMock()
    judge_service.call = AsyncMock(return_value=MagicMock(
        judge_output=make_judge_output(),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1}, model_used="gpt-test",
    ))

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge_service, speaker=raising_speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )
    await orch.on_enter(fake_agent)

    from livekit.agents.llm import ChatMessage
    from livekit.agents.llm import StopResponse
    msg = ChatMessage(role="user", content=["my answer"])
    with pytest.raises(StopResponse):
        await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    # Recovery line was sent — search for "apologize" or similar in the say calls.
    say_calls_text = " ".join(str(c) for c in fake_session.say.await_args_list)
    assert "apologize" in say_calls_text.lower() or "could you say that again" in say_calls_text.lower()

    from app.modules.interview_engine.event_kinds import SPEAKER_ERROR
    assert SPEAKER_ERROR in [e.kind for e in collector.events]


@pytest.mark.asyncio
async def test_on_close_returns_session_result_with_snapshots(make_session_config, make_question, make_judge_output):
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )
    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock(return_value=_FakeSpeakerHandle("hello"))
    judge_service = MagicMock()

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge_service, speaker=speaker_service,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )
    await orch.on_enter(fake_agent)

    result = await orch.on_close(fake_agent, audio_tuning_summary={"hint": "x"})
    assert result.session_id == cfg.session_id
    assert result.signal_ledger.next_seq >= 1
    assert result.audio_tuning_summary == {"hint": "x"}
    assert result.questions_skipped == 0
    assert result.questions_asked >= 1
    assert isinstance(result.audit_envelope_ref, (str, type(None)))


@pytest.mark.asyncio
async def test_judge_input_carries_recent_turns(make_session_config, make_question, make_judge_output):
    """Regression for I1: recent_turns must be populated from State Engine transcript, not [].

    After on_enter delivers turn 0 + on_user_turn_completed delivers turn 1, the next
    turn's Judge call should see at least 2 transcript entries in recent_turns.
    """
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", text="What is your first question response?", follow_ups=["fu0"]),
            make_question(qid="q2", text="What is your second question response?", follow_ups=[]),
        ],
        signals=["S1"],
    )
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("ok."))
    judge = MagicMock()
    judge.call = AsyncMock(return_value=MagicMock(
        judge_output=make_judge_output(target="q2"),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1}, model_used="gpt-test",
    ))

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )
    await orch.on_enter(fake_agent)

    from livekit.agents.llm import ChatMessage, StopResponse
    msg = ChatMessage(role="user", content=["I have JQL experience."])
    with pytest.raises(StopResponse):
        await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    # First Judge call's input_payload — recent_turns must be non-empty
    # (it should contain the prior agent utterance + the candidate response).
    judge_call_args = judge.call.await_args.kwargs["input_payload"]
    assert len(judge_call_args.recent_turns) >= 1, "Judge input must include recent transcript"


@pytest.mark.asyncio
async def test_on_enter_robust_to_publish_failure(make_session_config, make_question):
    """Regression for Bug 2: if attribute publish fails for any reason, on_enter
    must still deliver the first question (the speaker call must run).

    The primary fix is in agent.py — the entrypoint now awaits ctx.connect()
    + ctx.wait_for_participant() before constructing the orchestrator and
    starting the session, so on_enter no longer sees a pre-connect room.

    This test exercises the belt-and-suspenders layer in
    AttributePublisher.publish: if set_attributes raises (race, transient
    network, etc.), the publisher swallows + logs and the orchestrator
    continues on to deliver the first question via session.say.
    """
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )

    # Speaker still works.
    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock(return_value=_FakeSpeakerHandle("Hello — first question."))

    # Attribute publisher raises on first call (simulating the original
    # "cannot access local participant before connecting" race) and
    # succeeds on subsequent calls.
    room = MagicMock()
    call_count = {"n": 0}

    async def _set_attrs(attrs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("cannot access local participant before connecting")

    room.local_participant.set_attributes = AsyncMock(side_effect=_set_attrs)
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=MagicMock(), speaker=speaker_service,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )
    # Should not raise — first attribute publish failure must be tolerated.
    await orch.on_enter(fake_agent)

    # Speaker call SHOULD have run regardless.
    speaker_service.stream.assert_awaited_once()
    fake_session.say.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_uses_tenant_id_not_session_id(make_session_config, make_question):
    """Regression for I2: LLM tracing must receive tenant_id, not session_id."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("ok."))
    judge = MagicMock()

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="my-tenant-uuid-not-the-session-id",
    )
    await orch.on_enter(fake_agent)

    # Speaker.stream call must carry tenant_id="my-tenant-uuid-..."
    call_kwargs = speaker.stream.await_args.kwargs
    assert call_kwargs["tenant_id"] == "my-tenant-uuid-not-the-session-id"
    assert call_kwargs["tenant_id"] != cfg.session_id
