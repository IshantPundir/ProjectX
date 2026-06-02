"""DEV-ONLY clips spike. NOT production. Retired when the actor lands.

Usage (in the vision image, which has ffmpeg):
    docker compose exec nexus-vision-worker \
        python -m app.modules.reel.spike 5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6

Renders hand-picked, TRIMMED candidate segments with burned-in captions, to
validate the full clip path end-to-end:

  * positioning   -> app.modules.reel.timing (VAD span + wall anchor + auto lag)
  * trimming      -> a sub-range [trim_start_rel, trim_end_rel] within an answer
                     (this is the Director's job in production; hand-picked here)
  * captions      -> the turn's RELATIVE word timings anchored to the answer's
                     video start (word_video = answer_start_video + word_rel),
                     burned clip-relative by clips.cut_clip / captions.build_ass.

video_ms = t_ms + wall_anchor - pipeline_lag  (see timing.py). pipeline_lag is
auto-measured; REEL_PIPELINE_LAG_MS overrides it (debug only).
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
from app.modules.reel import render, timing
from app.modules.reel.director import ValidatedBeat


def _load_validated_edl(session_id: str) -> list[ValidatedBeat]:
    """Reconstruct the validated EDL beats from the Director dump.

    tmp/edl_<session>.json is written by `python -m app.modules.reel.director`.
    """
    path = f"/app/tmp/edl_{session_id}.json"
    if not os.path.exists(path):
        raise SystemExit(f"[spike] {path} missing — run "
                         f"`python -m app.modules.reel.director {session_id}` first")
    with open(path, encoding="utf-8") as f:
        edl = json.load(f)
    return [ValidatedBeat(**b) for b in edl.get("beats", [])]


async def _load_session(session_id: str) -> tuple[str, str, int]:
    """Return (tenant_id, recording_s3_key, recording_started_at_wall_ms)."""
    async with get_bypass_session() as db:
        await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        row = (await db.execute(text(
            "SELECT tenant_id, recording_s3_key, recording_started_at "
            "FROM sessions WHERE id = :sid"
        ), {"sid": session_id})).one()
        return str(row[0]), str(row[1]), int(row[2].timestamp() * 1000)


def _load_events(session_id: str) -> list[dict]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "engine-events", f"{session_id}.json")
    with open(os.path.abspath(path), encoding="utf-8") as f:
        return json.load(f)["events"]


def _load_transcript() -> list[dict]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "tests/fixtures/candidate_reel/session_5e004a4d_transcript.json")
    with open(os.path.abspath(path), encoding="utf-8") as f:
        return json.load(f)


async def main(session_id: str) -> int:
    tenant_id, rec_key, rec_start_wall = await _load_session(session_id)
    events = _load_events(session_id)
    transcript = _load_transcript()

    wall_anchor = timing.wall_anchor(events, rec_start_wall)
    speaking = timing.speaking_intervals(events)
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
            rec_speech = await timing.recording_speech_intervals(rec_path)
            pipeline_lag = timing.measure_pipeline_lag(speaking, rec_speech, wall_anchor)
            print(f"[spike] pipeline_lag = {pipeline_lag}ms (auto-measured)")
        anchor = wall_anchor - pipeline_lag             # video_ms = t_ms + anchor
        print(f"[spike] video_ms = t_ms + {anchor}ms")

        beats = _load_validated_edl(session_id)
        no_tts = os.environ.get("REEL_NO_TTS") == "1"
        print(f"[spike] full reel: {len(beats)} beats ("
              f"{sum(1 for b in beats if b.kind in ('clip','experience'))} clips), "
              f"tts={'off' if no_tts else 'on'}")
        out = os.path.join(out_dir, f"reel_{session_id}_full.mp4")
        await render.render_reel(
            beats=beats, recording_path=rec_path, events=events, speaking=speaking,
            transcript=transcript, anchor=anchor, tmp_dir=tmp, out_path=out,
            tts_enabled=not no_tts,
        )
    print(f"[spike] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
