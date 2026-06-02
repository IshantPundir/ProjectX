"""DEV-ONLY clips-only spike. NOT production. Retired when the actor lands.

Usage (in the vision image, which has ffmpeg):
    docker compose exec nexus-vision-worker \
        python -m app.modules.reel.spike 5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6

Loads the session's transcript + recording, measures the offset empirically,
cuts hand-picked clean candidate spans, concatenates -> /app/tmp/reel_<sid>_clips.mp4.
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
from app.modules.reel.offset import compute_offset_ms, measure_video_onset_ms
from app.modules.reel.render import concat_clips

# Hand-picked CLEAN candidate spans (session-ms) from 5e004a4d — explicitly NOT
# turn 1 (its pre-floor-gate "sure" leak skews start_ms). Tune after first view.
SPANS = [
    (183000, 205000),   # Workato workflow-design answer
    (415000, 440000),   # rate-limiting / idempotency answer
]
OPENER_SESSION_MS = 3520  # first directive.delivered t_ms (engine event log)


async def _load_session(session_id: str) -> tuple[str, str]:
    """Return (tenant_id, recording_s3_key) for the session."""
    async with get_bypass_session() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (await db.execute(text(
            "SELECT tenant_id, recording_s3_key FROM sessions WHERE id = :sid"
        ), {"sid": session_id})).one()
        return str(row[0]), str(row[1])


def _candidate_words_in(transcript: list[dict], start_ms: int, end_ms: int) -> list[dict]:
    out: list[dict] = []
    for e in transcript:
        if e.get("role") != "candidate":
            continue
        for w in e.get("words") or []:
            if start_ms <= w["start_ms"] <= end_ms:
                out.append(w)
    return out


async def main(session_id: str) -> int:
    tenant_id, rec_key = await _load_session(session_id)
    fixture = os.path.join(
        os.path.dirname(__file__), "..", "..", "..",
        "tests/fixtures/candidate_reel/session_5e004a4d_transcript.json")
    with open(os.path.abspath(fixture), encoding="utf-8") as f:
        transcript = json.load(f)

    out_dir = "/app/tmp"
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rec_path = os.path.join(tmp, "recording.mp4")
        print(f"[spike] downloading {rec_key} ...")
        await get_object_storage().download_to_path(rec_key, rec_path)

        onset = await measure_video_onset_ms(rec_path)
        if onset is None:
            print("[spike] WARN: no onset detected; assuming 0")
            onset = 0
        offset = compute_offset_ms(opener_session_ms=OPENER_SESSION_MS,
                                   video_onset_ms=onset)
        print(f"[spike] video_onset={onset}ms  offset={offset}ms "
              f"(video_ms = session_ms - offset)")

        clips: list[str] = []
        for i, (s, e) in enumerate(SPANS):
            words = _candidate_words_in(transcript, s, e)
            cp = os.path.join(tmp, f"clip_{i}.mp4")
            print(f"[spike] clip {i}: [{s},{e}]ms  {len(words)} words")
            await cut_clip(recording_path=rec_path, out_path=cp, words=words,
                           start_ms=s, end_ms=e, offset_ms=offset)
            clips.append(cp)

        out = os.path.join(out_dir, f"reel_{session_id}_clips.mp4")
        await concat_clips(clips, out)
    print(f"[spike] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
