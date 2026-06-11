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


def test_listen_for_required_nonempty():
    with pytest.raises(ValidationError):
        _q([{"dimension": "d1", "intent": "i", "seed_probe": "p", "listen_for": []}])
