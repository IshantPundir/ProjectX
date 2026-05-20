"""Unit tests for the meta_confession deterministic promotion rule.

Spec: docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md §6.

Promotion fires iff:
  - candidate_meta_confession=true                (Judge classified)
  - active_question.is_mandatory                  (knockout question)
  - question_state.push_back_count >= 1           (had a chance to recover)
  - remaining_probes is empty                     (probes already exhausted)
  - primary signal coverage in {none, partial}    (not already proven)
"""
from app.modules.interview_engine.models.judge import (
    AdvancePayload,
    AcknowledgeNoExperiencePayload,
    JudgeOutput,
    NextAction,
    Observation,
    PushBackPayload,
    TurnMetadata,
)
from app.modules.interview_engine.models.ledger import (
    CoverageState,
    SignalLedgerSnapshot,
    SignalSnapshot,
)
from app.modules.interview_engine.models.queue import QuestionState, QuestionStatus
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import (
    StateEngine,
    StateEngineConfig,
    ValidationWarning,
    _maybe_promote_meta_confession,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SignalMetadata,
    StageConfig,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_judge_output(*, meta_confession: bool) -> JudgeOutput:
    return JudgeOutput(
        reasoning="Test fixture: candidate gave a meta-confession on a follow-up.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(candidate_meta_confession=meta_confession),
    )


def _make_question(*, is_mandatory: bool, signal_value: str = "primary") -> QuestionConfig:
    """Minimal QuestionConfig referencing signal_value in its signal_values list."""
    return QuestionConfig(
        id="q-test",
        position=0,
        text="Tell me about your experience with this topic.",
        signal_values=[signal_value],
        estimated_minutes=2.0,
        is_mandatory=is_mandatory,
        follow_ups=[],
        positive_evidence=["evidence-a", "evidence-b", "evidence-c"],
        red_flags=["flag-x", "flag-y"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="Look for concrete examples.",
        question_kind="technical_depth",
    )


def _make_question_state(*, push_back_count: int) -> QuestionState:
    """Minimal QuestionState with the given push_back_count and no probes."""
    return QuestionState(
        question_id="q-test",
        position=0,
        is_mandatory=True,
        status=QuestionStatus.active,
        push_back_count=push_back_count,
        probes_remaining_ids=[],
    )


def _make_signal_metadata(
    signal_value: str = "primary",
    weight: int = 3,
) -> list[SignalMetadata]:
    return [SignalMetadata(
        value=signal_value,
        type="competency",
        priority="required",
        weight=weight,
        knockout=False,
        stage="screen",
        evaluation_method="verbal_response",
    )]


def _make_ledger(*, coverage_for_primary: CoverageState) -> SignalLedgerSnapshot:
    snapshots = {
        "primary": SignalSnapshot(
            signal_value="primary",
            coverage=coverage_for_primary,
            anchors_hit=[],
            last_observation_seq=None,
        ),
    }
    return SignalLedgerSnapshot(entries=[], snapshots=snapshots)


# ---------------------------------------------------------------------------
# 5 negative-condition gate tests
# ---------------------------------------------------------------------------

def test_no_promotion_when_meta_confession_false():
    """Guard 1: flag is False → no promotion regardless of other conditions."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=False),
        active_question=_make_question(is_mandatory=True),
        question_state=_make_question_state(push_back_count=1),
        remaining_probes={},
        ledger=_make_ledger(coverage_for_primary=CoverageState.partial),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is None


def test_no_promotion_when_not_mandatory():
    """Guard 2: non-mandatory question → no promotion (only knockout questions
    can produce a bluff-catch failure)."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=_make_question(is_mandatory=False),
        question_state=_make_question_state(push_back_count=1),
        remaining_probes={},
        ledger=_make_ledger(coverage_for_primary=CoverageState.partial),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is None


def test_no_promotion_when_push_back_count_zero():
    """Guard 3: push_back_count=0 → candidate never had a chance to recover;
    do NOT fire (first attempt on this question, give them room)."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=_make_question(is_mandatory=True),
        question_state=_make_question_state(push_back_count=0),
        remaining_probes={},
        ledger=_make_ledger(coverage_for_primary=CoverageState.partial),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is None


def test_no_promotion_when_probes_remain():
    """Guard 4: remaining probes → let the probe path run first before
    declaring the candidate unable to answer."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=_make_question(is_mandatory=True),
        question_state=_make_question_state(push_back_count=1),
        remaining_probes={"0": "Some probe still pending."},
        ledger=_make_ledger(coverage_for_primary=CoverageState.partial),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is None


def test_no_promotion_when_primary_signal_already_sufficient():
    """Guard 5: sufficient coverage → candidate already proved the signal;
    a later meta_confession on the same question does not reverse that."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=_make_question(is_mandatory=True),
        question_state=_make_question_state(push_back_count=1),
        remaining_probes={},
        ledger=_make_ledger(coverage_for_primary=CoverageState.sufficient),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is None


# ---------------------------------------------------------------------------
# Positive case
# ---------------------------------------------------------------------------

def test_promotion_fires_with_all_conditions_met():
    """All 5 gates pass → promotion fires and returns a ValidationWarning
    with the expected shape."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=_make_question(is_mandatory=True),
        question_state=_make_question_state(push_back_count=1),
        remaining_probes={},
        ledger=_make_ledger(coverage_for_primary=CoverageState.partial),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is not None
    assert isinstance(out, ValidationWarning)
    assert out.code == "meta_confession_knockout"
    assert out.details["promoted_to"] == NextAction.acknowledge_no_experience.value
    assert out.details["failed_signal_value"] == "primary"
    assert "meta_confession" in out.details["reason"]
    assert "push_back_count=1" in out.details["reason"]
    assert "primary_signal_uncovered" in out.details["reason"]


def test_promotion_fires_when_coverage_is_none():
    """Promotion also fires when the primary signal has never been touched
    (coverage=none, not just partial)."""
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=_make_question(is_mandatory=True),
        question_state=_make_question_state(push_back_count=2),
        remaining_probes={},
        ledger=_make_ledger(coverage_for_primary=CoverageState.none),
        session_signal_metadata=_make_signal_metadata(),
    )
    assert out is not None
    assert out.code == "meta_confession_knockout"
    assert out.details["failed_signal_value"] == "primary"


def test_promotion_selects_highest_weight_signal():
    """When the question covers multiple signals with different weights,
    the primary signal is the one with the highest weight."""
    two_signals = [
        SignalMetadata(
            value="low_weight", type="competency", priority="preferred",
            weight=1, knockout=False, stage="screen",
            evaluation_method="verbal_response",
        ),
        SignalMetadata(
            value="high_weight", type="competency", priority="required",
            weight=3, knockout=False, stage="screen",
            evaluation_method="verbal_response",
        ),
    ]
    question = QuestionConfig(
        id="q-multi",
        position=0,
        text="Tell me about your experience with both topics.",
        signal_values=["low_weight", "high_weight"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["ev-a", "ev-b", "ev-c"],
        red_flags=["fl-x", "fl-y"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint hint",
        question_kind="technical_depth",
    )
    ledger = SignalLedgerSnapshot(
        entries=[],
        snapshots={
            "low_weight": SignalSnapshot(
                signal_value="low_weight", coverage=CoverageState.partial,
                anchors_hit=[], last_observation_seq=None,
            ),
            "high_weight": SignalSnapshot(
                signal_value="high_weight", coverage=CoverageState.partial,
                anchors_hit=[], last_observation_seq=None,
            ),
        },
    )
    out = _maybe_promote_meta_confession(
        judge_output=_make_judge_output(meta_confession=True),
        active_question=question,
        question_state=_make_question_state(push_back_count=1),
        remaining_probes={},
        ledger=ledger,
        session_signal_metadata=two_signals,
    )
    assert out is not None
    # High-weight signal should be the failed one.
    assert out.details["failed_signal_value"] == "high_weight"


# ---------------------------------------------------------------------------
# Integration test: audit envelope shape via StateEngine.process_judge_output
# ---------------------------------------------------------------------------

def _make_session_config_for_meta(
    *,
    has_probes: bool = False,
    knockout: bool = False,
) -> SessionConfig:
    """Session with one mandatory question targeting 'primary' signal."""
    follow_ups = ["fu0"] if has_probes else []
    question = QuestionConfig(
        id="q1",
        position=0,
        text="Tell me about your experience with this platform.",
        signal_values=["primary"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=follow_ups,
        positive_evidence=["ev-a", "ev-b", "ev-c"],
        red_flags=["fl-x", "fl-y"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint hint",
        question_kind="technical_depth",
    )
    signal_metadata = [SignalMetadata(
        value="primary", type="competency", priority="required",
        weight=3, knockout=knockout, stage="screen",
        evaluation_method="verbal_response",
    )]
    return SessionConfig(
        session_id="sess-meta-test",
        job_id="job-meta-test",
        candidate_id="cand-meta-test",
        job_title="SRE",
        role_summary="role role role role role",
        seniority_level="Senior",
        company=CompanyContext(
            about="A test company building great things for engineers.",
            industry="software",
            company_stage="growth",
            hiring_bar="High bar only.",
        ),
        candidate=CandidateContext(name="Alice"),
        stage=StageConfig(
            stage_id="stg-meta",
            name="AI Screening",
            stage_type="ai_screening",
            difficulty="medium",
            duration_minutes=10,
            questions=[question],
        ),
        signals=["primary"],
        signal_metadata=signal_metadata,
    )


def test_promotion_emits_audit_event_with_meta_confession_knockout_code():
    """End-to-end: a turn that triggers meta_confession promotion emits the
    override audit event with code='meta_confession_knockout'.

    Setup:
      1. Activate q1 via the synthetic initialize_for_session_start.
      2. Issue one push_back turn (push_back_count → 1).
      3. Issue a second push_back turn WITH candidate_meta_confession=True
         and no probes remaining → promotion should fire.

    Assertion targets:
      - exactly one audit event with code='meta_confession_knockout'
      - the decision has instruction_kind == polite_close. This fixture has a
        single question, so under Option A the meta promotion acknowledges +
        advances, finds no pending question, and closes politely. (knockout is
        False here, so knockout_policy does NOT fire — the close comes purely
        from the queue being exhausted after the one-turn advance.)
    """
    cfg = _make_session_config_for_meta(has_probes=False, knockout=False)
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )

    # Step 1: Activate q1 via synthetic session start.
    eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )

    # Step 2: One push_back turn (push_back_count → 1).
    first_push_back = JudgeOutput(
        reasoning="Candidate gave a vague answer.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="vague_answer"),
        turn_metadata=TurnMetadata(),
    )
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=first_push_back,
        candidate_utterance_text="I think I've done something like this.",
        elapsed_ms=1000,
    )

    # Step 3: Second push_back with meta_confession=True, no probes.
    meta_push_back = JudgeOutput(
        reasoning="Candidate explicitly admitted they cannot answer.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(candidate_meta_confession=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-2",
        judge_output=meta_push_back,
        candidate_utterance_text="I honestly don't know, I can't give you a specific example.",
        elapsed_ms=2000,
    )

    codes = [w.code for w in decision.validation_warnings]
    assert "meta_confession_knockout" in codes, (
        f"Expected meta_confession_knockout in {codes}"
    )

    # Only one meta_confession_knockout event.
    meta_warnings = [
        w for w in decision.validation_warnings
        if w.code == "meta_confession_knockout"
    ]
    assert len(meta_warnings) == 1
    meta_warn = meta_warnings[0]
    assert "meta_confession" in meta_warn.details["reason"]
    assert meta_warn.details["failed_signal_value"] == "primary"

    # Instruction should be polite_close (non-knockout signal, so knockout_policy
    # does not fire). Under Option A the single-question meta promotion advances
    # then finds no pending question, so it closes politely after acknowledging.
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close


def test_promotion_chains_through_knockout_policy_for_knockout_signal():
    """When the primary signal is a knockout signal, meta_confession promotion
    should also trigger the knockout_policy close_polite override, resulting
    in polite_close instruction."""
    cfg = _make_session_config_for_meta(has_probes=False, knockout=True)
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )

    # Activate q1.
    eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )

    # One push_back turn.
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=JudgeOutput(
            reasoning="Candidate gave a vague and non-specific answer.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="vague_answer"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="I'm not too sure about that.",
        elapsed_ms=1000,
    )

    # Meta_confession push_back — primary signal is knockout.
    decision = eng.process_judge_output(
        turn_id="t-2",
        judge_output=JudgeOutput(
            reasoning="Candidate admitted they have no experience with the topic.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="missing_specifics"),
            turn_metadata=TurnMetadata(candidate_meta_confession=True),
        ),
        candidate_utterance_text="I've never actually done this, I can't give examples.",
        elapsed_ms=2000,
    )

    codes = [w.code for w in decision.validation_warnings]
    assert "meta_confession_knockout" in codes
    assert "knockout_policy_override" in codes
    # With knockout + close_polite, final instruction must be polite_close.
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert eng.lifecycle_snapshot().state.value == "closing"


def test_meta_confession_promotion_advances_queue_in_one_turn():
    """After promotion, the State Engine delivers the NEXT question with
    is_post_acknowledge (Option A) instead of staying on the same question."""
    from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
    from app.modules.interview_runtime.schemas import (
        CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
        SessionConfig, SignalMetadata, StageConfig,
    )
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, Observation, PushBackPayload, TurnMetadata,
        CoverageTransition, CoverageQuality,
    )
    from app.modules.interview_engine.models.speaker import InstructionKind

    def _q(qid, sig, pos, mandatory):
        return QuestionConfig(
            id=qid, position=pos, text="A question about the active topic here.",
            signal_values=[sig], estimated_minutes=2.0, is_mandatory=mandatory,
            follow_ups=[], positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
            rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
            evaluation_hint="Look for specifics here.", question_kind="technical_depth",
        )

    cfg = SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Eng",
        role_summary="r", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="S",
                          duration_minutes=15, difficulty="medium",
                          questions=[_q("q1", "sig_a", 0, True), _q("q2", "sig_b", 1, True)]),
        signals=["sig_a", "sig_b"],
        signal_metadata=[
            SignalMetadata(value="sig_a", type="competency", priority="required",
                           weight=3, knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="sig_b", type="competency", priority="required",
                           weight=3, knockout=False, stage="screen", evaluation_method="verbal_response"),
        ],
    )
    eng = StateEngine(session_config=cfg, config=StateEngineConfig(knockout_policy="record_only"))
    eng.process_judge_output(turn_id="t0", judge_output=eng.initialize_for_session_start(),
                             candidate_utterance_text=None, elapsed_ms=0)
    # Give q1 one push_back so promotion's push_back_count>=1 guard is satisfied,
    # and exhaust probes (q1 has none) — coverage stays uncovered.
    pb = JudgeOutput(
        reasoning="Candidate engaged but the answer was thin; pushing for specifics.",
        observations=[Observation(signal_value="sig_a", anchor_id=0, evidence_quote="I would log.",
                                  coverage_transition=CoverageTransition.none_to_partial,
                                  quality=CoverageQuality.thin)],
        candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata())
    eng.process_judge_output(turn_id="t1", judge_output=pb,
                             candidate_utterance_text="I would log.", elapsed_ms=1000)
    # Now meta_confession.
    mc = JudgeOutput(
        reasoning="Candidate admits they cannot answer THIS question after engaging earlier.",
        observations=[], candidate_claims=[], next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(candidate_meta_confession=True))
    decision = eng.process_judge_output(turn_id="t2", judge_output=mc,
                                        candidate_utterance_text="I don't know how to answer this.",
                                        elapsed_ms=2000)
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_acknowledge is True
    assert eng.queue_snapshot().active_index == 1


# ---------------------------------------------------------------------------
# Cluster G — Reverse-rule guard tests
#
# Guard: if a candidate already proved a knockout signal (coverage=sufficient)
# on a mandatory question, then later disclaims experience on a non-mandatory
# question targeting the SAME knockout signal, the earlier proof stands.
# The session must NOT close; the contradiction is recorded for review.
# ---------------------------------------------------------------------------

def _make_session_config_two_questions_same_knockout_signal() -> SessionConfig:
    """Session with:
       - q1: mandatory, knockout signal 'primary'
       - q2: non-mandatory, same knockout signal 'primary'
    """
    q1 = QuestionConfig(
        id="q1",
        position=0,
        text="Tell me about your experience with this platform.",
        signal_values=["primary"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["ev-a", "ev-b", "ev-c"],
        red_flags=["fl-x", "fl-y"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint hint",
        question_kind="technical_depth",
    )
    q2 = QuestionConfig(
        id="q2",
        position=1,
        text="Give another example of using this platform.",
        signal_values=["primary"],
        estimated_minutes=2.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=["ev-a", "ev-b", "ev-c"],
        red_flags=["fl-x", "fl-y"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint hint",
        question_kind="technical_depth",
    )
    signal_metadata = [SignalMetadata(
        value="primary", type="competency", priority="required",
        weight=3, knockout=True, stage="screen",
        evaluation_method="verbal_response",
    )]
    return SessionConfig(
        session_id="sess-reverse-rule-test",
        job_id="job-reverse-rule-test",
        candidate_id="cand-reverse-rule-test",
        job_title="SRE",
        role_summary="role role role role role",
        seniority_level="Senior",
        company=CompanyContext(
            about="A test company building great things for engineers.",
            industry="software",
            company_stage="growth",
            hiring_bar="High bar only.",
        ),
        candidate=CandidateContext(name="Alice"),
        stage=StageConfig(
            stage_id="stg-reverse-rule",
            name="AI Screening",
            stage_type="ai_screening",
            difficulty="medium",
            duration_minutes=10,
            questions=[q1, q2],
        ),
        signals=["primary"],
        signal_metadata=signal_metadata,
    )


def test_reverse_rule_guard_does_not_close_when_signal_already_sufficient():
    """Reverse-rule guard: candidate proved 'primary' on q1 (coverage=sufficient);
    later disclaims on q2 (non-mandatory, same signal). Session must NOT close.
    The audit trail must include 'knockout_policy_reverse_rule_skipped'."""
    from app.modules.interview_engine.models.judge import (
        CoverageQuality, CoverageTransition,
    )
    cfg = _make_session_config_two_questions_same_knockout_signal()
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )

    # Step 1: Activate q1.
    eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )

    # Step 2: Strong answer on q1 → coverage moves to sufficient.
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=JudgeOutput(
            reasoning="Candidate gave a strong concrete answer demonstrating the signal.",
            observations=[
                Observation(
                    signal_value="primary",
                    anchor_id=0,
                    evidence_quote="I used it for five years in production",
                    coverage_transition=CoverageTransition.none_to_sufficient,
                    quality=CoverageQuality.strong,
                )
            ],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q2"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="I used it for five years in production.",
        elapsed_ms=1000,
    )

    # Sanity: coverage should now be sufficient.
    snap = eng.ledger_snapshot().snapshots.get("primary")
    assert snap is not None and snap.coverage.value == "sufficient"

    # Step 3: On q2 (non-mandatory), candidate disclaims experience on 'primary'.
    decision = eng.process_judge_output(
        turn_id="t-2",
        judge_output=JudgeOutput(
            reasoning="Candidate now says they've never used the platform.",
            observations=[
                Observation(
                    signal_value="primary",
                    anchor_id=-1,
                    evidence_quote="actually I've never used it",
                    coverage_transition=CoverageTransition.sufficient_to_failed,
                )
            ],
            candidate_claims=[],
            next_action=NextAction.acknowledge_no_experience,
            next_action_payload=AcknowledgeNoExperiencePayload(
                failed_signal_value="primary",
            ),
            turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
        ),
        candidate_utterance_text="Actually I've never used it.",
        elapsed_ms=2000,
    )

    codes = [w.code for w in decision.validation_warnings]
    # Reverse-rule guard must have fired.
    assert "knockout_policy_reverse_rule_skipped" in codes, (
        f"Expected knockout_policy_reverse_rule_skipped in {codes}"
    )
    # Session must NOT close — polite_close is forbidden here.
    assert decision.speaker_input.instruction_kind != InstructionKind.polite_close, (
        "Reverse-rule guard failed: session was closed despite signal being already sufficient"
    )
    # knockout_policy_override must NOT be in the codes (the guard swallowed it).
    assert "knockout_policy_override" not in codes, (
        f"Unexpected knockout_policy_override fired when signal was already sufficient: {codes}"
    )
    # Session lifecycle must still be active (not closing).
    assert eng.lifecycle_snapshot().state.value == "active"


def test_reverse_rule_guard_does_not_fire_when_signal_not_sufficient():
    """When signal coverage is NOT sufficient (partial), the normal knockout_policy
    override fires as expected — no reverse-rule guard."""
    from app.modules.interview_engine.models.judge import (
        CoverageQuality, CoverageTransition,
    )
    cfg = _make_session_config_two_questions_same_knockout_signal()
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )

    # Activate q1.
    eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )

    # Partial answer on q1 → coverage stays at partial (not sufficient).
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=JudgeOutput(
            reasoning="Candidate gave a thin answer.",
            observations=[
                Observation(
                    signal_value="primary",
                    anchor_id=0,
                    evidence_quote="I used it a bit",
                    coverage_transition=CoverageTransition.none_to_partial,
                    quality=CoverageQuality.concrete,
                )
            ],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q2"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="I used it a bit.",
        elapsed_ms=1000,
    )

    # Sanity: coverage should be partial, NOT sufficient.
    snap = eng.ledger_snapshot().snapshots.get("primary")
    assert snap is not None and snap.coverage.value == "partial"

    # On q2, candidate disclaims — this should fire knockout_policy_override.
    decision = eng.process_judge_output(
        turn_id="t-2",
        judge_output=JudgeOutput(
            reasoning="Candidate says they've never really used the platform.",
            observations=[
                Observation(
                    signal_value="primary",
                    anchor_id=-1,
                    evidence_quote="I've never really used it seriously",
                    coverage_transition=CoverageTransition.partial_to_failed,
                )
            ],
            candidate_claims=[],
            next_action=NextAction.acknowledge_no_experience,
            next_action_payload=AcknowledgeNoExperiencePayload(
                failed_signal_value="primary",
            ),
            turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
        ),
        candidate_utterance_text="I've never really used it seriously.",
        elapsed_ms=2000,
    )

    codes = [w.code for w in decision.validation_warnings]
    # Normal knockout_policy_override should fire.
    assert "knockout_policy_override" in codes, (
        f"Expected knockout_policy_override in {codes}"
    )
    # Reverse-rule guard must NOT have fired.
    assert "knockout_policy_reverse_rule_skipped" not in codes
    # Session must have closed.
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert eng.lifecycle_snapshot().state.value == "closing"
