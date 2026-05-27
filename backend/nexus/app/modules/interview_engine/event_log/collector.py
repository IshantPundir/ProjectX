"""Accumulates v2 events + decision records and builds the per-session envelope."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.modules.interview_engine.audit import TurnDecisionRecord
from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EventCollector:
    def __init__(
        self,
        *,
        session_id: str,
        tenant_id: str,
        correlation_id: str,
        redaction_mode: str = "metadata",
    ) -> None:
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._redaction_mode = redaction_mode
        self._started_at = _now_iso()
        self._events: list[EventLogEvent] = []

    def record(self, kind: str, payload: dict[str, Any], *, t_ms: int, wall_ms: int) -> None:
        self._events.append(
            EventLogEvent(
                t_ms=t_ms, wall_ms=wall_ms, kind=kind, payload=payload,
                redaction=self._redaction_mode,  # type: ignore[arg-type]
            )
        )

    def record_decision(self, record: TurnDecisionRecord, *, t_ms: int, wall_ms: int) -> None:
        self.record("turn.decision", record.model_dump(mode="json"), t_ms=t_ms, wall_ms=wall_ms)

    def envelope(self, *, closed_at: str | None = None) -> EventLogEnvelope:
        return EventLogEnvelope(
            session_id=self._session_id,
            tenant_id=self._tenant_id,
            correlation_id=self._correlation_id,
            started_at=self._started_at,
            closed_at=closed_at,
            redaction_mode=self._redaction_mode,  # type: ignore[arg-type]
            events=list(self._events),
        )
