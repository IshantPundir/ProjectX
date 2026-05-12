"""Cross-field validator tests for JudgeOutput.

Covers the push_back ↔ observation-quality coupling rules:

- Rule 1 (STRICT): push_back + candidate_disclosed_no_experience is never
  valid. This is a structural contradiction the State Engine cannot recover
  from; the validator still raises.

- Rule 2 (SOFTENED 2026-05-12): push_back + concrete/strong observation is
  now allowed at the schema level. The State Engine's inverse_quality_gate
  handles the inversion by downgrading to probe (or advance if probes
  exhausted). Raising here used to route through synthesize_fallback and
  force-advance the queue — the root cause of the early-end bug seen in
  demo session 96946611 (29% fallback rate, 5/17 calls).
"""

import pytest
from pydantic import ValidationError


def test_push_back_with_concrete_observation_does_not_raise() -> None:
    """Regression test for the 2026-05-12 force-advance bug.

    The validator must NOT raise when push_back is paired with a
    concrete observation. The State Engine's inverse_quality_gate
    handles this case by downgrading to probe.
    """
    from app.modules.interview_engine.models.judge import (
        CoverageQuality,
        JudgeOutput,
        NextAction,
    )

    # This used to raise ValidationError; must succeed now.
    output = JudgeOutput.model_validate({
        "observations": [{
            "signal_value": "react_experience",
            "anchor_id": 0,
            "evidence_quote": "I built an enterprise operations platform",
            "coverage_transition": "none→partial",
            "quality": "concrete",
        }],
        "candidate_claims": [],
        "next_action": "push_back",
        "next_action_payload": {"kind": "push_back", "reason_code": "missing_specifics"},
        "turn_metadata": {},
    })
    assert output.next_action == NextAction.push_back
    assert output.observations[0].quality == CoverageQuality.concrete


def test_push_back_with_strong_observation_does_not_raise() -> None:
    """Regression test for the 2026-05-12 force-advance bug (strong variant).

    The validator must NOT raise when push_back is paired with a strong
    observation. The State Engine's inverse_quality_gate handles inversion.
    """
    from app.modules.interview_engine.models.judge import (
        CoverageQuality,
        JudgeOutput,
        NextAction,
    )

    output = JudgeOutput.model_validate({
        "observations": [{
            "signal_value": "system_design",
            "anchor_id": 1,
            "evidence_quote": "I designed a distributed caching layer for 10k RPS",
            "coverage_transition": "partial→sufficient",
            "quality": "strong",
        }],
        "candidate_claims": [],
        "next_action": "push_back",
        "next_action_payload": {"kind": "push_back", "reason_code": "deflection"},
        "turn_metadata": {},
    })
    assert output.next_action == NextAction.push_back
    assert output.observations[0].quality == CoverageQuality.strong


def test_push_back_with_no_experience_disclosure_still_raises() -> None:
    """The structural rule (push_back vs no-experience) stays strict.

    This is a non-recoverable contradiction — the State Engine cannot
    handle push_back when the candidate has disclosed no experience.
    """
    with pytest.raises(ValidationError) as exc_info:
        from app.modules.interview_engine.models.judge import JudgeOutput
        JudgeOutput.model_validate({
            "observations": [],
            "candidate_claims": [],
            "next_action": "push_back",
            "next_action_payload": {"kind": "push_back", "reason_code": "vague_answer"},
            "turn_metadata": {"candidate_disclosed_no_experience": True},
        })
    assert "candidate_disclosed_no_experience" in str(exc_info.value)


def test_push_back_with_thin_observation_still_passes() -> None:
    """The aligned case: push_back with thin obs remains valid."""
    from app.modules.interview_engine.models.judge import (
        CoverageQuality,
        JudgeOutput,
        NextAction,
    )

    output = JudgeOutput.model_validate({
        "observations": [{
            "signal_value": "react_experience",
            "anchor_id": 0,
            "evidence_quote": "I would probably use React",
            "coverage_transition": "none→partial",
            "quality": "thin",
        }],
        "candidate_claims": [],
        "next_action": "push_back",
        "next_action_payload": {"kind": "push_back", "reason_code": "vague_answer"},
        "turn_metadata": {},
    })
    assert output.next_action == NextAction.push_back
    assert output.observations[0].quality == CoverageQuality.thin
