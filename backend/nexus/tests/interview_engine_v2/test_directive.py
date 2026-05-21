"""Directive contract — closed enums, field defaults, ASK/PROBE require verbatim text."""

import pytest
from pydantic import ValidationError

from app.modules.interview_engine_v2.directive import Directive, DirectiveAct, DirectiveTone


def test_act_enum_is_closed_and_complete():
    acts = {a.value for a in DirectiveAct}
    assert acts == {
        "INTRO", "ASK", "PROBE", "CLARIFY", "ACK_ADVANCE", "REPEAT", "REDIRECT",
        "HOLD", "REASSURE", "HINT", "ANSWER_META", "CONFIRM", "CLOSE",
    }


def test_tone_enum_is_closed():
    assert {t.value for t in DirectiveTone} == {"WARM", "NEUTRAL", "ENCOURAGING", "CALM"}


def test_minimal_ask_directive():
    d = Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK,
                  say="Tell me about a tricky incident you owned.")
    assert d.tone == DirectiveTone.NEUTRAL          # default
    assert d.is_terminal is False
    assert d.speculative is False
    assert d.supersedes is None
    assert d.compose_hint is None


def test_ask_requires_say():
    with pytest.raises(ValidationError):
        Directive(id="d-2", turn_ref="t-1", act=DirectiveAct.ASK, say=None)


def test_probe_requires_say():
    with pytest.raises(ValidationError):
        Directive(id="d-3", turn_ref="t-1", act=DirectiveAct.PROBE, say=None)


def test_close_must_be_terminal():
    with pytest.raises(ValidationError):
        Directive(id="d-4", turn_ref="t-1", act=DirectiveAct.CLOSE,
                  compose_hint="thank warmly", is_terminal=False)


def test_terminal_only_on_close():
    with pytest.raises(ValidationError):
        Directive(id="d-5", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="Next question.", is_terminal=True)


def test_composed_act_allows_null_say():
    d = Directive(id="d-6", turn_ref="t-1", act=DirectiveAct.HOLD,
                  say=None, compose_hint="warm 'take your time', short")
    assert d.say is None
