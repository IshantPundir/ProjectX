import pytest

from app.modules.reel.offset import compute_offset_ms, parse_first_onset_ms


def test_compute_offset_is_opener_minus_video_onset():
    # opener at session-ms 3520; the recording has pre-roll before the engine's
    # monotonic zero, so the opener is heard 6040ms into the video.
    # offset = 3520 - 6040 = -2520 (negative, as expected for pre-roll).
    assert compute_offset_ms(opener_session_ms=3520, video_onset_ms=6040) == -2520


def test_parse_first_onset_reads_first_silence_end():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 0\n"
        "[silencedetect @ 0x1] silence_end: 1.234 | silence_duration: 1.234\n"
        "[silencedetect @ 0x1] silence_start: 5.0\n"
        "[silencedetect @ 0x1] silence_end: 6.5 | silence_duration: 1.5\n"
    )
    assert parse_first_onset_ms(stderr) == 1234


def test_parse_first_onset_none_when_no_silence_end():
    assert parse_first_onset_ms("no markers here") is None
