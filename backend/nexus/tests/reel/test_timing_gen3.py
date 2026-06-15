"""Reel timing (gen-3) — the pure recording-offset mapper.

Gen-3 replaces the event-log/VAD cross-correlation with a single pure offset:
``video_ms = session_ms + offset`` where ``offset`` = engine session start minus
recording start. No ffmpeg, no events, no pipeline-lag.
"""
from datetime import UTC, datetime

from app.modules.reel.timing import recording_offset_ms


def test_offset_is_session_start_minus_recording_start():
    rec_start = datetime(2026, 6, 14, 10, 0, 0, 0, tzinfo=UTC)
    sess_start = datetime(2026, 6, 14, 10, 0, 0, 90_000, tzinfo=UTC)  # +90ms
    assert recording_offset_ms(sess_start, rec_start) == 90


def test_offset_negative_when_session_before_recording():
    rec_start = datetime(2026, 6, 14, 10, 0, 1, 0, tzinfo=UTC)
    sess_start = datetime(2026, 6, 14, 10, 0, 0, 500_000, tzinfo=UTC)  # -500ms
    assert recording_offset_ms(sess_start, rec_start) == -500


def test_offset_seconds_scale():
    rec_start = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    sess_start = datetime(2026, 6, 14, 10, 0, 3, 250_000, tzinfo=UTC)  # +3.25s
    assert recording_offset_ms(sess_start, rec_start) == 3_250


def test_offset_zero_when_equal():
    t = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    assert recording_offset_ms(t, t) == 0
