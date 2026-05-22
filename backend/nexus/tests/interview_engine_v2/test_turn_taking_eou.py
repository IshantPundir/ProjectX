"""eou.py — backchannel gate + unresponsive ladder (pure, no livekit, no regex)."""

import pytest

from app.modules.interview_engine_v2.turn_taking.eou import (
    BACKCHANNEL_TOKENS,
    EouConfig,
    LadderAction,
    UnresponsiveLadder,
    is_backchannel,
)


@pytest.mark.parametrize("text", ["yeah", "haan", "mm", "right", "ok", "achha", "hmm"])
def test_single_backchannel_token_is_backchannel(text):
    assert is_backchannel(text, min_words=2) is True


@pytest.mark.parametrize("text", ["yeah yeah", "haan haan", "mm hmm", "ok ok"])
def test_multiword_all_backchannel_tokens_is_backchannel(text):
    # >= 2 words but EVERY word is a backchannel token -> still engagement.
    assert is_backchannel(text, min_words=2) is True


@pytest.mark.parametrize("text", [
    "I built the billing sync",
    "yeah so I migrated the connector",   # starts with a token but is a real clause
    "no I haven't used Java",
])
def test_real_clause_is_not_backchannel(text):
    assert is_backchannel(text, min_words=2) is False


def test_empty_or_blank_is_backchannel():
    # nothing meaningful spoken -> treat as non-turn (keep the floor / ignore).
    assert is_backchannel("", min_words=2) is True
    assert is_backchannel("   ", min_words=2) is True


def test_backchannel_tokens_includes_indian_english():
    for tok in ("haan", "achha", "theek"):
        assert tok in BACKCHANNEL_TOKENS


def _ladder() -> UnresponsiveLadder:
    return UnresponsiveLadder(
        EouConfig(prompt_1_s=7.0, prompt_2_s=15.0, max_no_responses=2)
    )


def test_ladder_rungs_in_order():
    lad = _ladder()
    lad.on_question_posed(at_s=0.0)
    assert lad.action(now_s=3.0) is LadderAction.NONE       # before rung 1
    assert lad.action(now_s=7.5) is LadderAction.PROMPT_1   # rung 1
    assert lad.action(now_s=8.0) is LadderAction.NONE       # already fired rung 1
    assert lad.action(now_s=15.5) is LadderAction.PROMPT_2  # rung 2 == 1 no-response
    assert lad.action(now_s=16.0) is LadderAction.NONE


def test_ladder_two_no_responses_closes():
    lad = _ladder()
    # 1st posed question goes fully unanswered through both rungs.
    lad.on_question_posed(at_s=0.0)
    assert lad.action(now_s=7.5) is LadderAction.PROMPT_1
    assert lad.action(now_s=15.5) is LadderAction.PROMPT_2   # no-response #1 recorded
    # re-posed (same or next question), again unanswered to rung 2.
    lad.on_question_posed(at_s=20.0)
    assert lad.action(now_s=27.5) is LadderAction.PROMPT_1
    assert lad.action(now_s=35.5) is LadderAction.CLOSE_UNRESPONSIVE  # no-response #2 -> close


def test_ladder_reset_on_response_clears_state():
    lad = _ladder()
    lad.on_question_posed(at_s=0.0)
    assert lad.action(now_s=7.5) is LadderAction.PROMPT_1
    lad.on_candidate_responded()       # real answer arrived
    lad.on_question_posed(at_s=10.0)
    assert lad.action(now_s=13.0) is LadderAction.NONE     # timer restarted from 10.0
    assert lad.action(now_s=17.5) is LadderAction.PROMPT_1
