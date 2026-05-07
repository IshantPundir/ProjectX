import pytest
from pydantic import ValidationError

from app.modules.interview_engine.models.judge import (
    AcknowledgeNoExperiencePayload,
    AdvancePayload,
    ClarifyPayload,
    ClaimEntry as JudgeClaimEntry,
    CoverageTransition,
    EndSessionPayload,
    JudgeOutput,
    NextAction,
    Observation,
    PoliteClosePayload,
    ProbePayload,
    RedirectAbusivePayload,
    RedirectOffTopicPayload,
    RepeatPayload,
    SafeRedirectInjectionPayload,
    TurnMetadata,
)


def test_next_action_values():
    expected = {
        "advance",
        "probe",
        "clarify",
        "repeat",
        "redirect_off_topic",
        "redirect_abusive",
        "safe_redirect_injection",
        "redirect",
        "acknowledge_no_experience",
        "polite_close",
        "end_session",
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


def test_probe_payload_requires_id_and_rationale():
    p = ProbePayload(probe_id="0", probe_rationale="missing anchor 1")
    assert p.kind == "probe"
    assert p.probe_id == "0"


def test_clarify_payload_no_extra_fields():
    p = ClarifyPayload()
    assert p.kind == "clarify"


def test_repeat_payload_no_extra_fields():
    p = RepeatPayload()
    assert p.kind == "repeat"


def test_acknowledge_no_experience_carries_failed_signal():
    p = AcknowledgeNoExperiencePayload(failed_signal_value="JQL fluency")
    assert p.failed_signal_value == "JQL fluency"


def test_polite_close_carries_reason():
    p = PoliteClosePayload(reason="knockout_recorded")
    assert p.reason == "knockout_recorded"


def test_end_session_initiated_by_enum():
    with pytest.raises(ValidationError):
        EndSessionPayload(initiated_by="random")
    p = EndSessionPayload(initiated_by="candidate_initiated")
    assert p.initiated_by == "candidate_initiated"


def test_judge_output_discriminator_alignment_passes():
    out = JudgeOutput(
        thought="thinking",
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
            thought="thinking",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=AdvancePayload(target_question_id="q-1"),
            turn_metadata=TurnMetadata(),
        )
    assert "does not match payload kind" in str(exc_info.value)


def test_judge_output_thought_length_capped():
    with pytest.raises(ValidationError):
        JudgeOutput(
            thought="x" * 601,
            observations=[],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q-1"),
            turn_metadata=TurnMetadata(),
        )


def test_redirect_action_with_payload():
    """New `redirect` action accepts a single RedirectPayload."""
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, RedirectPayload, TurnMetadata,
    )
    output = JudgeOutput(
        thought="off-topic; redirect",
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
