"""Reel timing — map engine (session) time onto the recording's (video) clock.

Gen-3 provides a word-timed ``SessionEvidence.transcript`` whose spans/words are
session-relative ms (engine session start = 0). The recording is the same clock
shifted by a single constant:

    video_ms = session_ms + offset

where ``offset`` (the STATIC offset) = the engine session start minus the
recording start (``recording_offset_ms``).

Static offset alone leaves a small RESIDUAL lag: the STT stream starts a moment
after ``meta.started_at`` and the realtime pipeline adds latency, so clips land a
constant fraction of a second early/late. ``measure_offset_correction`` recovers
that residual δ per-session by cross-correlating the candidate's speech envelope
(STT word times + the static offset, already in video ms) against the recording's
actual speech (ffmpeg ``silencedetect``). The render offset becomes
``static_offset + δ``. Calibration is BEST-EFFORT: an empty/low-confidence
correlation returns 0 → the renderer falls back to the static offset.

Import-light: the only heavy dependency (ffmpeg) is shelled out, and the import
itself (``asyncio``/``subprocess``) is std-lib only, so the lean image can still
import this module — ``recording_speech_intervals`` is just never CALLED there.
"""
from __future__ import annotations

import asyncio
from datetime import datetime


def recording_offset_ms(session_started_at: datetime,
                        recording_started_at: datetime) -> int:
    """``video_ms = session_ms + offset``; offset = engine session start - recording start.

    Both arguments are ``datetime``s. A positive offset means the engine session
    began after the recording (session_ms must be shifted forward to land on the
    video clock); a negative offset means the session began first.
    """
    return round((session_started_at - recording_started_at).total_seconds() * 1000)


# --- ffmpeg silencedetect → recording speech envelope ----------------------

def _parse_silencedetect(stderr: str, total_ms: int) -> list[tuple[int, int]]:
    """Parse ffmpeg ``silencedetect`` stderr → the SPEECH intervals (ms).

    ``silencedetect`` logs ``silence_start: <s>`` / ``silence_end: <s>`` pairs for
    each detected SILENCE window. The speech intervals are the GAPS between those
    windows (clamped to ``[0, total_ms]``). Pure — unit-tested directly.
    """
    silences: list[tuple[float, float | None]] = []
    cur_start: float | None = None
    for line in stderr.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                cur_start = float(line.split("silence_start:")[1].split("|")[0].strip())
            except (ValueError, IndexError):
                cur_start = None
        elif "silence_end:" in line:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip())
            except (ValueError, IndexError):
                continue
            # An end without a matching start (ffmpeg quirk) starts the window at 0.
            silences.append((cur_start if cur_start is not None else 0.0, end))
            cur_start = None
    # A trailing silence_start with no end runs to the file's end.
    if cur_start is not None:
        silences.append((cur_start, None))

    speech: list[tuple[int, int]] = []
    cursor = 0  # ms — where the previous silence left off (speech resumes here)
    for s_start, s_end in silences:
        s_start_ms = max(0, round(s_start * 1000))
        if s_start_ms > cursor:
            speech.append((cursor, min(s_start_ms, total_ms)))
        cursor = total_ms if s_end is None else max(cursor, round(s_end * 1000))
    if cursor < total_ms:
        speech.append((cursor, total_ms))
    return [(a, b) for a, b in speech if b > a]


async def _probe_total_ms(rec_path: str) -> int:
    """Recording duration in ms via ffprobe (the speech-envelope tail bound)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", rec_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return round(float(out.decode().strip()) * 1000)
    except (ValueError, AttributeError):
        return 0


async def recording_speech_intervals(rec_path: str, *, noise_db: int = -30,
                                     min_silence_s: float = 0.4) -> list[tuple[int, int]]:
    """Speech intervals (ms) of the recording's audio via ffmpeg ``silencedetect``.

    Shells ``ffmpeg -i <rec> -af silencedetect=noise=<noise_db>dB:d=<min_silence_s>
    -f null -`` and parses the SILENCE windows out of stderr; returns their
    complement (the speech). ffmpeg-only — only CALLED in the vision image (the
    actor path); ``timing.py`` stays importable in the lean image.
    """
    total_ms = await _probe_total_ms(rec_path)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostats", "-i", rec_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg silencedetect failed ({proc.returncode}): "
            f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return _parse_silencedetect(stderr.decode("utf-8", "replace"), total_ms)


# --- pure cross-correlation -------------------------------------------------

def _rasterize(intervals: list[tuple[int, int]], bin_ms: int,
               n_bins: int, base: int) -> list[bool]:
    """Mark bins covered by any interval. ``base`` is the grid origin (ms)."""
    grid = [False] * n_bins
    for a, b in intervals:
        lo = max(0, (a - base) // bin_ms)
        hi = min(n_bins, (b - base + bin_ms - 1) // bin_ms)
        for i in range(lo, hi):
            grid[i] = True
    return grid


def measure_offset_correction(cand_intervals: list[tuple[int, int]],
                              rec_speech: list[tuple[int, int]],
                              *, max_lag_ms: int = 8000, bin_ms: int = 40) -> int:
    """Residual offset δ (ms) that best aligns the candidate envelope to the recording.

    Both lists are SPEECH intervals already on the video clock (the candidate ones
    carry the static offset). We rasterize both onto a shared ``bin_ms`` grid and
    find the integer shift δ ∈ [−max_lag_ms, +max_lag_ms] (in bin steps) that
    MAXIMIZES the count of bins where the δ-shifted candidate envelope overlaps the
    recording envelope. SIGN: δ > 0 means the candidate (and thus every clip) must
    be pushed LATER to match the recording — the final render offset is
    ``static_offset + δ``.

    Confidence guard: if the best overlap is too weak (< a few bins AND a small
    fraction of the candidate's own speech bins), return 0 — a noisy correlation is
    not trusted and the caller falls back to the static offset. Returns 0 on empty
    input. O(bins × shifts).
    """
    if not cand_intervals or not rec_speech:
        return 0

    # Shared grid spanning both envelopes plus the lag slack on each side.
    base = min(min(a for a, _ in cand_intervals),
               min(a for a, _ in rec_speech)) - max_lag_ms
    end = max(max(b for _, b in cand_intervals),
              max(b for _, b in rec_speech)) + max_lag_ms
    n_bins = max(1, (end - base) // bin_ms + 1)

    cand = _rasterize(cand_intervals, bin_ms, n_bins, base)
    rec = _rasterize(rec_speech, bin_ms, n_bins, base)
    cand_bins = sum(cand)
    if cand_bins == 0:
        return 0

    max_shift_bins = max_lag_ms // bin_ms
    best_score = -1
    best_shift = 0
    for shift in range(-max_shift_bins, max_shift_bins + 1):
        score = 0
        # candidate bin i moves to i+shift; overlap with rec at that position.
        lo = max(0, -shift)
        hi = min(n_bins, n_bins - shift)
        for i in range(lo, hi):
            if cand[i] and rec[i + shift]:
                score += 1
        if score > best_score:
            best_score = score
            best_shift = shift

    # Confidence guard — reject a noisy/coincidental peak.
    if best_score < 3 or best_score < 0.15 * cand_bins:
        return 0
    return best_shift * bin_ms
