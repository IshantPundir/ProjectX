import pytest
from pydantic import ValidationError

from app.modules.question_bank.schemas import FollowUpDimension, GeneratedQuestion


def _q(follow_ups):
    return GeneratedQuestion(
        position=0, text="A real spoken question here?", primary_signal="s", signal_values=["s"],
        estimated_minutes=2.0, is_mandatory=False, follow_ups=follow_ups,
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric={"excellent": "e" * 20, "meets_bar": "m" * 20, "below_bar": "b" * 20},
        evaluation_hint="h" * 12, question_kind="technical_scenario", difficulty="medium",
    )


def test_generated_question_accepts_dimensions():
    q = _q([{"dimension": "d1", "intent": "i", "seed_probe": "p", "listen_for": ["x"]}])
    assert isinstance(q.follow_ups[0], FollowUpDimension)
    assert q.follow_ups[0].dimension == "d1"


def test_max_three_follow_ups():
    with pytest.raises(ValidationError):
        _q([{"dimension": f"d{i}", "intent": "i", "seed_probe": "p", "listen_for": ["x"]} for i in range(4)])


def test_listen_for_required_nonempty_on_generation():
    # The GENERATION model enforces non-empty listen_for (LLM guarantee).
    with pytest.raises(ValidationError):
        _q([{"dimension": "d1", "intent": "i", "seed_probe": "p", "listen_for": []}])


def test_followup_dimension_allows_empty_listen_for_on_read_path():
    # The shared/read shape is PERMISSIVE: legacy/backfilled banks have listen_for=[].
    d = FollowUpDimension(dimension="d1", intent="i", seed_probe="p")
    assert d.listen_for == []


def test_question_response_renders_backfilled_empty_listen_for():
    # Regression: GET /questions must not 500 on a backfilled bank whose follow-ups
    # carry listen_for=[] (migration 0055 shape). QuestionResponse must accept it.
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.modules.question_bank.schemas import QuestionResponse

    resp = QuestionResponse(
        id=uuid4(), bank_id=uuid4(), position=0, source="ai_generated",
        text="Assess a messy tenant?", signal_values=["s"], estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[{"dimension": "validate_impact", "intent": "i", "seed_probe": "p", "listen_for": []}],
        positive_evidence=["a"], red_flags=["r"],
        rubric={"excellent": "e" * 20, "meets_bar": "m" * 20, "below_bar": "b" * 20},
        evaluation_hint="h" * 12, edited_by_recruiter=False, question_kind="technical_scenario",
        primary_signal="s", difficulty="medium",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    assert resp.follow_ups[0].listen_for == []
