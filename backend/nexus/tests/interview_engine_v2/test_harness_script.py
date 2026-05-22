"""Pure helpers behind the M4 harness: the DirectiveScript + v2 keyterm assembler."""

from app.modules.interview_engine_v2.agent import DirectiveScript, assemble_v2_keyterms
from app.modules.interview_engine_v2.directive import DirectiveAct


def test_directive_script_intro_then_asks_then_close():
    script = DirectiveScript(questions=["Q1?", "Q2?"])
    d_intro = script.next_startup()
    d_ask1 = script.next_startup()
    assert d_intro.act is DirectiveAct.INTRO
    assert d_ask1.act is DirectiveAct.ASK and d_ask1.say == "Q1?"
    assert script.next_startup() is None
    d2 = script.next_after_turn(turn_ref="t-1")
    assert d2.act is DirectiveAct.ACK_ADVANCE and d2.say == "Q2?" and d2.turn_ref == "t-1"
    d3 = script.next_after_turn(turn_ref="t-2")
    assert d3.act is DirectiveAct.CLOSE and d3.is_terminal is True
    assert script.next_after_turn(turn_ref="t-3") is None


def test_directive_script_empty_bank_intro_then_close():
    script = DirectiveScript(questions=[])
    assert script.next_startup().act is DirectiveAct.INTRO
    assert script.next_startup() is None
    assert script.next_after_turn(turn_ref="t-1").act is DirectiveAct.CLOSE


def test_supersession_scenario_stages_speculative_then_superseder():
    script = DirectiveScript(questions=["Q1?", "Q2?"], scenario="supersession")
    script.next_startup(); script.next_startup()
    spec, real = script.supersession_pair(turn_ref="t-1")
    assert spec.speculative is True and spec.turn_ref == "t-1"
    assert real.supersedes == spec.id and real.turn_ref == "t-1"


def test_assemble_v2_keyterms_dedup_and_cap():
    terms = assemble_v2_keyterms(candidate_first_name="Ravi", bank_keyterms=["Workato", "ravi", "iPaaS"])
    assert terms[0] == "Ravi"                 # candidate name first
    assert "Workato" in terms and "iPaaS" in terms
    # case-insensitive dedup: "ravi" collides with "Ravi"
    assert sum(1 for t in terms if t.lower() == "ravi") == 1
    assert len(terms) <= 50
