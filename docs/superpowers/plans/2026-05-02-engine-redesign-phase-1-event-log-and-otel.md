# Engine Redesign — Phase 1: Audit Event Log + Engine OTel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an audit-grade per-session JSON event log with sink-agnostic destination (local FS in dev, S3 deploy gate) and bootstrap OpenTelemetry inside the LiveKit engine container so realtime spans become aggregator-pluggable.

**Architecture:** New `app/modules/interview_engine/event_log/` package with pure-function redaction, pydantic envelope, in-memory `EventCollector`, and two sinks (`LocalFileSink`, `S3Sink`) selected by env. The collector is fed from inside the existing `_wire_session_observability` listeners in `agent.py`. OTel `TracerProvider` is bootstrapped in `prewarm` using the existing `app.ai.otel.bootstrap_tracer_provider()` helper; production runs ship no exporters by default.

**Tech Stack:** Python 3.13, structlog 25.x, pydantic v2, boto3 (already a project dep for S3 resume uploads), opentelemetry-api 1.39.x, pytest + pytest-asyncio.

**Spec anchor:** `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` §3.3, §3.4, §5.2, §5.3, §6, §8 (Phase 1), §9 (Phase 1 test gates).

---

## File structure

| File | Purpose |
|---|---|
| `backend/nexus/app/modules/interview_engine/event_log/__init__.py` | Public API: `EventCollector`, `build_sink_from_settings()`, `EventLogEnvelope` re-export |
| `backend/nexus/app/modules/interview_engine/event_log/envelope.py` | `EventLogEvent` + `EventLogEnvelope` pydantic models |
| `backend/nexus/app/modules/interview_engine/event_log/redaction.py` | Pure function `redact_payload(kind, payload, mode)` enforcing the §5.2 boundary |
| `backend/nexus/app/modules/interview_engine/event_log/sink.py` | `EventLogSink` protocol + `BaseSink` shared logic |
| `backend/nexus/app/modules/interview_engine/event_log/local_file.py` | `LocalFileSink` writing to `${ENGINE_EVENT_LOG_DIR}/{session_id}.json` |
| `backend/nexus/app/modules/interview_engine/event_log/s3.py` | `S3Sink` writing to `s3://{bucket}/{tenant_id}/{session_id}/engine_events.json` |
| `backend/nexus/app/modules/interview_engine/event_log/factory.py` | `build_sink_from_settings()` env-driven dispatch |
| `backend/nexus/app/modules/interview_engine/event_log/collector.py` | `EventCollector` in-memory aggregator (handles `t_ms`/`wall_ms` math, calls redaction) |
| `backend/nexus/app/modules/interview_engine/prompt_hash.py` | `hash_prompt_file(relative_path) -> str` — sha256 of prompt body, prefixed `sha256:` |
| `backend/nexus/app/config.py` | **MODIFIED** — add 4 settings: `engine_event_log_sink`, `engine_event_log_dir`, `engine_event_log_redaction`, `aws_s3_bucket_engine_events` |
| `backend/nexus/app/modules/interview_engine/agent.py` | **MODIFIED** — bootstrap OTel in `prewarm`, instantiate `EventCollector` + sink in `entrypoint`, append events inside `_wire_session_observability`, write envelope in `_handle_close` |
| `backend/nexus/.env.example` | **MODIFIED** — document the 4 new env vars |
| `backend/nexus/tests/interview_engine/test_event_log_envelope.py` | Tests for envelope serialization roundtrip |
| `backend/nexus/tests/interview_engine/test_event_log_redaction.py` | Tests asserting metadata mode strips every content field enumerated in §5.2 |
| `backend/nexus/tests/interview_engine/test_event_log_local_sink.py` | Tests for `LocalFileSink` using `tmp_path` |
| `backend/nexus/tests/interview_engine/test_event_log_s3_sink.py` | Tests for `S3Sink` with monkeypatched boto3 client |
| `backend/nexus/tests/interview_engine/test_event_log_factory.py` | Tests asserting sink selection by settings |
| `backend/nexus/tests/interview_engine/test_event_log_collector.py` | Tests for `EventCollector` time-math + append + close |
| `backend/nexus/tests/interview_engine/test_engine_otel_bootstrap.py` | Test asserting engine prewarm calls the existing bootstrap |
| `backend/nexus/tests/interview_engine/test_prompt_hash.py` | Tests for `hash_prompt_file` |
| `backend/nexus/tests/interview_engine/test_event_log_integration.py` | End-to-end test: fake session run produces a parseable envelope |

---

## Task 1: Add settings for event log sink config

**Files:**
- Modify: `backend/nexus/app/config.py:208-209` (insert new fields after `engine_log_user_transcripts`)
- Modify: `backend/nexus/.env.example` (add documented examples)
- Test: `backend/nexus/tests/test_config.py` (extend if file exists; otherwise create new test)

- [ ] **Step 1: Find the existing engine settings block**

Run: `grep -n "engine_log_user_transcripts" backend/nexus/app/config.py`
Expected: line 209 (`engine_log_user_transcripts: bool = False`).

- [ ] **Step 2: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_engine_event_log_settings.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_engine_event_log_settings.py -v`
Expected: 3 failures with `AttributeError: 'Settings' object has no attribute 'engine_event_log_sink'`.

- [ ] **Step 4: Add the settings fields**

Open `backend/nexus/app/config.py`, find line 209 (`engine_log_user_transcripts: bool = False`), and insert immediately after it:

```python
    # Phase 1 (engine redesign) — event log sink config. The engine writes a
    # per-session JSON envelope at session close; the sink chosen here decides
    # where it lands. Production runs `metadata` redaction (no PII content);
    # `full` is consent-gated audit replay only and must never be the default.
    # `none` disables the writer entirely (smoke tests, ephemeral envs).
    engine_event_log_sink: Literal["local", "s3", "none"] = "local"
    engine_event_log_dir: str = "/tmp/engine-events"
    engine_event_log_redaction: Literal["metadata", "full"] = "metadata"
    aws_s3_bucket_engine_events: str = ""
```

Verify the import block at the top of `config.py` has `from typing import Literal` (look for it; if missing, add `from typing import Literal` near the existing `from typing import` line).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_engine_event_log_settings.py -v`
Expected: 3 passes.

- [ ] **Step 6: Update .env.example**

Open `backend/nexus/.env.example`. Find the existing engine settings block (search for `ENGINE_LOG_USER_TRANSCRIPTS=false`). Append immediately after it:

```bash
# Phase 1 — engine event log sink. `local` writes JSON to ENGINE_EVENT_LOG_DIR;
# `s3` writes to AWS_S3_BUCKET_ENGINE_EVENTS; `none` disables the writer.
ENGINE_EVENT_LOG_SINK=local
ENGINE_EVENT_LOG_DIR=/tmp/engine-events
# `metadata` strips all PII content from the envelope (production default).
# `full` keeps verbatim transcripts + LLM bodies — consent-gated audit replay only.
ENGINE_EVENT_LOG_REDACTION=metadata
# Required when ENGINE_EVENT_LOG_SINK=s3. Bucket must have versioning ON.
AWS_S3_BUCKET_ENGINE_EVENTS=
```

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/.env.example backend/nexus/tests/interview_engine/test_engine_event_log_settings.py
git commit -m "$(cat <<'EOF'
feat(engine): add event log sink settings (Phase 1)

Adds engine_event_log_sink, engine_event_log_dir, engine_event_log_redaction,
and aws_s3_bucket_engine_events. Defaults are local-FS dev-safe; production
runs metadata redaction. Pins the field names so the sink factory in the
next task has a stable contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Define event log envelope schema

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/event_log/__init__.py`
- Create: `backend/nexus/app/modules/interview_engine/event_log/envelope.py`
- Test: `backend/nexus/tests/interview_engine/test_event_log_envelope.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_envelope.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_envelope.py -v`
Expected: 3 failures with `ModuleNotFoundError: No module named 'app.modules.interview_engine.event_log'`.

- [ ] **Step 3: Create the package init**

Create `backend/nexus/app/modules/interview_engine/event_log/__init__.py`:

```python
"""Audit-grade event log for the interview engine.

Phase 1 of the engine redesign. Provides:
- EventLogEnvelope: the single JSON file written per session
- EventLogEvent: one row in the envelope's events list
- EventCollector: in-memory aggregator fed by agent.py listeners
- EventLogSink protocol + LocalFileSink + S3Sink
- build_sink_from_settings: env-driven sink dispatch

See docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md §3.3-§3.4.
"""

from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)

__all__ = ["EventLogEnvelope", "EventLogEvent"]
```

- [ ] **Step 4: Create the envelope module**

Create `backend/nexus/app/modules/interview_engine/event_log/envelope.py`:

```python
"""Pydantic models for the audit event log envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EventLogEvent(BaseModel):
    """One event in the per-session envelope.

    `t_ms` is monotonic milliseconds since session start (relative).
    `wall_ms` is unix-epoch milliseconds (absolute).  Both are always
    present so the file can be sorted chronologically without external
    metadata.

    `redaction` carries the per-event mode at write time.  Even in
    `metadata` mode some events (e.g., audio.metrics.*) are inherently
    content-free; in `full` mode every event keeps its native payload.
    """

    t_ms: int = Field(ge=0)
    wall_ms: int = Field(ge=0)
    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction: Literal["metadata", "full"]


class EventLogEnvelope(BaseModel):
    """The single JSON file written per session.

    Schema is intentionally permissive on `payload` and `task_prompt_hashes`
    so adding new event kinds in later phases doesn't require migrations
    of historical files.  Audit-replay tooling SHOULD treat unknown event
    kinds as opaque.
    """

    session_id: str
    tenant_id: str
    correlation_id: str
    started_at: str
    closed_at: str | None
    controller_prompt_hash: str
    task_prompt_hashes: dict[str, str] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)
    redaction_mode: Literal["metadata", "full"]
    events: list[EventLogEvent] = Field(default_factory=list)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_envelope.py -v`
Expected: 3 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_log/__init__.py backend/nexus/app/modules/interview_engine/event_log/envelope.py backend/nexus/tests/interview_engine/test_event_log_envelope.py
git commit -m "$(cat <<'EOF'
feat(engine): event log envelope schema (Phase 1)

EventLogEnvelope + EventLogEvent pydantic models, with redaction_mode
required at envelope level so audit replay can branch without scanning
events. Permissive payload typing means adding new event kinds in later
phases doesn't break historical files.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Implement redaction module

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/event_log/redaction.py`
- Test: `backend/nexus/tests/interview_engine/test_event_log_redaction.py`

The redaction boundary in §5.2 of the spec is a per-event-kind whitelist of fields that must NEVER appear in `metadata` mode. Implement as a pure function so it's trivially testable.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_redaction.py`:

```python
"""Phase 1 — event log redaction.

Asserts the per-kind boundary in spec §5.2: every content field
enumerated there must be stripped in `metadata` mode and preserved in
`full` mode.
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.event_log.redaction import redact_payload


def test_metadata_mode_strips_stt_transcript() -> None:
    payload = {"transcript": "I have ten years experience", "transcript_chars": 28, "is_final": True}
    out = redact_payload("audio.stt.transcribed", payload, mode="metadata")
    assert "transcript" not in out
    assert out["transcript_chars"] == 28
    assert out["is_final"] is True


def test_full_mode_keeps_stt_transcript() -> None:
    payload = {"transcript": "I have ten years experience", "transcript_chars": 28}
    out = redact_payload("audio.stt.transcribed", payload, mode="full")
    assert out["transcript"] == "I have ten years experience"


def test_metadata_mode_strips_llm_message_content() -> None:
    payload = {"role": "assistant", "content": "How would you design X?", "content_chars": 24}
    out = redact_payload("llm.message.added", payload, mode="metadata")
    assert "content" not in out
    assert out["role"] == "assistant"
    assert out["content_chars"] == 24


def test_metadata_mode_strips_tool_args_and_output() -> None:
    payload = {
        "tool_name": "record_observation",
        "tool_call_id": "call_abc",
        "arguments": {"answer_summary": "Candidate said X"},
        "output": "next question text",
        "argument_keys": ["answer_summary", "wants_to_probe"],
    }
    out = redact_payload("llm.tool.executed", payload, mode="metadata")
    assert "arguments" not in out
    assert "output" not in out
    assert out["tool_name"] == "record_observation"
    assert out["argument_keys"] == ["answer_summary", "wants_to_probe"]


def test_metadata_mode_strips_disqualify_reason() -> None:
    payload = {"question_id": "q1", "reason": "candidate refused to answer"}
    out = redact_payload("disqualify.knockout", payload, mode="metadata")
    assert "reason" not in out
    assert out["question_id"] == "q1"


def test_metadata_mode_keeps_audio_metrics_payload() -> None:
    """Audio metrics carry no content — pass through unchanged."""
    payload = {"ttft": 0.312, "tokens_in": 850, "tokens_out": 42}
    out = redact_payload("audio.metrics.llm", payload, mode="metadata")
    assert out == payload


def test_unknown_kind_passes_through_in_metadata_mode() -> None:
    """Unknown kinds default to passthrough — the redaction layer is
    additive: every NEW event kind that carries content MUST add a rule
    here, but absence of a rule shouldn't crash production."""
    payload = {"foo": "bar"}
    out = redact_payload("future.unknown.kind", payload, mode="metadata")
    assert out == payload


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError):
        redact_payload("audio.stt.transcribed", {}, mode="enterprise")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_redaction.py -v`
Expected: 8 failures with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

Create `backend/nexus/app/modules/interview_engine/event_log/redaction.py`:

```python
"""Per-event-kind redaction boundary.

Enforces spec §5.2.  In `metadata` mode every CONTENT field listed in
``_CONTENT_FIELDS_BY_KIND`` is stripped; in `full` mode the payload is
returned unchanged.

This is a pure function — no IO, no globals, no logging. Reasoning about
it should require nothing but the input.

Adding a new event kind that carries content REQUIRES adding an entry
to ``_CONTENT_FIELDS_BY_KIND`` in the same PR. CI (when wired) greps for
new kinds in agent.py and fails if a content-bearing kind is missing
from this map.
"""

from __future__ import annotations

from typing import Any, Literal

# kind -> list of payload keys that carry user-content/PII and must be
# stripped in metadata mode.
_CONTENT_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "audio.stt.transcribed": ("transcript",),
    "llm.message.added": ("content",),
    "llm.tool.executed": ("arguments", "output"),
    "disqualify.knockout": ("reason",),
    # Phase 2 will add: task.completed (result_dict), controller.intent.end_early (summary)
}


def redact_payload(
    kind: str,
    payload: dict[str, Any],
    *,
    mode: Literal["metadata", "full"],
) -> dict[str, Any]:
    """Return a redacted copy of ``payload`` per ``mode``.

    ``mode="full"`` returns the input unchanged (a shallow copy).
    ``mode="metadata"`` strips every key listed in
    ``_CONTENT_FIELDS_BY_KIND`` for the given ``kind``.

    Unknown kinds pass through unchanged in both modes — see module
    docstring on the discipline required when adding new kinds.
    """
    if mode not in ("metadata", "full"):
        raise ValueError(f"invalid redaction mode: {mode!r}")

    if mode == "full":
        return dict(payload)

    blocked = _CONTENT_FIELDS_BY_KIND.get(kind, ())
    if not blocked:
        return dict(payload)
    return {k: v for k, v in payload.items() if k not in blocked}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_redaction.py -v`
Expected: 8 passes.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_log/redaction.py backend/nexus/tests/interview_engine/test_event_log_redaction.py
git commit -m "$(cat <<'EOF'
feat(engine): event log redaction module (Phase 1)

Pure-function redact_payload(kind, payload, mode) implementing spec §5.2.
Per-kind allowlist; unknown kinds pass through. Discipline: any new
content-bearing event kind requires an entry in _CONTENT_FIELDS_BY_KIND
in the same PR (CI gate to follow).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: EventLogSink protocol + LocalFileSink

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/event_log/sink.py`
- Create: `backend/nexus/app/modules/interview_engine/event_log/local_file.py`
- Test: `backend/nexus/tests/interview_engine/test_event_log_local_sink.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_local_sink.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_local_sink.py -v`
Expected: 5 failures with `ModuleNotFoundError`.

- [ ] **Step 3: Define the protocol**

Create `backend/nexus/app/modules/interview_engine/event_log/sink.py`:

```python
"""EventLogSink protocol — destination-agnostic envelope writer.

Sinks are SYNCHRONOUS by deliberate choice — boto3 (the only S3 client
in the codebase) is sync, and `asyncio.to_thread` is the standard escape
from agent.py's async context. Keeping sinks sync keeps each
implementation tiny and testable without an event loop.

Implementations:
- LocalFileSink (dev default; backend/nexus/app/modules/interview_engine/event_log/local_file.py)
- S3Sink (deploy gate; backend/nexus/app/modules/interview_engine/event_log/s3.py)
"""

from __future__ import annotations

from typing import Protocol

from app.modules.interview_engine.event_log.envelope import EventLogEnvelope


class EventLogSink(Protocol):
    """Write a session envelope to durable storage.

    Implementations MUST be idempotent on retry — the close handler in
    agent.py may be invoked more than once on certain shutdown paths.
    """

    def write(self, envelope: EventLogEnvelope) -> str:
        """Persist `envelope`. Return a string identifier of where it
        landed (file path / s3 key) for logging."""
        ...
```

- [ ] **Step 4: Implement LocalFileSink**

Create `backend/nexus/app/modules/interview_engine/event_log/local_file.py`:

```python
"""LocalFileSink — writes the envelope as JSON to a directory on disk.

Default for dev. Filename is `{session_id}.json`. Overwrites on retry.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from app.modules.interview_engine.event_log.envelope import EventLogEnvelope

logger = structlog.get_logger("engine.event_log.local")


def _validate_session_id_for_path(session_id: str) -> None:
    """Defense in depth: refuse anything that isn't bare UUID-shaped.

    The envelope's session_id field is upstream-validated as a UUID
    string by Nexus's session module, but the sink is the last line
    before disk and shouldn't trust its caller.
    """
    if not session_id:
        raise ValueError("session_id must not be empty")
    if "/" in session_id or ".." in session_id or "\x00" in session_id:
        raise ValueError(f"unsafe session_id for filesystem path: {session_id!r}")


class LocalFileSink:
    """Concrete sink writing one JSON file per envelope to ``directory``."""

    def __init__(self, *, directory: str) -> None:
        self._directory = Path(directory)

    def write(self, envelope: EventLogEnvelope) -> str:
        _validate_session_id_for_path(envelope.session_id)
        os.makedirs(self._directory, exist_ok=True)
        path = self._directory / f"{envelope.session_id}.json"
        # model_dump_json gives us the canonical pydantic serialization;
        # write_text is atomic-enough on POSIX for our scale.
        path.write_text(envelope.model_dump_json(), encoding="utf-8")
        logger.info(
            "event_log.local.written",
            path=str(path),
            session_id=envelope.session_id,
            events=len(envelope.events),
        )
        return str(path)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_local_sink.py -v`
Expected: 5 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_log/sink.py backend/nexus/app/modules/interview_engine/event_log/local_file.py backend/nexus/tests/interview_engine/test_event_log_local_sink.py
git commit -m "$(cat <<'EOF'
feat(engine): EventLogSink protocol + LocalFileSink (Phase 1)

Sync sink interface. LocalFileSink writes {session_id}.json into a
directory, creates the directory if missing, refuses path-traversal-shaped
session_ids as defense in depth. Dev-default destination per spec §3.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Implement S3Sink

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/event_log/s3.py`
- Test: `backend/nexus/tests/interview_engine/test_event_log_s3_sink.py`

S3 client pattern matches the existing `app/modules/candidates/resume_service.py` convention: a small `_create_s3_client()` helper that tests can monkeypatch with a fake.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_s3_sink.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_s3_sink.py -v`
Expected: 3 failures with `ModuleNotFoundError`.

- [ ] **Step 3: Implement S3Sink**

Create `backend/nexus/app/modules/interview_engine/event_log/s3.py`:

```python
"""S3Sink — writes the envelope to s3://{bucket}/{tenant_id}/{session_id}/engine_events.json.

Deploy-gate destination per spec §3.3. Bucket must have versioning ON
(deployment-side concern; not enforced here). Sync via boto3 — call from
asyncio.to_thread() in agent code.
"""

from __future__ import annotations

import boto3
import structlog

from app.config import settings
from app.modules.interview_engine.event_log.envelope import EventLogEnvelope

logger = structlog.get_logger("engine.event_log.s3")


def _create_s3_client():
    """Create a fresh S3 client. Overridden via monkeypatch in tests.

    Mirrors the same pattern used in app/modules/candidates/resume_service.py.
    """
    return boto3.client("s3", region_name=settings.aws_region)


class S3Sink:
    """Concrete sink writing one JSON object per envelope to S3."""

    def __init__(self, *, bucket: str) -> None:
        if not bucket:
            raise ValueError("S3Sink requires a non-empty bucket name")
        self._bucket = bucket

    def write(self, envelope: EventLogEnvelope) -> str:
        key = f"{envelope.tenant_id}/{envelope.session_id}/engine_events.json"
        body = envelope.model_dump_json()
        client = _create_s3_client()
        client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        s3_uri = f"s3://{self._bucket}/{key}"
        logger.info(
            "event_log.s3.written",
            uri=s3_uri,
            session_id=envelope.session_id,
            events=len(envelope.events),
        )
        return s3_uri
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_s3_sink.py -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_log/s3.py backend/nexus/tests/interview_engine/test_event_log_s3_sink.py
git commit -m "$(cat <<'EOF'
feat(engine): S3Sink for event log (Phase 1)

Writes envelope JSON to s3://{bucket}/{tenant_id}/{session_id}/engine_events.json
via boto3. _create_s3_client() pattern matches resume_service.py so tests
monkeypatch identically. Bucket-versioning is a deploy-side concern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement sink factory

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/event_log/factory.py`
- Modify: `backend/nexus/app/modules/interview_engine/event_log/__init__.py`
- Test: `backend/nexus/tests/interview_engine/test_event_log_factory.py`

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_factory.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_factory.py -v`
Expected: 4 failures with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the factory**

Create `backend/nexus/app/modules/interview_engine/event_log/factory.py`:

```python
"""Sink factory — env-driven dispatch."""

from __future__ import annotations

from app.config import settings
from app.modules.interview_engine.event_log.local_file import LocalFileSink
from app.modules.interview_engine.event_log.s3 import S3Sink
from app.modules.interview_engine.event_log.sink import EventLogSink


def build_sink_from_settings() -> EventLogSink | None:
    """Return the configured sink, or None when disabled.

    Reads `engine_event_log_sink` (`local`|`s3`|`none`).
    `local` -> LocalFileSink(directory=engine_event_log_dir)
    `s3` -> S3Sink(bucket=aws_s3_bucket_engine_events) — raises if bucket empty
    `none` -> None (no envelope is written; structlog stdout remains the only artifact)
    """
    sink_kind = settings.engine_event_log_sink
    if sink_kind == "none":
        return None
    if sink_kind == "local":
        return LocalFileSink(directory=settings.engine_event_log_dir)
    if sink_kind == "s3":
        bucket = settings.aws_s3_bucket_engine_events
        if not bucket:
            raise ValueError(
                "engine_event_log_sink=s3 but AWS_S3_BUCKET_ENGINE_EVENTS is empty"
            )
        return S3Sink(bucket=bucket)
    raise ValueError(f"unknown engine_event_log_sink: {sink_kind!r}")
```

- [ ] **Step 4: Re-export factory from package __init__**

Modify `backend/nexus/app/modules/interview_engine/event_log/__init__.py` — replace its body with:

```python
"""Audit-grade event log for the interview engine.

Phase 1 of the engine redesign. Provides:
- EventLogEnvelope: the single JSON file written per session
- EventLogEvent: one row in the envelope's events list
- EventCollector: in-memory aggregator fed by agent.py listeners
- EventLogSink protocol + LocalFileSink + S3Sink
- build_sink_from_settings: env-driven sink dispatch

See docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md §3.3-§3.4.
"""

from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)
from app.modules.interview_engine.event_log.factory import build_sink_from_settings
from app.modules.interview_engine.event_log.sink import EventLogSink

__all__ = [
    "EventLogEnvelope",
    "EventLogEvent",
    "EventLogSink",
    "build_sink_from_settings",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_factory.py -v`
Expected: 4 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_log/factory.py backend/nexus/app/modules/interview_engine/event_log/__init__.py backend/nexus/tests/interview_engine/test_event_log_factory.py
git commit -m "$(cat <<'EOF'
feat(engine): event log sink factory (Phase 1)

build_sink_from_settings() returns LocalFileSink | S3Sink | None per
ENGINE_EVENT_LOG_SINK. S3 selection without a configured bucket raises
on factory call rather than at first write. Factory is the only place
agent.py needs to import from event_log/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Implement EventCollector

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/event_log/collector.py`
- Modify: `backend/nexus/app/modules/interview_engine/event_log/__init__.py` (re-export)
- Test: `backend/nexus/tests/interview_engine/test_event_log_collector.py`

The collector is the single in-memory aggregator. agent.py's listeners call `collector.append(kind, payload, wall_ms)`; the collector owns the redaction call, the relative-time math, and the closure into the final envelope.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_collector.py`:

```python
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
    assert env.started_at.startswith("2024-12-23T")  # 1735000000000 ms = 2024-12-23T22:13:20+00:00


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_collector.py -v`
Expected: 7 failures with `ModuleNotFoundError`.

- [ ] **Step 3: Implement EventCollector**

Create `backend/nexus/app/modules/interview_engine/event_log/collector.py`:

```python
"""EventCollector — in-memory aggregator for the per-session envelope.

Lives for the duration of a single AgentSession. Receives append()
calls from agent.py listeners; produces a final EventLogEnvelope on
close().

Time math: t_ms is monotonic ms since the FIRST appended event (so the
first event always has t_ms=0). wall_ms is what the caller passed in
(LiveKit event objects expose .created_at as a unix-epoch float; agent.py
multiplies by 1000 before handing it here).

Redaction: applied at append time, not on close, so the in-memory list
never holds content the envelope-level mode promises to drop.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal

from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)
from app.modules.interview_engine.event_log.redaction import redact_payload

_RedactionMode = Literal["metadata", "full"]


class EventCollector:
    """In-memory aggregator. NOT thread-safe — agent.py runs on a single
    asyncio event loop and only that loop appends to the collector."""

    def __init__(
        self,
        *,
        session_id: str,
        tenant_id: str,
        correlation_id: str,
        controller_prompt_hash: str,
        model_versions: dict[str, str],
        redaction_mode: _RedactionMode,
        task_prompt_hashes: dict[str, str] | None = None,
    ) -> None:
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._controller_prompt_hash = controller_prompt_hash
        self._task_prompt_hashes = dict(task_prompt_hashes or {})
        self._model_versions = dict(model_versions)
        self._redaction_mode: _RedactionMode = redaction_mode
        self._events: list[EventLogEvent] = []
        # Set on first append.
        self._t0_monotonic: float | None = None
        self._first_wall_ms: int | None = None

    def append(self, *, kind: str, payload: dict[str, Any], wall_ms: int) -> None:
        """Record one event. Redaction is applied here, not on close."""
        now = time.monotonic()
        if self._t0_monotonic is None:
            self._t0_monotonic = now
            self._first_wall_ms = wall_ms
        t_ms = int((now - self._t0_monotonic) * 1000)
        redacted = redact_payload(kind, payload, mode=self._redaction_mode)
        self._events.append(
            EventLogEvent(
                t_ms=t_ms,
                wall_ms=wall_ms,
                kind=kind,
                payload=redacted,
                redaction=self._redaction_mode,
            )
        )

    def set_task_prompt_hash(self, *, question_id: str, sha: str) -> None:
        """Phase 2 will populate this per QuestionTask construction.

        Phase 1 leaves it empty — the field exists in the envelope so
        the schema is stable across phases."""
        self._task_prompt_hashes[question_id] = sha

    def close(self, *, closed_at: str) -> EventLogEnvelope:
        """Build and return the final envelope.

        ``started_at`` is the first appended event's wall time (UTC ISO-8601);
        when no events were appended, falls back to ``closed_at``.
        """
        if self._first_wall_ms is None:
            started_at = closed_at
        else:
            started_at = (
                datetime.fromtimestamp(self._first_wall_ms / 1000, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        return EventLogEnvelope(
            session_id=self._session_id,
            tenant_id=self._tenant_id,
            correlation_id=self._correlation_id,
            started_at=started_at,
            closed_at=closed_at,
            controller_prompt_hash=self._controller_prompt_hash,
            task_prompt_hashes=self._task_prompt_hashes,
            model_versions=self._model_versions,
            redaction_mode=self._redaction_mode,
            events=list(self._events),
        )
```

- [ ] **Step 4: Re-export from package __init__**

Modify `backend/nexus/app/modules/interview_engine/event_log/__init__.py` — update the import block and `__all__`:

```python
"""Audit-grade event log for the interview engine.

Phase 1 of the engine redesign. Provides:
- EventLogEnvelope: the single JSON file written per session
- EventLogEvent: one row in the envelope's events list
- EventCollector: in-memory aggregator fed by agent.py listeners
- EventLogSink protocol + LocalFileSink + S3Sink
- build_sink_from_settings: env-driven sink dispatch

See docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md §3.3-§3.4.
"""

from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)
from app.modules.interview_engine.event_log.factory import build_sink_from_settings
from app.modules.interview_engine.event_log.sink import EventLogSink

__all__ = [
    "EventCollector",
    "EventLogEnvelope",
    "EventLogEvent",
    "EventLogSink",
    "build_sink_from_settings",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_collector.py -v`
Expected: 7 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_log/collector.py backend/nexus/app/modules/interview_engine/event_log/__init__.py backend/nexus/tests/interview_engine/test_event_log_collector.py
git commit -m "$(cat <<'EOF'
feat(engine): EventCollector in-memory aggregator (Phase 1)

Owns the session's monotonic clock zero, applies redaction at append
time (not on close), and produces the final EventLogEnvelope on close().
NOT thread-safe — single-event-loop assumption matches agent.py's
LiveKit pattern. Schema-stable for Phase 2 task lifecycle additions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Engine-side OTel bootstrap

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py:82-104` (extend `prewarm` + add shutdown)
- Test: `backend/nexus/tests/interview_engine/test_engine_otel_bootstrap.py`

`app/ai/otel.py:bootstrap_tracer_provider()` already exists. We just call it from the engine's `prewarm` hook (which runs once at worker startup), set it as the global tracer provider, and call `shutdown()` from a process-exit hook so spans flush before exit.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_engine_otel_bootstrap.py`:

```python
"""Phase 1 — engine-side OTel bootstrap.

The engine container historically didn't have an OTel TracerProvider
registered, which means livekit-agents' built-in spans went nowhere even
when an OTLP endpoint was configured. Phase 1 wires a TracerProvider
into the engine's prewarm hook.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from livekit.agents import JobProcess

from app.modules.interview_engine import agent as agent_mod


def test_prewarm_bootstraps_otel_tracer_provider() -> None:
    """prewarm should call app.ai.otel.bootstrap_tracer_provider() and
    register it as the global provider."""
    proc = MagicMock(spec=JobProcess)
    proc.userdata = {}

    fake_provider = MagicMock()
    with patch.object(agent_mod, "bootstrap_tracer_provider", return_value=fake_provider) as bsp, \
         patch.object(agent_mod, "_otel_set_global_provider") as set_global:
        agent_mod.prewarm(proc)
        bsp.assert_called_once_with()
        set_global.assert_called_once_with(fake_provider)
        # Provider stashed on proc.userdata so the close path can shut it down.
        assert proc.userdata["otel_provider"] is fake_provider


def test_prewarm_still_loads_silero_vad() -> None:
    """Adding OTel must not break the existing Silero load."""
    proc = MagicMock(spec=JobProcess)
    proc.userdata = {}
    with patch.object(agent_mod.silero.VAD, "load") as vad_load, \
         patch.object(agent_mod, "bootstrap_tracer_provider", return_value=MagicMock()), \
         patch.object(agent_mod, "_otel_set_global_provider"):
        agent_mod.prewarm(proc)
        vad_load.assert_called_once()
        assert "vad" in proc.userdata
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_engine_otel_bootstrap.py -v`
Expected: 2 failures with `AttributeError` referencing `bootstrap_tracer_provider` on `agent_mod`.

- [ ] **Step 3: Modify agent.py to bootstrap OTel in prewarm**

Open `backend/nexus/app/modules/interview_engine/agent.py`. Add to the imports at the top (near the other `from app.ai.realtime import ...` block, around line 60):

```python
from app.ai.otel import bootstrap_tracer_provider
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider
```

Replace the body of `prewarm` (lines 82-101) — the entire function body — with:

```python
def prewarm(proc: JobProcess) -> None:
    """Process-startup hook.

    1. Bootstrap a TracerProvider so livekit-agents' built-in spans plus
       any explicit spans we add later (Phase 2 tasks) actually ship to
       an aggregator. Production-safe default: no env vars set -> spans
       go nowhere. Setting OTEL_EXPORTER_OTLP_ENDPOINT (Langfuse / Sentry
       / generic OTLP) flips the engine on.
    2. Load Silero VAD into shared process memory.

    Tuning knobs (``activation_threshold``, ``min_speech_duration``,
    ``min_silence_duration``) come from ``InterviewEngineConfig`` so the
    VAD sensitivity can be tuned per-deploy without a code change.
    Lower ``activation_threshold`` makes VAD catch quieter speech at the
    cost of occasional false-positive triggers from background noise.
    """
    provider = bootstrap_tracer_provider()
    _otel_set_global_provider(provider)
    proc.userdata["otel_provider"] = provider
    log.info("engine.otel.bootstrapped", service_name=settings.otel_service_name)

    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=settings.engine_silero_activation_threshold,
        min_speech_duration=settings.engine_silero_min_speech_duration,
        min_silence_duration=settings.engine_silero_min_silence_duration,
    )
    log.info(
        "engine.vad.prewarmed",
        activation_threshold=settings.engine_silero_activation_threshold,
        min_speech_duration=settings.engine_silero_min_speech_duration,
        min_silence_duration=settings.engine_silero_min_silence_duration,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_engine_otel_bootstrap.py -v`
Expected: 2 passes.

- [ ] **Step 5: Smoke-test prewarm doesn't break existing tests**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/ -v`
Expected: every test in the directory passes (existing `test_graceful_close.py` and `test_progress_attributes.py` plus all new Phase 1 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py backend/nexus/tests/interview_engine/test_engine_otel_bootstrap.py
git commit -m "$(cat <<'EOF'
feat(engine): bootstrap OTel TracerProvider in prewarm (Phase 1)

The engine container had no global TracerProvider, so livekit-agents'
built-in spans went nowhere. Bootstrap via the existing app.ai.otel
helper; production-safe default (no exporters) when no env vars are set.
Sets up the realtime path to ship spans to Langfuse / Sentry / OTLP
endpoints by env var alone — no code change required.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Prompt-file hashing helper

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/prompt_hash.py`
- Test: `backend/nexus/tests/interview_engine/test_prompt_hash.py`

The envelope records `controller_prompt_hash` so audit replay can recover the exact prompt body via `git show <hash>:<path>`. We need a tiny helper that hashes a prompt file by relative path under `backend/nexus/prompts/v1/`.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_prompt_hash.py`:

```python
"""Phase 1 — prompt file hashing.

Helper that returns sha256:HEX for a prompt file's body. Audit replay
uses the hash to recover the prompt body from git history.
"""

from __future__ import annotations

import hashlib

import pytest

from app.modules.interview_engine.prompt_hash import hash_prompt_file


def test_hash_prompt_file_matches_known_value() -> None:
    """interview/interviewer.txt exists in this repo (Phase 1 still ships
    on the legacy prompt). Hash should be deterministic + content-only."""
    sha = hash_prompt_file("interview/interviewer.txt")
    assert sha.startswith("sha256:")
    # Hex section is 64 chars
    assert len(sha) == len("sha256:") + 64


def test_hash_prompt_file_is_deterministic() -> None:
    a = hash_prompt_file("interview/interviewer.txt")
    b = hash_prompt_file("interview/interviewer.txt")
    assert a == b


def test_hash_prompt_file_raises_on_missing() -> None:
    with pytest.raises(FileNotFoundError):
        hash_prompt_file("interview/does_not_exist.txt")


def test_hash_prompt_file_uses_sha256_of_bytes() -> None:
    """Sanity check that the helper hashes the file's bytes, not its
    name — manually compute and compare."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.get("interview/interviewer")
    expected_hex = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert hash_prompt_file("interview/interviewer.txt") == f"sha256:{expected_hex}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_prompt_hash.py -v`
Expected: 4 failures with `ModuleNotFoundError`.

- [ ] **Step 3: Find the prompt root path**

Run: `grep -n "prompts/v1\|PROMPTS_ROOT\|prompt_loader" backend/nexus/app/ai/prompts.py | head -10`
Note the resolution path used by the existing `prompt_loader` (it reads from `backend/nexus/prompts/v1/`).

- [ ] **Step 4: Implement the helper**

Create `backend/nexus/app/modules/interview_engine/prompt_hash.py`:

```python
"""Prompt-file SHA-256 helper.

Audit replay records ``sha256:<hex>`` for each prompt file the agent
loaded at session start. Recovery of the exact prompt body for a
historical session is then ``git show <hash>:prompts/v1/<relpath>`` —
git is durable, content-addressed, and access-controlled.

The hash space is the prompt file BYTES, not the path. Two files with
identical content have identical hashes (intentional — same prompt,
same hash, regardless of where it's mounted).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The repo's prompt root. `app.ai.prompts.prompt_loader` reads from this
# same directory; we resolve the path the same way it does so test and
# runtime see identical bytes.
_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts" / "v1"


def hash_prompt_file(relative_path: str) -> str:
    """Return ``sha256:<hex>`` of the prompt file at
    ``backend/nexus/prompts/v1/<relative_path>``.

    Raises FileNotFoundError if the path does not exist.
    """
    path = _PROMPTS_ROOT / relative_path
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    return f"sha256:{digest}"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_prompt_hash.py -v`
Expected: 4 passes.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/prompt_hash.py backend/nexus/tests/interview_engine/test_prompt_hash.py
git commit -m "$(cat <<'EOF'
feat(engine): prompt-file sha256 helper (Phase 1)

hash_prompt_file(relative_path) returns 'sha256:<hex>' for prompt files
under prompts/v1/. Audit replay uses these hashes to recover prompt
bodies via 'git show <hash>:<path>'; no separate prompt store. Hash
space is the file bytes, not the path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Wire EventCollector + sink into agent.py

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py` (multiple edits)
- Test: integration test in Task 11

This is the largest single edit in Phase 1: instantiate the collector + sink in `entrypoint`, append events from each existing `_wire_session_observability` listener, and write the envelope from `_handle_close`.

- [ ] **Step 1: Add imports + helper to agent.py**

Open `backend/nexus/app/modules/interview_engine/agent.py`. Add to the imports near the top (after the existing `from app.modules.interview_runtime import build_session_config` line):

```python
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.interview_engine.prompt_hash import hash_prompt_file
```

- [ ] **Step 2: Construct EventCollector + sink in entrypoint**

In `backend/nexus/app/modules/interview_engine/agent.py`, find the entrypoint function around line 107. After the existing `_log_session_setup(config)` call (around line 146) and before the `agent = InterviewerAgent(...)` line (around line 148), insert:

```python
    # Phase 1 — audit event log. Build the sink from settings (None when
    # ENGINE_EVENT_LOG_SINK=none) and a per-session collector that the
    # observability listeners feed via append().
    event_sink: EventLogSink | None = build_sink_from_settings()
    event_collector = EventCollector(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
        controller_prompt_hash=hash_prompt_file("interview/interviewer.txt"),
        model_versions={
            "llm": ai_config.interview_llm_model,
            "stt": ai_config.interview_stt_model,
            "tts": ai_config.interview_tts_model,
            "turn_detector_unlikely_threshold": str(
                ai_config.interview_turn_detector_unlikely_threshold
            ),
            "noise_cancellation_model": ai_config.interview_noise_cancellation_model,
            "noise_cancellation_level": str(
                ai_config.interview_noise_cancellation_level
            ),
        },
        redaction_mode=settings.engine_event_log_redaction,
    )
    log.info(
        "engine.event_log.opened",
        sink=settings.engine_event_log_sink,
        redaction=settings.engine_event_log_redaction,
    )
```

- [ ] **Step 3: Pass collector through to wiring + close handler**

Still in `entrypoint`, the existing call sites are:

```python
    if settings.engine_log_audio_events:
        _wire_session_observability(
            session,
            log_verbose_content=settings.engine_log_user_transcripts,
        )

    _wire_close_handler(session, agent)
```

Replace with:

```python
    _wire_session_observability(
        session,
        collector=event_collector,
        log_verbose_content=settings.engine_log_user_transcripts,
        log_audio_events=settings.engine_log_audio_events,
    )

    _wire_close_handler(session, agent, collector=event_collector, sink=event_sink)
```

- [ ] **Step 4: Update `_wire_session_observability` signature + listeners**

In the same file, replace the entire `_wire_session_observability` function (currently lines 274-457) with the version below.

The shape: each listener still does its existing structlog `log.info(...)`, AND now also `collector.append(kind=..., payload=..., wall_ms=int(ev.created_at * 1000))`. The `log_audio_events` flag gates the structlog calls (production may want them off); the collector is fed regardless.

```python
def _wire_session_observability(
    session: AgentSession,
    *,
    collector: EventCollector,
    log_verbose_content: bool,
    log_audio_events: bool,
) -> None:
    """Attach structlog + EventCollector listeners covering every AgentSession event.

    Two destinations:
    1. structlog stdout — live debugging, gated behind ``log_audio_events``
       so production can quiet it without losing the durable artifact.
    2. EventCollector — durable per-session audit envelope, always fed
       so a session that crashes mid-flight still has a partial record
       on disk (whatever was written before the crash).

    PII discipline:
    - Always-on payload fields are metadata only (state names, finality
      flags, character counts, token counts, latency numbers, error types).
    - Verbose content (verbatim STT transcripts, LLM message bodies,
      function-tool args/outputs) is gated TWICE: structlog by
      ``log_verbose_content``, and the EventCollector by its own
      ``redaction_mode``. Production runs both at minimum (audio events
      on, verbose off, metadata redaction).
    """
    state: dict[str, float | None] = {"t0_monotonic": None}

    def _ts(ev_created_at: float) -> dict[str, int]:
        now = time.monotonic()
        if state["t0_monotonic"] is None:
            state["t0_monotonic"] = now
        elapsed_ms = int((now - state["t0_monotonic"]) * 1000)
        return {
            "elapsed_ms": elapsed_ms,
            "wall_ms": int(ev_created_at * 1000),
        }

    def _emit(kind: str, payload: dict[str, object], ev_created_at: float) -> None:
        wall_ms = int(ev_created_at * 1000)
        collector.append(kind=kind, payload=dict(payload), wall_ms=wall_ms)
        if log_audio_events:
            log.info(kind, **payload, **_ts(ev_created_at))

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        _emit(
            "audio.user.state",
            {"old_state": ev.old_state, "new_state": ev.new_state},
            ev.created_at,
        )

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        _emit(
            "audio.agent.state",
            {"old_state": ev.old_state, "new_state": ev.new_state},
            ev.created_at,
        )

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: UserInputTranscribedEvent) -> None:
        payload: dict[str, object] = {
            "is_final": ev.is_final,
            "transcript_chars": len(ev.transcript),
            "language": str(ev.language) if ev.language else None,
            "speaker_id": ev.speaker_id,
        }
        if log_verbose_content:
            payload["transcript"] = ev.transcript
        _emit("audio.stt.transcribed", payload, ev.created_at)

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        _emit(f"audio.metrics.{m.type}", payload, ev.created_at)

    @session.on("conversation_item_added")
    def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        role = getattr(item, "role", None) or getattr(item, "type", None)
        content_text = getattr(item, "text_content", None)
        if callable(content_text):
            try:
                content_text = content_text()
            except Exception:  # noqa: BLE001
                content_text = None
        payload: dict[str, object] = {
            "role": role,
            "item_type": getattr(item, "type", None),
        }
        if isinstance(content_text, str):
            payload["content_chars"] = len(content_text)
            if log_verbose_content:
                payload["content"] = content_text
        _emit("llm.message.added", payload, ev.created_at)

    @session.on("function_tools_executed")
    def _on_tools_executed(ev: FunctionToolsExecutedEvent) -> None:
        for call, output in ev.zipped():
            payload: dict[str, object] = {
                "tool_name": call.name,
                "tool_call_id": getattr(call, "call_id", None),
                "has_output": output is not None,
                "output_is_error": (
                    bool(getattr(output, "is_error", False)) if output else None
                ),
            }
            # Always include the *keys* of arguments (no values) so audit
            # replay can see which args the LLM produced without leaking
            # their content.
            try:
                arg_keys = list(getattr(call, "arguments", {}) or {})
            except Exception:  # noqa: BLE001
                arg_keys = []
            payload["argument_keys"] = arg_keys
            if log_verbose_content:
                payload["arguments"] = getattr(call, "arguments", None)
                payload["output"] = (
                    getattr(output, "output", None) if output else None
                )
            _emit("llm.tool.executed", payload, ev.created_at)

    @session.on("agent_false_interruption")
    def _on_false_interruption(ev: AgentFalseInterruptionEvent) -> None:
        _emit("audio.interruption.false", {"resumed": ev.resumed}, ev.created_at)

    @session.on("overlapping_speech")
    def _on_overlap(ev: OverlappingSpeechEvent) -> None:
        ev_created = getattr(ev, "created_at", time.time())
        _emit("audio.overlap", {}, ev_created)

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent) -> None:
        try:
            usage = ev.usage.model_dump()
        except Exception:  # noqa: BLE001
            usage = {"raw": str(ev.usage)}
        _emit("session.usage", usage, ev.created_at)

    @session.on("speech_created")
    def _on_speech_created(ev: SpeechCreatedEvent) -> None:
        _emit(
            "audio.speech.created",
            {"source": ev.source, "user_initiated": ev.user_initiated},
            ev.created_at,
        )

    @session.on("error")
    def _on_error(ev: ErrorEvent) -> None:
        payload = {
            "source": type(ev.source).__name__,
            "error": str(ev.error),
            "error_type": type(ev.error).__name__,
        }
        # Errors bypass the log_audio_events gate — always log.
        log.error("audio.pipeline.error", **payload, **_ts(ev.created_at))
        collector.append(
            kind="audio.pipeline.error",
            payload=payload,
            wall_ms=int(ev.created_at * 1000),
        )
```

- [ ] **Step 5: Update `_wire_close_handler` to write the envelope**

Replace the existing `_wire_close_handler` (currently around lines 460-494) with:

```python
def _wire_close_handler(
    session: AgentSession,
    agent: InterviewerAgent,
    *,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Attach the close-event handler that:
    1. persists the SessionResult (existing behavior)
    2. publishes the session_outcome attribute (existing behavior)
    3. closes the EventCollector and writes the envelope to the sink (Phase 1 NEW)

    Two paths reach close:

    1. State machine emits Action.CLOSE → ``record_observation`` already
       persisted + published ``session_outcome='completed'`` and set
       ``agent._persisted=True``. The close handler is a no-op for the
       SessionResult path; the envelope is still written here.
    2. Candidate disconnects mid-session — clicks End Call, refreshes the
       page (which closes their old room session), or network drops past
       the SDK's reconnect window. ``close_on_disconnect=True`` (default)
       fires the AgentSession close with ``reason=PARTICIPANT_DISCONNECTED``.
       The state machine never reached CLOSE; we persist a partial result
       here, AND we write a partial envelope.

    ``ERROR`` reason (LLM/STT/TTS plugin error) routes outcome='error' so the
    frontend's ``useSessionOutcome`` hook surfaces ``DisconnectError`` with
    ``ENGINE_ERROR`` rather than ``CompletionScreen``. The envelope is still
    written for forensic review.
    """

    @session.on("close")
    def _on_close(ev: CloseEvent) -> None:
        asyncio.create_task(_handle_close(ev, agent, collector, sink))
```

- [ ] **Step 6: Update `_handle_close` to drain the envelope**

Replace `_handle_close` (currently around lines 496-523) with:

```python
async def _handle_close(
    ev: CloseEvent,
    agent: InterviewerAgent,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Async body of the close-event handler. See ``_wire_close_handler`` docstring."""
    log = structlog.get_logger("interview-engine")
    log.info(
        "session.close",
        reason=ev.reason.value,
        has_error=bool(ev.error),
        already_persisted=agent._persisted,
    )

    outcome = "error" if ev.reason == CloseReason.ERROR else "completed"

    # 1. Persist the SessionResult (existing behavior).
    if not agent._persisted:
        try:
            result = agent._build_session_result()
            await agent._persist_result(result)
            agent._persisted = True
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # 2. Publish session_outcome (existing behavior).
    await agent._publish_session_outcome(outcome)

    # 3. Phase 1 — close the EventCollector and write the envelope.
    if sink is not None:
        envelope = collector.close(
            closed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        try:
            target = await asyncio.to_thread(sink.write, envelope)
            log.info("session.close.event_log_written", target=target)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.event_log_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
```

Add the missing imports near the top of `agent.py` if not already present:

```python
from datetime import datetime, timezone
```

- [ ] **Step 7: Run the existing engine tests to confirm nothing regressed**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_progress_attributes.py tests/interview_engine/test_graceful_close.py -v`
Expected: every existing test still passes.

- [ ] **Step 8: Run the full Phase 1 test suite**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/ -v`
Expected: every test passes.

- [ ] **Step 9: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(engine): wire EventCollector + sink into agent.py (Phase 1)

Instantiates the collector and sink in entrypoint(); each existing
_wire_session_observability listener now feeds the collector via _emit().
_wire_close_handler closes the envelope and writes it to the sink via
asyncio.to_thread (sinks are sync). Existing graceful-close + progress
behavior preserved. Error events bypass the log_audio_events gate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: End-to-end integration test

**Files:**
- Create: `backend/nexus/tests/interview_engine/test_event_log_integration.py`

The unit tests cover each piece in isolation. This integration test wires a fake AgentSession-shaped object, fires the listener inputs the way LiveKit does, and asserts that the final on-disk JSON parses cleanly into an `EventLogEnvelope`.

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/interview_engine/test_event_log_integration.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/test_event_log_integration.py -v`
Expected: 2 passes.

- [ ] **Step 3: Run the FULL Phase 1 test suite + the existing engine tests one more time**

Run: `cd backend/nexus && uv run pytest tests/interview_engine/ -v`
Expected: every test passes.

- [ ] **Step 4: Verify the engine still imports cleanly**

Run: `cd backend/nexus && uv run python -c "from app.modules.interview_engine import agent; print('engine imports OK')"`
Expected: prints `engine imports OK` with no exceptions.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/tests/interview_engine/test_event_log_integration.py
git commit -m "$(cat <<'EOF'
test(engine): Phase 1 end-to-end envelope integration test

Drives EventCollector + LocalFileSink with a slim-but-realistic session
timeline; asserts the on-disk JSON parses back to an EventLogEnvelope,
that metadata-mode redaction stripped STT transcript + tool arguments,
that audio metrics passed through, and that t_ms values are monotonic.
Satisfies spec §9 Phase 1 test gate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Acceptance gates for Phase 1

When the plan is fully executed:

1. `pytest tests/interview_engine/` passes with all new + existing tests green.
2. Starting the engine container with default env (`ENGINE_EVENT_LOG_SINK=local`,
   `ENGINE_EVENT_LOG_DIR=/tmp/engine-events`, no `OTEL_*` set) and running a real
   interview session against the live `7d96c5d1` Bot Screening stage produces a file
   at `/tmp/engine-events/{session_id}.json` that parses cleanly into
   `EventLogEnvelope`.
3. Setting `OTEL_DEV_CONSOLE_EXPORTER=true` produces console-printed spans for LLM
   chat completions during the live session.
4. With `ENGINE_EVENT_LOG_REDACTION=metadata` (default), the produced envelope
   contains zero verbatim transcripts and zero tool argument values.
5. With `ENGINE_EVENT_LOG_REDACTION=full`, the produced envelope contains verbatim
   transcripts and tool argument values.
6. With `ENGINE_EVENT_LOG_SINK=none`, no envelope is written and no error is logged
   on close.

---

## Self-review notes

**Spec coverage:** Phase 1 deliverables in spec §8 are: `EventLogSink` interface
(Task 4), `LocalFileSink` (Task 4), redaction module (Task 3), envelope schema (Task 2),
engine-side `bootstrap_tracer_provider()` (Task 8), and wiring through existing
`_wire_session_observability` listeners (Task 10). Plus the Phase 1 test gates in §9
(unit tests for sink + redaction + integration test). Task 5 (`S3Sink`) and Task 6
(factory) are explicit in spec §3.3 and §8 ("sink-agnostic"). Task 7 (`EventCollector`)
is the in-memory aggregator implied by §3.4 ("envelope is built up over the session"). Task 9
(prompt hashing) is required by §3.3 envelope schema (`controller_prompt_hash`).

**Placeholder scan:** Every step has actual code. No "TBD", no "implement later", no
"similar to above" without code, no abstract instructions.

**Type consistency:** `EventLogSink.write()` signature takes `EventLogEnvelope` and
returns `str` consistently across Tasks 4, 5, 10. `EventCollector.append()` takes
keyword args `kind` (str), `payload` (dict), `wall_ms` (int) consistently across
Tasks 7, 10, 11. `redact_payload(kind, payload, *, mode)` consistent across Tasks 3 and 7.
`build_sink_from_settings()` returns `EventLogSink | None` consistent across Tasks 6 and 10.

**Frequent commits:** 11 commits — one per task. Each task is the smallest unit that
leaves the engine still working.
