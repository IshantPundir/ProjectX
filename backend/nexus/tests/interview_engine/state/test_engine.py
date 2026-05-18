import pytest

from app.modules.interview_engine.models.judge import (
    AdvancePayload, ClarifyPayload, ClarifyKind, CoverageTransition,
    JudgeOutput, NextAction, Observation, ProbePayload, RepeatPayload,
    ClaimEntry as JudgeClaimEntry, TurnMetadata,
    AcknowledgeNoExperiencePayload,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import (
    StateEngine, StateEngineDecision, StateEngineConfig,
)
from app.modules.interview_runtime.schemas import (
    SessionConfig, QuestionConfig, QuestionRubric, SignalMetadata,
    StageConfig, CompanyContext, CandidateContext,
)


def _question(qid: str, position: int, mandatory: bool, follow_ups: list[str], signal_values: list[str]):
    return QuestionConfig(
        id=qid, position=position, text=f"Tell me about {qid} please.",
        signal_values=signal_values, estimated_minutes=2.0,
        is_mandatory=mandatory, follow_ups=follow_ups,
        positive_evidence=["evidence-0", "evidence-1", "evidence-2"],
        red_flags=["flag-0", "flag-1"],
        rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
        evaluation_hint="hint hint hint hint",
        question_kind="technical_depth",
    )


def _config():
    questions = [
        _question("q1", 0, True, ["fu0", "fu1"], ["S1"]),
        _question("q2", 1, True, ["fu0"], ["S2"]),
    ]
    return SessionConfig(
        session_id="sess-1", job_id="job-1", candidate_id="cand-1",
        job_title="SRE",
        role_summary="rrrrrr",
        seniority_level="Senior",
        company=CompanyContext(
            about="Acme is an enterprise software company building tools for hiring teams.",
            industry="software",
            company_stage="growth",
            hiring_bar="High bar — only senior engineers.",
        ),
        candidate=CandidateContext(name="Alice"),
        stage=StageConfig(
            stage_id="stg-1",
            stage_type="ai_screening",
            name="AI Screening",
            duration_minutes=10,
            difficulty="medium",
            questions=questions,
        ),
        signals=["S1", "S2"],
        signal_metadata=[
            SignalMetadata(value="S1", type="competency", priority="required", weight=3,
                           knockout=False, stage="screen", evaluation_method="verbal_response"),
            SignalMetadata(value="S2", type="competency", priority="required", weight=3,
                           knockout=True, stage="screen", evaluation_method="verbal_response"),
        ],
    )


def _judge_advance(target: str) -> JudgeOutput:
    return JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id=target),
        turn_metadata=TurnMetadata(),
    )


def _engine() -> StateEngine:
    return StateEngine(
        session_config=_config(),
        config=StateEngineConfig(claims_pool_max=50),
    )


def test_initialize_for_session_start_returns_advance_to_position_zero():
    eng = _engine()
    j = eng.initialize_for_session_start()
    assert j.next_action == NextAction.advance
    assert j.next_action_payload.target_question_id == "q1"


def test_process_advance_resolves_first_question_speaker_input():
    eng = _engine()
    j = eng.initialize_for_session_start()
    decision = eng.process_judge_output(
        turn_id="t-0", judge_output=j, candidate_utterance_text=None,
        elapsed_ms=0,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_first_question
    assert "Tell me about q1" in (decision.speaker_input.bank_text or "")


def test_process_probe_consumes_remaining():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S1", anchor_id=0, evidence_quote="ev",
                        coverage_transition=CoverageTransition.none_to_partial),
        ],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(probe_id="0"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="my answer",
        elapsed_ms=4000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    assert decision.speaker_input.bank_text == "fu0"


def test_no_experience_disclosure_marks_signal_failed():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S1", anchor_id=-1, evidence_quote="never used it",
                        coverage_transition=CoverageTransition.none_to_failed),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S1"),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="never used", elapsed_ms=2000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.acknowledge_no_experience
    assert decision.speaker_input.failed_signal_value == "S1"


def test_repeat_action_uses_cached_utterance():
    """When Judge emits repeat, decision carries cached_utterance + bypasses Speaker."""
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-0", text="Tell me about your work with q1.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-0", text="Tell me about your work with q1.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.repeat,
        next_action_payload=RepeatPayload(),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="can you repeat that?",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.repeat
    assert decision.cached_utterance == "Tell me about your work with q1."
    assert decision.cached_source_turn_id == "t-0"


def test_repeat_without_prior_utterance_degrades_to_clarify():
    """If repeat is requested before any agent utterance exists, degrade to clarify with a warning."""
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # NO register_agent_utterance call.
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.repeat,
        next_action_payload=RepeatPayload(),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="repeat",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.clarify
    assert any(
        w.code == "repeat_without_prior_question" for w in decision.validation_warnings
    )


def test_invalid_probe_id_falls_back_to_first_unused_followup():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(probe_id="99"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="answer",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    assert decision.speaker_input.bank_text == "fu0"  # first unused
    assert any(
        w.code == "invalid_probe_id" for w in decision.validation_warnings
    )


def test_advance_to_unknown_target_picks_next_pending_mandatory():
    """Judge emits advance with bogus target_id -> State Engine falls back
    to next_pending_mandatory_id. Phase 9.2: a concrete observation is
    needed in the same turn so the advance-quality-gate doesn't downgrade
    to push_back before the queue-error path can fire (the gate runs
    first by design — both checks have to pass for advance to land)."""
    from app.modules.interview_engine.models.judge import CoverageQuality
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Judge advance with bogus target + one concrete obs (post-Phase-9.2,
    # the gate requires >=1 concrete obs on the active question or the
    # advance is downgraded to push_back).
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S1", anchor_id=0,
                evidence_quote="concrete answer fragment",
                coverage_transition=CoverageTransition.none_to_sufficient,
                quality=CoverageQuality.concrete,
            )
        ],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q-DOES-NOT-EXIST"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="answer", elapsed_ms=1000,
    )
    assert decision.speaker_input.bank_text and "q2" in decision.speaker_input.bank_text
    assert any(w.code == "invalid_target_question_id" for w in decision.validation_warnings)


def test_end_session_blocked_without_knockout_or_complete():
    eng = _engine()
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    from app.modules.interview_engine.models.judge import EndSessionPayload
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.end_session,
        next_action_payload=EndSessionPayload(initiated_by="agent_initiated"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j, candidate_utterance_text="x", elapsed_ms=1000,
    )
    # Should fall back to advance (q2) not actually end.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert any(w.code == "end_session_not_allowed" for w in decision.validation_warnings)


def test_speaker_input_includes_candidate_name(make_session_config, make_question, make_judge_output):
    """The State Engine must pass SessionConfig.candidate.name into SpeakerInput.candidate_name."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S1"],
    )
    # The make_session_config fixture sets candidate.name to "Alice".
    eng = StateEngine(session_config=cfg, config=StateEngineConfig())
    eng.set_persona_name("Sam")

    decision = eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    assert decision.speaker_input.candidate_name == "Alice"
    assert decision.speaker_input.persona_name == "Sam"


# ---------------------------------------------------------------------------
# Knockout enforcement: ?→failed observations on knockout=True signals
# ---------------------------------------------------------------------------


def test_knockout_signal_failure_records_knockout_failure(make_session_config, make_question, make_judge_output):
    """Layer 1: failure observation on a knockout=True signal records a KnockoutFailure."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="record_only"),  # not close, just record
    )
    # Get the queue active first
    eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S_KO", anchor_id=-1,
                        evidence_quote="I have no experience with this",
                        coverage_transition=CoverageTransition.none_to_failed),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S_KO"),
        turn_metadata=TurnMetadata(),
    )
    eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="I have no experience with this", elapsed_ms=2000,
    )
    lifecycle = eng.lifecycle_snapshot()
    assert len(lifecycle.knockout_failures) == 1
    assert lifecycle.knockout_failures[0].signal_values == ["S_KO"]


def test_close_polite_policy_overrides_action_on_knockout(make_session_config, make_question):
    """Layer 2: policy=close_polite + knockout failure → instruction overridden to polite_close."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S_KO", anchor_id=-1,
                        evidence_quote="never used it",
                        coverage_transition=CoverageTransition.none_to_failed),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S_KO"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="never used it", elapsed_ms=2000,
    )
    # Instruction kind should be polite_close, NOT acknowledge_no_experience.
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert any(w.code == "knockout_policy_override" for w in decision.validation_warnings)
    # Lifecycle should have transitioned to closing.
    assert eng.lifecycle_snapshot().state.value == "closing"
    assert eng.lifecycle_snapshot().last_outcome.value == "knockout_closed"


def test_record_only_policy_does_not_close_on_knockout(make_session_config, make_question):
    """Layer 1+2: policy=record_only records but does NOT override action."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="record_only"),
    )
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S_KO", anchor_id=-1,
                        evidence_quote="never used",
                        coverage_transition=CoverageTransition.none_to_failed),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S_KO"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="never used", elapsed_ms=2000,
    )
    # KnockoutFailure recorded:
    assert len(eng.lifecycle_snapshot().knockout_failures) == 1
    # But action NOT overridden:
    assert decision.speaker_input.instruction_kind == InstructionKind.acknowledge_no_experience
    # Lifecycle still active.
    assert eng.lifecycle_snapshot().state.value == "active"


def test_failed_with_positive_anchor_is_dropped(make_session_config, make_question):
    """Bug C — Judge sometimes emits sufficient->failed with anchor_id=0
    on a positive answer. State Engine must drop the observation, not
    propagate it into a knockout.

    Reproduces the session 8317142f-3166-4236-a43c-18c8ab4592e1 turn-7
    pattern: signal in `sufficient`, Judge emits a sufficient->failed with
    a real positive-evidence anchor (anchor_id=0). Ledger precondition
    matches, knockout=True signal would fire a KnockoutFailure and the
    close_polite policy would override the Judge's actual `probe` action.
    """
    cfg = make_session_config(
        questions=[
            make_question(
                qid="q1",
                text="Tell me about your work on this topic.",
                signal_values=["S_KO"],
                follow_ups=["fu0", "fu1"],
            ),
        ],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    engine = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    # Drive to active state via initialize_for_session_start + a first turn.
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )

    # Walk S_KO into `sufficient` so the bogus sufficient->failed
    # observation passes the ledger precondition (matching the
    # session 8317142f turn-7 state).
    warmup = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S_KO", anchor_id=1,
                evidence_quote="solid context",
                coverage_transition=CoverageTransition.none_to_sufficient,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(probe_id="0"),
        turn_metadata=TurnMetadata(),
    )
    engine.process_judge_output(
        turn_id="t-warmup", judge_output=warmup,
        candidate_utterance_text="answer", elapsed_ms=500,
    )

    # Build a Judge output with the bogus -> failed observation.
    bogus_obs = Observation(
        signal_value="S_KO",
        anchor_id=0,                                  # POSITIVE anchor — illegal for ->failed
        evidence_quote="I use validators to enforce required actions",
        coverage_transition=CoverageTransition.sufficient_to_failed,
    )
    output = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[bogus_obs],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(
            probe_id="1",
        ),
        turn_metadata=TurnMetadata(),
    )

    decision = engine.process_judge_output(
        turn_id="t1", judge_output=output,
        candidate_utterance_text="I use validators...", elapsed_ms=1000,
    )

    # The bogus observation must be dropped: no knockout, lifecycle still active.
    assert engine.lifecycle_snapshot().knockout_failures == []
    assert engine.lifecycle_snapshot().state.value == "active"
    # The Judge's original action (probe) must survive — no policy override.
    assert decision.speaker_input.instruction_kind.value == "deliver_probe"
    # And the warning is recorded.
    codes = [w.code for w in decision.validation_warnings]
    assert "illegal_failure_observation" in codes


def test_repeat_replays_last_question_not_redirect():
    """Bug B — `_resolve_repeat` previously returned the most recent
    AGENT utterance regardless of kind. Now it must return the most
    recent QUESTION-bearing utterance (deliver_first_question /
    deliver_question / deliver_probe), skipping redirects, clarifies,
    polite_closes, etc."""
    engine = _engine()
    synthetic = engine.initialize_for_session_start()
    engine.process_judge_output(
        turn_id="t0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )

    # Simulate the orchestrator registering the first-question utterance.
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    engine.register_agent_utterance(
        turn_id="t0", text="Walk me through your Jira workflow.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    engine.register_agent_question_for_repeat(
        turn_id="t0", text="Walk me through your Jira workflow.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    # Then simulate a redirect utterance from a later turn.
    # redirect is not a question kind — register_agent_question_for_repeat would no-op,
    # so only the transcript call is needed here (matches orchestrator behavior).
    engine.register_agent_utterance(
        turn_id="t1",
        text="Let's stay on the Jira workflow side for now.",
        instruction_kind=InstructionKind.redirect,
    )

    # Now exercise repeat resolution.
    instruction, cached, source_turn = engine._resolve_repeat(warnings=[])
    assert instruction == InstructionKind.repeat
    assert cached == "Walk me through your Jira workflow."
    assert source_turn == "t0"


def test_redirect_action_maps_to_redirect_instruction_kind():
    """Task 8: NextAction.redirect dispatches to InstructionKind.redirect.

    Verifies the new collapsed redirect action wires through the State
    Engine without mutating ledger / queue / claims / lifecycle state.
    """
    from app.modules.interview_engine.models.judge import RedirectPayload

    eng = _engine()
    synthetic = eng.initialize_for_session_start()
    eng.process_judge_output(
        turn_id="t0", judge_output=synthetic,
        candidate_utterance_text=None, elapsed_ms=0,
    )
    output = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.redirect,
        next_action_payload=RedirectPayload(),
        turn_metadata=TurnMetadata(candidate_off_topic=True),
    )
    decision = eng.process_judge_output(
        turn_id="t1", judge_output=output,
        candidate_utterance_text="What's the salary?", elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.redirect
    # No state mutation — lifecycle unchanged.
    assert eng.lifecycle_snapshot().state.value == "active"


def test_non_knockout_signal_failure_does_not_record_knockout(make_session_config, make_question):
    """Failure on a non-knockout signal: NO KnockoutFailure recorded."""
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="What is your first question response?")],
        signals=["S_PLAIN"],
        knockout_signal=None,  # NO knockout signal
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S_PLAIN", anchor_id=-1,
                        evidence_quote="never used",
                        coverage_transition=CoverageTransition.none_to_failed),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S_PLAIN"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="never used", elapsed_ms=2000,
    )
    # No KnockoutFailure for non-knockout signal.
    assert len(eng.lifecycle_snapshot().knockout_failures) == 0
    # Action NOT overridden.
    assert decision.speaker_input.instruction_kind == InstructionKind.acknowledge_no_experience


def test_drift_guard_drops_failure_obs_when_action_is_clarify(
    make_session_config, make_question,
):
    """Bug E (session 33f044ce-fb25-4872-a85f-10c19fe7f253, turn 6).

    Candidate said "I didn't understand the question. Can you please
    elaborate?" The Judge correctly emitted `clarify` BUT also fabricated
    a `none→failed` observation on a knockout signal — citing the
    candidate's clarification request as the evidence. The State Engine
    used to record the knockout, fire the close_polite policy override,
    and end the session on someone just asking for help.

    The drift guard now drops `→failed` observations whose `next_action`
    is not `acknowledge_no_experience` or `polite_close`. The
    clarify action is preserved; the bogus failure obs never reaches the
    ledger; no knockout fires.
    """
    from app.modules.interview_engine.models.judge import (
        ClarifyPayload, CoverageTransition, JudgeOutput, NextAction,
        Observation, TurnMetadata,
    )
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", text="Walk me through workflow design."),
        ],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    bogus = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S_KO",
                anchor_id=-1,
                evidence_quote="I didn't understand the question. Can you please elaborate?",
                coverage_transition=CoverageTransition.none_to_failed,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=bogus,
        candidate_utterance_text="I didn't understand the question.",
        elapsed_ms=1000,
    )

    # The bogus failure observation is dropped — it never enters the ledger.
    assert eng.ledger_snapshot().entries == []
    # No knockout was recorded — the candidate is NOT being closed-out.
    assert eng.lifecycle_snapshot().knockout_failures == []
    # The clarify action is preserved — the candidate gets help.
    assert decision.speaker_input.instruction_kind == InstructionKind.clarify
    # Lifecycle stays active; no policy override fires.
    assert eng.lifecycle_snapshot().state.value == "active"
    # Drift was logged for audit visibility.
    assert any(
        w.code == "failure_obs_without_acknowledge_action"
        for w in decision.validation_warnings
    )


def test_drift_guard_does_not_fire_for_legitimate_acknowledge(
    make_session_config, make_question,
):
    """Negative control: when the Judge correctly pairs a `→failed`
    observation with `acknowledge_no_experience`, the drift guard MUST
    NOT fire and the failure SHOULD be recorded."""
    from app.modules.interview_engine.models.judge import (
        AcknowledgeNoExperiencePayload, CoverageTransition, JudgeOutput,
        NextAction, Observation, TurnMetadata,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Walk me through X.")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="record_only"),
    )
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S_KO",
                anchor_id=-1,
                evidence_quote="I've never used X.",
                coverage_transition=CoverageTransition.none_to_failed,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S_KO"),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="I've never used X.", elapsed_ms=1000,
    )

    # Legitimate failure obs: applied, knockout recorded.
    assert len(eng.ledger_snapshot().entries) == 1
    assert len(eng.lifecycle_snapshot().knockout_failures) == 1
    # Drift guard MUST NOT fire here.
    assert not any(
        w.code == "failure_obs_without_acknowledge_action"
        for w in decision.validation_warnings
    )


def test_drift_guard_drops_failure_obs_for_redirect_action(
    make_session_config, make_question,
):
    """Generalize beyond clarify — redirect with a fabricated failure
    obs is the same incoherent shape and must be dropped too."""
    from app.modules.interview_engine.models.judge import (
        CoverageTransition, JudgeOutput, NextAction, Observation,
        RedirectPayload, TurnMetadata,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Walk me through X.")],
        signals=["S_KO"],
        knockout_signal="S_KO",
    )
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(knockout_policy="close_polite"),
    )
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    bogus = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S_KO",
                anchor_id=-1,
                evidence_quote="What is the salary?",
                coverage_transition=CoverageTransition.none_to_failed,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.redirect,
        next_action_payload=RedirectPayload(),
        turn_metadata=TurnMetadata(candidate_off_topic=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=bogus,
        candidate_utterance_text="What is the salary?", elapsed_ms=1000,
    )

    assert eng.ledger_snapshot().entries == []
    assert eng.lifecycle_snapshot().knockout_failures == []
    assert decision.speaker_input.instruction_kind == InstructionKind.redirect
    assert eng.lifecycle_snapshot().state.value == "active"


def test_acknowledge_without_failure_obs_downgrades_to_clarify(
    make_session_config, make_question,
):
    """Bug F (session 06013de7-8e33-4eb5-8edc-f67470aa8a64, turn 6).

    The Judge mis-classified a substantive candidate answer as a
    no-experience disclosure, emitting `acknowledge_no_experience`
    with a `→failed` observation that anchored to a positive anchor_id.
    The existing illegal_failure_observation guard dropped the bogus
    obs, BUT the action survived — leaving the Speaker asked to
    acknowledge a non-existent disclosure. Speaker emitted nothing
    against the contradictory inputs.

    Inverse-coupling guard: when every `→failed` observation gets
    dropped (so no failure entered the ledger), downgrade the action
    to clarify so the Speaker rephrases the question rather than
    fabricating an acknowledgement.
    """
    from app.modules.interview_engine.models.judge import (
        AcknowledgeNoExperiencePayload, CoverageTransition, JudgeOutput,
        NextAction, Observation, TurnMetadata,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Walk me through X.")],
        signals=["S1"],
    )
    eng = StateEngine(session_config=cfg)
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    # Bogus shape: action=acknowledge_no_experience, but the only
    # failure obs has anchor_id=3 (illegal — →failed requires sentinel)
    # so the existing guard drops it. Nothing survives → no real failure.
    bogus = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S1",
                anchor_id=3,  # POSITIVE anchor with →failed → dropped by existing guard
                evidence_quote="I would use shared schemes and pilots",
                coverage_transition=CoverageTransition.sufficient_to_failed,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S1"),
        # Setting the no-experience flag is ALSO load-bearing here:
        # the existing model_validator only fails when the flag is set
        # AND the action is wrong; flag set + acknowledge_no_experience
        # passes the validator, exposing the inverse-coupling gap that
        # the new drift guard plugs.
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=bogus,
        candidate_utterance_text="I would use shared schemes and pilots.",
        elapsed_ms=1000,
    )

    # Existing guard fired (illegal_failure_observation) AND the new guard
    # fired (acknowledge_without_failure_obs).
    codes = [w.code for w in decision.validation_warnings]
    assert "illegal_failure_observation" in codes
    assert "acknowledge_without_failure_obs" in codes

    # The action was downgraded — speaker is told to clarify, NOT acknowledge.
    assert decision.speaker_input.instruction_kind == InstructionKind.clarify

    # No failure entered the ledger (it was dropped before this guard ran).
    assert eng.ledger_snapshot().entries == []

    # No knockout was recorded; lifecycle stays active.
    assert eng.lifecycle_snapshot().knockout_failures == []
    assert eng.lifecycle_snapshot().state.value == "active"


def test_acknowledge_with_real_failure_obs_does_not_downgrade(
    make_session_config, make_question,
):
    """Negative control: when the Judge correctly emits
    acknowledge_no_experience with a valid `→failed` observation
    (sentinel anchor_id=-1), the inverse-coupling guard MUST NOT fire."""
    from app.modules.interview_engine.models.judge import (
        AcknowledgeNoExperiencePayload, CoverageTransition, JudgeOutput,
        NextAction, Observation, TurnMetadata,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Walk me through X.")],
        signals=["S1"],
    )
    eng = StateEngine(session_config=cfg)
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S1", anchor_id=-1,
                evidence_quote="I've never used X",
                coverage_transition=CoverageTransition.none_to_failed,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S1"),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="I've never used X.", elapsed_ms=1000,
    )

    codes = [w.code for w in decision.validation_warnings]
    assert "acknowledge_without_failure_obs" not in codes
    assert decision.speaker_input.instruction_kind == InstructionKind.acknowledge_no_experience
    assert len(eng.ledger_snapshot().entries) == 1


def test_acknowledge_with_no_observations_at_all_downgrades(
    make_session_config, make_question,
):
    """Edge case: Judge emits acknowledge_no_experience with empty
    observations[] and the no-experience flag set. The Pydantic
    validator passes (flag + action are aligned), but there's still no
    actual failure recorded. Downgrade to clarify."""
    from app.modules.interview_engine.models.judge import (
        AcknowledgeNoExperiencePayload, JudgeOutput, NextAction, TurnMetadata,
    )
    cfg = make_session_config(
        questions=[make_question(qid="q1", text="Walk me through X.")],
        signals=["S1"],
    )
    eng = StateEngine(session_config=cfg)
    eng.process_judge_output(
        turn_id="t-0", judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )

    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],  # ← no failure obs at all
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S1"),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="something", elapsed_ms=1000,
    )

    codes = [w.code for w in decision.validation_warnings]
    assert "acknowledge_without_failure_obs" in codes
    assert decision.speaker_input.instruction_kind == InstructionKind.clarify


# ---------------------------------------------------------------------------
# Phase 9.2 — push_back action handling, quality gate, cap behavior
# ---------------------------------------------------------------------------


def _judge_push_back(reason_code: str, observations=None) -> JudgeOutput:
    from app.modules.interview_engine.models.judge import (
        CoverageQuality, PushBackPayload,
    )
    return JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=observations or [],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code=reason_code),
        turn_metadata=TurnMetadata(),
    )


def _judge_advance_with_quality(
    target: str, quality_value: str, signal: str = "S1",
    transition: CoverageTransition = CoverageTransition.none_to_sufficient,
) -> JudgeOutput:
    """Advance with one observation marked at the given quality grade.

    Default transition is ``none→sufficient`` because the test fixture
    starts both S1 and S2 at coverage=none. Tests that have already moved
    a signal forward can override ``transition`` to keep the LHS state
    consistent with the ledger.
    """
    from app.modules.interview_engine.models.judge import CoverageQuality
    return JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value=signal,
                anchor_id=0,
                evidence_quote="some evidence",
                coverage_transition=transition,
                quality=CoverageQuality(quality_value),
            )
        ],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id=target),
        turn_metadata=TurnMetadata(),
    )


def _activate_q1(eng: StateEngine) -> None:
    """Helper: synthesize the session-start advance to put q1 active."""
    eng.process_judge_output(
        turn_id="t-start",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )


def test_push_back_action_increments_count_no_queue_mutation():
    """push_back leaves the queue position alone (no advance, no probe
    consumption) and bumps push_back_count on the active question."""
    eng = _engine()
    _activate_q1(eng)

    decision = eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="validation checks",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.push_back
    assert decision.speaker_input.push_back_reason_code == "vague_answer"
    snap = eng.queue_snapshot()
    assert snap.questions[snap.active_index].push_back_count == 1
    # Active question unchanged, no probes consumed (queue stores probe
    # IDs as integer strings "0", "1", ... — the fu0/fu1 strings in
    # the fixture are follow-up texts, not IDs).
    assert snap.active_index == 0
    assert snap.questions[0].probes_remaining_ids == ["0", "1"]


def test_push_back_at_cap_downgrades_to_advance():
    """3rd incoming push_back on the same question is downgraded to advance
    (or polite_close if no mandatory remains). Emits push_back_cap_reached."""
    eng = _engine()
    _activate_q1(eng)

    # Two push_backs accepted.
    eng.process_judge_output(
        turn_id="t-1", judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="thin", elapsed_ms=1000,
    )
    eng.process_judge_output(
        turn_id="t-2", judge_output=_judge_push_back("missing_specifics"),
        candidate_utterance_text="still thin", elapsed_ms=2000,
    )
    assert eng.queue_snapshot().questions[0].push_back_count == 2

    # Third push_back should be downgraded.
    decision = eng.process_judge_output(
        turn_id="t-3", judge_output=_judge_push_back("deflection"),
        candidate_utterance_text="still thin", elapsed_ms=3000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "push_back_cap_reached" in codes
    # Downgraded to advance — moves queue forward to q2.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert eng.queue_snapshot().active_index == 1


def test_push_back_at_cap_with_no_mandatory_remaining_polite_closes():
    """When the cap is hit on the LAST mandatory question, the fallback
    chain ends at polite_close with last_outcome=completed."""
    eng = _engine()
    # Advance straight through q1 to q2 with concrete obs so the gate passes.
    _activate_q1(eng)
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_advance_with_quality("q2", "concrete"),
        candidate_utterance_text="real answer",
        elapsed_ms=1000,
    )
    # Now active=q2, push it three times to trip the cap on the final mandatory.
    for i in range(3):
        decision = eng.process_judge_output(
            turn_id=f"t-pb-{i}",
            judge_output=_judge_push_back("vague_answer"),
            candidate_utterance_text="thin",
            elapsed_ms=2000 + i * 1000,
        )
    codes = [w.code for w in decision.validation_warnings]
    assert "push_back_cap_reached" in codes
    # No mandatory remains -> fallback to polite_close.
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert eng.lifecycle_snapshot().state.value == "closing"


def test_advance_with_only_thin_observations_is_downgraded_to_push_back():
    """LOAD-BEARING: the bug from session 4cf43291 turn 18. The Judge
    advanced after deflection-as-evidence; the State Engine now downgrades
    to push_back missing_specifics so the Speaker asks for one concrete
    piece."""
    eng = _engine()
    _activate_q1(eng)

    # Advance with quality=thin should be downgraded.
    decision = eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_advance_with_quality("q2", "thin"),
        candidate_utterance_text="not my responsibility but I helped",
        elapsed_ms=1000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "quality_gated_advance" in codes
    assert decision.speaker_input.instruction_kind == InstructionKind.push_back
    assert decision.speaker_input.push_back_reason_code == "missing_specifics"
    # Still on q1, but push_back_count incremented.
    snap = eng.queue_snapshot()
    assert snap.active_index == 0
    assert snap.questions[0].push_back_count == 1


def test_advance_with_one_concrete_observation_is_honored():
    """Inverse: one concrete obs is enough to pass the gate cleanly."""
    eng = _engine()
    _activate_q1(eng)

    decision = eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_advance_with_quality("q2", "concrete"),
        candidate_utterance_text="I built a workflow validator with ScriptRunner",
        elapsed_ms=1000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "quality_gated_advance" not in codes
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert eng.queue_snapshot().active_index == 1


def test_advance_with_one_strong_observation_is_honored():
    """`strong` quality also passes the gate."""
    eng = _engine()
    _activate_q1(eng)

    decision = eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_advance_with_quality("q2", "strong"),
        candidate_utterance_text="logged via SLF4J to Splunk with sampling",
        elapsed_ms=1000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "quality_gated_advance" not in codes
    assert eng.queue_snapshot().active_index == 1


def test_advance_with_mixed_qualities_is_honored():
    """Multiple obs in one turn, at least one concrete -> advance honored."""
    from app.modules.interview_engine.models.judge import CoverageQuality
    eng = _engine()
    _activate_q1(eng)

    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S1", anchor_id=0, evidence_quote="thin1",
                        coverage_transition=CoverageTransition.none_to_partial,
                        quality=CoverageQuality.thin),
            Observation(signal_value="S1", anchor_id=2, evidence_quote="concrete1",
                        coverage_transition=CoverageTransition.partial_to_sufficient,
                        quality=CoverageQuality.concrete),
        ],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q2"),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="answer", elapsed_ms=1000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "quality_gated_advance" not in codes
    assert eng.queue_snapshot().active_index == 1


def test_quality_gate_bypassed_at_push_back_cap():
    """Once push_back_count is at the cap, the quality gate stops firing
    (otherwise we'd loop forever on a candidate who can't give specifics)."""
    eng = _engine()
    _activate_q1(eng)
    # Drive count to 2 with two push_backs.
    for i in range(2):
        eng.process_judge_output(
            turn_id=f"t-pb-{i}", judge_output=_judge_push_back("vague_answer"),
            candidate_utterance_text="thin", elapsed_ms=1000 + i * 1000,
        )

    # Now an advance with all-thin obs should be honored (cap escape valve).
    decision = eng.process_judge_output(
        turn_id="t-adv",
        judge_output=_judge_advance_with_quality("q2", "thin"),
        candidate_utterance_text="still thin",
        elapsed_ms=4000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "quality_gated_advance" not in codes, (
        "quality gate must NOT downgrade once push_back_count is at cap"
    )
    assert eng.queue_snapshot().active_index == 1


def test_quality_observation_counts_recorded_per_grade():
    """Every applied observation increments quality_observations[quality]."""
    from app.modules.interview_engine.models.judge import CoverageQuality
    eng = _engine()
    _activate_q1(eng)

    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(signal_value="S1", anchor_id=0, evidence_quote="t",
                        coverage_transition=CoverageTransition.none_to_partial,
                        quality=CoverageQuality.thin),
            Observation(signal_value="S1", anchor_id=2, evidence_quote="c",
                        coverage_transition=CoverageTransition.partial_to_partial,
                        quality=CoverageQuality.concrete),
        ],
        candidate_claims=[],
        next_action=NextAction.probe,
        next_action_payload=ProbePayload(probe_id="0"),
        turn_metadata=TurnMetadata(),
    )
    eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="answer", elapsed_ms=1000,
    )
    snap = eng.queue_snapshot()
    counts = snap.questions[0].quality_observations
    assert counts.get("thin") == 1
    assert counts.get("concrete") == 1


def test_quality_gate_skips_synthetic_session_start():
    """The session-start synthetic advance has no active question yet —
    the gate must not fire on it."""
    eng = _engine()
    decision = eng.process_judge_output(
        turn_id="t-start",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "quality_gated_advance" not in codes
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_first_question


def test_push_back_cap_bookkeeping_resets_per_question():
    """push_back_count is per-question; advancing to a new question starts
    fresh at 0 on the new question."""
    eng = _engine()
    _activate_q1(eng)
    # Push q1 once.
    eng.process_judge_output(
        turn_id="t-1", judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="thin", elapsed_ms=1000,
    )
    assert eng.queue_snapshot().questions[0].push_back_count == 1
    # Advance to q2 with concrete obs.
    eng.process_judge_output(
        turn_id="t-2",
        judge_output=_judge_advance_with_quality("q2", "concrete"),
        candidate_utterance_text="real answer", elapsed_ms=2000,
    )
    snap = eng.queue_snapshot()
    assert snap.active_index == 1
    # q2 starts fresh at 0; q1's count is preserved on the completed entry.
    assert snap.questions[1].push_back_count == 0
    assert snap.questions[0].push_back_count == 1


# ---------------------------------------------------------------------------
# Phase 9.3 — Q-2: cap-forced advance flag on SpeakerInput
# ---------------------------------------------------------------------------


def test_cap_forced_advance_sets_is_post_cap_advance_on_speaker_input():
    """When push_back hits cap=2 and downgrades to advance, the resulting
    deliver_question SpeakerInput MUST carry is_post_cap_advance=True so
    the Speaker scaffold adds a topic-shift segue instead of a normal
    'Got it' acknowledgement."""
    eng = _engine()
    _activate_q1(eng)
    # Drive count to 2.
    for i in range(2):
        eng.process_judge_output(
            turn_id=f"t-pb-{i}",
            judge_output=_judge_push_back("vague_answer"),
            candidate_utterance_text="thin", elapsed_ms=1000 + i * 1000,
        )
    # Third push_back -> cap downgrade -> deliver_question on q2.
    decision = eng.process_judge_output(
        turn_id="t-pb-cap",
        judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="still thin", elapsed_ms=4000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_cap_advance is True


def test_clean_advance_does_not_set_is_post_cap_advance():
    """Inverse — a normal advance (with concrete obs) does NOT mark the
    SpeakerInput as post-cap. The Speaker uses the normal acknowledgment."""
    eng = _engine()
    _activate_q1(eng)
    decision = eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_advance_with_quality("q2", "concrete"),
        candidate_utterance_text="real concrete answer",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_cap_advance is False


# ---------------------------------------------------------------------------
# Phase 9.3 — Q-3: knockout-aware polite_close threads failed_signal_value
# ---------------------------------------------------------------------------


def test_knockout_policy_override_threads_failed_signal_to_polite_close():
    """When a knockout fires under close_polite policy, the resulting
    polite_close SpeakerInput carries the failed signal so the scaffold
    can acknowledge the disclosure."""
    from app.modules.interview_engine.models.judge import CoverageQuality
    cfg = _config()  # S2 is the knockout signal
    eng = StateEngine(
        session_config=cfg,
        config=StateEngineConfig(
            claims_pool_max=50, knockout_policy="close_polite",
        ),
    )
    # Activate q1 via the synthetic.
    eng.process_judge_output(
        turn_id="t-0",
        judge_output=eng.initialize_for_session_start(),
        candidate_utterance_text=None, elapsed_ms=0,
    )
    # Advance to q2 (which targets the knockout signal S2) with a concrete
    # observation on q1 so the gate doesn't downgrade.
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_advance_with_quality("q2", "concrete"),
        candidate_utterance_text="answer", elapsed_ms=1000,
    )
    # Candidate discloses no experience on q2's knockout signal S2.
    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="S2", anchor_id=-1,
                evidence_quote="I don't have any experience with that.",
                coverage_transition=CoverageTransition.none_to_failed,
                quality=CoverageQuality.concrete,
            ),
        ],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="S2"),
        turn_metadata=TurnMetadata(
            candidate_disclosed_no_experience=True,
            candidate_disclosed_knockout=True,
        ),
    )
    decision = eng.process_judge_output(
        turn_id="t-2", judge_output=j,
        candidate_utterance_text="I don't have any experience with that.",
        elapsed_ms=2000,
    )
    codes = [w.code for w in decision.validation_warnings]
    assert "knockout_policy_override" in codes
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert decision.speaker_input.failed_signal_value == "S2", (
        "polite_close after knockout must carry failed_signal_value so "
        "the Speaker scaffold can acknowledge the disclosure"
    )


def test_clean_polite_close_has_no_failed_signal_value():
    """A non-knockout polite_close (all mandatory complete) leaves
    failed_signal_value=None — the Speaker takes the clean-completion
    branch in polite_close.txt."""
    from app.modules.interview_engine.models.judge import PoliteClosePayload
    eng = _engine()
    _activate_q1(eng)

    j = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.polite_close,
        next_action_payload=PoliteClosePayload(),
        turn_metadata=TurnMetadata(),
    )
    decision = eng.process_judge_output(
        turn_id="t-1", judge_output=j,
        candidate_utterance_text="That covers everything.",
        elapsed_ms=1000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.polite_close
    assert decision.speaker_input.failed_signal_value is None


# ---------------------------------------------------------------------------
# recent_reply_starts — anti-repetition signal extracted from transcript
# ---------------------------------------------------------------------------


def test_recent_reply_starts_extracted_from_transcript():
    """The Speaker for non-contextual kinds receives the first 4 words
    of the last 3 agent utterances so it can vary its reply opening
    across consecutive redirects/push_backs."""
    eng = _engine()
    # Synthesize 4 agent utterances directly via register_agent_utterance.
    for i, text in enumerate([
        "First utterance text.",
        "I hear you, please continue with the question.",
        "Got it, Ishant, walk me through the rest.",
        "Sure, let's stay focused on the topic.",
    ]):
        eng.register_agent_utterance(
            turn_id=f"t-{i}", text=text,
            instruction_kind=InstructionKind.redirect,
        )
    # _RECENT_REPLY_WINDOW is 3 → only the last 3 are returned.
    starts = eng._recent_reply_starts()
    assert len(starts) == 3
    assert starts[0] == "I hear you, please"
    assert starts[1] == "Got it, Ishant, walk"
    assert starts[2] == "Sure, let's stay focused"


def test_recent_reply_starts_empty_at_session_start():
    """No agent utterances yet -> empty list. Speaker scaffold treats
    this as 'any opening is fine'."""
    eng = _engine()
    assert eng._recent_reply_starts() == []


# ---------------------------------------------------------------------------
# Phase 9.4 — Fix #2: consecutive_dont_know_count + I-don't-know detection
# ---------------------------------------------------------------------------


def test_dont_know_regex_matches_common_phrasings():
    """The regex must match the common 'I don't know' family verbatim
    so the State Engine increments the counter correctly."""
    from app.modules.interview_engine.state.engine import _is_dont_know_utterance
    POSITIVE = [
        "I don't know.",
        "I don't know",
        "I dont know",
        "I don't know how to answer that.",
        "I don't know how to answer.",
        "I'm not sure.",
        "I'm not sure how to answer.",
        "I have no idea.",
        "no idea",
        "Don't know.",
    ]
    for utt in POSITIVE:
        assert _is_dont_know_utterance(utt), f"{utt!r} should match"


def test_dont_know_regex_does_not_match_substantive_answers():
    """Conservative anchoring — utterances that mention 'don't know'
    embedded in a real answer must NOT count as I-don't-know."""
    from app.modules.interview_engine.state.engine import _is_dont_know_utterance
    NEGATIVE = [
        "I don't know the exact API but I'd look at the docs first.",
        "I know how to write a validator.",
        "I don't know off the top of my head — let me think.",
        "I would add validators to the workflow.",
        "Yes, I have done that.",
        "",
    ]
    for utt in NEGATIVE:
        assert not _is_dont_know_utterance(utt), f"{utt!r} must NOT match"


def test_dont_know_count_increments_on_match():
    """State Engine bumps consecutive_dont_know_count on each matching
    utterance against the active question. Reset on substantive answer."""
    eng = _engine()
    _activate_q1(eng)
    # Three "I don't know" responses in a row.
    for i, utt in enumerate(["I don't know.", "I'm not sure.", "no idea"]):
        eng.process_judge_output(
            turn_id=f"t-{i}",
            judge_output=_judge_push_back("vague_answer"),
            candidate_utterance_text=utt, elapsed_ms=1000 + i * 1000,
        )
        snap = eng.queue_snapshot()
        assert snap.questions[0].consecutive_dont_know_count == i + 1, (
            f"After utterance {i+1} the count must be {i+1}"
        )


def test_dont_know_count_resets_on_substantive_answer():
    """Any non-I-don't-know utterance breaks the streak."""
    eng = _engine()
    _activate_q1(eng)
    # Two I-don't-know.
    for i, utt in enumerate(["I don't know.", "I'm not sure."]):
        eng.process_judge_output(
            turn_id=f"t-{i}",
            judge_output=_judge_push_back("vague_answer"),
            candidate_utterance_text=utt, elapsed_ms=1000 + i * 1000,
        )
    assert eng.queue_snapshot().questions[0].consecutive_dont_know_count == 2

    # Substantive answer.
    eng.process_judge_output(
        turn_id="t-real",
        judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="I would write a validator that checks PR status.",
        elapsed_ms=4000,
    )
    assert eng.queue_snapshot().questions[0].consecutive_dont_know_count == 0


def test_dont_know_count_threaded_into_orchestrator_judge_input():
    """The orchestrator reads consecutive_dont_know_count off the active
    QuestionState and passes it to build_judge_input — without this, the
    Judge prompt's escalation rule has no signal to fire on."""
    # This is exercised via the orchestrator's on_user_turn_completed path,
    # which is composition-tested elsewhere. Here we just verify the
    # State Engine -> queue field is correctly readable from the snapshot.
    eng = _engine()
    _activate_q1(eng)
    eng.process_judge_output(
        turn_id="t-1",
        judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="I don't know.", elapsed_ms=1000,
    )
    snap = eng.queue_snapshot()
    assert snap.active_index == 0
    # Orchestrator reads exactly this field for the Judge input.
    assert snap.questions[snap.active_index].consecutive_dont_know_count == 1


# ---------------------------------------------------------------------------
# Phase 9.6 — Bug C: push_back + clarify enter the repeat cache
# ---------------------------------------------------------------------------


def test_repeat_after_push_back_replays_push_back_text():
    """The candidate's last-heard question was the push_back drilling
    question. A subsequent 'repeat' must replay the push_back text, NOT
    the original bank question. (Session 403e7d45 turn 2 regression.)"""
    eng = _engine()
    _activate_q1(eng)
    # Q1 was delivered as deliver_first_question — this is the original
    # cache entry.
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-q1", text="What is q1 please?",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-q1", text="What is q1 please?",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    push_back_text = "Got it — which specific issue types would you define?"
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-pb", text=push_back_text,
        instruction_kind=InstructionKind.push_back,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-pb", text=push_back_text,
        instruction_kind=InstructionKind.push_back,
    )
    # Internal contract: push_back text IS in the repeat cache.
    assert eng._question_utterances.get("t-pb") == push_back_text
    # _resolve_repeat returns the most recent cache entry (push_back).
    instruction, cached, source = eng._resolve_repeat(warnings=[])
    assert instruction == InstructionKind.repeat
    assert cached == push_back_text
    assert source == "t-pb"


def test_repeat_after_clarify_replays_clarify_text():
    """The candidate's last-heard question was the clarify rephrase. A
    subsequent 'repeat' must replay the clarify text. (Session 403e7d45
    turn 5 regression.)"""
    eng = _engine()
    _activate_q1(eng)
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-q1", text="Original Q1 wording.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-q1", text="Original Q1 wording.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    clarify_text = "Sure, let me rephrase. Imagine a client tells you..."
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-cl", text=clarify_text,
        instruction_kind=InstructionKind.clarify,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-cl", text=clarify_text,
        instruction_kind=InstructionKind.clarify,
    )
    instruction, cached, source = eng._resolve_repeat(warnings=[])
    assert instruction == InstructionKind.repeat
    assert cached == clarify_text
    assert source == "t-cl"


def test_repeat_skips_redirect_intervening_between_clarify_and_repeat():
    """Even when a redirect happened between the clarify and the
    repeat (e.g., candidate cursed at the agent before asking repeat),
    the cache replays the clarify — redirect is excluded from
    _QUESTION_KINDS so it never enters the cache."""
    eng = _engine()
    _activate_q1(eng)
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-q1", text="Q1 wording.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-q1", text="Q1 wording.",
        instruction_kind=InstructionKind.deliver_first_question,
    )
    clarify_text = "Let me rephrase. <clarify content>"
    # Phase 9.9 — orchestrator now calls both methods (transcript + cache).
    eng.register_agent_utterance(
        turn_id="t-cl", text=clarify_text,
        instruction_kind=InstructionKind.clarify,
    )
    eng.register_agent_question_for_repeat(
        turn_id="t-cl", text=clarify_text,
        instruction_kind=InstructionKind.clarify,
    )
    # redirect is not a question kind — register_agent_question_for_repeat would no-op,
    # so only the transcript call is needed here (matches orchestrator behavior).
    eng.register_agent_utterance(
        turn_id="t-redir", text="Let's keep this professional.",
        instruction_kind=InstructionKind.redirect,
    )
    # The redirect is NOT in the cache; the most recent cache entry
    # is still the clarify.
    assert "t-redir" not in eng._question_utterances
    instruction, cached, source = eng._resolve_repeat(warnings=[])
    assert cached == clarify_text
    assert source == "t-cl"


def test_redirect_acknowledge_polite_close_still_excluded_from_cache():
    """Anti-regression: the kinds that were excluded BEFORE Bug C must
    stay excluded. Caching a redirect/acknowledge/polite_close would
    re-introduce the original Bug B (replay redirect text on repeat)."""
    eng = _engine()
    _activate_q1(eng)
    for tid, kind in (
        ("t-r", InstructionKind.redirect),
        ("t-a", InstructionKind.acknowledge_no_experience),
        ("t-p", InstructionKind.polite_close),
    ):
        eng.register_agent_utterance(
            turn_id=tid, text=f"text for {kind.value}",
            instruction_kind=kind,
        )
        assert tid not in eng._question_utterances, (
            f"{kind.value}: must NOT be cached for repeat replay"
        )


def test_question_kinds_set_includes_push_back_and_clarify():
    """Locks the set of question-bearing kinds. Adding/removing entries
    here changes the repeat behavior; the test guards both directions."""
    expected = {
        InstructionKind.deliver_first_question,
        InstructionKind.deliver_question,
        InstructionKind.deliver_probe,
        InstructionKind.push_back,
        InstructionKind.clarify,
    }
    assert StateEngine._QUESTION_KINDS == frozenset(expected)


def test_register_agent_question_for_repeat_writes_for_question_kinds_with_non_empty_text(
    make_session_config, make_question,
):
    """Happy path: a question-bearing kind with non-empty text updates
    the repeat cache."""
    cfg = make_session_config(questions=[make_question(qid="q1")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="What is your favorite tool?",
        instruction_kind=InstructionKind.deliver_question,
    )
    assert engine._question_utterances["t-1"] == "What is your favorite tool?"


def test_register_agent_question_for_repeat_skips_empty_text(
    make_session_config, make_question,
):
    """An empty text MUST NOT update the cache (Phase 9.9 contract).
    The interrupted/empty Speaker handlers depend on this — if they
    pollute the cache with empty entries, NextAction.repeat replays
    silence and the candidate hears nothing."""
    cfg = make_session_config(questions=[make_question(qid="q1")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="",
        instruction_kind=InstructionKind.push_back,
    )
    assert "t-1" not in engine._question_utterances


def test_register_agent_question_for_repeat_skips_whitespace_only(
    make_session_config, make_question,
):
    """Whitespace-only counts as empty for cache purposes."""
    cfg = make_session_config(questions=[make_question(qid="q1")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="   \n  ",
        instruction_kind=InstructionKind.deliver_question,
    )
    assert "t-1" not in engine._question_utterances


def test_register_agent_question_for_repeat_skips_non_question_kinds(
    make_session_config, make_question,
):
    """Non-question kinds (redirect, repeat, polite_close,
    acknowledge_no_experience) MUST NOT enter the repeat cache —
    same as today's contract on the underlying _QUESTION_KINDS filter."""
    cfg = make_session_config(questions=[make_question(qid="q1")])
    engine = StateEngine(session_config=cfg)
    for non_q_kind in [
        InstructionKind.redirect, InstructionKind.polite_close,
        InstructionKind.acknowledge_no_experience,
    ]:
        engine.register_agent_question_for_repeat(
            turn_id=f"t-{non_q_kind.value}", text="something",
            instruction_kind=non_q_kind,
        )
        assert f"t-{non_q_kind.value}" not in engine._question_utterances


def test_register_agent_utterance_no_longer_updates_cache(
    make_session_config, make_question,
):
    """Phase 9.9 contract: register_agent_utterance is transcript-only.
    The repeat cache update was hoisted into a separate method
    (register_agent_question_for_repeat). Confirm a single call to
    register_agent_utterance no longer touches _question_utterances."""
    cfg = make_session_config(questions=[make_question(qid="q1")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_utterance(
        turn_id="t-1", text="A real agent question",
        instruction_kind=InstructionKind.deliver_question,
    )
    # Transcript IS appended.
    assert engine._transcript[-1].text == "A real agent question"
    # Cache is NOT touched.
    assert "t-1" not in engine._question_utterances


def test_register_agent_utterance_appends_empty_text_to_transcript(
    make_session_config, make_question,
):
    """Empty text is a valid transcript fact (the agent emitted nothing
    on this turn — recorded for forensic completeness alongside the
    speaker.interrupted / speaker.output.empty audit event)."""
    cfg = make_session_config(questions=[make_question(qid="q1")])
    engine = StateEngine(session_config=cfg)
    engine.register_agent_utterance(
        turn_id="t-1", text="",
        instruction_kind=InstructionKind.push_back,
    )
    assert engine._transcript[-1].text == ""
    assert "t-1" not in engine._question_utterances


# ---------------------------------------------------------------------------
# Phase 9.3 — Q-2 (addendum): Judge-voluntary advance at cap fires flag
# (Session a13ec188 T8 reproducer)
# ---------------------------------------------------------------------------


def test_is_post_cap_advance_fires_on_judge_voluntary_advance_at_cap() -> None:
    """Session a13ec188 T8 reproducer: Judge picked advance VOLUNTARILY
    knowing push_back_count was already at cap=2 ("the cleanest move is
    to advance to the next mandatory question"). The SE-forced downgrade
    path (push_back branch) never ran — so is_post_cap_advance stayed
    False and the Speaker had to discover the topic shift on its own.

    After the fix, the NextAction.advance branch captures
    prior_push_back_count BEFORE the queue mutation; if >= 2 it sets
    is_post_cap_advance=True. Speaker scaffold uses the flag to emit
    the topic-shift segue ("Thanks for that. Now —") instead of a
    cold jump.

    Spec: docs/superpowers/specs/2026-05-18-speaker-intent-layer-design.md §9
    """
    eng = _engine()
    _activate_q1(eng)

    # Drive two thin push_backs on Q1 so push_back_count reaches cap=2.
    # Both must carry thin observations only — concrete observations would
    # trigger the inverse_quality_gate and downgrade push_back→probe.
    for i in range(2):
        eng.process_judge_output(
            turn_id=f"t-pb-{i}",
            judge_output=_judge_push_back("vague_answer"),
            candidate_utterance_text="thin answer",
            elapsed_ms=1000 + i * 1000,
        )

    # Confirm push_back_count is now 2 (at the cap).
    assert eng.queue_snapshot().questions[0].push_back_count == 2

    # Now the Judge picks advance VOLUNTARILY — not a third push_back.
    # push_back_count < 2 check in quality_gated_advance is False, so
    # no quality downgrade fires. The advance goes through the try block.
    decision = eng.process_judge_output(
        turn_id="t-voluntary-advance",
        judge_output=_judge_advance_with_quality("q2", "thin"),
        candidate_utterance_text="another thin one",
        elapsed_ms=3000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_cap_advance is True, (
        "Judge picked advance with push_back_count already at cap; "
        "Speaker must receive is_post_cap_advance=True so it adds the "
        "topic-shift segue (per design 2026-05-18 §9)."
    )


def test_is_post_cap_advance_false_on_clean_advance_below_cap() -> None:
    """Sanity: advance with push_back_count < 2 (just 1 prior push_back)
    must NOT raise the flag. Speaker uses its normal acknowledgment path."""
    eng = _engine()
    _activate_q1(eng)

    # One thin push_back — count is now 1, below cap=2.
    eng.process_judge_output(
        turn_id="t-pb-0",
        judge_output=_judge_push_back("vague_answer"),
        candidate_utterance_text="partial answer",
        elapsed_ms=1000,
    )
    assert eng.queue_snapshot().questions[0].push_back_count == 1

    # Advance with a concrete observation — quality gate passes cleanly.
    decision = eng.process_judge_output(
        turn_id="t-advance",
        judge_output=_judge_advance_with_quality("q2", "concrete"),
        candidate_utterance_text="now a concrete answer",
        elapsed_ms=2000,
    )
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_question
    assert decision.speaker_input.is_post_cap_advance is False


