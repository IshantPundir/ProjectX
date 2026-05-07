import pytest
from pydantic import ValidationError

from app.modules.interview_engine.models.judge import (
    NextAction,
    CoverageTransition,
    Observation,
    ClaimEntry as JudgeClaimEntry,
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
