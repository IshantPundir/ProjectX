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
        "reasoning": "Test-synthesized reasoning string for unit test fixture.",
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
        "reasoning": "Test-synthesized reasoning string for unit test fixture.",
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
            "reasoning": "Test-synthesized reasoning string for unit test fixture.",
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
        "reasoning": "Test-synthesized reasoning string for unit test fixture.",
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


def test_meta_confession_flag_defaults_false():
    from app.modules.interview_engine.models.judge import TurnMetadata
    md = TurnMetadata()
    assert md.candidate_meta_confession is False


def test_meta_confession_flag_can_be_set():
    from app.modules.interview_engine.models.judge import TurnMetadata
    md = TurnMetadata(candidate_meta_confession=True)
    assert md.candidate_meta_confession is True


def test_reasoning_field_is_required():
    """JudgeOutput.reasoning is required (no default) and must be ≥20 chars."""
    from pydantic import ValidationError
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, ClarifyPayload,
    )

    with pytest.raises(ValidationError) as exc:
        JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(),
        )
    assert "reasoning" in str(exc.value).lower()


def test_reasoning_field_min_length_20():
    """reasoning shorter than 20 chars rejected."""
    from pydantic import ValidationError
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, ClarifyPayload,
    )

    with pytest.raises(ValidationError) as exc:
        JudgeOutput(
            reasoning="too short",  # 9 chars
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(),
        )
    assert "min_length" in str(exc.value) or "at least 20" in str(exc.value)


def test_reasoning_field_max_length_2000():
    """reasoning longer than 2000 chars rejected."""
    from pydantic import ValidationError
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, ClarifyPayload,
    )

    with pytest.raises(ValidationError) as exc:
        JudgeOutput(
            reasoning="x" * 2001,
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(),
        )
    assert "max_length" in str(exc.value) or "at most 2000" in str(exc.value)


def test_reasoning_field_valid():
    """A valid 50-char reasoning + minimal payload constructs successfully."""
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, ClarifyPayload,
    )

    out = JudgeOutput(
        reasoning="Candidate asked for clarification of the term. Emitting clarify.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(),
    )
    assert out.reasoning.startswith("Candidate asked")


def test_meta_confession_forbids_acknowledge_no_experience():
    from pydantic import ValidationError
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, AcknowledgeNoExperiencePayload, TurnMetadata,
    )
    with pytest.raises(ValidationError) as exc:
        JudgeOutput(
            reasoning="Candidate said they cannot answer this question. Flagging meta_confession.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.acknowledge_no_experience,
            next_action_payload=AcknowledgeNoExperiencePayload(failed_signal_value="some signal"),
            turn_metadata=TurnMetadata(candidate_meta_confession=True),
        )
    assert "meta_confession" in str(exc.value).lower()


def test_meta_confession_forbids_polite_close():
    from pydantic import ValidationError
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PoliteClosePayload, TurnMetadata,
    )
    with pytest.raises(ValidationError) as exc:
        JudgeOutput(
            reasoning="Meta confession on the final question; trying to close politely.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.polite_close,
            next_action_payload=PoliteClosePayload(),
            turn_metadata=TurnMetadata(candidate_meta_confession=True),
        )
    assert "meta_confession" in str(exc.value).lower()


def test_meta_confession_allows_push_back():
    """The canonical pairing: meta_confession=true + push_back."""
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, PushBackPayload, TurnMetadata,
    )
    out = JudgeOutput(
        reasoning="Candidate said 'I don't know how to answer this question' after engaging earlier.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.push_back,
        next_action_payload=PushBackPayload(reason_code="missing_specifics"),
        turn_metadata=TurnMetadata(candidate_meta_confession=True),
    )
    assert out.turn_metadata.candidate_meta_confession is True


def test_greeting_flag_requires_redirect():
    """social_or_greeting=true forces next_action=redirect."""
    from pydantic import ValidationError
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, ClarifyPayload, TurnMetadata,
    )
    with pytest.raises(ValidationError) as exc:
        JudgeOutput(
            reasoning="Candidate said 'Hi, what is X?' — set greeting flag and tried to clarify.",
            observations=[],
            candidate_claims=[],
            next_action=NextAction.clarify,
            next_action_payload=ClarifyPayload(),
            turn_metadata=TurnMetadata(candidate_social_or_greeting=True),
        )
    assert "social_or_greeting" in str(exc.value).lower() or "redirect" in str(exc.value).lower()


def test_greeting_flag_with_redirect_ok():
    from app.modules.interview_engine.models.judge import (
        JudgeOutput, NextAction, RedirectPayload, TurnMetadata,
    )
    out = JudgeOutput(
        reasoning="Candidate said 'Hi' — pure greeting. Redirecting to the question.",
        observations=[],
        candidate_claims=[],
        next_action=NextAction.redirect,
        next_action_payload=RedirectPayload(),
        turn_metadata=TurnMetadata(candidate_social_or_greeting=True),
    )
    assert out.turn_metadata.candidate_social_or_greeting is True
