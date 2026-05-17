import pytest

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
        next_pending_mandatory_id="q2",
    )
    assert payload.active_question_id == "q1"
    assert payload.candidate_utterance == "I worked on it."
    assert payload.time_remaining_seconds == 350
    assert payload.next_pending_mandatory_question_id == "q2"
    # signal_coverage carries the per-signal current state, not the
    # full append-only entries[] log.
    assert "S1" in payload.signal_coverage
    assert payload.signal_coverage["S1"].coverage == CoverageState.none


def test_recent_turns_passthrough_uncapped_at_input_builder_level():
    """build_judge_input does NOT itself slice recent_turns — the orchestrator
    is responsible for capping. Treating the builder as a pure projection
    keeps unit tests deterministic."""
    turns = [
        TranscriptEntry(role="candidate", text=f"c{i}", timestamp_ms=i, question_id="q1")
        for i in range(20)
    ]
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=turns,
        candidate_utterance="x",
        time_remaining_seconds=10,
        next_pending_mandatory_id=None,
    )
    assert len(payload.recent_turns) == 20
    assert payload.recent_turns[0].text == "c0"
    assert payload.recent_turns[-1].text == "c19"


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
        next_pending_mandatory_id=None,
    )
    assert payload.active_question_positive_evidence == ["a", "b", "c"]
    assert payload.active_question_red_flags == ["x", "y"]
    # remaining_probes defaults to empty dict so the Judge cannot pick a
    # probe whose underlying probe has already been consumed.
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
        next_pending_mandatory_id=None,
        active_remaining_probes={"1": "fu1", "2": "fu2"},
    )
    # Probe "0" is intentionally NOT in the dict (already consumed); the
    # Judge will be unable to pick it because it's not in this input map.
    assert payload.active_question_remaining_probes == {"1": "fu1", "2": "fu2"}
    assert "0" not in payload.active_question_remaining_probes


def test_active_signal_metadata_carries_through():
    """ActiveSignalMeta is surfaced when supplied; default is empty list."""
    meta = [
        ActiveSignalMeta(value="S_KO", type="experience", knockout=True, priority="required"),
        ActiveSignalMeta(value="S_PLAIN", type="competency", knockout=False, priority="preferred"),
    ]
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
        next_pending_mandatory_id=None,
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
        next_pending_mandatory_id=None,
    )
    assert payload.active_question_signal_metadata == []


def test_judge_input_payload_does_not_carry_full_ledger_or_queue():
    """Anti-regression: full SignalLedgerSnapshot.entries[] and
    QuestionQueueSnapshot fields were removed from the LLM input shape.
    They contributed ~30 tok/turn of audit data the Judge never used."""
    fields = set(JudgeInputPayload.model_fields.keys())
    assert "ledger_snapshot" not in fields
    assert "queue_snapshot" not in fields
    assert "claims_snapshot" not in fields
    # New slim fields are present.
    assert "signal_coverage" in fields
    assert "candidate_claims" in fields
    assert "next_pending_mandatory_question_id" in fields


def test_judge_input_field_order_stable_first_dynamic_last():
    """Pydantic respects declaration order in model_dump_json. The order is
    the cache-stability contract — moving a dynamic field above a stable
    one would defeat OpenAI prompt caching."""
    expected_order = [
        "active_question_id",
        "active_question_text",
        "active_question_positive_evidence",
        "active_question_red_flags",
        "active_question_rubric",
        "active_question_evaluation_hint",
        "active_question_signal_metadata",
        "next_pending_mandatory_question_id",
        "active_question_push_back_count",
        "active_question_consecutive_dont_know_count",
        "active_question_remaining_probes",
        "signal_coverage",
        "candidate_claims",
        "recent_turns",
        "candidate_utterance",
        "time_remaining_seconds",
    ]
    actual = list(JudgeInputPayload.model_fields.keys())
    assert actual == expected_order, (
        f"Field order mismatch — cache-stability contract violated.\n"
        f"  expected: {expected_order}\n"
        f"  actual:   {actual}"
    )


def test_push_back_count_defaults_zero():
    """When the orchestrator omits push_back_count (or there's no active
    question), the field defaults to 0. The Judge prompt's cap=2 rule is
    a no-op at 0."""
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
        next_pending_mandatory_id=None,
    )
    assert payload.active_question_push_back_count == 0


def test_push_back_count_passthrough():
    """Orchestrator reads push_back_count from QuestionState and passes it
    through. The Judge prompt §3 push_back entry depends on this field
    being accurate to enforce the cap=2 rule."""
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
        next_pending_mandatory_id=None,
        active_question_push_back_count=2,
    )
    assert payload.active_question_push_back_count == 2


def test_active_signal_meta_type_carries_through_build_judge_input():
    """When ActiveSignalMeta is built with type='experience', that type
    is preserved through build_judge_input into the payload."""
    meta = [
        ActiveSignalMeta(value="S1", type="experience", knockout=False, priority="required"),
    ]
    payload = build_judge_input(
        active_question=_q(),
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[],
        candidate_utterance="x",
        time_remaining_seconds=10,
        next_pending_mandatory_id=None,
        active_signal_metadata=meta,
    )
    assert len(payload.active_question_signal_metadata) == 1
    assert payload.active_question_signal_metadata[0].type == "experience"


def test_active_signal_meta_requires_type():
    from pydantic import ValidationError
    from app.modules.interview_engine.judge.input_builder import ActiveSignalMeta

    with pytest.raises(ValidationError) as exc:
        ActiveSignalMeta(
            value="some signal",
            knockout=False,
            priority="required",
            # missing type
        )
    assert "type" in str(exc.value).lower()


def test_active_signal_meta_type_must_be_in_enum():
    from pydantic import ValidationError
    from app.modules.interview_engine.judge.input_builder import ActiveSignalMeta

    with pytest.raises(ValidationError):
        ActiveSignalMeta(
            value="some signal",
            type="invalid_type",  # not in Literal[...]
            knockout=False,
            priority="required",
        )


def test_active_signal_meta_accepts_valid_types():
    from app.modules.interview_engine.judge.input_builder import ActiveSignalMeta

    for t in ("experience", "credential", "competency", "behavioral"):
        m = ActiveSignalMeta(value="x", type=t, knockout=False, priority="required")
        assert m.type == t
