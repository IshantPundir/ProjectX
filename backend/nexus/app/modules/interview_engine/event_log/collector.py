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
