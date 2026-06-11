from app.modules.interview_engine.driver import _is_question_act
from app.modules.interview_engine.contracts import DirectiveAct


def test_question_acts_classified():
    assert _is_question_act(DirectiveAct.ask)
    assert _is_question_act(DirectiveAct.probe)
    assert _is_question_act(DirectiveAct.repeat)


def test_non_question_acts_not_classified():
    for a in (DirectiveAct.clarify, DirectiveAct.hold, DirectiveAct.reassure,
              DirectiveAct.confirm, DirectiveAct.answer_meta, DirectiveAct.redirect, DirectiveAct.close):
        assert not _is_question_act(a)
