"""DEV-ONLY clips-only spike. NOT production. Retired when the actor lands.

Usage (in the vision image, which has ffmpeg):
    docker compose exec nexus-vision-worker \
        python -m app.modules.reel.spike 5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6

Clips the candidate's REAL spoken answers using data the engine ALREADY stored
live — no re-VAD, no re-STT, no offset hunting:

  * `audio.user.state` events (live VAD) give the candidate's exact speech
    start/stop on the engine monotonic clock (t_ms), with wall_ms.
  * `turn.captured.pause_before_commit_ms` is the endpointing tail, so the real
    speech end = commit_t_ms - pause_before_commit_ms (the transcript's turn
    start_ms/end_ms are stamped at COMMIT and lag real speech — unusable).
  * The recording IS the engine's clock: video_ms = wall_ms - recording_started_at.
    engine t_ms=0 is wall = recording_started_at + ~tens of ms, so
    video_ms = t_ms + (engine_t0_wall - recording_started_at_wall)  (~90ms here).
    This is why the report's question timeline works with offset 0.

So each clip = the candidate's VAD speech span for a chosen turn, mapped to the
recording by the wall-clock anchor. Captions deferred (positioning first).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile

from sqlalchemy import text

from app.database import get_bypass_session
from app.storage import get_object_storage
from app.modules.reel.clips import cut_clip
from app.modules.reel.render import concat_clips

MAX_CLIP_MS = 24_000          # cap a long answer so the reel stays watchable
SEG_PAD_MS = 120

# The engine's VAD timestamps (audio.user.state t_ms) LAG the egress-recorded
# audio by the agent's audio-receive pipeline latency (jitter buffer + ai-coustics
# NC + VAD) — a fixed pipeline depth, constant per session but NOT a universal
# constant. It is MEASURED per session (see _measure_pipeline_lag) by cross-
# correlating the candidate VAD speech envelope against the recording's speech
# envelope. REEL_PIPELINE_LAG_MS overrides the auto-measurement (debug only).
LAG_SEARCH_MAX_MS = 8_000
LAG_BIN_MS = 40

# Featured answers, by their candidate turn.captured commit t_ms (substantive,
# clean answers from 5e004a4d). Verified spans (video): Workato 179.3-202.6s,
# rate-limit 414.1-456.9s, idempotency 469.8-483.1s.
FEATURED_COMMITS = [203401, 457752, 484258]


async def _load_session(session_id: str) -> tuple[str, str, int]:
    """Return (tenant_id, recording_s3_key, recording_started_at_wall_ms)."""
    async with get_bypass_session() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (await db.execute(text(
            "SELECT tenant_id, recording_s3_key, recording_started_at "
            "FROM sessions WHERE id = :sid"
        ), {"sid": session_id})).one()
        rec_start_ms = int(row[2].timestamp() * 1000)
        return str(row[0]), str(row[1]), rec_start_ms


def _load_events(session_id: str) -> list[dict]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "engine-events", f"{session_id}.json")
    with open(os.path.abspath(path), encoding="utf-8") as f:
        return json.load(f)["events"]


def _speaking_intervals(events: list[dict]) -> list[tuple[int, int]]:
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


async def _recording_speech_intervals(rec_path: str) -> list[tuple[int, int]]:
    """Speech intervals (ms, video clock) in the recording via silencedetect."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", rec_path, "-af", "silencedetect=noise=-30dB:d=0.4",
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


def _measure_pipeline_lag(speaking: list[tuple[int, int]],
                          rec_speech: list[tuple[int, int]],
                          wall_anchor: int) -> int:
    """Cross-correlate the candidate VAD envelope (t_ms+wall_anchor) against the
    recording's speech envelope to find the lag L (ms) by which the engine's VAD
    trails the recorded audio. The candidate speaks only during candidate audio,
    so the overlap peaks at the true L; agent speech contributes nothing (VAD=0).
    """
    bin_ms = LAG_BIN_MS
    span_ms = max(max((b for _, b in rec_speech), default=0),
                  max((b + wall_anchor for _, b in speaking), default=0)) + 1000
    n = span_ms // bin_ms + 1
    vad = bytearray(n)   # candidate speaking, in (t_ms + wall_anchor) coordinate
    rec = bytearray(n)   # any speech, in video coordinate
    for a, b in speaking:
        for i in range((a + wall_anchor) // bin_ms, (b + wall_anchor) // bin_ms + 1):
            if 0 <= i < n:
                vad[i] = 1
    for a, b in rec_speech:
        for i in range(a // bin_ms, b // bin_ms + 1):
            if 0 <= i < n:
                rec[i] = 1
    max_shift = LAG_SEARCH_MAX_MS // bin_ms
    best_lag, best_score = 0, -1
    for shift in range(0, max_shift + 1):       # rec is EARLIER, so vad[i] vs rec[i-shift]
        score = sum(1 for i in range(shift, n) if vad[i] and rec[i - shift])
        if score > best_score:
            best_score, best_lag = score, shift * bin_ms
    return best_lag


def _answer_span(events: list[dict], speaking: list[tuple[int, int]],
                 commit_t_ms: int) -> tuple[int, int] | None:
    """Real speech [start,end] t_ms for the candidate turn committed at commit_t_ms.

    Bounded below by the previous turn.captured commit and above by
    (commit - pause_before_commit_ms), the real end of speech.
    """
    caps = sorted(int(e["t_ms"]) for e in events if e.get("kind") == "turn.captured")
    prev = max((c for c in caps if c < commit_t_ms), default=0)
    cap_ev = next(e for e in events
                  if e.get("kind") == "turn.captured" and int(e["t_ms"]) == commit_t_ms)
    real_end = commit_t_ms - int(cap_ev["payload"].get("pause_before_commit_ms", 0))
    segs = [(a, b) for (a, b) in speaking if a >= prev - 200 and b <= real_end + 1500]
    if not segs:
        return None
    return segs[0][0], segs[-1][1]


async def main(session_id: str) -> int:
    tenant_id, rec_key, rec_start_wall = await _load_session(session_id)
    events = _load_events(session_id)
    eng0_wall = next(e["wall_ms"] for e in events if e["kind"] == "engine.v2.dispatched")
    wall_anchor = eng0_wall - rec_start_wall            # wall-clock: ~+90ms
    speaking = _speaking_intervals(events)
    print(f"[spike] {len(speaking)} VAD speaking intervals; wall_anchor={wall_anchor}ms")

    out_dir = "/app/tmp"
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rec_path = os.path.join(tmp, "recording.mp4")
        print(f"[spike] downloading {rec_key} ...")
        await get_object_storage().download_to_path(rec_key, rec_path)

        override = os.environ.get("REEL_PIPELINE_LAG_MS")
        if override is not None:
            pipeline_lag = int(override)
            print(f"[spike] pipeline_lag = {pipeline_lag}ms (override)")
        else:
            rec_speech = await _recording_speech_intervals(rec_path)
            pipeline_lag = _measure_pipeline_lag(speaking, rec_speech, wall_anchor)
            print(f"[spike] pipeline_lag = {pipeline_lag}ms (auto, cross-correlated "
                  f"VAD vs {len(rec_speech)} recording speech intervals)")
        anchor = wall_anchor - pipeline_lag             # video_ms = t_ms + anchor
        print(f"[spike] video_ms = t_ms + {anchor}ms")

        clips: list[str] = []
        for i, commit in enumerate(FEATURED_COMMITS):
            span = _answer_span(events, speaking, commit)
            if span is None:
                print(f"[spike] clip {i}: no VAD span for commit {commit} — skipped")
                continue
            start_v = span[0] + anchor - SEG_PAD_MS
            end_v = min(span[1] + anchor + SEG_PAD_MS, start_v + MAX_CLIP_MS)
            cp = os.path.join(tmp, f"clip_{i}.mp4")
            print(f"[spike] clip {i}: commit {commit} -> speech t_ms[{span[0]},{span[1]}] "
                  f"-> video [{start_v/1000:.2f},{end_v/1000:.2f}]s ({(end_v-start_v)/1000:.1f}s)")
            await cut_clip(recording_path=rec_path, out_path=cp,
                           start_ms=int(start_v), end_ms=int(end_v), offset_ms=0)
            clips.append(cp)

        out = os.path.join(out_dir, f"reel_{session_id}_clips.mp4")
        await concat_clips(clips, out)
    print(f"[spike] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
