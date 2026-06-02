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
import sys
import tempfile

from sqlalchemy import text

from app.database import get_bypass_session
from app.storage import get_object_storage
from app.modules.reel.clips import cut_clip
from app.modules.reel.render import concat_clips

MAX_CLIP_MS = 24_000          # cap a long answer so the reel stays watchable
SEG_PAD_MS = 120

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
    anchor = eng0_wall - rec_start_wall  # video_ms = t_ms + anchor
    print(f"[spike] wall anchor: video_ms = t_ms + {anchor}ms "
          f"(engine_t0_wall={eng0_wall}, recording_started_at={rec_start_wall})")

    speaking = _speaking_intervals(events)
    print(f"[spike] {len(speaking)} VAD speaking intervals")

    out_dir = "/app/tmp"
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rec_path = os.path.join(tmp, "recording.mp4")
        print(f"[spike] downloading {rec_key} ...")
        await get_object_storage().download_to_path(rec_key, rec_path)

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
