"""Phase 1 — event log envelope schema.

The envelope is the single JSON file written at session close. Schema
stability matters because audit-replay tooling will load these files
later and must not break across engine versions.
"""

from __future__ import annotations

from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)


def test_event_log_event_minimal_fields() -> None:
    event = EventLogEvent(
        t_ms=123,
        wall_ms=1735000000123,
        kind="audio.user.state",
        payload={"old_state": "listening", "new_state": "speaking"},
        redaction="metadata",
    )
    assert event.t_ms == 123
    assert event.kind == "audio.user.state"
    assert event.redaction == "metadata"


def test_event_log_envelope_roundtrip() -> None:
    env = EventLogEnvelope(
        session_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        correlation_id="11111111-1111-1111-1111-111111111111",
        started_at="2026-05-02T10:00:00Z",
        closed_at="2026-05-02T10:15:00Z",
        controller_prompt_hash="sha256:abc",
        task_prompt_hashes={"q1": "sha256:def"},
        model_versions={"llm": "gpt-5.3-chat-latest", "stt": "nova-3"},
        redaction_mode="metadata",
        events=[
            EventLogEvent(
                t_ms=0,
                wall_ms=1735000000000,
                kind="session.started",
                payload={},
                redaction="metadata",
            ),
        ],
    )
    blob = env.model_dump_json()
    restored = EventLogEnvelope.model_validate_json(blob)
    assert restored == env


def test_event_log_envelope_redaction_mode_is_required() -> None:
    """redaction_mode is required at envelope-level so audit replay can
    branch on metadata-vs-full without inspecting individual events."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EventLogEnvelope(
            session_id="x",
            tenant_id="y",
            correlation_id="z",
            started_at="2026-05-02T10:00:00Z",
            closed_at=None,
            controller_prompt_hash="sha256:a",
            task_prompt_hashes={},
            model_versions={},
            # redaction_mode missing
            events=[],
        )
