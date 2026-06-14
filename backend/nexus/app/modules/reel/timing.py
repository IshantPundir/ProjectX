"""Reel timing — map engine (session) time onto the recording's (video) clock.

Gen-3 provides a word-timed ``SessionEvidence.transcript`` whose spans/words are
session-relative ms (engine session start = 0). The recording is the same clock
shifted by a single constant:

    video_ms = session_ms + offset

where ``offset`` = the engine session start minus the recording start. There is
NO event-log, NO VAD cross-correlation, NO pipeline-lag — the recording and the
engine clock agree to ~tens of ms, and the offset captures the start skew.

Pure + import-light.
"""
from __future__ import annotations

from datetime import datetime


def recording_offset_ms(session_started_at: datetime,
                        recording_started_at: datetime) -> int:
    """``video_ms = session_ms + offset``; offset = engine session start - recording start.

    Both arguments are ``datetime``s. A positive offset means the engine session
    began after the recording (session_ms must be shifted forward to land on the
    video clock); a negative offset means the session began first.
    """
    return round((session_started_at - recording_started_at).total_seconds() * 1000)
