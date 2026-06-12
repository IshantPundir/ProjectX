import uuid
import pytest
from app.modules.question_bank.state_machine import (
    LEGAL,
    transition_to_self_reviewing,
    transition_to_reviewing_after_critic,
    transition_to_generating,
)
from app.modules.question_bank.errors import IllegalTransitionError


class _Bank:
    def __init__(self, status):
        self.status = status
        self.id = uuid.uuid4()
        self.generation_error = None
        self.generated_at = None
        self.generated_by = None
        self.updated_at = None


def test_generating_goes_to_self_reviewing():
    b = _Bank("generating")
    transition_to_self_reviewing(b)
    assert b.status == "self_reviewing"


def test_self_reviewing_goes_to_reviewing():
    b = _Bank("self_reviewing")
    uid = uuid.uuid4()
    transition_to_reviewing_after_critic(b, user_id=uid)
    assert b.status == "reviewing"
    assert b.generated_by == uid


def test_generating_to_reviewing_edge_is_removed():
    # The direct edge is gone — generation must route through self_reviewing.
    assert "reviewing" not in LEGAL["generating"]
    assert "self_reviewing" in LEGAL["generating"]


def test_self_reviewing_transition_rejects_wrong_source():
    b = _Bank("draft")
    with pytest.raises(IllegalTransitionError):
        transition_to_self_reviewing(b)


def test_reviewing_after_critic_rejects_wrong_source():
    b = _Bank("generating")
    with pytest.raises(RuntimeError):
        transition_to_reviewing_after_critic(b, user_id=uuid.uuid4())


def test_self_reviewing_can_restart_to_generating():
    # A worker crash after the self_reviewing commit must be recoverable: a retry
    # re-enters Phase A and transitions self_reviewing -> generating to restart.
    b = _Bank("self_reviewing")
    transition_to_generating(b)
    assert b.status == "generating"
