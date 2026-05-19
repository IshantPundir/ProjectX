"""Verifies the session.timer_started event kind is registered."""
from app.modules.interview_engine.event_kinds import (
    ALL_EVENT_KINDS, SESSION_TIMER_STARTED,
)
from app.modules.interview_engine.audit_events import SessionTimerStartedPayload


def test_session_timer_started_in_registry():
    assert SESSION_TIMER_STARTED == "session.timer_started"
    assert SESSION_TIMER_STARTED in ALL_EVENT_KINDS


def test_session_timer_started_payload_validates():
    payload = SessionTimerStartedPayload(wall_ms=1747654321000)
    assert payload.wall_ms == 1747654321000


def test_session_timer_started_payload_rejects_negative():
    import pytest
    with pytest.raises(Exception):
        SessionTimerStartedPayload(wall_ms=-1)
