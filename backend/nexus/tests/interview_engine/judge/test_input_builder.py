from app.modules.interview_engine.judge.input_builder import (
    ActiveSignalMeta, JudgeInputPayload, build_judge_input,
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
    # The remaining-probes field defaults to an empty dict when the caller
    # doesn't pass active_remaining_probes — ensures the Judge cannot pick
    # a probe id whose underlying probe has already been consumed.
    assert payload.active_question_remaining_probes == {}


def test_build_judge_input_remaining_probes_passthrough():
    """active_remaining_probes is surfaced as a dict keyed by probe_id."""
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
        active_remaining_probes={"1": "fu1", "2": "fu2"},
    )
    # Probe "0" is intentionally NOT in the dict (already consumed); the
    # Judge will be unable to pick it because it's not in this input map.
    assert payload.active_question_remaining_probes == {"1": "fu1", "2": "fu2"}
    assert "0" not in payload.active_question_remaining_probes


def test_active_signal_metadata_carries_through():
    """ActiveSignalMeta is surfaced when supplied; default is empty list."""
    meta = [
        ActiveSignalMeta(value="S_KO", knockout=True, priority="required"),
        ActiveSignalMeta(value="S_PLAIN", knockout=False, priority="preferred"),
    ]
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
        active_signal_metadata=meta,
    )
    assert len(payload.active_question_signal_metadata) == 2
    assert payload.active_question_signal_metadata[0].value == "S_KO"
    assert payload.active_question_signal_metadata[0].knockout is True
    assert payload.active_question_signal_metadata[0].priority == "required"
    assert payload.active_question_signal_metadata[1].knockout is False
    assert payload.active_question_signal_metadata[1].priority == "preferred"


def test_active_signal_metadata_default_empty_list():
    """When the caller omits active_signal_metadata, the field is an empty list."""
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
    )
    assert payload.active_question_signal_metadata == []
