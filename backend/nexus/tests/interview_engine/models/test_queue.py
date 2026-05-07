from app.modules.interview_engine.models.queue import (
    QuestionStatus,
    QuestionState,
    QuestionQueueSnapshot,
)


def test_question_status_values():
    assert QuestionStatus.pending == "pending"
    assert QuestionStatus.active == "active"
    assert QuestionStatus.completed == "completed"
    assert QuestionStatus.skipped == "skipped"


def test_question_state_defaults():
    state = QuestionState(
        question_id="q-1",
        position=0,
        is_mandatory=True,
        status=QuestionStatus.pending,
    )
    assert state.main_asked_at_turn is None
    assert state.probes_asked_ids == []
    assert state.probes_remaining_ids == []
    assert state.anchors_hit_ids == []
    assert state.time_spent_ms == 0
    assert state.turn_count == 0


def test_question_queue_snapshot_default_active_index_none():
    snap = QuestionQueueSnapshot(questions=[])
    assert snap.active_index is None
