"""floor.py — yield invariant + barge-in resumption SCAFFOLD (pure, no livekit)."""

from app.modules.interview_engine.turn_taking.floor import (
    ResumptionLabel,
    ResumptionSignals,
    classify_resumption,
    should_yield,
)


def test_yields_on_genuine_speech():
    assert should_yield(word_count=4, is_backchannel=False) is True


def test_does_not_yield_on_backchannel():
    assert should_yield(word_count=1, is_backchannel=True) is False
    assert should_yield(word_count=3, is_backchannel=True) is False  # "yeah yeah yeah"


def test_continuation_when_prior_incomplete_and_quick_resume():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=False, gap_ms=900,
        ai_prompt_fully_delivered=False, word_count=6, is_backchannel=False,
    ))
    assert label is ResumptionLabel.CONTINUATION


def test_barge_in_when_ai_prompt_was_cut_off():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=True, gap_ms=3000,
        ai_prompt_fully_delivered=False, word_count=5, is_backchannel=False,
    ))
    assert label is ResumptionLabel.BARGE_IN


def test_early_answer_when_prompt_delivered_and_prior_complete():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=True, gap_ms=2500,
        ai_prompt_fully_delivered=True, word_count=8, is_backchannel=False,
    ))
    assert label is ResumptionLabel.EARLY_ANSWER


def test_backchannel_short_circuits():
    label = classify_resumption(ResumptionSignals(
        prior_utterance_complete=True, gap_ms=500,
        ai_prompt_fully_delivered=True, word_count=1, is_backchannel=True,
    ))
    assert label is ResumptionLabel.BACKCHANNEL


def test_scaffold_label_is_advisory_only():
    # Negative control: the function is pure data->label; it must expose NO
    # side effect / no "should the AI yield" coupling. should_yield ignores it.
    assert should_yield(word_count=6, is_backchannel=False) is True  # regardless of label
