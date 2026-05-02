"""Phase 1 — S3Sink.

The deploy-gate sink writes the envelope to s3://{bucket}/{tenant_id}/{session_id}/engine_events.json.
Tests monkeypatch the boto3 client factory so no real AWS calls are made.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)
from app.modules.interview_engine.event_log import s3 as s3_mod
from app.modules.interview_engine.event_log.s3 import S3Sink


class _FakeS3Client:
    """In-memory stand-in for boto3.client('s3')."""

    def __init__(self) -> None:
        self.put_object_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_object_calls.append(kwargs)
        return {"ETag": '"deadbeef"'}


def _make_envelope() -> EventLogEnvelope:
    return EventLogEnvelope(
        session_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        correlation_id="11111111-1111-1111-1111-111111111111",
        started_at="2026-05-02T10:00:00Z",
        closed_at="2026-05-02T10:15:00Z",
        controller_prompt_hash="sha256:abc",
        task_prompt_hashes={},
        model_versions={},
        redaction_mode="metadata",
        events=[],
    )


def test_s3_sink_writes_to_expected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(s3_mod, "_create_s3_client", lambda: fake)
    sink = S3Sink(bucket="ev-bucket")
    env = _make_envelope()
    key = sink.write(env)
    assert key == f"s3://ev-bucket/{env.tenant_id}/{env.session_id}/engine_events.json"
    assert len(fake.put_object_calls) == 1
    call = fake.put_object_calls[0]
    assert call["Bucket"] == "ev-bucket"
    assert call["Key"] == f"{env.tenant_id}/{env.session_id}/engine_events.json"
    assert call["ContentType"] == "application/json"


def test_s3_sink_writes_envelope_body_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(s3_mod, "_create_s3_client", lambda: fake)
    sink = S3Sink(bucket="ev-bucket")
    env = _make_envelope()
    env.events = [
        EventLogEvent(t_ms=0, wall_ms=1735000000000, kind="session.started",
                      payload={}, redaction="metadata"),
    ]
    sink.write(env)
    body = fake.put_object_calls[0]["Body"]
    restored = EventLogEnvelope.model_validate_json(body)
    assert restored == env


def test_s3_sink_rejects_empty_bucket() -> None:
    with pytest.raises(ValueError):
        S3Sink(bucket="")
