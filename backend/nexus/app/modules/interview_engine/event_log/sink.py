"""v2-native local-file audit sink (self-contained — no interview_engine import; D6/CMI-1).

Writes the v2 EventLogEnvelope to engine_event_log_dir/{session_id}.json. Mirrors v1's
LocalFileSink (path-safety + overwrite-on-retry) but is its own copy so the M6 v1
deletion can't break it.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog

from app.modules.interview_engine.event_log.envelope import EventLogEnvelope

log = structlog.get_logger("interview_engine.event_log")


def _validate_session_id_for_path(session_id: str) -> None:
    if not session_id:
        raise ValueError("session_id must not be empty")
    if "/" in session_id or ".." in session_id or "\x00" in session_id:
        raise ValueError(f"unsafe session_id for filesystem path: {session_id!r}")


class LocalFileSink:
    def __init__(self, *, directory: str) -> None:
        self._directory = Path(directory)

    def write(self, envelope: EventLogEnvelope) -> str:
        """Write the envelope to {directory}/{session_id}.json and return the path.

        Path-safety validated before any filesystem access. Overwrites on retry
        (idempotent — the envelope is deterministic for a given session).
        """
        _validate_session_id_for_path(envelope.session_id)
        os.makedirs(self._directory, exist_ok=True)
        path = self._directory / f"{envelope.session_id}.json"
        path.write_text(envelope.model_dump_json(), encoding="utf-8")
        log.info(
            "engine.v2.event_log.written",
            path=str(path),
            session_id=envelope.session_id,
            events=len(envelope.events),
        )
        return str(path)
