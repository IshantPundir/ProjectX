"""input_builder.py — bounded, cache-stable per-act message assembly (pure)."""

from app.modules.interview_engine_v2.directive import Directive, DirectiveAct, DirectiveTone
from app.modules.interview_engine_v2.mouth.input_builder import (
    build_mouth_messages,
)

_PERSONA = "PERSONA-PREFIX (byte-stable)"
_ACT_BLOCK = "INTENT: ASK — deliver the question."


def _ask(**kw):
    return Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK,
                     say="Tell me about your last integration project.", **kw)


def test_messages_are_persona_then_act_then_dynamic():
    msgs = build_mouth_messages(
        directive=_ask(), persona_preamble=_PERSONA, act_block=_ACT_BLOCK,
        candidate_utterance="I worked on billing syncs for a year.", last_question=None,
    )
    assert [m["role"] for m in msgs] == ["system", "system", "user"]
    assert msgs[0]["content"] == _PERSONA            # stable cache prefix is message[0]
    assert msgs[1]["content"] == _ACT_BLOCK
    # dynamic suffix carries the directive + the fenced candidate utterance
    assert "Tell me about your last integration project." in msgs[2]["content"]
    assert "CANDIDATE SAID:" in msgs[2]["content"]
    assert "billing syncs" in msgs[2]["content"]


def test_persona_prefix_is_identical_across_acts_and_turns():
    # R6: message[0] (the persona preamble) is byte-identical regardless of act/turn/utterance.
    a = build_mouth_messages(directive=_ask(), persona_preamble=_PERSONA, act_block="ASK BLOCK",
                             candidate_utterance="foo", last_question=None)
    b = build_mouth_messages(
        directive=Directive(id="d-2", turn_ref="t-2", act=DirectiveAct.PROBE,
                            say="And what did YOU do?"),
        persona_preamble=_PERSONA, act_block="PROBE BLOCK",
        candidate_utterance="completely different", last_question=None)
    assert a[0]["content"] == b[0]["content"] == _PERSONA


def test_no_candidate_block_when_no_utterance():
    # INTRO / proactive deliveries have no preceding candidate turn.
    msgs = build_mouth_messages(
        directive=Directive(id="d-3", turn_ref="t-0", act=DirectiveAct.INTRO,
                            say=None, compose_hint="warm, brief"),
        persona_preamble=_PERSONA, act_block="INTRO BLOCK",
        candidate_utterance=None, last_question=None,
    )
    assert "CANDIDATE SAID:" not in msgs[2]["content"]


def test_repeat_uses_cached_last_question():
    msgs = build_mouth_messages(
        directive=Directive(id="d-4", turn_ref="t-5", act=DirectiveAct.REPEAT, say=None),
        persona_preamble=_PERSONA, act_block="REPEAT BLOCK",
        candidate_utterance="sorry, can you say that again?",
        last_question="What part did you personally build?",
    )
    assert "What part did you personally build?" in msgs[2]["content"]


def test_mouth_messages_carry_no_history_only_one_directive():
    # negative control: bounded prompt — exactly persona + act + one dynamic message.
    msgs = build_mouth_messages(directive=_ask(), persona_preamble=_PERSONA, act_block=_ACT_BLOCK,
                                candidate_utterance="x", last_question=None)
    assert len(msgs) == 3


def test_tone_is_surfaced_in_dynamic_suffix():
    msgs = build_mouth_messages(
        directive=_ask(tone=DirectiveTone.WARM), persona_preamble=_PERSONA, act_block=_ACT_BLOCK,
        candidate_utterance=None, last_question=None)
    assert "WARM" in msgs[2]["content"]


def test_no_candidate_block_when_whitespace_only_utterance():
    # STT can emit whitespace-only strings after noise-gating; they must NOT open a CANDIDATE block.
    msgs = build_mouth_messages(
        directive=_ask(), persona_preamble=_PERSONA, act_block=_ACT_BLOCK,
        candidate_utterance="   ", last_question=None,
    )
    assert "CANDIDATE SAID:" not in msgs[2]["content"]


def test_repeat_fallback_when_no_cached_question():
    # First-turn REPEAT with nothing cached yields the explicit fallback, never None.
    msgs = build_mouth_messages(
        directive=Directive(id="d-5", turn_ref="t-1", act=DirectiveAct.REPEAT, say=None),
        persona_preamble=_PERSONA, act_block="REPEAT BLOCK",
        candidate_utterance="can you repeat that?",
        last_question=None,
    )
    assert "(no previous question to repeat)" in msgs[2]["content"]


def test_build_messages_includes_just_said_filler():
    msgs = build_mouth_messages(
        directive=Directive(id="d", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                            say="How long with Workato?"),
        persona_preamble="P", act_block="A", candidate_utterance="five years python",
        last_question=None, just_said_filler="Mm — five years, mostly Python…")
    suffix = msgs[2]["content"]
    assert "YOU JUST SAID: «Mm — five years, mostly Python…»" in suffix
    assert "continue from that" in suffix.lower()


def test_build_messages_omits_just_said_when_absent():
    msgs = build_mouth_messages(
        directive=Directive(id="d", turn_ref="t-1", act=DirectiveAct.ASK, say="Q?"),
        persona_preamble="P", act_block="A", candidate_utterance=None, last_question=None)
    assert "YOU JUST SAID" not in msgs[2]["content"]


def test_conversation_plane_forwards_just_said_filler():
    from app.ai.prompts import PromptLoader
    from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
    from app.modules.interview_engine_v2.mouth.service import ConversationPlane
    plane = ConversationPlane(loader=PromptLoader(version="v3"),
                              persona_name="Arjun", job_title="Backend Engineer")
    msgs = plane.build_turn_messages(
        Directive(id="d", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="How long with Workato in production?"),
        candidate_utterance="about five years",
        just_said_filler="Mm — five years, mostly Python…")
    suffix = msgs[-1]["content"]
    assert "YOU JUST SAID: «Mm — five years, mostly Python…»" in suffix


def test_conversation_plane_exposes_last_question_after_voicing():
    from app.ai.prompts import PromptLoader
    from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
    from app.modules.interview_engine_v2.mouth.service import ConversationPlane
    plane = ConversationPlane(loader=PromptLoader(version="v3"),
                              persona_name="Arjun", job_title="Backend Engineer")
    assert plane.last_question is None
    plane.build_turn_messages(
        Directive(id="d", turn_ref="t-1", act=DirectiveAct.ASK,
                  say="Tell me about a Python backend."),
        candidate_utterance=None)
    assert plane.last_question == "Tell me about a Python backend."
