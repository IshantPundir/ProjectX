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
