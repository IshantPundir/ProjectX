import pytest
from pydantic import ValidationError

from app.modules.interview_engine.models.judge import (
    AcknowledgeNoExperiencePayload,
    AdvancePayload,
    ClarifyPayload,
    ClarifyKind,
    ClaimEntry as JudgeClaimEntry,
    CoverageQuality,
    CoverageTransition,
    EndSessionPayload,
    JudgeOutput,
    NextAction,
    Observation,
    PoliteClosePayload,
    ProbePayload,
    PushBackPayload,
    RepeatPayload,
    TurnMetadata,
)


def test_next_action_values():
    expected = {
        "advance",
        "probe",
        "clarify",
        "repeat",
        "redirect",
        "acknowledge_no_experience",
        "polite_close",
        "end_session",
        "push_back",
    }
    assert {a.value for a in NextAction} == expected


def test_coverage_transition_includes_failure_branches():
    transitions = {t.value for t in CoverageTransition}
    assert "none→partial" in transitions
    assert "partial→partial" in transitions
    assert "partial→sufficient" in transitions
    assert "none→sufficient" in transitions
    assert "none→failed" in transitions
    assert "partial→failed" in transitions
    assert "sufficient→failed" in transitions
    assert "failed→failed" in transitions
    # No "strong" — verify nothing leaked back in.
    assert not any("strong" in t for t in transitions)


def test_observation_no_confidence_field():
    """Per locked design: confidence was removed as wasted tokens."""
    obs = Observation(
        signal_value="ScriptRunner expertise",
        anchor_id=0,
        evidence_quote="I built a custom validator with ScriptRunner.",
        coverage_transition=CoverageTransition.none_to_partial,
    )
    assert not hasattr(obs, "confidence")


def test_observation_anchor_id_negative_for_failure():
    obs = Observation(
        signal_value="JQL fluency",
        anchor_id=-1,
        evidence_quote="I've never used JQL.",
        coverage_transition=CoverageTransition.none_to_failed,
    )
    assert obs.anchor_id == -1


def test_judge_claim_entry_no_capture_metadata():
    """Judge emits a narrower shape; State Engine adds captured_at_*."""
    claim = JudgeClaimEntry(
        claim_topic="automation",
        claim_text="Built CI pipelines for 50+ services.",
        source_quote="I built CI pipelines for over fifty services.",
    )
    assert not hasattr(claim, "captured_at_turn")
    assert not hasattr(claim, "captured_at_seq")


def test_turn_metadata_defaults_all_false():
    meta = TurnMetadata()
    for attr in (
        "candidate_disclosed_no_experience",
        "candidate_disclosed_knockout",
        "candidate_off_topic",
        "candidate_abusive",
        "candidate_attempted_injection",
        "candidate_wants_to_end",
    ):
        assert getattr(meta, attr) is False


def test_advance_payload_kind_constant():
    p = AdvancePayload(target_question_id="q-1")
    assert p.kind == "advance"


def test_probe_payload_requires_id():
    """probe_rationale was removed (audit-only, unused by State Engine)."""
    p = ProbePayload(probe_id="0")
    assert p.kind == "probe"
    assert p.probe_id == "0"


def test_clarify_payload_no_extra_fields():
    p = ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase)
    assert p.kind == "clarify"


def test_repeat_payload_no_extra_fields():
    p = RepeatPayload()
    assert p.kind == "repeat"


def test_acknowledge_no_experience_carries_failed_signal():
    p = AcknowledgeNoExperiencePayload(failed_signal_value="JQL fluency")
    assert p.failed_signal_value == "JQL fluency"


def test_polite_close_payload_no_extra_fields():
    """reason was removed (audit-only); fallback context is logged on the
    JUDGE_FALLBACK event instead."""
    p = PoliteClosePayload()
    assert p.kind == "polite_close"


def test_end_session_initiated_by_enum():
    with pytest.raises(ValidationError):
        EndSessionPayload(initiated_by="random")
    p = EndSessionPayload(initiated_by="candidate_initiated")
    assert p.initiated_by == "candidate_initiated"


def test_judge_output_discriminator_alignment_passes():
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q-1"),
        turn_metadata=TurnMetadata(),
    )
    assert out.next_action_payload.kind == "advance"


def test_judge_output_discriminator_mismatch_rejected():
    with pytest.raises(ValidationError) as exc_info:
        JudgeOutput(
            reasoning="Test-synthesized reasoning string for unit test fixture.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=AdvancePayload(target_question_id="q-1"),
            turn_metadata=TurnMetadata(),
        )
    assert "does not match payload kind" in str(exc_info.value)


def test_no_experience_flag_with_redirect_action_rejected():
    """Anti-regression for session 1f02f55d turns 13-14: Judge set the
    no-experience flag in turn_metadata but emitted `redirect`/`clarify`
    instead of `acknowledge_no_experience`, perpetuating a 3-turn dead-air
    loop. The validator now rejects the inconsistent shape — the schema
    cannot enforce cross-field constraints, so we catch it post-LLM."""
    from app.modules.interview_engine.models.judge import RedirectPayload
    with pytest.raises(ValidationError) as exc_info:
        JudgeOutput(
            reasoning="Test-synthesized reasoning string for unit test fixture.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.redirect,
            next_action_payload=RedirectPayload(),
            turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
        )
    assert "candidate_disclosed_no_experience" in str(exc_info.value)


def test_no_experience_flag_with_clarify_action_rejected():
    with pytest.raises(ValidationError) as exc_info:
        JudgeOutput(
            reasoning="Test-synthesized reasoning string for unit test fixture.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase),
            turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
        )
    assert "candidate_disclosed_no_experience" in str(exc_info.value)


def test_no_experience_flag_with_acknowledge_action_passes():
    """The aligned shape: flag set + acknowledge_no_experience action."""
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.acknowledge_no_experience,
        next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="x"),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    assert out.turn_metadata.candidate_disclosed_no_experience is True


def test_no_experience_flag_with_polite_close_passes():
    """polite_close is also coherent — used when all mandatory questions
    are complete and the disclosure happens on the way out."""
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.polite_close,
        next_action_payload=PoliteClosePayload(),
        turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
    )
    assert out.next_action == NextAction.polite_close


def test_judge_output_no_thought_field():
    """thought was removed (audit-only, never read by State Engine)."""
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q-1"),
        turn_metadata=TurnMetadata(),
    )
    assert not hasattr(out, "thought")


def test_redirect_action_with_payload():
    """New `redirect` action accepts a single RedirectPayload."""
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, RedirectPayload, TurnMetadata,
    )
    output = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.redirect,
        next_action_payload=RedirectPayload(),
        turn_metadata=TurnMetadata(candidate_off_topic=True),
    )
    assert output.next_action == NextAction.redirect
    assert output.next_action_payload.kind == "redirect"


def test_turn_metadata_has_social_greeting_flag():
    from app.modules.interview_engine.models.judge import TurnMetadata
    md = TurnMetadata(candidate_social_or_greeting=True)
    assert md.candidate_social_or_greeting is True
    # Default false on a fresh instance.
    assert TurnMetadata().candidate_social_or_greeting is False


# ---------------------------------------------------------------------------
# Phase 9.2 — push_back action + observation quality grading
# ---------------------------------------------------------------------------


def test_coverage_quality_values():
    """Three discrete grades: thin / concrete / strong. No numeric scale."""
    assert {q.value for q in CoverageQuality} == {"thin", "concrete", "strong"}


def test_observation_quality_defaults_concrete():
    """Default = concrete keeps back-compat with pre-v2 sessions and the
    synthesizer fallback. Old persisted observations roundtrip without
    needing a migration."""
    obs = Observation(
        signal_value="x",
        anchor_id=0,
        evidence_quote="I built a workflow validator.",
        coverage_transition=CoverageTransition.none_to_partial,
    )
    assert obs.quality == CoverageQuality.concrete


def test_observation_quality_accepts_all_three_grades():
    for grade in (CoverageQuality.thin, CoverageQuality.concrete, CoverageQuality.strong):
        obs = Observation(
            signal_value="x",
            anchor_id=0,
            evidence_quote="evidence text",
            coverage_transition=CoverageTransition.none_to_partial,
            quality=grade,
        )
        assert obs.quality == grade


def test_push_back_payload_kind_constant():
    p = PushBackPayload(reason_code="vague_answer")
    assert p.kind == "push_back"


def test_push_back_payload_accepts_all_reason_codes():
    for code in (
        "vague_answer",
        "deflection",
        "missing_specifics",
        "unanswered_subquestion",
    ):
        p = PushBackPayload(reason_code=code)
        assert p.reason_code == code


def test_push_back_payload_rejects_unknown_reason_code():
    with pytest.raises(ValidationError):
        PushBackPayload(reason_code="not_a_real_reason")


def test_push_back_action_with_thin_observations_passes():
    """The aligned shape: push_back action + observations all marked thin."""
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="x",
                anchor_id=0,
                evidence_quote="I would add validation checks",
                coverage_transition=CoverageTransition.partial_to_partial,
                quality=CoverageQuality.thin,
            )
        ],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="vague_answer"),
        turn_metadata=TurnMetadata(),
    )
    assert out.next_action == NextAction.push_back
    assert out.next_action_payload.reason_code == "vague_answer"


def test_push_back_action_with_concrete_observation_passes():
    """Regression test for the 2026-05-12 force-advance bug.

    The validator no longer raises when push_back is paired with a concrete
    observation. The State Engine's inverse_quality_gate handles this case
    by downgrading to probe. Previously this routed through
    synthesize_fallback and force-advanced the queue.
    """
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="x",
                anchor_id=0,
                evidence_quote="I built a workflow validator using ScriptRunner",
                coverage_transition=CoverageTransition.partial_to_sufficient,
                quality=CoverageQuality.concrete,
            )
        ],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="vague_answer"),
        turn_metadata=TurnMetadata(),
    )
    assert out.next_action == NextAction.push_back
    assert out.observations[0].quality == CoverageQuality.concrete


def test_push_back_action_with_strong_observation_passes():
    """Regression test for the 2026-05-12 force-advance bug (strong variant).

    The validator no longer raises when push_back is paired with a strong
    observation. The State Engine's inverse_quality_gate handles inversion.
    """
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="x",
                anchor_id=0,
                evidence_quote="evidence",
                coverage_transition=CoverageTransition.partial_to_partial,
                quality=CoverageQuality.strong,
            )
        ],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(),
    )
    assert out.next_action == NextAction.push_back
    assert out.observations[0].quality == CoverageQuality.strong


def test_push_back_action_rejects_no_experience_flag():
    """push_back is for candidates who engaged but didn't engage well —
    a no-experience disclosure is a different category that should route
    to acknowledge_no_experience (or polite_close)."""
    with pytest.raises(ValidationError) as exc_info:
        JudgeOutput(
            reasoning="Test-synthesized reasoning string for unit test fixture.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="vague_answer"),
            turn_metadata=TurnMetadata(candidate_disclosed_no_experience=True),
        )
    # Two validators fire (no_experience+action + push_back+no_experience);
    # both messages mention candidate_disclosed_no_experience.
    assert "candidate_disclosed_no_experience" in str(exc_info.value)


def test_push_back_action_with_no_observations_passes():
    """push_back without any observation is valid — the candidate said
    something on-topic but the Judge couldn't anchor any partial coverage."""
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(),
    )
    assert out.observations == []
    assert out.next_action == NextAction.push_back


def test_advance_with_thin_observation_passes_validator():
    """The Pydantic validator does NOT enforce the advance-quality gate —
    that's the State Engine's job. The schema only enforces push_back<->thin
    coupling. An advance with thin obs is schema-legal; the State Engine
    decides whether to honor it."""
    out = JudgeOutput(
        reasoning="Test-synthesized reasoning string for unit test fixture.",
        observations=[
            Observation(
                signal_value="x",
                anchor_id=0,
                evidence_quote="evidence",
                coverage_transition=CoverageTransition.partial_to_sufficient,
                quality=CoverageQuality.thin,
            )
        ],
        candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q-1"),
        turn_metadata=TurnMetadata(),
    )
    assert out.next_action == NextAction.advance
