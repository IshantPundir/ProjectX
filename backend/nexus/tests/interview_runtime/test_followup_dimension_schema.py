import pytest
from pydantic import ValidationError

from app.modules.interview_runtime.schemas import FollowUpDimension, QuestionConfig


def test_followup_dimension_valid():
    d = FollowUpDimension(
        dimension="validate_impact",
        intent="Did they verify policy impact before changing it?",
        seed_probe="How would you validate impact before adjusting a policy?",
        listen_for=["pilot/canary group", "rollback readiness"],
    )
    assert d.dimension == "validate_impact"
    assert d.listen_for == ["pilot/canary group", "rollback readiness"]


def test_followup_dimension_listen_for_defaults_empty():
    d = FollowUpDimension(
        dimension="d1", intent="i", seed_probe="p",
    )
    assert d.listen_for == []


def test_followup_dimension_rejects_blank_dimension():
    with pytest.raises(ValidationError):
        FollowUpDimension(dimension="", intent="i", seed_probe="p")


def test_question_config_follow_ups_are_dimensions():
    q = QuestionConfig(
        id="q1", position=0, text="A real question here?",
        signal_values=["sig"], estimated_minutes=2.0, is_mandatory=False,
        follow_ups=[
            {"dimension": "d1", "intent": "i1", "seed_probe": "p1", "listen_for": ["x"]},
        ],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric={"excellent": "e" * 20, "meets_bar": "m" * 20, "below_bar": "b" * 20},
        evaluation_hint="h" * 12, question_kind="technical_scenario",
        primary_signal="sig", difficulty="medium",
    )
    assert q.follow_ups[0].dimension == "d1"
    assert isinstance(q.follow_ups[0], FollowUpDimension)
