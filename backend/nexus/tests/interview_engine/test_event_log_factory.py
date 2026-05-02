"""Phase 1 — sink factory.

build_sink_from_settings() reads ENGINE_EVENT_LOG_SINK and returns the
matching sink, or None when sink="none". Centralised dispatch so agent.py
doesn't import every sink module.
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.event_log.factory import build_sink_from_settings
from app.modules.interview_engine.event_log.local_file import LocalFileSink
from app.modules.interview_engine.event_log.s3 import S3Sink


def test_factory_returns_local_sink_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.engine_event_log_sink", "local")
    monkeypatch.setattr("app.config.settings.engine_event_log_dir", "/tmp/test-engine-events")
    sink = build_sink_from_settings()
    assert isinstance(sink, LocalFileSink)


def test_factory_returns_s3_sink_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.engine_event_log_sink", "s3")
    monkeypatch.setattr("app.config.settings.aws_s3_bucket_engine_events", "ev-bucket")
    sink = build_sink_from_settings()
    assert isinstance(sink, S3Sink)


def test_factory_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.engine_event_log_sink", "none")
    sink = build_sink_from_settings()
    assert sink is None


def test_factory_raises_when_s3_bucket_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.config.settings.engine_event_log_sink", "s3")
    monkeypatch.setattr("app.config.settings.aws_s3_bucket_engine_events", "")
    with pytest.raises(ValueError, match="AWS_S3_BUCKET_ENGINE_EVENTS"):
        build_sink_from_settings()
