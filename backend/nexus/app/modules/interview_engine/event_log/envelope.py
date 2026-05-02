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
