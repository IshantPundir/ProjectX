"""Question-banner planning — pure helper tests (lean nexus image)."""
from app.modules.reel.overlays import plan_banner_texts


def test_first_clip_with_label_shows():
    assert plan_banner_texts([("q1", "Q: first?")]) == ["Q: first?"]


def test_first_clip_without_label_is_suppressed():
    assert plan_banner_texts([("q1", None)]) == [None]
    assert plan_banner_texts([("q1", "")]) == [None]


def test_same_question_consecutive_clip_is_suppressed():
    out = plan_banner_texts([("q1", "Q: a?"), ("q1", "Q: a again?")])
    assert out == ["Q: a?", None]


def test_different_question_shows_again():
    out = plan_banner_texts([("q1", "Q: a?"), ("q2", "Q: b?")])
    assert out == ["Q: a?", "Q: b?"]


def test_question_changes_back_shows_again():
    # compare to the IMMEDIATELY preceding clip, not the last shown
    out = plan_banner_texts([("q1", "Q: a?"), ("q2", "Q: b?"), ("q1", "Q: a?")])
    assert out == ["Q: a?", "Q: b?", "Q: a?"]


def test_none_qid_falls_back_to_label_comparison():
    out = plan_banner_texts([(None, "Q: a?"), (None, "Q: a?"), (None, "Q: c?")])
    assert out == ["Q: a?", None, "Q: c?"]


def test_clip_with_no_label_does_not_suppress_a_later_different_question():
    # a label-less middle clip still becomes the "preceding clip" by qid
    out = plan_banner_texts([("q1", "Q: a?"), ("q1", None), ("q2", "Q: b?")])
    assert out == ["Q: a?", None, "Q: b?"]
