"""ConversationPlane — per-turn message orchestration + REPEAT cache + reflex fallback."""

import pytest

from app.ai.prompts import PromptLoader
from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
from app.modules.interview_engine_v2.mouth.service import (
    ConversationPlane,
    ReflexCueVariants,
)


def _plane() -> ConversationPlane:
    return ConversationPlane(
        loader=PromptLoader(version="v3"), persona_name="Arjun", job_title="Integration Engineer",
    )


def test_build_turn_messages_picks_the_right_act_block():
    plane = _plane()
    msgs = plane.build_turn_messages(
        Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK, say="Tell me about X."),
        candidate_utterance=None,
    )
    assert msgs[0]["content"].count("Arjun") >= 1                 # persona prefix rendered
    assert "INTENT: ASK" in msgs[1]["content"]                    # ask.txt loaded as the act block
    assert "Tell me about X." in msgs[2]["content"]


def test_repeat_replays_the_last_question_delivered():
    plane = _plane()
    plane.build_turn_messages(
        Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK, say="What did you build?"),
        candidate_utterance=None,
    )
    msgs = plane.build_turn_messages(
        Directive(id="d-2", turn_ref="t-2", act=DirectiveAct.REPEAT, say=None),
        candidate_utterance="sorry, again?",
    )
    assert "What did you build?" in msgs[2]["content"]


@pytest.mark.asyncio
async def test_prerender_reflex_variants_falls_back_on_error(monkeypatch):
    plane = _plane()

    async def _boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(plane, "_call_reflex_llm", _boom)
    variants = await plane.prerender_reflex_variants(
        hold_seed="Take your time.", nudge_seed="Whenever you're ready.", still_seed="Are you still there?",
    )
    # On failure the seeds are used as single-element variant lists (behavioral layer never breaks).
    assert variants.hold_space == ["Take your time."]
    assert variants.gentle_nudge == ["Whenever you're ready."]
    assert variants.still_there == ["Are you still there?"]


@pytest.mark.asyncio
async def test_prerender_reflex_variants_uses_llm_when_available(monkeypatch):
    plane = _plane()

    async def _ok(*a, **k):
        return ReflexCueVariants(
            hold_space=["Take your time, ya.", "No rush at all."],
            gentle_nudge=["Whenever you're ready."],
            still_there=["You still with me?"],
        )

    monkeypatch.setattr(plane, "_call_reflex_llm", _ok)
    variants = await plane.prerender_reflex_variants(
        hold_seed="Take your time.", nudge_seed="Whenever you're ready.", still_seed="Are you still there?",
    )
    assert "No rush at all." in variants.hold_space
    assert variants.still_there == ["You still with me?"]


def test_say_none_question_bearing_act_does_not_corrupt_repeat_cache():
    plane = _plane()
    # Prime the cache with a real ASK question.
    plane.build_turn_messages(
        Directive(id="d-1", turn_ref="t-1", act=DirectiveAct.ASK, say="What did you build?"),
        candidate_utterance=None,
    )
    # ACK_ADVANCE is question-bearing but its say is optional; say=None must NOT overwrite the cache.
    plane.build_turn_messages(
        Directive(id="d-2", turn_ref="t-2", act=DirectiveAct.ACK_ADVANCE, say=None),
        candidate_utterance="ok",
    )
    # REPEAT should still replay the original ASK question, not a None/fallback.
    msgs = plane.build_turn_messages(
        Directive(id="d-3", turn_ref="t-3", act=DirectiveAct.REPEAT, say=None),
        candidate_utterance="say that again?",
    )
    assert "What did you build?" in msgs[2]["content"]
    assert "(no previous question to repeat)" not in msgs[2]["content"]
