import pytest
from pydantic import ValidationError

from app.modules.interview_engine.brain.decision import (
    BrainDecision,
    BrainMove,
    CandidateIntent,
    CoverageDeltaItem,
)


def _cov(**kw) -> list[CoverageDeltaItem]:
    """{signal: state} -> list[CoverageDeltaItem] (the new strict-mode-safe shape)."""
    return [CoverageDeltaItem(signal=s, state=st) for s, st in kw.items()]


def _minimal(**over):
    base = dict(
        reasoning="Candidate named a concrete tool and an outcome; signal is sufficient; advance.",
        candidate_intent=CandidateIntent.answer,
        move=BrainMove.advance,
    )
    base.update(over)
    return BrainDecision(**base)


def test_reasoning_is_the_first_field():
    """doc 13: the reasoning text field must be FIRST so the model grounds before committing."""
    assert list(BrainDecision.model_fields.keys())[0] == "reasoning"


def test_minimal_decision_defaults():
    d = _minimal()
    assert d.move is BrainMove.advance
    assert d.grade is None
    assert d.coverage_delta == []
    assert d.coverage_map() == {}
    assert d.tapped_out is False
    assert d.is_knockout is False
    assert d.answer_meta_grounded is True
    assert d.tone == "NEUTRAL"


def test_closed_move_and_intent_enums():
    with pytest.raises(ValidationError):
        _minimal(move="grovel")
    with pytest.raises(ValidationError):
        _minimal(candidate_intent="banter")


def test_grade_literal():
    assert _minimal(grade="strong").grade == "strong"
    with pytest.raises(ValidationError):
        _minimal(grade="amazing")


def test_knockout_block_fields_present():
    d = _minimal(
        move=BrainMove.knockout_close,
        is_knockout=True,
        or_alternatives=["java", "python", "ruby"],
        or_alternatives_checked=True,
        reflect_confirmed=True,
    )
    assert d.or_alternatives == ["java", "python", "ruby"]
    assert d.or_alternatives_checked and d.reflect_confirmed


def test_probe_and_ask_reference_fields():
    d = _minimal(move=BrainMove.probe, bank_follow_up_index=1)
    assert d.bank_follow_up_index == 1
    d2 = _minimal(move=BrainMove.advance, bank_question_id="q-7")
    assert d2.bank_question_id == "q-7"


def test_coverage_delta_is_list_of_items_and_maps_to_dict():
    d = _minimal(coverage_delta=_cov(python="sufficient", kafka="failed"))
    assert [(i.signal, i.state) for i in d.coverage_delta] == [
        ("python", "sufficient"),
        ("kafka", "failed"),
    ]
    assert d.coverage_map() == {"python": "sufficient", "kafka": "failed"}


def test_spoken_setup_optional_defaults_none():
    d = BrainDecision(
        reasoning="r", candidate_intent=CandidateIntent.answer, move=BrainMove.advance
    )
    assert d.spoken_setup is None


def test_spoken_setup_round_trips():
    d = BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                      move=BrainMove.advance, bank_question_id="q3",
                      spoken_setup="Say tickets arrive from a system like Jira.")
    assert d.spoken_setup == "Say tickets arrive from a system like Jira."
