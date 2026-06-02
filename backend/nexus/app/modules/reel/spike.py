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
from app.modules.reel import timing
from app.modules.reel.clips import cut_clip
from app.modules.reel.render import concat_clips

# Hand-picked trimmed segments (fallback when no Director EDL is present):
#   (commit_t_ms, trim_start_rel_ms, trim_end_rel_ms, label)
# Each starts MID-answer to exercise trimming, not just full answers.
TRIMS = [
    (203401, 880, 6700, "workflow-design"),     # "I designed this workflow ... instead of fully autonomous routing"
    (457752, 23190, 32400, "throttle"),         # "then I would introduce ... throttle ... instead of hitting that limit"
    (484258, 5150, 10440, "idempotency"),       # "I'd use idempotency keys so that retries don't create any duplicate(s)"
]


def _trims_from_edl(session_id: str) -> list[tuple[int, int, int, str]] | None:
    """Load clip/experience beats from a Director EDL dump, if present.

    tmp/edl_<session>.json is written by `python -m app.modules.reel.director`.
    Its clip/experience beats map directly: source_turn_ref=commit,
    in_ms/out_ms = turn-relative trims (the same coordinate as TRIMS).
    """
    path = f"/app/tmp/edl_{session_id}.json"
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        edl = json.load(f)
    trims: list[tuple[int, int, int, str]] = []
    for i, b in enumerate(edl.get("beats", [])):
        if b.get("kind") in ("clip", "experience") and b.get("in_ms") is not None:
            trims.append((int(b["source_turn_ref"]), int(b["in_ms"]),
                          int(b["out_ms"]), f"{b['kind']}-{i}"))
    return trims or None


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


def _turn_for_commit(transcript: list[dict], commit_t_ms: int) -> dict | None:
    """Candidate transcript turn whose commit (timestamp_ms) matches (±5ms)."""
    best, best_d = None, 6
    for e in transcript:
        if e.get("role") != "candidate":
            continue
        d = abs(int(e.get("timestamp_ms") or -10**9) - commit_t_ms)
        if d < best_d:
            best, best_d = e, d
    return best


def _caption_words(turn: dict, answer_start_video_ms: int,
                   trim_start_rel: int, trim_end_rel: int) -> list[dict]:
    """Words inside the trim window, on the VIDEO clock for captions.

    Turn word timings are turn-relative (first word=0, accurate); the answer's
    first word sits at answer_start_video_ms, so word_video = start + rel.
    """
    out = []
    for w in turn.get("words") or []:
        if trim_start_rel <= int(w["start_ms"]) <= trim_end_rel:
            out.append({
                "text": w["text"],
                "start_ms": answer_start_video_ms + int(w["start_ms"]),
                "end_ms": answer_start_video_ms + int(w["end_ms"]),
            })
    return out


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

        trims = _trims_from_edl(session_id) or TRIMS
        print(f"[spike] {len(trims)} trims from "
              f"{'Director EDL' if _trims_from_edl(session_id) else 'hand-picked TRIMS'}")

        clips: list[str] = []
        for i, (commit, ts_rel, te_rel, label) in enumerate(trims):
            span = timing.answer_span(events, speaking, commit)
            turn = _turn_for_commit(transcript, commit)
            if span is None or turn is None:
                print(f"[spike] clip {i} ({label}): no span/turn — skipped")
                continue
            ans_start_v = span[0] + anchor
            words = _caption_words(turn, ans_start_v, ts_rel, te_rel)
            clip_start_v = ans_start_v + ts_rel
            clip_end_v = ans_start_v + te_rel
            cp = os.path.join(tmp, f"clip_{i}.mp4")
            print(f"[spike] clip {i} ({label}): video [{clip_start_v/1000:.2f},"
                  f"{clip_end_v/1000:.2f}]s ({(te_rel-ts_rel)/1000:.1f}s), {len(words)} caption words")
            await cut_clip(recording_path=rec_path, out_path=cp, words=words,
                           start_ms=clip_start_v, end_ms=clip_end_v, offset_ms=0)
            clips.append(cp)

        out = os.path.join(out_dir, f"reel_{session_id}_clips.mp4")
        await concat_clips(clips, out)
    print(f"[spike] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1])))
