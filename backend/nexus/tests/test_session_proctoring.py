import pytest

from app.modules.session.proctoring import (
    VIOLATION_SEVERITY,
    classify_severity,
    decide_termination,
)


def test_severity_map_is_complete():
    assert VIOLATION_SEVERITY["tab_switch"] == "hard"
    # focus_loss = returned within the grace window (soft warning);
    # focus_abandoned = grace expired (hard) — mirrors the fullscreen pair.
    assert VIOLATION_SEVERITY["focus_loss"] == "soft"
    assert VIOLATION_SEVERITY["focus_abandoned"] == "hard"
    assert VIOLATION_SEVERITY["fullscreen_abandoned"] == "hard"
    assert VIOLATION_SEVERITY["devtools"] == "hard"
    assert VIOLATION_SEVERITY["fullscreen_exit"] == "soft"
    assert VIOLATION_SEVERITY["keyboard"] == "soft"


def test_focus_abandoned_terminates_with_kind_as_outcome():
    terminal, outcome = decide_termination(
        kind="focus_abandoned", soft_count_including_new=0, soft_limit=3
    )
    assert terminal is True
    assert outcome == "focus_abandoned"


def test_focus_loss_is_soft_and_does_not_terminate_below_limit():
    terminal, outcome = decide_termination(
        kind="focus_loss", soft_count_including_new=1, soft_limit=3
    )
    assert terminal is False
    assert outcome is None


def test_hard_violation_terminates_with_kind_as_outcome():
    terminal, outcome = decide_termination(
        kind="devtools", soft_count_including_new=0, soft_limit=3
    )
    assert terminal is True
    assert outcome == "devtools"


def test_soft_below_limit_does_not_terminate():
    terminal, outcome = decide_termination(
        kind="keyboard", soft_count_including_new=3, soft_limit=3
    )
    assert terminal is False
    assert outcome is None


def test_soft_over_limit_terminates_with_threshold_outcome():
    terminal, outcome = decide_termination(
        kind="keyboard", soft_count_including_new=4, soft_limit=3
    )
    assert terminal is True
    assert outcome == "soft_threshold_exceeded"


def test_classify_severity_rejects_unknown_kind():
    with pytest.raises(KeyError):
        classify_severity("nope")
