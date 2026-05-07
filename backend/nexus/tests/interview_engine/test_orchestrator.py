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
        # Per-call prompt hash exposed by the real SpeakerStreamHandle
        # post-Task 11. The orchestrator reads it for the SPEAKER_CALL
        # audit event; without it AttributeError trips the speaker-error
        # recovery path.
        self.prompt_hash = "sha256:" + ("0" * 64)

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
    # No StopResponse expected: the orchestrator returns normally so the
    # framework's auto-append → conversation_item_added → chat_history
    # capture chain stays alive. Duplicate-reply suppression is handled
    # by StructuredInterviewAgent.llm_node yielding nothing.
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
    fake_session.shutdown = MagicMock()
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
    msg = ChatMessage(role="user", content=["my answer"])
    # No StopResponse expected — see test_on_user_turn_completed_happy_path.
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

    from livekit.agents.llm import ChatMessage
    msg = ChatMessage(role="user", content=["I have JQL experience."])
    # No StopResponse expected — see test_on_user_turn_completed_happy_path.
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
async def test_llm_node_yields_nothing():
    """Regression for Bug 4 (proper fix): llm_node must yield nothing.

    LiveKit's chat_history capture is driven by ``conversation_item_added``
    events, which fire when the framework auto-appends ``new_message`` to
    ``chat_ctx`` AFTER ``on_user_turn_completed`` returns normally. The
    orchestrator now returns normally (no StopResponse), so the auto-append
    fires and user messages land in chat_history.

    But returning normally also means the framework would otherwise call
    its default LLM node and stream a duplicate reply on top of the
    orchestrator's session.say(). The fix is to override ``llm_node`` to
    yield nothing — that suppresses the duplicate reply while keeping the
    chat-context auto-append path active.

    This regression check asserts that ``llm_node`` produces zero chunks.
    """
    from app.modules.interview_engine.agent import StructuredInterviewAgent

    orch = MagicMock()
    agent = StructuredInterviewAgent(
        orchestrator=orch,
        instructions="(test)",
    )
    chunks = []
    async for chunk in agent.llm_node(
        chat_ctx=MagicMock(), tools=[], model_settings=MagicMock(),
    ):
        chunks.append(chunk)
    assert chunks == [], "llm_node must yield no chunks"


@pytest.mark.asyncio
async def test_on_user_turn_completed_returns_normally_for_chat_history():
    """Regression for Bug 4 (proper fix): the agent must NOT raise StopResponse.

    The framework's auto-append of ``new_message`` to ``chat_ctx`` only
    fires when ``on_user_turn_completed`` returns normally. The previous
    implementation raised StopResponse, which short-circuited that path
    and silently dropped every candidate utterance from LiveKit's
    ``chat_history.json``. This test asserts the agent simply delegates
    to the orchestrator and returns.
    """
    from app.modules.interview_engine.agent import StructuredInterviewAgent
    from livekit.agents.llm import ChatContext, ChatMessage

    mock_orch = MagicMock()
    mock_orch.on_user_turn_completed = AsyncMock()

    agent = StructuredInterviewAgent(
        orchestrator=mock_orch,
        instructions="(see Speaker prompt — agent has no top-level instructions)",
    )

    turn_ctx = ChatContext()
    new_message = ChatMessage(role="user", content=["I have JQL experience."])

    # Must return normally (no StopResponse) so the framework's auto-append
    # fires and conversation_item_added populates chat_history.
    result = await agent.on_user_turn_completed(turn_ctx, new_message)
    assert result is None
    mock_orch.on_user_turn_completed.assert_awaited_once_with(
        agent, turn_ctx, new_message,
    )


@pytest.mark.asyncio
async def test_post_close_turn_plays_canned_message_and_skips_judge(
    make_session_config, make_question,
):
    """Once lifecycle is closing, any candidate input gets the canned
    terminal message and Judge is NOT called.

    Regression for the "agent keeps talking after polite_close" bug.
    """
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("hello"))
    judge = MagicMock()
    judge.call = AsyncMock()  # should NOT be called

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_session.shutdown = MagicMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    state_engine = StateEngine(session_config=cfg)
    # Manually transition lifecycle to closing.
    state_engine._lifecycle.transition_to_active()
    state_engine._lifecycle.transition_to_closing()
    from app.modules.interview_engine.state.lifecycle import SessionOutcome
    state_engine._lifecycle.set_last_outcome(SessionOutcome.knockout_closed)

    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )

    from livekit.agents.llm import ChatMessage
    msg = ChatMessage(role="user", content=["Are we still going?"])
    await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    # Judge was NOT called.
    judge.call.assert_not_awaited()
    # Speaker was NOT called (Speaker LLM is bypassed in the hard-stop path).
    speaker.stream.assert_not_awaited()
    # session.say WAS called with the canned terminal message.
    fake_session.say.assert_awaited_once()
    say_args, say_kwargs = fake_session.say.call_args
    msg_arg = say_args[0] if say_args else say_kwargs.get("text", "")
    assert "this session has ended" in msg_arg.lower()
    # Shutdown was scheduled.
    fake_session.shutdown.assert_called_once()
    # Audit event recorded.
    from app.modules.interview_engine.event_kinds import SESSION_TERMINAL_DELIVERED
    assert SESSION_TERMINAL_DELIVERED in [e.kind for e in collector.events]


@pytest.mark.asyncio
async def test_normal_turn_then_knockout_triggers_shutdown(
    make_session_config, make_question, make_judge_output,
):
    """A normal turn that causes a knockout_policy_override should trigger
    session.shutdown after the polite_close speaker output.

    Regression for the second half of the same bug — when a knockout
    legitimately closes the session via Judge → State Engine, the
    LiveKit session must shut down so the candidate's tab disconnects.
    """
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("polite close text"))

    from app.modules.interview_engine.models.judge import (
        Observation, CoverageTransition, AcknowledgeNoExperiencePayload,
        NextAction, TurnMetadata,
    )
    from app.modules.interview_engine.models.judge import JudgeOutput
    judge = MagicMock()
    judge.call = AsyncMock(return_value=MagicMock(
        judge_output=JudgeOutput(
            thought="t",
            observations=[Observation(
                signal_value="S_KO", anchor_id=-1,
                evidence_quote="never used",
                coverage_transition=CoverageTransition.none_to_failed,
            )],
            candidate_claims=[],
            next_action=NextAction.acknowledge_no_experience,
            next_action_payload=AcknowledgeNoExperiencePayload(
                failed_signal_value="S_KO"),
            turn_metadata=TurnMetadata(),
        ),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1}, model_used="gpt-test",
    ))

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_session.shutdown = MagicMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    from app.modules.interview_engine.state.engine import StateEngineConfig
    state_engine = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )
    await orch.on_enter(fake_agent)

    from livekit.agents.llm import ChatMessage
    msg = ChatMessage(role="user", content=["I have no experience."])
    await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    # Knockout was recorded → policy override → shutdown scheduled.
    fake_session.shutdown.assert_called_once()
    # Lifecycle is closing.
    assert state_engine.lifecycle_snapshot().state.value == "closing"


@pytest.mark.asyncio
async def test_time_remaining_seconds_decreases_each_turn(
    make_session_config, make_question, make_judge_output, monkeypatch,
):
    """time_remaining_seconds must reflect actual elapsed wall-clock,
    not stay stuck at the initial budget.

    Regression: prior to the orchestrator calling set_time_elapsed each
    turn, time_elapsed_seconds stayed at 0 — so time_remaining_seconds
    was always equal to the full budget and the frontend timer never
    counted down. We assert the published attribute moves below the
    initial budget after a turn.
    """
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", text="What is your first question response?"),
            make_question(qid="q2", text="What is your second question response?"),
        ],
        signals=["S1"],
        duration_minutes=15,
    )
    initial_budget = cfg.stage.duration_minutes * 60  # 900s

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
    fake_session.shutdown = MagicMock()
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

    # Patch time.monotonic so on_enter establishes a t0 and the next
    # call returns t0 + 5s — guaranteeing elapsed_ms > 0 without sleep.
    import time as time_module
    counter = {"calls": 0}
    real_monotonic = time_module.monotonic
    base = real_monotonic()

    def _fake_monotonic():
        counter["calls"] += 1
        # First call (in on_enter) → base. Subsequent calls advance.
        return base + (counter["calls"] - 1) * 5.0

    monkeypatch.setattr(
        "app.modules.interview_engine.orchestrator.time.monotonic",
        _fake_monotonic,
    )

    await orch.on_enter(fake_agent)

    from livekit.agents.llm import ChatMessage
    msg = ChatMessage(role="user", content=["I have JQL experience."])
    await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    # Pull the most recent time_remaining publish — it must be < initial budget.
    published = []
    for a in room.local_participant.set_attributes.await_args_list:
        published.append(a.args[0])
    time_remaining_values = [
        int(p[ATTR_TIME_REMAINING_SECONDS])
        for p in published
        if ATTR_TIME_REMAINING_SECONDS in p
    ]
    assert time_remaining_values, "time_remaining_seconds was never published"
    assert min(time_remaining_values) < initial_budget, (
        f"time_remaining never decreased below {initial_budget}; "
        f"saw {time_remaining_values}"
    )


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


# ---------------------------------------------------------------------------
# resolve_close_outcome — controller_end_outcome propagation regression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_close_outcome_returns_lifecycle_last_outcome(
    make_session_config, make_question,
):
    """When ``state_engine.lifecycle.last_outcome`` is set (e.g. by the
    knockout policy override path), :meth:`InterviewOrchestrator.resolve_close_outcome`
    must return that value regardless of the LiveKit-reported close
    reason.

    Regression: prior to this fix the close handler read
    ``agent._end_outcome`` (a local mirror nothing populated for
    structured-close paths), so the ``session.close`` audit event and
    the ``session_outcome`` participant attribute both serialized as
    ``null`` even though lifecycle had an outcome.
    """
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
    )
    state_engine = StateEngine(session_config=cfg)
    state_engine._lifecycle.transition_to_active()
    state_engine._lifecycle.transition_to_closing()
    from app.modules.interview_engine.state.lifecycle import SessionOutcome
    state_engine._lifecycle.set_last_outcome(SessionOutcome.knockout_closed)

    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=MagicMock(), speaker=MagicMock(),
        attr_publisher=AttributePublisher(room=MagicMock()),
        event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )

    # LiveKit reports user_initiated (because the agent called shutdown),
    # but the structured outcome must win.
    assert orch.resolve_close_outcome(close_reason="user_initiated") == "knockout_closed"
    # Even participant_disconnected (candidate closed their tab during
    # the polite-close drain) must NOT downgrade to candidate_disconnected.
    assert orch.resolve_close_outcome(close_reason="participant_disconnected") == "knockout_closed"


@pytest.mark.asyncio
async def test_resolve_close_outcome_falls_back_to_livekit_reason(
    make_session_config, make_question,
):
    """When lifecycle.last_outcome is None, the resolver maps the
    LiveKit-reported close reason to a SessionOutcome string."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    # No transitions, no outcome set — pristine pre_start lifecycle.
    assert state_engine.lifecycle_snapshot().last_outcome is None

    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=MagicMock(), speaker=MagicMock(),
        attr_publisher=AttributePublisher(room=MagicMock()),
        event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )

    assert orch.resolve_close_outcome(close_reason="participant_disconnected") == "candidate_disconnected"
    assert orch.resolve_close_outcome(close_reason="user_initiated") == "completed"
    assert orch.resolve_close_outcome(close_reason="error") == "error"
    # Unknown / None default to "error" for safety.
    assert orch.resolve_close_outcome(close_reason=None) == "error"
    assert orch.resolve_close_outcome(close_reason="some_future_reason") == "error"


@pytest.mark.asyncio
async def test_session_close_outcome_reflects_lifecycle_last_outcome(
    make_session_config, make_question,
):
    """End-to-end regression: when a knockout drives lifecycle to
    closing with last_outcome=knockout_closed, the orchestrator's
    resolver returns "knockout_closed" — which is what the agent.py
    close handler will write to the session.close audit payload's
    ``controller_end_outcome`` field and the ``session_outcome``
    participant attribute.
    """
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    speaker = MagicMock()
    speaker.stream = AsyncMock(return_value=_FakeSpeakerHandle("polite close text"))

    from app.modules.interview_engine.models.judge import (
        AcknowledgeNoExperiencePayload,
        CoverageTransition,
        JudgeOutput,
        NextAction,
        Observation,
        TurnMetadata,
    )
    judge = MagicMock()
    judge.call = AsyncMock(return_value=MagicMock(
        judge_output=JudgeOutput(
            thought="t",
            observations=[Observation(
                signal_value="S_KO", anchor_id=-1,
                evidence_quote="never used",
                coverage_transition=CoverageTransition.none_to_failed,
            )],
            candidate_claims=[],
            next_action=NextAction.acknowledge_no_experience,
            next_action_payload=AcknowledgeNoExperiencePayload(
                failed_signal_value="S_KO"),
            turn_metadata=TurnMetadata(),
        ),
        is_fallback=False, fallback_reason=None,
        original_failure_context=None, latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1}, model_used="gpt-test",
    ))

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)
    fake_session = MagicMock()
    fake_session.say = AsyncMock()
    fake_session.shutdown = MagicMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    from app.modules.interview_engine.state.engine import StateEngineConfig
    state_engine = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    collector = _collector()
    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=judge, speaker=speaker,
        attr_publisher=pub, event_collector=collector,
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )
    await orch.on_enter(fake_agent)

    # Trigger knockout via candidate utterance.
    from livekit.agents.llm import ChatMessage
    msg = ChatMessage(role="user", content=["I have no experience."])
    await orch.on_user_turn_completed(fake_agent, MagicMock(), msg)

    # Lifecycle.last_outcome must be knockout_closed.
    snap = state_engine.lifecycle_snapshot()
    assert snap.last_outcome is not None
    assert snap.last_outcome.value == "knockout_closed"

    # Resolver returns knockout_closed regardless of the LiveKit reason.
    # This is the value agent.py will write to:
    #   1. session.close audit payload's controller_end_outcome field
    #   2. participant attribute session_outcome
    assert orch.resolve_close_outcome(close_reason="user_initiated") == "knockout_closed"
    assert orch.resolve_close_outcome(close_reason="participant_disconnected") == "knockout_closed"


@pytest.mark.asyncio
async def test_resolve_close_outcome_error_overrides_lifecycle(
    make_session_config, make_question,
):
    """An ERROR close reason wins over any structured lifecycle outcome.

    Pipeline-level errors (transport / plugin failure) must serialize as
    "error" in the audit envelope so post-incident triage can find them
    by outcome alone, even if the State Engine had already recorded
    e.g. ``knockout_closed`` before the error fired.
    """
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
    )
    state_engine = StateEngine(session_config=cfg)
    state_engine._lifecycle.transition_to_active()
    state_engine._lifecycle.transition_to_closing()
    from app.modules.interview_engine.state.lifecycle import SessionOutcome
    state_engine._lifecycle.set_last_outcome(SessionOutcome.knockout_closed)

    orch = InterviewOrchestrator(
        session_config=cfg, tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine, judge=MagicMock(), speaker=MagicMock(),
        attr_publisher=AttributePublisher(room=MagicMock()),
        event_collector=_collector(),
        correlation_id="c", config=OrchestratorConfig(),
        tenant_id="t",
    )

    assert orch.resolve_close_outcome(close_reason="error") == "error"


# ---------------------------------------------------------------------------
# Bug D — empty Speaker output fallback
# ---------------------------------------------------------------------------

def _empty_async_iter():
    """Empty async generator. Used to mock Speaker handle.stream() when
    the Speaker LLM streamed nothing audible."""
    async def _gen():
        return
        yield  # pragma: no cover — unreachable, makes this an async generator
    return _gen()


def _build_speaker_input(
    *, instruction_kind: str, bank_text: str | None,
) -> SpeakerInput:
    """Build a minimal SpeakerInput for fallback tests."""
    return SpeakerInput(
        instruction_kind=InstructionKind(instruction_kind),
        bank_text=bank_text,
        last_candidate_utterance=None,
        recent_turns=[],
        claims_pool_snapshot=[],
        persona_name="Sam",
        candidate_name="Alice",
    )


def _build_orchestrator_with_mocked_deps(
    make_session_config, make_question,
) -> InterviewOrchestrator:
    """Instantiate an orchestrator with real session_config + state_engine,
    mocked Judge / Speaker, real EventCollector. The test bodies override
    ``orch._speaker.stream`` to control the handle returned per call.
    """
    cfg = make_session_config(
        questions=[make_question(
            qid="q1", text="What is your first question response?",
        )],
        signals=["S1"],
    )
    state_engine = StateEngine(session_config=cfg)
    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock()
    judge_service = MagicMock()
    judge_service.call = MagicMock()
    pub = AttributePublisher(room=MagicMock())
    return InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge_service,
        speaker=speaker_service,
        attr_publisher=pub,
        event_collector=_collector(),
        correlation_id="c",
        config=OrchestratorConfig(),
        tenant_id="t",
    )


@pytest.mark.asyncio
async def test_empty_speaker_output_triggers_fallback(
    make_session_config, make_question,
):
    """Bug D — Speaker LLM occasionally streams empty text. Orchestrator
    must play a deterministic fallback so the candidate doesn't hear
    silence, and emit speaker.output.empty in the audit envelope."""
    from app.modules.interview_engine.event_kinds import SPEAKER_OUTPUT_EMPTY

    orch = _build_orchestrator_with_mocked_deps(make_session_config, make_question)
    speaker_input = _build_speaker_input(
        instruction_kind="deliver_question",
        bank_text="Walk me through your Jira workflow.",
    )

    handle = MagicMock()
    handle.stream.return_value = _empty_async_iter()
    handle.final_text = AsyncMock(return_value="")
    handle.latency_ms_first_token = 0
    handle.latency_ms_total = 0
    handle.usage = None
    orch._speaker.stream = AsyncMock(return_value=handle)

    agent = MagicMock()
    agent.session.say = AsyncMock()

    final_text = await orch._stream_speaker_and_say(
        agent=agent, turn_id="t1", speaker_input=speaker_input,
    )

    # Fallback content includes a restate of bank_text.
    assert "Walk me through your Jira workflow." in final_text
    # session.say was called with the fallback text.
    agent.session.say.assert_awaited()
    args, kwargs = agent.session.say.call_args
    assert args[0] == final_text
    # Audit event was emitted.
    audit_kinds = [e.kind for e in orch._collector.events]
    assert SPEAKER_OUTPUT_EMPTY in audit_kinds
    # The successful-path SPEAKER_CALL / SPEAKER_OUTPUT events MUST NOT
    # be emitted on the empty-output path — those describe a successful
    # LLM call.
    assert SPEAKER_CALL not in audit_kinds
    assert SPEAKER_OUTPUT not in audit_kinds


@pytest.mark.asyncio
async def test_empty_speaker_output_fallback_without_bank_text(
    make_session_config, make_question,
):
    """No bank_text (e.g., the past redirect_* kinds) → generic fallback.

    Whitespace-only output ("   \\n") also counts as empty — the guard
    uses .strip() to detect it.
    """
    orch = _build_orchestrator_with_mocked_deps(make_session_config, make_question)
    speaker_input = _build_speaker_input(
        instruction_kind="redirect", bank_text=None,
    )

    handle = MagicMock()
    handle.stream.return_value = _empty_async_iter()
    handle.final_text = AsyncMock(return_value="   \n")  # whitespace counts as empty
    handle.latency_ms_first_token = 0
    handle.latency_ms_total = 0
    handle.usage = None
    orch._speaker.stream = AsyncMock(return_value=handle)

    agent = MagicMock()
    agent.session.say = AsyncMock()

    final_text = await orch._stream_speaker_and_say(
        agent=agent, turn_id="t2", speaker_input=speaker_input,
    )
    assert final_text == "Could you take it from the top?"
