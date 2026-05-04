"""Pure-unit tests for orchestrator/flow.py (Phase B linear progression).

No LiveKit, no DB, no LLM. Tests construct InterviewState + SessionConfig
with the minimum fields to exercise pick_next_question and
evaluate_exit_condition.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewState,
    QuestionState,
    evaluate_exit_condition,
    pick_next_question,
)
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)


def _make_question(
    qid: str, position: int, is_mandatory: bool = True
) -> QuestionConfig:
    """Build a QuestionConfig with the minimum schema-valid fields."""
    return QuestionConfig(
        id=qid,
        position=position,
        text=f"Tell me about your experience with {qid}.",
        signal_values=[f"signal_{qid}"],
        estimated_minutes=2.0,
        is_mandatory=is_mandatory,
        follow_ups=[],
        positive_evidence=[
            "Names specific tools",
            "Cites concrete outcomes",
            "Describes step-by-step",
        ],
        red_flags=[
            "Vague generalities",
            "No specifics",
        ],
        rubric=QuestionRubric(
            excellent="Names tools and outcomes",
            meets_bar="Describes general approach",
            below_bar="Refuses to engage",
        ),
        evaluation_hint="Look for specificity in tool naming.",
    )


def _make_config(questions: list[QuestionConfig]) -> SessionConfig:
    """Build a SessionConfig with given questions in given order."""
    return SessionConfig(  # noqa: E501
        session_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        job_title="Backend Engineer",
        role_summary="Backend engineering work",
        seniority_level="mid",
        company=CompanyContext(
            about="A test company building test things for testing here today.",
            industry="testing",
            company_stage="seed",
            hiring_bar="High bar text reaching the 20-character minimum.",
        ),
        candidate=CandidateContext(name="Test Candidate"),
        stage=StageConfig(
            stage_id="00000000-0000-0000-0000-000000000004",
            stage_type="ai_screening",
            name="Phone Screen",
            duration_minutes=15,
            difficulty="medium",
            questions=questions,
        ),
        signals=[],
        signal_metadata=[],
    )


def _make_state(question_ids: list[str]) -> InterviewState:
    """Build an InterviewState with one QuestionState per question_id."""
    return InterviewState(  # noqa: E501
        session_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000005",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        target_duration_seconds=900,
        started_at=datetime.now(UTC),
        questions=[
            QuestionState(question_id=qid, position=i, is_mandatory=True)
            for i, qid in enumerate(question_ids)
        ],
    )


def test_pick_next_returns_first_unasked() -> None:
    """Fresh state, three questions; returns the position-0 question."""
    questions = [_make_question(f"q{i}", i) for i in range(3)]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "q0"


def test_pick_next_skips_completed() -> None:
    """Position-0 has completed_at set; returns position-1 question."""
    questions = [_make_question(f"q{i}", i) for i in range(3)]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])
    state.questions[0].completed_at = datetime.now(UTC)
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "q1"


def test_pick_next_returns_none_when_all_complete() -> None:
    """All three questions have completed_at set; returns None."""
    questions = [_make_question(f"q{i}", i) for i in range(3)]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])
    now = datetime.now(UTC)
    for qs in state.questions:
        qs.completed_at = now
    next_q = pick_next_question(state, config)
    assert next_q is None


def test_pick_next_empty_questions_returns_none() -> None:
    """config.stage.questions=[]; returns None.

    StageConfig itself admits an empty questions list (no min_length on
    the field); the "banks have >=1 question" rule lives upstream in
    build_session_config / question-bank generation, not in the wire
    schema. pick_next_question must defensively handle the empty case
    even though build_session_config prevents it in production.
    """
    config = _make_config([])
    state = _make_state([])
    next_q = pick_next_question(state, config)
    assert next_q is None


def test_pick_next_returns_in_progress_question() -> None:
    """Position-0 has asked_at set but completed_at None (in progress);
    returns position-0 (resumable, not skipped)."""
    questions = [_make_question(f"q{i}", i) for i in range(2)]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])
    state.questions[0].asked_at = datetime.now(UTC)
    # completed_at is None — pick_next should still return q0
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "q0"


def test_pick_next_walks_upstream_order() -> None:
    """`pick_next_question` walks `config.stage.questions` in the order
    the list was given — it does NOT re-sort by position. Upstream
    `build_session_config` orders by `is_mandatory DESC, position ASC`,
    so a list with [mandatory_pos2, optional_pos0, mandatory_pos1] in
    that order returns mandatory_pos2 first.

    This test guards against future drift where `pick_next_question`
    might be tempted to sort by position alone — that would silently
    break mandatory-first selection upstream. The list deliberately
    interleaves `optional_pos0` BETWEEN the two mandatory rows so a
    stable sort by `position ASC` would put `optional_pos0` first
    (failing) AND a stable sort by `is_mandatory DESC` alone would
    keep them in this exact order (passing for the wrong reason);
    only "walk the list as-given" produces the expected sequence.
    """
    # Construct mirroring build_session_config's `is_mandatory DESC,
    # position ASC` output, with the optional row interleaved between
    # the two mandatory rows for stronger regression coverage.
    questions = [
        _make_question("mandatory_pos2", position=2, is_mandatory=True),
        _make_question("optional_pos0", position=0, is_mandatory=False),
        _make_question("mandatory_pos1", position=1, is_mandatory=True),
    ]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])

    # First pick: mandatory_pos2 (first in list, even though position=2).
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "mandatory_pos2"

    # Mark mandatory_pos2 done; next pick: optional_pos0 (next in list).
    state.questions[0].completed_at = datetime.now(UTC)
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "optional_pos0"

    # Mark optional_pos0 done; next pick: mandatory_pos1 (last in list).
    state.questions[1].completed_at = datetime.now(UTC)
    next_q = pick_next_question(state, config)
    assert next_q is not None
    assert next_q.id == "mandatory_pos1"


def test_evaluate_exit_returns_completed_when_pick_next_none() -> None:
    """All questions complete; returns ExitMode.COMPLETED."""
    questions = [_make_question("q0", 0)]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])
    state.questions[0].completed_at = datetime.now(UTC)
    assert evaluate_exit_condition(state, config) == ExitMode.COMPLETED


def test_evaluate_exit_returns_none_during_progress() -> None:
    """At least one question not yet completed; returns None."""
    questions = [_make_question(f"q{i}", i) for i in range(2)]
    config = _make_config(questions)
    state = _make_state([q.id for q in questions])
    # q0 done, q1 not yet
    state.questions[0].completed_at = datetime.now(UTC)
    assert evaluate_exit_condition(state, config) is None
