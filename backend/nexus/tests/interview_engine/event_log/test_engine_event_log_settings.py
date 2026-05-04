"""Phase 1 — engine event log settings.

The engine selects an EventLogSink by env. These tests pin the field
names and defaults so the sink factory in event_log/factory.py has a
stable contract.
"""

from __future__ import annotations

from app.config import Settings


def test_engine_event_log_settings_defaults(monkeypatch) -> None:
    """Verify the documented defaults — independent of any .env file the
    developer may have set locally for live debugging (the user's .env
    has ENGINE_EVENT_LOG_REDACTION=full so they can read transcripts in
    audit envelopes; that environment override must not bleed into
    default-assertion tests).
    """
    # Strip the env vars whose .env values would shadow the class-level
    # defaults we're trying to pin.
    for var in (
        "ENGINE_EVENT_LOG_SINK",
        "ENGINE_EVENT_LOG_DIR",
        "ENGINE_EVENT_LOG_REDACTION",
        "AWS_S3_BUCKET_ENGINE_EVENTS",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings(
        candidate_jwt_secret="x" * 32,
        interview_engine_jwt_secret="x" * 32,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert s.engine_event_log_sink == "local"
    assert s.engine_event_log_dir == "/tmp/engine-events"
    assert s.engine_event_log_redaction == "metadata"
    assert s.aws_s3_bucket_engine_events == ""


def test_engine_event_log_sink_accepts_known_values() -> None:
    for value in ("local", "s3", "none"):
        # s3 sink requires the bucket to be set; other sinks don't need it
        kwargs = {
            "candidate_jwt_secret": "x" * 32,
            "interview_engine_jwt_secret": "x" * 32,
            "engine_event_log_sink": value,
        }
        if value == "s3":
            kwargs["aws_s3_bucket_engine_events"] = "my-bucket"
        s = Settings(**kwargs)
        assert s.engine_event_log_sink == value


def test_engine_event_log_redaction_accepts_known_values() -> None:
    for value in ("metadata", "full"):
        s = Settings(
            candidate_jwt_secret="x" * 32,
            interview_engine_jwt_secret="x" * 32,
            engine_event_log_redaction=value,
        )
        assert s.engine_event_log_redaction == value


def test_engine_event_log_sink_rejects_invalid_values() -> None:
    """Pydantic should reject invalid sink values at Settings instantiation."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            candidate_jwt_secret="x" * 32,
            interview_engine_jwt_secret="x" * 32,
            engine_event_log_sink="invalid_value",  # type: ignore[arg-type]
        )


def test_engine_event_log_redaction_rejects_invalid_values() -> None:
    """Pydantic should reject invalid redaction modes at Settings instantiation."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            candidate_jwt_secret="x" * 32,
            interview_engine_jwt_secret="x" * 32,
            engine_event_log_redaction="invalid_value",  # type: ignore[arg-type]
        )


def test_s3_sink_without_bucket_rejected() -> None:
    """Cross-field validator: ENGINE_EVENT_LOG_SINK=s3 with empty bucket fails fast."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="AWS_S3_BUCKET_ENGINE_EVENTS"):
        Settings(
            candidate_jwt_secret="x" * 32,
            interview_engine_jwt_secret="x" * 32,
            engine_event_log_sink="s3",
            aws_s3_bucket_engine_events="",
        )
