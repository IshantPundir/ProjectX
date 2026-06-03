"""Reel timing — map engine events onto the recording's (video) clock.

The recording IS the engine monotonic clock plus two corrections:

    video_ms = t_ms + wall_anchor - pipeline_lag

  * ``wall_anchor`` = engine_t0_wall - recording_started_at_wall  (tens of ms):
    the recording and the engine clock share an origin.
  * ``pipeline_lag`` = the agent's audio-RECEIVE latency (LiveKit jitter buffer +
    ai-coustics NC + VAD). It is INVISIBLE to every stored timestamp
    (recording_started_at, MP4 creation_time, engine t0 all agree to ~tens of ms),
    so it is MEASURED per session by cross-correlating the candidate VAD speech
    envelope against the recording's speech envelope.

Candidate clip spans come from the engine's LIVE VAD (``audio.user.state`` events)
bounded by ``turn.captured`` — NOT the commit-lagged transcript ``start_ms``/``end_ms``
(those are stamped after the endpointing silence; see ``turn.captured.pause_before_commit_ms``).

No heavy imports — keep importable in the lean worker image. ``recording_speech_intervals``
shells ffmpeg and must only be called where ffmpeg exists (the vision image).
"""
from __future__ import annotations

import asyncio
import re

ENGINE_DISPATCH_KIND = "engine.v2.dispatched"


def engine_t0_wall(events: list[dict]) -> int:
    """Wall-clock ms at engine t_ms=0 (the dispatch event)."""
    return next(e["wall_ms"] for e in events if e.get("kind") == ENGINE_DISPATCH_KIND)


def wall_anchor(events: list[dict], recording_started_at_ms: int) -> int:
    """``video_ms = t_ms + wall_anchor`` before the pipeline-lag correction."""
    return engine_t0_wall(events) - int(recording_started_at_ms)


def speaking_intervals(events: list[dict]) -> list[tuple[int, int]]:
    """Candidate speech [start_t_ms, end_t_ms] from live VAD user-state events."""
    out: list[tuple[int, int]] = []
    open_t: int | None = None
    for e in events:
        if e.get("kind") != "audio.user.state":
            continue
        ns = e["payload"]["new_state"]
        if ns == "speaking" and open_t is None:
            open_t = e["t_ms"]
        elif ns == "listening" and open_t is not None:
            out.append((open_t, e["t_ms"]))
            open_t = None
    return out


def answer_span(events: list[dict], speaking: list[tuple[int, int]],
                commit_t_ms: int, *, tol_ms: int = 5) -> tuple[int, int] | None:
    """Real speech [start, end] t_ms for the candidate turn committed at commit_t_ms.

    Bounded below by the previous ``turn.captured`` commit and above by
    ``commit - pause_before_commit_ms`` (the real end of speech).

    ``commit_t_ms`` is the transcript ``timestamp_ms``, which can differ from the
    ``turn.captured`` event ``t_ms`` by ~1ms (float->int rounding). Matches the
    NEAREST ``turn.captured`` within ``tol_ms`` and anchors on its event t_ms;
    returns ``None`` (never raises) when no commit is within tolerance.
    """
    cap_events = [e for e in events if e.get("kind") == "turn.captured"]
    if not cap_events:
        return None
    cap_ev = min(cap_events, key=lambda e: abs(int(e["t_ms"]) - commit_t_ms))
    cap_t = int(cap_ev["t_ms"])
    if abs(cap_t - commit_t_ms) > tol_ms:
        return None
    caps = sorted(int(e["t_ms"]) for e in cap_events)
    prev = max((c for c in caps if c < cap_t), default=0)
    real_end = cap_t - int(cap_ev["payload"].get("pause_before_commit_ms", 0))
    segs = [(a, b) for (a, b) in speaking if a >= prev - 200 and b <= real_end + 1500]
    if not segs:
        return None
    return segs[0][0], segs[-1][1]


def measure_pipeline_lag(speaking: list[tuple[int, int]],
                         rec_speech: list[tuple[int, int]],
                         wall_anchor_ms: int, *,
                         max_lag_ms: int = 8_000, bin_ms: int = 40) -> int:
    """Lag (ms) by which the engine's VAD trails the recorded audio.

    Cross-correlate the candidate VAD envelope (in ``t_ms + wall_anchor``) against
    the recording's speech envelope (video clock); the candidate speaks only during
    candidate audio, so the overlap peaks at the true lag (agent speech contributes
    nothing — VAD=0 there). Returns the lag maximizing overlap.
    """
    if not speaking or not rec_speech:
        return 0
    span_ms = max(max(b for _, b in rec_speech),
                  max(b + wall_anchor_ms for _, b in speaking)) + 1_000
    n = span_ms // bin_ms + 1
    vad = bytearray(n)   # candidate speaking, in (t_ms + wall_anchor) coordinate
    rec = bytearray(n)   # any speech, in video coordinate
    for a, b in speaking:
        for i in range((a + wall_anchor_ms) // bin_ms, (b + wall_anchor_ms) // bin_ms + 1):
            if 0 <= i < n:
                vad[i] = 1
    for a, b in rec_speech:
        for i in range(a // bin_ms, b // bin_ms + 1):
            if 0 <= i < n:
                rec[i] = 1
    best_lag, best_score = 0, -1
    for shift in range(0, max_lag_ms // bin_ms + 1):   # rec is EARLIER: vad[i] vs rec[i-shift]
        score = sum(1 for i in range(shift, n) if vad[i] and rec[i - shift])
        if score > best_score:
            best_score, best_lag = score, shift * bin_ms
    return best_lag


async def recording_speech_intervals(rec_path: str, *, noise_db: int = -30,
                                     min_silence_s: float = 0.4
                                     ) -> list[tuple[int, int]]:
    """Speech intervals (ms, video clock) in the recording via ffmpeg silencedetect.

    Calibration only (the speech *envelope*); not a content re-derivation. ffmpeg
    required — call only in the vision image.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", rec_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    txt = stderr.decode("utf-8", "replace")
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", txt)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", txt)]
    speech: list[tuple[int, int]] = []
    cursor = 0.0
    for s_start, s_end in zip(starts, ends):
        if s_start > cursor:
            speech.append((int(cursor * 1000), int(s_start * 1000)))
        cursor = max(cursor, s_end)
    return speech
