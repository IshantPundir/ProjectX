"""Phase 1 — EventCollector.

In-memory aggregator that:
- maintains the session's monotonic clock zero
- redacts each appended payload per the envelope-level mode
- closes into a parseable EventLogEnvelope
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.event_log.envelope import EventLogEnvelope


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_collector_first_event_has_t_ms_zero() -> None:
    c = EventCollector(
        session_id="s",
        tenant_id="t",
        correlation_id="c",
        controller_prompt_hash="sha256:a",
        model_versions={},
        redaction_mode="metadata",
    )
    c.append(kind="session.started", payload={}, wall_ms=1735000000000)
    env = c.close(closed_at=_now_iso())
    assert env.events[0].t_ms == 0


def test_collector_subsequent_events_have_monotonic_t_ms() -> None:
    c = EventCollector(
        session_id="s",
        tenant_id="t",
        correlation_id="c",
        controller_prompt_hash="sha256:a",
        model_versions={},
        redaction_mode="metadata",
    )
    c.append(kind="audio.user.state", payload={}, wall_ms=1735000000000)
    time.sleep(0.01)
    c.append(kind="audio.user.state", payload={}, wall_ms=1735000000010)
    env = c.close(closed_at=_now_iso())
    assert env.events[0].t_ms == 0
    assert env.events[1].t_ms >= 5  # ≥5ms elapsed (allows for jitter)


def test_collector_metadata_mode_strips_content() -> None:
    c = EventCollector(
        session_id="s",
        tenant_id="t",
        correlation_id="c",
        controller_prompt_hash="sha256:a",
        model_versions={},
        redaction_mode="metadata",
    )
    c.append(
        kind="audio.stt.transcribed",
        payload={"transcript": "hello world", "transcript_chars": 11},
        wall_ms=1735000000000,
    )
    env = c.close(closed_at=_now_iso())
    assert "transcript" not in env.events[0].payload
    assert env.events[0].payload["transcript_chars"] == 11
    assert env.events[0].redaction == "metadata"


def test_collector_full_mode_keeps_content() -> None:
    c = EventCollector(
        session_id="s",
        tenant_id="t",
        correlation_id="c",
        controller_prompt_hash="sha256:a",
        model_versions={},
        redaction_mode="full",
    )
    c.append(
        kind="audio.stt.transcribed",
        payload={"transcript": "hello world"},
        wall_ms=1735000000000,
    )
    env = c.close(closed_at=_now_iso())
    assert env.events[0].payload["transcript"] == "hello world"
    assert env.events[0].redaction == "full"


def test_collector_close_returns_valid_envelope() -> None:
    c = EventCollector(
        session_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        correlation_id="11111111-1111-1111-1111-111111111111",
        controller_prompt_hash="sha256:a",
        model_versions={"llm": "x"},
        redaction_mode="metadata",
    )
    c.append(kind="session.started", payload={}, wall_ms=1735000000000)
    env = c.close(closed_at="2026-05-02T10:15:00Z")
    blob = env.model_dump_json()
    restored = EventLogEnvelope.model_validate_json(blob)
    assert restored.session_id == c._session_id  # type: ignore[attr-defined]
    assert restored.redaction_mode == "metadata"
    assert len(restored.events) == 1


def test_collector_records_started_at_at_first_append() -> None:
    c = EventCollector(
        session_id="s",
        tenant_id="t",
        correlation_id="c",
        controller_prompt_hash="sha256:a",
        model_versions={},
        redaction_mode="metadata",
    )
    c.append(kind="session.started", payload={}, wall_ms=1735000000000)
    env = c.close(closed_at="2026-05-02T10:15:00Z")
    # started_at is "first wall_ms converted to ISO 8601 UTC"
    assert env.started_at.startswith("2024-12-24T")  # 1735000000000 ms = 2024-12-24T00:26:40Z


def test_collector_close_with_no_events_still_valid() -> None:
    c = EventCollector(
        session_id="s",
        tenant_id="t",
        correlation_id="c",
        controller_prompt_hash="sha256:a",
        model_versions={},
        redaction_mode="metadata",
    )
    env = c.close(closed_at="2026-05-02T10:15:00Z")
    assert env.events == []
    # started_at falls back to closed_at when no events were appended
    assert env.started_at == "2026-05-02T10:15:00Z"
