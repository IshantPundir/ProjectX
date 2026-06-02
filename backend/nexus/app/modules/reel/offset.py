"""Transcript->video offset calibration (spike: measure empirically).

video_ms = session_ms - offset_ms.  offset_ms = opener_session_ms - video_onset_ms,
typically negative because the recording starts before the engine's monotonic zero.
Both clocks run at real-time 1:1, so a single constant per session is exact.
"""
from __future__ import annotations

import asyncio
import re

_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def compute_offset_ms(*, opener_session_ms: int, video_onset_ms: int) -> int:
    return int(video_onset_ms) - int(opener_session_ms)


def parse_first_onset_ms(stderr: str) -> int | None:
    """First speech onset in the video = end of the leading silence (first
    ``silence_end`` reported by ffmpeg's silencedetect). ms, or None."""
    m = _SILENCE_END_RE.search(stderr)
    if not m:
        return None
    return int(round(float(m.group(1)) * 1000))


async def measure_video_onset_ms(recording_path: str, *, noise_db: int = -30,
                                 min_silence_s: float = 0.3) -> int | None:
    """Shell ffmpeg silencedetect over the recording's audio; return the first
    onset in ms. Best-effort: returns None if no leading silence is detected."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", recording_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return parse_first_onset_ms(stderr.decode("utf-8", "replace"))
