from __future__ import annotations

from app.modules.interview_engine.directive import DirectiveAct
from app.modules.interview_engine.mouth.input_builder import question_id_for_agent_line


def test_question_bearing_acts_carry_active_question_id():
    for act in (DirectiveAct.ASK, DirectiveAct.PROBE, DirectiveAct.ACK_ADVANCE,
                DirectiveAct.CLARIFY, DirectiveAct.REDIRECT):
        assert question_id_for_agent_line(act, "q-123") == "q-123"


def test_non_question_acts_carry_none():
    for act in (DirectiveAct.INTRO, DirectiveAct.HOLD, DirectiveAct.REASSURE,
                DirectiveAct.CLOSE, DirectiveAct.ANSWER_META):
        assert question_id_for_agent_line(act, "q-123") is None


def test_question_bearing_with_no_active_question_is_none():
    assert question_id_for_agent_line(DirectiveAct.ASK, None) is None
