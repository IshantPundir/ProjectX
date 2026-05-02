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
