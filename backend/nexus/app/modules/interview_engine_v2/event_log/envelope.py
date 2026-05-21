"""v2 per-session audit envelope (shape mirrors interview_engine/event_log/envelope.py)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EventLogEvent(BaseModel):
    t_ms: int = Field(ge=0, description="Monotonic ms since session start (relative).")
    wall_ms: int = Field(ge=0, description="Unix-epoch ms (absolute).")
    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction: Literal["metadata", "full"] = "metadata"


class EventLogEnvelope(BaseModel):
    session_id: str
    tenant_id: str
    correlation_id: str
    started_at: str
    closed_at: str | None = None
    engine_version: str = "v2"
    model_versions: dict[str, str] = Field(default_factory=dict)
    redaction_mode: Literal["metadata", "full"] = "metadata"
    events: list[EventLogEvent] = Field(default_factory=list)
