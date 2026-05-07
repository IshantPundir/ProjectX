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
