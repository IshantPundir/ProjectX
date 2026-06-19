"""Task A2 — per-question 0-10 score (star rating).

TDD: test written first, code added to make it pass.
"""
from app.modules.reporting.schemas import QuestionGradeOut, QuestionOut
from app.modules.reporting.scoring.question_grade import score_from_level


def test_questiongradeout_has_score_field():
    g = QuestionGradeOut(level="strong", score=10)
    assert g.score == 10


def test_score_from_level_maps_levels_to_ten_scale():
    assert score_from_level("strong") == 10
    assert score_from_level("solid") == 8
    assert score_from_level("thin") == 4
    assert score_from_level("absent") == 1
    assert score_from_level("not_reached") == 1


def test_questionout_carries_score():
    q = QuestionOut(seq=1, question_id="q1", title="t", status_badge="passed",
                    status_tone="ok", question_text="full text", candidate_quote="",
                    score=8)
    assert q.score == 8


def test_questionout_score_defaults_to_none():
    """QuestionOut.score is None when not supplied (not graded / not asked)."""
    q = QuestionOut(seq=1, question_id="q2", title="t", status_badge="not_attempted",
                    status_tone="neutral", question_text="full text", candidate_quote="")
    assert q.score is None


def test_score_wiring_from_grade_to_out():
    """QuestionOut created with score from a QuestionGradeOut carries the right value."""
    g = QuestionGradeOut(level="solid", score=8)
    q = QuestionOut(
        seq=1, question_id="q3", title="t", status_badge="passed",
        status_tone="ok", question_text="full text", candidate_quote="",
        score=(g.score if g else None),
    )
    assert q.score == 8


def test_score_wiring_none_when_no_grade():
    """QuestionOut.score is None when g is None (not reached / not asked)."""
    g = None
    q = QuestionOut(
        seq=1, question_id="q4", title="t", status_badge="not_attempted",
        status_tone="neutral", question_text="full text", candidate_quote="",
        score=(g.score if g else None),
    )
    assert q.score is None
