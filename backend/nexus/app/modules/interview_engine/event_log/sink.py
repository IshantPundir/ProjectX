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
