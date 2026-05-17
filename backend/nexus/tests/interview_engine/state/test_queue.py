import pytest

from app.modules.interview_engine.models.queue import QuestionStatus
from app.modules.interview_engine.state.queue import (
    QuestionQueue,
    QueueError,
    NoActiveQuestionError,
)


def _build_queue() -> QuestionQueue:
    """3-question queue: q1 mandatory, q2 mandatory, q3 optional. follow_ups: q1 has 2, q2 has 1."""
    return QuestionQueue.from_initial(
        questions=[
            {"question_id": "q1", "is_mandatory": True, "follow_ups": ["fu0", "fu1"]},
            {"question_id": "q2", "is_mandatory": True, "follow_ups": ["fu0"]},
            {"question_id": "q3", "is_mandatory": False, "follow_ups": []},
        ],
    )


def test_initial_state_no_active():
    q = _build_queue()
    snap = q.snapshot()
    assert snap.active_index is None
    assert all(state.status == QuestionStatus.pending for state in snap.questions)


def test_advance_to_first_makes_it_active():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    snap = q.snapshot()
    assert snap.active_index == 0
    assert snap.questions[0].status == QuestionStatus.active
    assert snap.questions[0].main_asked_at_turn == 0
    assert snap.questions[0].probes_remaining_ids == ["0", "1"]


def test_advance_to_marks_prior_completed():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=2)
    snap = q.snapshot()
    assert snap.questions[0].status == QuestionStatus.completed
    assert snap.active_index == 1
    assert snap.questions[1].status == QuestionStatus.active


def test_cannot_advance_backward():
    q = _build_queue()
    q.advance_to("q2", at_turn=0)
    with pytest.raises(QueueError):
        q.advance_to("q1", at_turn=1)


def test_apply_probe_consumes_remaining_id():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    q.apply_probe(probe_id="0", at_turn=1)
    snap = q.snapshot()
    assert snap.questions[0].probes_asked_ids == ["0"]
    assert snap.questions[0].probes_remaining_ids == ["1"]


def test_apply_probe_unknown_id_raises():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    with pytest.raises(QueueError):
        q.apply_probe(probe_id="99", at_turn=1)


def test_apply_probe_no_active_raises():
    q = _build_queue()
    with pytest.raises(NoActiveQuestionError):
        q.apply_probe(probe_id="0", at_turn=0)


def test_record_anchor_dedup():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    q.record_anchor_hit(anchor_id=0)
    q.record_anchor_hit(anchor_id=0)
    snap = q.snapshot()
    assert snap.questions[0].anchors_hit_ids == [0]


def test_next_pending_mandatory():
    q = _build_queue()
    assert q.next_pending_mandatory_id() == "q1"
    q.advance_to("q1", at_turn=0)
    assert q.next_pending_mandatory_id() == "q2"
    q.advance_to("q2", at_turn=2)
    assert q.next_pending_mandatory_id() is None


def test_active_question_id_returns_none_initially():
    q = _build_queue()
    assert q.active_question_id() is None
    q.advance_to("q1", at_turn=0)
    assert q.active_question_id() == "q1"


def test_increment_turn_updates_active_state():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    q.increment_active_turn(elapsed_ms=4500)
    snap = q.snapshot()
    assert snap.questions[0].turn_count == 1
    assert snap.questions[0].time_spent_ms == 4500


def test_completed_when_all_mandatory_done():
    q = _build_queue()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=2)
    q.complete_active(at_turn=4)
    assert q.all_mandatory_complete() is True


# ---------------------------------------------------------------------------
# Tests for next_pending_question_id (Cluster G)
# ---------------------------------------------------------------------------

from app.modules.interview_engine.models.ledger import CoverageState, SignalSnapshot  # noqa: E402


def _make_coverage(
    mapping: dict[str, CoverageState],
) -> dict[str, "SignalSnapshot"]:
    """Build a signal_coverage dict from signal_value → CoverageState."""
    from app.modules.interview_engine.models.ledger import SignalSnapshot
    return {
        sv: SignalSnapshot(signal_value=sv, coverage=state)
        for sv, state in mapping.items()
    }


def _build_queue_with_signals() -> QuestionQueue:
    """3-question queue:
        q1: mandatory, signal "sig_a"
        q2: mandatory, signal "sig_b"
        q3: non-mandatory, signal "sig_c"
        q4: non-mandatory, signals "sig_d"
    """
    return QuestionQueue.from_initial(
        questions=[
            {
                "question_id": "q1",
                "is_mandatory": True,
                "follow_ups": [],
                "signal_values": ["sig_a"],
            },
            {
                "question_id": "q2",
                "is_mandatory": True,
                "follow_ups": [],
                "signal_values": ["sig_b"],
            },
            {
                "question_id": "q3",
                "is_mandatory": False,
                "follow_ups": [],
                "signal_values": ["sig_c"],
            },
            {
                "question_id": "q4",
                "is_mandatory": False,
                "follow_ups": [],
                "signal_values": ["sig_d"],
            },
        ],
    )


def test_next_pending_question_mandatory_pending():
    """Mandatory still pending → returns mandatory ID with is_mandatory=True."""
    q = _build_queue_with_signals()
    result = q.next_pending_question_id(signal_coverage={})
    assert result == ("q1", True)


def test_next_pending_question_mandatory_all_done_no_nonmandatory():
    """All mandatory done, no non-mandatory questions → returns None."""
    q = QuestionQueue.from_initial(
        questions=[
            {"question_id": "q1", "is_mandatory": True, "follow_ups": [], "signal_values": ["sig_a"]},
            {"question_id": "q2", "is_mandatory": True, "follow_ups": [], "signal_values": ["sig_b"]},
        ],
    )
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=1)
    q.complete_active(at_turn=2)
    result = q.next_pending_question_id(signal_coverage=_make_coverage({"sig_a": CoverageState.sufficient, "sig_b": CoverageState.sufficient}))
    assert result is None


def test_next_pending_question_nonmandatory_all_signals_sufficient():
    """All mandatory done; non-mandatory's signal already sufficient → returns None."""
    q = _build_queue_with_signals()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=1)
    q.complete_active(at_turn=2)
    # Both non-mandatory signals are already sufficient
    coverage = _make_coverage({
        "sig_a": CoverageState.sufficient,
        "sig_b": CoverageState.sufficient,
        "sig_c": CoverageState.sufficient,
        "sig_d": CoverageState.sufficient,
    })
    result = q.next_pending_question_id(signal_coverage=coverage)
    assert result is None


def test_next_pending_question_nonmandatory_uncovered_signal():
    """All mandatory done; q3 has uncovered signal → returns (q3_id, False)."""
    q = _build_queue_with_signals()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=1)
    q.complete_active(at_turn=2)
    # sig_c is uncovered, sig_d is sufficient
    coverage = _make_coverage({
        "sig_a": CoverageState.sufficient,
        "sig_b": CoverageState.sufficient,
        "sig_c": CoverageState.none,
        "sig_d": CoverageState.sufficient,
    })
    result = q.next_pending_question_id(signal_coverage=coverage)
    assert result == ("q3", False)


def test_next_pending_question_nonmandatory_position_order():
    """First non-mandatory with uncovered signals wins (position order)."""
    q = _build_queue_with_signals()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=1)
    q.complete_active(at_turn=2)
    # Both non-mandatory signals uncovered → first (q3) wins
    coverage = _make_coverage({
        "sig_a": CoverageState.sufficient,
        "sig_b": CoverageState.sufficient,
        "sig_c": CoverageState.none,
        "sig_d": CoverageState.partial,
    })
    result = q.next_pending_question_id(signal_coverage=coverage)
    assert result == ("q3", False)


def test_next_pending_question_first_nonmandatory_sufficient_second_uncovered():
    """First non-mandatory has all-sufficient signals; second has uncovered → returns second."""
    q = _build_queue_with_signals()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=1)
    q.complete_active(at_turn=2)
    # sig_c (q3's signal) is sufficient; sig_d (q4's signal) is none
    coverage = _make_coverage({
        "sig_a": CoverageState.sufficient,
        "sig_b": CoverageState.sufficient,
        "sig_c": CoverageState.sufficient,
        "sig_d": CoverageState.none,
    })
    result = q.next_pending_question_id(signal_coverage=coverage)
    assert result == ("q4", False)


def test_next_pending_question_unknown_signal_counts_as_uncovered():
    """A non-mandatory question whose signal has no coverage entry is treated as uncovered."""
    q = _build_queue_with_signals()
    q.advance_to("q1", at_turn=0)
    q.advance_to("q2", at_turn=1)
    q.complete_active(at_turn=2)
    # No coverage entry for sig_c or sig_d → both uncovered → q3 wins (position order)
    result = q.next_pending_question_id(signal_coverage={})
    assert result == ("q3", False)
