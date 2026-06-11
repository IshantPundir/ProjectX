"""Unit test for followups_to_jsonb serialization helper."""
from app.modules.question_bank.schemas import FollowUpDimension, followups_to_jsonb


def test_followups_to_jsonb_serializes_objects() -> None:
    out = followups_to_jsonb(
        [FollowUpDimension(dimension="d1", intent="i", seed_probe="p", listen_for=["x"])]
    )
    assert out == [{"dimension": "d1", "intent": "i", "seed_probe": "p", "listen_for": ["x"]}]


def test_followups_to_jsonb_empty_list() -> None:
    assert followups_to_jsonb([]) == []


def test_followups_to_jsonb_multiple_objects() -> None:
    fu1 = FollowUpDimension(dimension="d1", intent="i1", seed_probe="p1", listen_for=["a"])
    fu2 = FollowUpDimension(dimension="d2", intent="i2", seed_probe="p2", listen_for=["b", "c"])
    out = followups_to_jsonb([fu1, fu2])
    assert len(out) == 2
    assert out[0] == {"dimension": "d1", "intent": "i1", "seed_probe": "p1", "listen_for": ["a"]}
    assert out[1] == {"dimension": "d2", "intent": "i2", "seed_probe": "p2", "listen_for": ["b", "c"]}
    # All items are plain dicts — JSON-serializable
    assert all(isinstance(item, dict) for item in out)
