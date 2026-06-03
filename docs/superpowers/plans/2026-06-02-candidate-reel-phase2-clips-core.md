# Candidate Reel — Phase 2 Clips-Only Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a watchable MP4 of 2–3 hand-picked candidate highlight clips from session `5e004a4d` — correctly offset against the recording, with burned-in captions — proving the load-bearing transcript→video offset + clip-cut + caption pipeline before any director/cards/TTS work.

**Architecture:** A new `app/modules/reel/` package with small, focused, mostly-pure functions (offset math, `.ass` caption generation, ffmpeg command construction) plus thin ffmpeg subprocess wrappers, driven by a throwaway `spike.py` dev entrypoint. Pure functions are TDD'd; the ffmpeg orchestration is validated by running the spike and watching the MP4 (consistent with the manual-agent-testing preference). All real functions (`offset`/`clips`/`render`) are exactly what the production actor will later call.

**Tech Stack:** Python 3.13, ffmpeg (subprocess), libass (`subtitles=` filter), SQLAlchemy async (bypass session to read the session row), boto3 R2 (`get_object_storage().download_to_path`). Runs in the `nexus-vision-worker` container (the only image with ffmpeg).

**Scope note:** This plan covers ONLY the clips-only core (Step 1 of the Phase 2 build plan, `docs/superpowers/specs/2026-06-02-candidate-reel-phase2-build-design.md`). Director, cards, TTS, full render, and the production scaffold (table/actor/endpoints) are deliberately deferred to later plans, informed by this milestone's measured offset and visual result.

---

## Reference facts (verified during design)

- Transcript fixture: `backend/nexus/tests/fixtures/candidate_reel/session_5e004a4d_transcript.json` — a list of entries; candidate entries carry `start_ms`, `end_ms`, `words: [{text,start_ms,end_ms,confidence}]` on the **session (monotonic) clock**. Agent entries have `words: null`.
- Engine event log: `backend/nexus/engine-events/5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6.json` — `{"events":[{"t_ms","wall_ms","kind","payload"}...]}`. First `directive.delivered` (the agent opener) ≈ `t_ms` 3520. Use its `t_ms` as `opener_session_ms`.
- Offset convention: `video_ms = session_ms − offset_ms`; `offset_ms = opener_session_ms − video_onset_ms` (typically **negative** — video starts before the engine's monotonic zero).
- Recording key: `recording_object_key(tenant_id, session_id)` in `app/modules/session/livekit.py` = `f"{settings.recording_key_prefix}/{tenant_id}/{session_id}.mp4"`. The session row also stores `recording_s3_key`.
- Storage: `from app.storage import get_object_storage` → `await storage.download_to_path(key, dest)`.
- Bypass DB: `from app.database import get_bypass_session` → `async with get_bypass_session() as db: await db.execute(text(f"SET LOCAL app.current_tenant = '{tid}'"))`.
- ffmpeg lives in the vision image only. Run everything via `docker compose exec nexus-vision-worker ...`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/modules/reel/__init__.py` | Package marker. **No heavy imports** (no Pillow/TTS/livekit) so the clips path stays importable in the vision image. |
| `app/modules/reel/captions.py` | Pure: group `words[]` into caption lines + render an `.ass` subtitle string (clip-relative timings). |
| `app/modules/reel/offset.py` | Pure offset math + `silencedetect` stderr parsing; thin `measure_offset(...)` that shells ffmpeg. |
| `app/modules/reel/clips.py` | `cut_clip(...)` — build + run the ffmpeg cut/normalize/caption command for one span. |
| `app/modules/reel/render.py` | `concat_clips(...)` — ffmpeg concat-demuxer join of normalized clips. |
| `app/modules/reel/spike.py` | Dev entrypoint: load session, pick spans, download recording, measure offset, cut, concat, write `tmp/`. Throwaway. |
| `tests/reel/test_captions.py` | Unit tests for caption grouping + `.ass` rendering. |
| `tests/reel/test_offset.py` | Unit tests for offset math + silencedetect parsing. |

---

## Task 1: Reel package scaffold

**Files:**
- Create: `backend/nexus/app/modules/reel/__init__.py`
- Create: `backend/nexus/tests/reel/__init__.py`

- [x] **Step 1: Create the package marker with an explicit no-heavy-imports note**

`app/modules/reel/__init__.py`:
```python
"""Candidate Reel module.

Phase 2 clips-only core. Keep this __init__ free of heavy/optional imports
(Pillow, livekit TTS plugins) so the clip path imports cleanly in the lean
vision worker image. Submodules are imported directly by callers.
"""
```

- [x] **Step 2: Create the test package marker**

`tests/reel/__init__.py`:
```python
```

- [x] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/reel/__init__.py backend/nexus/tests/reel/__init__.py
git commit -m "feat(reel): scaffold reel module package"
```

---

## Task 2: Caption `.ass` generation (pure, TDD)

**Files:**
- Create: `backend/nexus/app/modules/reel/captions.py`
- Test: `backend/nexus/tests/reel/test_captions.py`

- [x] **Step 1: Write the failing tests**

`tests/reel/test_captions.py`:
```python
from app.modules.reel.captions import group_caption_lines, build_ass, _ass_ts


def _w(text, start_ms, end_ms):
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms, "confidence": 1.0}


def test_group_caption_lines_splits_by_max_words():
    words = [_w(t, i * 100, i * 100 + 80) for i, t in enumerate("a b c d e f g".split())]
    lines = group_caption_lines(words, max_words=3)
    assert [len(ln) for ln in lines] == [3, 3, 1]
    # each line keeps its words' original timings
    assert lines[0][0]["text"] == "a" and lines[1][0]["text"] == "d"


def test_ass_ts_formats_centiseconds():
    assert _ass_ts(0) == "0:00:00.00"
    assert _ass_ts(1500) == "0:00:01.50"
    assert _ass_ts(3661230) == "1:01:01.23"


def test_build_ass_timings_are_clip_relative():
    # clip starts at session-ms 12000; first word at 12400 -> 0.40s into the clip
    words = [_w("six", 12400, 12720), _w("years", 12800, 13300)]
    ass = build_ass(words, clip_start_ms=12000, max_words=5)
    assert "[Events]" in ass and "PlayResX: 1280" in ass
    assert "Dialogue: 0,0:00:00.40,0:00:01.30,Default,,0,0,0,,six years" in ass


def test_build_ass_empty_words_has_no_dialogue():
    ass = build_ass([], clip_start_ms=0)
    assert "Dialogue:" not in ass
```

- [x] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_captions.py -q`
Expected: FAIL with `ModuleNotFoundError: app.modules.reel.captions`.

- [x] **Step 3: Implement `captions.py`**

`app/modules/reel/captions.py`:
```python
"""Burned-in caption generation from word timings -> ASS subtitle string.

Pure functions; no ffmpeg here. Timings in the emitted ASS are CLIP-RELATIVE
(ms since the clip's first frame), so the caller passes the clip's session-ms
start. `words[]` is the source of truth (it may be a superset of the turn text).
"""
from __future__ import annotations

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,Arial,48,&H00FFFFFF,&H00000000,&H64000000,1,3,1,2,60,60,60

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def group_caption_lines(words: list[dict], *, max_words: int = 5) -> list[list[dict]]:
    """Chunk words into caption lines of at most ``max_words`` words each."""
    return [words[i:i + max_words] for i in range(0, len(words), max_words)]


def _ass_ts(ms: int) -> str:
    """Milliseconds -> ASS timestamp ``H:MM:SS.CC`` (centiseconds, floored)."""
    ms = max(0, int(ms))
    cs = (ms % 1000) // 10
    s = (ms // 1000) % 60
    m = (ms // 60_000) % 60
    h = ms // 3_600_000
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(words: list[dict], *, clip_start_ms: int, max_words: int = 5) -> str:
    """Render an ASS subtitle file for one clip span.

    Each caption line spans from its first word's start to its last word's end,
    expressed relative to ``clip_start_ms``.
    """
    out = [_ASS_HEADER]
    for line in group_caption_lines(words, max_words=max_words):
        if not line:
            continue
        start = _ass_ts(int(line[0]["start_ms"]) - clip_start_ms)
        end = _ass_ts(int(line[-1]["end_ms"]) - clip_start_ms)
        text = " ".join(str(w["text"]) for w in line).replace("\n", " ")
        out.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return "\n".join(out) + "\n"
```

- [x] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_captions.py -q`
Expected: PASS (4 passed).

- [x] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reel/captions.py backend/nexus/tests/reel/test_captions.py
git commit -m "feat(reel): ASS caption generation from word timings"
```

---

## Task 3: Offset math + silencedetect parsing (pure, TDD)

**Files:**
- Create: `backend/nexus/app/modules/reel/offset.py`
- Test: `backend/nexus/tests/reel/test_offset.py`

- [x] **Step 1: Write the failing tests**

`tests/reel/test_offset.py`:
```python
import pytest

from app.modules.reel.offset import compute_offset_ms, parse_first_onset_ms


def test_compute_offset_is_opener_minus_video_onset():
    # opener at session-ms 3520, heard 1000ms into the video -> offset -2520
    assert compute_offset_ms(opener_session_ms=3520, video_onset_ms=1000) == -2520


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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_offset.py -q`
Expected: FAIL with `ModuleNotFoundError: app.modules.reel.offset`.

- [x] **Step 3: Implement the pure parts of `offset.py`**

`app/modules/reel/offset.py`:
```python
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
    return int(opener_session_ms) - int(video_onset_ms)


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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_offset.py -q`
Expected: PASS (3 passed).

- [x] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reel/offset.py backend/nexus/tests/reel/test_offset.py
git commit -m "feat(reel): offset math + silencedetect onset parsing"
```

---

## Task 4: `cut_clip` — ffmpeg cut + normalize + burn captions

**Files:**
- Create: `backend/nexus/app/modules/reel/clips.py`

No unit test (ffmpeg subprocess); validated by the spike in Task 6. Keep the command construction in a pure helper so it's inspectable.

- [x] **Step 1: Implement `clips.py`**

`app/modules/reel/clips.py`:
```python
"""Cut one candidate span from the recording -> a normalized, captioned 16:9 clip.

Normalization to identical params (1280x720, 30fps, H.264+AAC, SAR 1:1) lets
render.concat_clips use the fast concat demuxer with no re-encode at join.
"""
from __future__ import annotations

import asyncio
import os

from app.modules.reel.captions import build_ass

TARGET_W, TARGET_H, FPS = 1280, 720, 30
PAD_MS = 150  # safety pad so a beat never starts a hair late


def build_cut_cmd(*, recording_path: str, ass_path: str, out_path: str,
                  start_ms: int, end_ms: int, offset_ms: int,
                  pad_ms: int = PAD_MS) -> list[str]:
    """Pure: the ffmpeg argv for one normalized, captioned clip.

    video position = session_ms - offset_ms (+/- pad). Seek before -i for speed,
    then the subtitles filter burns the clip-relative .ass.
    """
    v_start = max(0, start_ms - offset_ms - pad_ms)
    v_end = max(v_start + 1, end_ms - offset_ms + pad_ms)
    ss = v_start / 1000.0
    to = v_end / 1000.0
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={FPS},"
        f"subtitles='{ass_path}'"
    )
    return [
        "ffmpeg", "-y",
        "-ss", f"{ss:.3f}", "-to", f"{to:.3f}", "-i", recording_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out_path,
    ]


async def cut_clip(*, recording_path: str, out_path: str, words: list[dict],
                   start_ms: int, end_ms: int, offset_ms: int,
                   pad_ms: int = PAD_MS) -> str:
    """Write a normalized captioned clip for [start_ms, end_ms] (session clock)."""
    ass_path = out_path + ".ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(build_ass(words, clip_start_ms=start_ms))
    cmd = build_cut_cmd(
        recording_path=recording_path, ass_path=ass_path, out_path=out_path,
        start_ms=start_ms, end_ms=end_ms, offset_ms=offset_ms, pad_ms=pad_ms,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg cut failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
```

- [x] **Step 2: Smoke-check the import + pure command builder**

Run:
```bash
docker compose exec -T nexus-vision-worker python -c "
from app.modules.reel.clips import build_cut_cmd
cmd = build_cut_cmd(recording_path='r.mp4', ass_path='c.ass', out_path='o.mp4',
                    start_ms=183000, end_ms=190000, offset_ms=-2500)
print(' '.join(cmd))
assert '-ss' in cmd and 'subtitles=' in ' '.join(cmd)
print('OK')
"
```
Expected: prints the ffmpeg argv ending in `OK` (proves `clips.py` imports in the vision image and the seek math runs).

- [x] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/reel/clips.py
git commit -m "feat(reel): cut_clip ffmpeg cut/normalize/caption-burn"
```

---

## Task 5: `concat_clips` — join normalized clips

**Files:**
- Create: `backend/nexus/app/modules/reel/render.py`

- [x] **Step 1: Implement `render.py`**

`app/modules/reel/render.py`:
```python
"""Join normalized clips into one MP4 via ffmpeg's concat demuxer.

All inputs MUST share codec/params (clips.cut_clip guarantees this), so concat
is a stream copy -- fast and glitch-free. Grows later to interleave card+TTS beats.
"""
from __future__ import annotations

import asyncio
import os


def build_concat_file(clip_paths: list[str]) -> str:
    """Pure: the concat-demuxer list file body (one `file '<abspath>'` per line)."""
    return "".join(f"file '{os.path.abspath(p)}'\n" for p in clip_paths)


async def concat_clips(clip_paths: list[str], out_path: str) -> str:
    if not clip_paths:
        raise ValueError("concat_clips: no clips")
    list_path = out_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        f.write(build_concat_file(clip_paths))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", "-movflags", "+faststart", out_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg concat failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
```

- [x] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/reel/render.py
git commit -m "feat(reel): concat_clips concat-demuxer join"
```

---

## Task 6: `spike.py` dev entrypoint + run it on session 5e004a4d

**Files:**
- Create: `backend/nexus/app/modules/reel/spike.py`

- [x] **Step 1: Implement the spike**

`app/modules/reel/spike.py`:
```python
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
```

- [x] **Step 2: Run the spike in the vision container**

Run:
```bash
docker compose exec nexus-vision-worker \
  python -m app.modules.reel.spike 5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6
```
Expected: logs the downloaded key, a measured `offset` (a few-second NEGATIVE number), two clip cuts, and `wrote /app/tmp/reel_..._clips.mp4`. The file appears on the host at `backend/nexus/tmp/reel_5e004a4d..._clips.mp4` (the `.:/app` mount).

- [x] **Step 3: MANUAL verification — watch the MP4** ✅ confirmed 2026-06-02 (candidate-only answers, correctly positioned).

> **IMPORTANT — the offset approach in this plan was SUPERSEDED during execution.**
> Live testing on 5e004a4d disproved the plan's two core assumptions:
> 1. **There is no single per-session offset for candidate clips.** The transcript's
>    candidate turn `start_ms`/`end_ms` are stamped at STT *commit* time, lagging real
>    speech by the endpointing delay (`turn.captured.pause_before_commit_ms`, 0.8–10s,
>    variable per turn). So `silencedetect`-opener-onset + `compute_offset_ms` cannot align them.
> 2. **The recording already IS the engine clock.** `video_ms = wall_ms − recording_started_at`,
>    and engine `t_ms=0` wall ≈ `recording_started_at` (+90ms on 5e004a4d), so `video_ms ≈ t_ms`.
>    This is why the report's question timeline works with `offset_ms = 0`.
>
> **Working approach (in `spike.py`):** clip the candidate's REAL speech from the engine's
> live VAD (`audio.user.state` events, on `t_ms` with `wall_ms`), bounded by `turn.captured`,
> mapped to the recording by the wall anchor `video_ms = t_ms + (engine_t0_wall − recording_started_at)`.
> No re-VAD, no re-STT, no offset hunting. `offset.py` (opener-onset) is retained but unused by the
> clip path. Word-level timings remain valid for captions (turn-relative, accurate) once anchored to
> the VAD span. Captions deferred per the operator's call (positioning proven first).

- [x] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/reel/spike.py
git commit -m "feat(reel): clips-only spike entrypoint (session 5e004a4d)"
```

---

## Self-Review notes (done while writing)

- **Spec coverage:** clips-only core = build-plan §2 step 1 + §5 (cut/normalize/captions) + §4 offset-A. Director/cards/TTS/scaffold intentionally out of scope (later plans). ✅
- **Offset sign:** `compute_offset_ms = opener_session_ms − video_onset_ms` (negative); `build_cut_cmd` uses `session_ms − offset_ms` → consistent. ✅
- **Image discipline:** ffmpeg-dependent code runs only via `nexus-vision-worker`; `reel/__init__.py` carries no heavy imports; pure-function tests run in the lean `nexus` image. ✅
- **Type consistency:** `words` dicts use `{text,start_ms,end_ms,confidence}` everywhere; `build_ass`/`cut_clip`/`spike` agree. ✅

## After this milestone

Once the clips look right, the next plans (separate) are: Director (LLM→EDL) → Cards (Pillow) → TTS (offline synth) → full interleaved render → production scaffold (`session_reels` table + actor on extended vision image + endpoints) → Phase 3 frontend.

### Status after execution (2026-06-02) — clips-only core DONE + operator-confirmed
Positioning, **captions, and trimming** all confirmed in-sync on session 5e004a4d. Timing model (NOT the plan's opener-onset offset, which was deleted):
`video_ms = t_ms + wall_anchor − pipeline_lag`, all in `app/modules/reel/timing.py`:
- `wall_anchor` = `engine_t0_wall − recording_started_at` (~90ms).
- `pipeline_lag` = engine audio-receive latency (~3.6s), AUTO-measured per session by cross-correlating the candidate VAD envelope (`audio.user.state`) vs the recording's speech envelope.
- clip spans from live VAD bounded by `turn.captured`; captions from turn-relative word timings anchored to the answer's VAD video start.
Commits `4f150061`, `c48d43de`, `cbf616e1` on `feat/candidate-reel-phase2` (not pushed). Trim/select is the **Director's** job (hand-picked in the spike for this test).

### Follow-ups (filed)
1. **SOURCE FIX (preferred long-term): stamp `audio.user.state`/word events against the audio-FRAME clock the egress shares** (instead of engine processing time). Then `pipeline_lag` → 0 by construction and no per-session calibration is needed. The cross-correlation calibrator stays as a fallback for existing recordings. Engine change in `app/modules/interview_engine/` audio path + event log.
2. Lift `timing.py` from spike usage into the production actor; retire `spike.py` at the production-scaffold step.
3. Director snaps trim in/out to word boundaries (the spike does this by hand; `clips.py` tail pad already protects the last word).
