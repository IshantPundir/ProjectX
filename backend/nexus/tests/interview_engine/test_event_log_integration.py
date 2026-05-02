"""Phase 1 — end-to-end integration test.

Drives the EventCollector + LocalFileSink in concert: append a handful
of events, close, write to a tmp dir, parse back, assert structural
correctness. This is the test gate for spec §9 Phase 1: "a fake session
run produces a valid envelope JSON parseable back into EventLogEnvelope".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogEnvelope,
)
from app.modules.interview_engine.event_log.local_file import LocalFileSink


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def test_phase_1_envelope_e2e_parses_back(tmp_path: Path) -> None:
    sink = LocalFileSink(directory=str(tmp_path))
    collector = EventCollector(
        session_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        correlation_id="11111111-1111-1111-1111-111111111111",
        controller_prompt_hash="sha256:abc",
        model_versions={
            "llm": "gpt-5.3-chat-latest",
            "stt": "nova-3",
            "tts": "sonic-2",
        },
        redaction_mode="metadata",
    )

    # Mimic a slim-but-realistic session timeline.
    collector.append(
        kind="audio.agent.state",
        payload={"old_state": "listening", "new_state": "thinking"},
        wall_ms=1735000000000,
    )
    collector.append(
        kind="audio.stt.transcribed",
        payload={"transcript": "candidate said something", "transcript_chars": 24, "is_final": True},
        wall_ms=1735000000200,
    )
    collector.append(
        kind="llm.tool.executed",
        payload={
            "tool_name": "record_observation",
            "argument_keys": ["answer_summary", "wants_to_probe"],
            "arguments": {"answer_summary": "should be redacted"},
        },
        wall_ms=1735000000800,
    )
    collector.append(
        kind="audio.metrics.llm",
        payload={"ttft": 0.312, "tokens_in": 850, "tokens_out": 42},
        wall_ms=1735000001000,
    )

    envelope = collector.close(closed_at=_now_iso())
    target = sink.write(envelope)

    blob = Path(target).read_text(encoding="utf-8")
    restored = EventLogEnvelope.model_validate_json(blob)

    # Structural assertions.
    assert restored.session_id == "11111111-1111-1111-1111-111111111111"
    assert restored.redaction_mode == "metadata"
    assert len(restored.events) == 4

    # Redaction was applied — STT transcript and tool arguments stripped.
    stt_event = next(e for e in restored.events if e.kind == "audio.stt.transcribed")
    assert "transcript" not in stt_event.payload
    assert stt_event.payload["transcript_chars"] == 24

    tool_event = next(e for e in restored.events if e.kind == "llm.tool.executed")
    assert "arguments" not in tool_event.payload
    assert tool_event.payload["argument_keys"] == ["answer_summary", "wants_to_probe"]
    assert tool_event.payload["tool_name"] == "record_observation"

    # Audio metrics passed through (no content fields registered for that kind).
    metrics_event = next(e for e in restored.events if e.kind == "audio.metrics.llm")
    assert metrics_event.payload["tokens_in"] == 850

    # Monotonic clock invariant — events appear in the order they were appended.
    t_values = [e.t_ms for e in restored.events]
    assert t_values == sorted(t_values)


def test_phase_1_full_mode_keeps_content_for_audit_replay(tmp_path: Path) -> None:
    """Same session driven in `full` redaction mode produces a payload
    that still contains the verbatim transcript + tool arguments."""
    sink = LocalFileSink(directory=str(tmp_path))
    collector = EventCollector(
        session_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        correlation_id="11111111-1111-1111-1111-111111111111",
        controller_prompt_hash="sha256:abc",
        model_versions={},
        redaction_mode="full",
    )
    collector.append(
        kind="audio.stt.transcribed",
        payload={"transcript": "verbatim", "is_final": True},
        wall_ms=1735000000000,
    )
    target = sink.write(collector.close(closed_at=_now_iso()))
    restored = EventLogEnvelope.model_validate_json(Path(target).read_text(encoding="utf-8"))
    stt = restored.events[0]
    assert stt.payload["transcript"] == "verbatim"
    assert stt.redaction == "full"
    assert restored.redaction_mode == "full"
