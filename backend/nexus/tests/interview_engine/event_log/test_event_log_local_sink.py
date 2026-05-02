"""Phase 1 — LocalFileSink.

The dev-default sink writes one JSON file per session under
ENGINE_EVENT_LOG_DIR/{session_id}.json. tmp_path is the right scope —
no real filesystem leakage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)
from app.modules.interview_engine.event_log.local_file import LocalFileSink


def _make_envelope(session_id: str = "11111111-1111-1111-1111-111111111111") -> EventLogEnvelope:
    return EventLogEnvelope(
        session_id=session_id,
        tenant_id="22222222-2222-2222-2222-222222222222",
        correlation_id=session_id,
        started_at="2026-05-02T10:00:00Z",
        closed_at="2026-05-02T10:15:00Z",
        controller_prompt_hash="sha256:abc",
        task_prompt_hashes={},
        model_versions={"llm": "gpt-5.3-chat-latest"},
        redaction_mode="metadata",
        events=[
            EventLogEvent(
                t_ms=0, wall_ms=1735000000000, kind="session.started",
                payload={}, redaction="metadata",
            ),
        ],
    )


def test_local_sink_writes_file_at_expected_path(tmp_path: Path) -> None:
    sink = LocalFileSink(directory=str(tmp_path))
    env = _make_envelope()
    path = sink.write(env)
    assert Path(path).exists()
    assert Path(path).name == f"{env.session_id}.json"
    assert Path(path).parent == tmp_path


def test_local_sink_creates_directory_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deeper" / "engine-events"
    sink = LocalFileSink(directory=str(nested))
    env = _make_envelope()
    sink.write(env)
    assert nested.is_dir()


def test_local_sink_writes_valid_envelope_json(tmp_path: Path) -> None:
    sink = LocalFileSink(directory=str(tmp_path))
    env = _make_envelope()
    path = sink.write(env)
    blob = Path(path).read_text(encoding="utf-8")
    restored = EventLogEnvelope.model_validate_json(blob)
    assert restored == env


def test_local_sink_overwrites_on_second_write(tmp_path: Path) -> None:
    """Same session_id writing twice (e.g., retry on close) should
    leave the second envelope on disk, not append."""
    sink = LocalFileSink(directory=str(tmp_path))
    env1 = _make_envelope()
    env2 = _make_envelope()
    env2.events = []  # different content
    sink.write(env1)
    sink.write(env2)
    blob = (tmp_path / f"{env1.session_id}.json").read_text(encoding="utf-8")
    restored = EventLogEnvelope.model_validate_json(blob)
    assert restored.events == []


def test_local_sink_path_is_safe_from_session_id_traversal(tmp_path: Path) -> None:
    """Defense in depth — even though session_id comes from validated
    UUIDs, the sink should refuse path-traversal-shaped values."""
    sink = LocalFileSink(directory=str(tmp_path))
    env = _make_envelope(session_id="../../../etc/passwd")
    with pytest.raises(ValueError):
        sink.write(env)
