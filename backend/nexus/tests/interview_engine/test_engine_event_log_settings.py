"""Phase 1 — engine event log settings.

The engine selects an EventLogSink by env. These tests pin the field
names and defaults so the sink factory in event_log/factory.py has a
stable contract.
"""

from __future__ import annotations

from app.config import Settings


def test_engine_event_log_settings_defaults() -> None:
    s = Settings(
        # Required-in-non-test fields (skip via test envvars in conftest);
        # we instantiate Settings directly here to assert defaults.
        candidate_jwt_secret="x" * 32,
        interview_engine_jwt_secret="x" * 32,
    )
    assert s.engine_event_log_sink == "local"
    assert s.engine_event_log_dir == "/tmp/engine-events"
    assert s.engine_event_log_redaction == "metadata"
    assert s.aws_s3_bucket_engine_events == ""


def test_engine_event_log_sink_accepts_known_values() -> None:
    for value in ("local", "s3", "none"):
        s = Settings(
            candidate_jwt_secret="x" * 32,
            interview_engine_jwt_secret="x" * 32,
            engine_event_log_sink=value,
        )
        assert s.engine_event_log_sink == value


def test_engine_event_log_redaction_accepts_known_values() -> None:
    for value in ("metadata", "full"):
        s = Settings(
            candidate_jwt_secret="x" * 32,
            interview_engine_jwt_secret="x" * 32,
            engine_event_log_redaction=value,
        )
        assert s.engine_event_log_redaction == value
