from app.modules.interview_engine.judge.input_builder import (
    JudgeInputPayload, build_judge_input,
)
from app.modules.interview_engine.models.ledger import (
    CoverageState, SignalLedgerSnapshot, SignalSnapshot,
)
from app.modules.interview_engine.models.queue import (
    QuestionState, QuestionStatus, QuestionQueueSnapshot,
)
from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_runtime.schemas import (
    QuestionConfig, QuestionRubric, TranscriptEntry,
)


def _q():
    return QuestionConfig(
        id="q1", position=0, text="Tell me about your work with X.",
        signal_values=["S1"], estimated_minutes=2.0, is_mandatory=True,
        follow_ups=["fu0", "fu1"],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint",
        question_kind="technical_depth",
    )


def test_build_judge_input_carries_active_question_only():
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(
            entries=[],
            snapshots={"S1": SignalSnapshot(signal_value="S1", coverage=CoverageState.none)},
            next_seq=1,
        ),
        queue_snapshot=QuestionQueueSnapshot(
            questions=[QuestionState(
                question_id="q1", position=0, is_mandatory=True,
                status=QuestionStatus.active,
                probes_remaining_ids=["0", "1"],
            )],
            active_index=0,
        ),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[
            TranscriptEntry(role="agent", text="prior agent", timestamp_ms=0, question_id="q1"),
            TranscriptEntry(role="candidate", text="prior candidate", timestamp_ms=1, question_id="q1"),
        ],
        candidate_utterance="I worked on it.",
        time_remaining_seconds=350,
    )
    assert payload.active_question_id == "q1"
    assert payload.candidate_utterance == "I worked on it."
    assert payload.time_remaining_seconds == 350


def test_recent_turns_truncated_to_8():
    turns = [
        TranscriptEntry(role="candidate", text=f"c{i}", timestamp_ms=i, question_id="q1")
        for i in range(20)
    ]
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(
            entries=[], snapshots={}, next_seq=1,
        ),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=turns,
        candidate_utterance="x",
        time_remaining_seconds=10,
    )
    assert len(payload.recent_turns) == 8
    assert payload.recent_turns[0].text == "c12"  # last 8


def test_build_judge_input_excludes_other_questions_rubric():
    """Only active question's rubric flows through."""
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
    )
    assert payload.active_question_positive_evidence == ["a", "b", "c"]
    assert payload.active_question_red_flags == ["x", "y"]
    assert payload.active_question_follow_ups == ["fu0", "fu1"]
