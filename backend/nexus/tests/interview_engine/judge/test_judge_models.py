import pytest
from pydantic import ValidationError
from app.modules.interview_engine.models.judge import (
    ClarifyPayload, ClarifyKind, JudgeOutput, NextAction, RedirectPayload, TurnMetadata,
)


def test_still_confused_allowed_with_clarify():
    out = JudgeOutput(
        reasoning="Candidate is generically confused again after we already rephrased once.",
        observations=[], candidate_claims=[],
        next_action=NextAction.clarify,
        next_action_payload=ClarifyPayload(clarify_kind=ClarifyKind.broad_rephrase),
        turn_metadata=TurnMetadata(candidate_still_confused=True),
    )
    assert out.turn_metadata.candidate_still_confused is True


def test_still_confused_rejected_without_clarify():
    with pytest.raises(ValidationError):
        JudgeOutput(
            reasoning="Candidate is confused but we are emitting redirect — incoherent pairing.",
            observations=[], candidate_claims=[],
            next_action=NextAction.redirect,
            next_action_payload=RedirectPayload(),
            turn_metadata=TurnMetadata(candidate_still_confused=True),
        )
