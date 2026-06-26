# Candidate Reel — Question Banner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Burn a short, LLM-authored question paraphrase at the top-center of each candidate clip in the reel, shown only when the question changes from the previous clip.

**Architecture:** The director LLM emits a `question_label` per clip beat; `validate_edl` threads the answer-run's `question_id` plus that label onto the validated clip. A pure planner decides, per clip in render order, whether to show the banner (question_id differs from the immediately preceding clip). When shown, a transparent full-frame banner PNG (Pillow, brand-styled) is composited onto the clip via an ffmpeg `overlay` filter inside `cut_clip`.

**Tech Stack:** Python 3.13, Pydantic v2, Pillow (overlay PNG, vision image only), ffmpeg (clip cut + overlay), OpenAI Responses API (director). Tests are pure-function pytest in the lean nexus image.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-26-reel-question-banner-design.md`.
- The banner text is an LLM-authored paraphrase (~6–10 words), phrased AS a question, never restating the answer.
- Banner shows on a clip only when its `question_id` differs from the **immediately preceding clip's** `question_id`. The first clip always shows (if it has a label). Suppression is deterministic on `question_id`; when a `question_id` is `None`, fall back to comparing the label string to the preceding clip's label.
- A missing/empty `question_label` → no banner for that clip (graceful). Banner rendering is **best-effort**: any Pillow/ffmpeg overlay failure must NOT fail the reel — cut the clip without the banner and continue.
- Styling: top-center over a dark top scrim; a violet `Q` prefix + white question text (DejaVu), wrapped to ≤2 lines. Brand colors live in `cards.py` (`_INK` white, `_ACCENT` violet `#6C5CD0`, `_INK_SOFT`).
- No DB migration; `question_label`/`question_id` live inside the `edl` JSONB. No question-bank dependency.
- The reel renderer/director runs in the long-lived `nexus-vision-worker`, which has **NO hot-reload** — restart it after code/prompt changes before manual verification.
- Pillow is NOT available in the lean test env — PNG-drawing functions import PIL lazily and are verified by a rendered frame, not unit tests. Only pure helpers are unit-tested.
- Test runner (containers already up): `docker compose exec -T nexus python -m pytest <path> -q` from `backend/nexus/`. Ignore the harmless `PytestCacheWarning: ... Permission denied: '/app/.pytest_cache'`.
- Scope discipline: stage only the files each task names; never `git add -A`; one commit per task; never amend existing commits.

---

### Task 1: Director schema — `question_label` on the beat, `question_id`+`question_label` on the validated clip

**Files:**
- Modify: `app/modules/reel/director.py` (ReelBeat ~line 60, ValidatedBeat ~line 75, `_resolve_clip` ~line 128-155)
- Test: `tests/reel/test_director.py`

**Interfaces:**
- Consumes: existing `AnswerRun.question_id` (str|None) and `answer_runs(transcript)`.
- Produces:
  - `ReelBeat.question_label: str | None = None` (LLM output field).
  - `ValidatedBeat.question_id: str | None = None` and `ValidatedBeat.question_label: str | None = None`.
  - `_resolve_clip` sets `question_id=run.question_id` and `question_label=beat.question_label` on the returned `ValidatedBeat`. `edl_to_dict` already uses `asdict`, so these serialize into the `edl` JSONB automatically.

- [ ] **Step 1: Write failing tests**

Add to `tests/reel/test_director.py` (the `_clip` helper passes `**kw` to `ReelBeat`, so `question_label=` flows through):

```python
def test_clip_threads_question_id_from_run_and_label():
    edl = ReelEdlOut(beats=[_clip(0, 0, 3, question_label="Q: where do you start?")])
    run = _cand("t-1", 10, qid="qid-abc")
    vb = _by_kind(validate_edl(edl, [run]), "clip")[0]
    assert vb.question_id == "qid-abc"
    assert vb.question_label == "Q: where do you start?"


def test_clip_without_label_has_none_label_but_still_threads_qid():
    edl = ReelEdlOut(beats=[_clip(0, 0, 3)])
    vb = _by_kind(validate_edl(edl, [_cand("t-1", 10, qid="qid-xyz")]), "clip")[0]
    assert vb.question_id == "qid-xyz"
    assert vb.question_label is None
```

Note: `_cand(...)` already accepts a `qid="q1"` kwarg (see the helper at the top of the test file) and sets it as the candidate turn's `question_id`, which `answer_runs` copies onto the run. No helper change needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_director.py -q`
Expected: FAIL — `AttributeError: 'ValidatedBeat' object has no attribute 'question_id'` (and `ReelBeat` rejects `question_label` until the field is added — a `TypeError`/`ValidationError` on construction).

- [ ] **Step 3: Add the fields and threading in `director.py`**

In the `ReelBeat` Pydantic model (the block with `kind: BeatKind`, around line 60-66), add the field after `narration_text`:

```python
    narration_text: str | None = None    # Arjun TTS script for card beats
    question_label: str | None = None    # clip/experience: short question paraphrase (overlay)
```

In the `ValidatedBeat` dataclass (around line 75-84), add two fields after `narration_text`:

```python
    on_screen_text: str | None = None
    narration_text: str | None = None
    question_id: str | None = None        # the run's question_id (for banner dedup)
    question_label: str | None = None     # short question paraphrase (clip overlay)
```

In `_resolve_clip` (the `return ValidatedBeat(...)` near line 150-154), add the two fields:

```python
    return ValidatedBeat(
        kind=beat.kind, duration_ms=_estimate_clip_duration(words),
        source_turn_ref=ref, words=words, on_screen_text=beat.on_screen_text,
        narration_text=beat.narration_text,
        question_id=run.question_id, question_label=beat.question_label,
    )
```

(`run` is already in scope in `_resolve_clip` — it's fetched as `run = runs_by_ref.get(ref)` near the top of the function.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_director.py -q`
Expected: PASS (all director tests green).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/director.py tests/reel/test_director.py
git commit -m "feat(reel): carry question_id + question_label onto validated clip beats

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Banner planner + banner PNG renderer (`overlays.py`)

**Files:**
- Create: `app/modules/reel/overlays.py`
- Test: `tests/reel/test_overlays.py` (new)

**Interfaces:**
- Consumes: `cards.wrap_to_width`, `cards.CARD_W`, `cards.CARD_H`, and brand colors from `cards.py`.
- Produces:
  - `plan_banner_texts(clips: list[tuple[str | None, str | None]]) -> list[str | None]` — pure. Input is `(question_id, question_label)` per clip in render order; output is the banner text to show per clip, or `None` to suppress.
  - `render_question_banner(*, text: str, out_path: str, width: int = cards.CARD_W, height: int = cards.CARD_H) -> str` — writes a transparent banner PNG; returns `out_path`. (Pillow lazy import; verified by a rendered frame, not unit-tested.)

- [ ] **Step 1: Write failing tests for the pure planner**

Create `tests/reel/test_overlays.py`:

```python
"""Question-banner planning — pure helper tests (lean nexus image)."""
from app.modules.reel.overlays import plan_banner_texts


def test_first_clip_with_label_shows():
    assert plan_banner_texts([("q1", "Q: first?")]) == ["Q: first?"]


def test_first_clip_without_label_is_suppressed():
    assert plan_banner_texts([("q1", None)]) == [None]
    assert plan_banner_texts([("q1", "")]) == [None]


def test_same_question_consecutive_clip_is_suppressed():
    out = plan_banner_texts([("q1", "Q: a?"), ("q1", "Q: a again?")])
    assert out == ["Q: a?", None]


def test_different_question_shows_again():
    out = plan_banner_texts([("q1", "Q: a?"), ("q2", "Q: b?")])
    assert out == ["Q: a?", "Q: b?"]


def test_question_changes_back_shows_again():
    # compare to the IMMEDIATELY preceding clip, not the last shown
    out = plan_banner_texts([("q1", "Q: a?"), ("q2", "Q: b?"), ("q1", "Q: a?")])
    assert out == ["Q: a?", "Q: b?", "Q: a?"]


def test_none_qid_falls_back_to_label_comparison():
    out = plan_banner_texts([(None, "Q: a?"), (None, "Q: a?"), (None, "Q: c?")])
    assert out == ["Q: a?", None, "Q: c?"]


def test_clip_with_no_label_does_not_suppress_a_later_different_question():
    # a label-less middle clip still becomes the "preceding clip" by qid
    out = plan_banner_texts([("q1", "Q: a?"), ("q1", None), ("q2", "Q: b?")])
    assert out == ["Q: a?", None, "Q: b?"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_overlays.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.reel.overlays'`.

- [ ] **Step 3: Create `app/modules/reel/overlays.py`**

```python
"""Question banner overlaid on candidate clips (Pillow) — transparent top band.

The banner is a transparent full-frame PNG composited onto a clip via an ffmpeg
``overlay`` filter (see clips.cut_clip). Pillow is imported lazily inside
``render_question_banner`` so the pure ``plan_banner_texts`` helper (and its
tests) import cleanly in the lean nexus image; the renderer runs only in the
vision image.
"""
from __future__ import annotations

import html
import os

from app.modules.reel import cards

# Reuse the cards' brand surface so the overlay matches the cards.
_FONT_BOLD = cards._FONT_BOLD
_FONT_REG = cards._FONT_REG
_INK = cards._INK
_ACCENT_SOFT = cards._ACCENT_SOFT


def plan_banner_texts(clips: list[tuple[str | None, str | None]]) -> list[str | None]:
    """Per-clip banner text (or None) — show iff the question changed vs the
    IMMEDIATELY preceding clip. Dedup on question_id; fall back to label string
    when a question_id is None. A clip with no label never shows."""
    out: list[str | None] = []
    prev_qid: str | None = None
    prev_label: str | None = None
    first = True
    for qid, label in clips:
        if not label:
            show = False
        elif first:
            show = True
        elif qid is not None and prev_qid is not None:
            show = qid != prev_qid
        else:
            show = label != prev_label
        out.append(label if show else None)
        prev_qid, prev_label, first = qid, label, False
    return out


def render_question_banner(*, text: str, out_path: str,
                           width: int = cards.CARD_W, height: int = cards.CARD_H) -> str:
    """Render a transparent banner PNG: a dark top scrim + a violet ``Q`` + the
    wrapped white question, top-center. Returns ``out_path``."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Dark scrim across the top so white text reads over bright footage.
    scrim_h = 170
    scrim = Image.new("RGBA", (width, scrim_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(scrim_h):
        a = int(150 * (1 - y / scrim_h))   # 150 alpha at top → 0 at the band's base
        sd.line([(0, y), (width, y)], fill=(6, 11, 16, a))
    scrim = scrim.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(scrim, (0, 0))

    draw = ImageDraw.Draw(img)

    def font(path: str, size: int):
        return ImageFont.truetype(path, size)

    def text_w(s: str, f) -> float:
        return draw.textlength(s, font=f)

    qfont = font(_FONT_REG, 34)
    text = html.unescape(text.strip())
    # Wrap to <= 2 lines within 84% width.
    lines = cards.wrap_to_width(text, width * 0.84, lambda t: text_w(t, qfont))[:2]

    # Violet "Q" prefix sits to the left of the first line, top band.
    pre_font = font(_FONT_BOLD, 34)
    prefix = "Q"
    asc, desc = qfont.getmetrics()
    lh = asc + desc + 8
    block_h = lh * len(lines)
    top = 34
    pw = text_w(prefix + "  ", pre_font)
    # center the (prefix + widest line) block horizontally
    widest = max((text_w(ln, qfont) for ln in lines), default=0)
    block_w = pw + widest
    x0 = (width - block_w) / 2
    draw.text((x0, top), prefix, font=pre_font, fill=_ACCENT_SOFT)
    y = top
    for ln in lines:
        draw.text((x0 + pw, y), ln, font=qfont, fill=_INK)
        y += lh

    img.save(out_path, "PNG")
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_overlays.py -q`
Expected: PASS (the 7 pure-planner tests; the PNG renderer is verified by a frame in Task 6).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/overlays.py tests/reel/test_overlays.py
git commit -m "feat(reel): question-banner planner + transparent banner PNG renderer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `cut_clip` optional overlay compositing

**Files:**
- Modify: `app/modules/reel/clips.py` (`build_cut_cmd` + `cut_clip`)
- Test: `tests/reel/test_clips.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_cut_cmd(..., overlay_png: str | None = None)` and `cut_clip(..., overlay_png: str | None = None)`. When `overlay_png` is set, the ffmpeg argv adds it as a 2nd input and composites it over the scaled/padded video via `-filter_complex` + `overlay`, mapping the filter's video out and the source audio. When `None`, the argv is byte-for-byte the existing `-vf` form.

- [ ] **Step 1: Write failing tests**

Add to `tests/reel/test_clips.py`:

```python
from app.modules.reel.clips import build_cut_cmd


def test_cut_cmd_without_overlay_uses_vf_and_no_extra_input():
    cmd = build_cut_cmd(recording_path="rec.mp4", out_path="o.mp4",
                        start_ms=1000, end_ms=2000, offset_ms=0)
    # exactly one input, classic -vf path, no filter_complex/overlay
    assert cmd.count("-i") == 1
    assert "-vf" in cmd
    assert "-filter_complex" not in cmd
    assert "overlay" not in " ".join(cmd)


def test_cut_cmd_with_overlay_adds_input_and_overlay_filter():
    cmd = build_cut_cmd(recording_path="rec.mp4", out_path="o.mp4",
                        start_ms=1000, end_ms=2000, offset_ms=0,
                        overlay_png="banner.png")
    assert cmd.count("-i") == 2
    assert "banner.png" in cmd
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay" in fc
    # the video map is the filter output, audio comes from the source
    assert "-map" in cmd
    j = " ".join(cmd)
    assert "[v]" in j and "0:a" in j
    assert "-vf" not in cmd   # mutually exclusive with filter_complex here
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_clips.py -q`
Expected: FAIL — `build_cut_cmd() got an unexpected keyword argument 'overlay_png'`.

- [ ] **Step 3: Add the overlay path to `build_cut_cmd` + `cut_clip`**

In `app/modules/reel/clips.py`, change `build_cut_cmd`'s signature and split the video-filter handling. Replace the function body from the `vf = (...)` assignment through the `return [...]` with:

```python
    v_start = max(0, start_ms - offset_ms - pad_ms)
    v_end = max(v_start + 1, end_ms - offset_ms + tail_pad_ms)
    ss = v_start / 1000.0
    dur = (v_end - v_start) / 1000.0
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={FPS}"
    )
    cmd = ["ffmpeg", "-y", "-ss", f"{ss:.3f}", "-i", recording_path]
    if overlay_png:
        # Composite the transparent banner PNG over the normalized video.
        cmd += ["-i", overlay_png,
                "-filter_complex", f"[0:v]{vf}[base];[base][1:v]overlay=0:0[v]",
                "-map", "[v]", "-map", "0:a"]
    else:
        cmd += ["-vf", vf]
    cmd += [
        "-t", f"{dur:.3f}",
        "-vsync", "cfr",
        "-af", "aresample=async=1:first_pts=0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out_path,
    ]
    return cmd
```

Update the signature line to add the param:

```python
def build_cut_cmd(*, recording_path: str, out_path: str,
                  start_ms: int, end_ms: int, offset_ms: int,
                  pad_ms: int = PAD_MS, tail_pad_ms: int = TAIL_PAD_MS,
                  overlay_png: str | None = None) -> list[str]:
```

Update `cut_clip` to accept and forward `overlay_png`:

```python
async def cut_clip(*, recording_path: str, out_path: str,
                   start_ms: int, end_ms: int, offset_ms: int,
                   pad_ms: int = PAD_MS, tail_pad_ms: int = TAIL_PAD_MS,
                   overlay_png: str | None = None) -> str:
    """Write a normalized, A/V-locked clip for [start_ms, end_ms] (source clock)."""
    cmd = build_cut_cmd(
        recording_path=recording_path, out_path=out_path,
        start_ms=start_ms, end_ms=end_ms, offset_ms=offset_ms,
        pad_ms=pad_ms, tail_pad_ms=tail_pad_ms, overlay_png=overlay_png,
    )
```

(The rest of `cut_clip` — the subprocess exec + error check — is unchanged.)

Note on the existing test `test_cut_cmd_offset_shifts_window_back` and `test_cut_cmd_is_cfr_av_locked_and_output_bounded`: they call `build_cut_cmd` without `overlay_png`, so they hit the `-vf` branch and must still pass unchanged. Confirm they do in Step 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_clips.py -q`
Expected: PASS (new overlay tests + all pre-existing clip tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/clips.py tests/reel/test_clips.py
git commit -m "feat(reel): cut_clip can composite a transparent overlay PNG

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire the banner into `render_reel`

**Files:**
- Modify: `app/modules/reel/render.py` (imports + `render_reel` clip branch)
- Test: `tests/reel/test_render.py`

**Interfaces:**
- Consumes: `overlays.plan_banner_texts`, `overlays.render_question_banner` (Task 2); `clips.cut_clip(..., overlay_png=...)` (Task 3); `ValidatedBeat.question_id`/`question_label` (Task 1).
- Produces: a pure module-level helper `banner_texts_by_index(beats: list) -> dict[int, str]` mapping each clip beat's index → banner text to show (only entries that should show). `render_reel` uses it to render+overlay the banner for shown clips.

- [ ] **Step 1: Write failing tests for the index-mapping helper**

Add to `tests/reel/test_render.py` (the file already defines a `_KindBeat` with just `kind`; this helper needs `question_id`/`question_label`, so add a small local beat class):

```python
from app.modules.reel.render import banner_texts_by_index


class _ClipBeat:
    def __init__(self, kind, question_id=None, question_label=None):
        self.kind = kind
        self.question_id = question_id
        self.question_label = question_label


def test_banner_texts_by_index_maps_only_shown_clips():
    beats = [
        _ClipBeat("point"),                                  # 0: card, ignored
        _ClipBeat("clip", "q1", "Q: a?"),                    # 1: show
        _ClipBeat("point"),                                  # 2: card, ignored
        _ClipBeat("clip", "q1", "Q: a again?"),              # 3: same q -> suppress
        _ClipBeat("clip", "q2", "Q: b?"),                    # 4: show
        _ClipBeat("outro"),                                  # 5: card, ignored
    ]
    assert banner_texts_by_index(beats) == {1: "Q: a?", 4: "Q: b?"}


def test_banner_texts_by_index_empty_when_no_clips():
    assert banner_texts_by_index([_ClipBeat("point"), _ClipBeat("outro")]) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'banner_texts_by_index'`.

- [ ] **Step 3: Implement the helper + wire it into `render_reel`**

In `app/modules/reel/render.py`, extend the existing reel import to include `overlays`:

```python
from app.modules.reel import cards, clips, overlays, tts
```

Add the pure helper near `first_point_index`:

```python
def banner_texts_by_index(beats: list) -> dict[int, str]:
    """Map each clip beat's index -> the banner text to SHOW (suppressed clips
    and card beats are absent). Clip beats are the non-card beats carrying words;
    here identified by having a ``question_label`` attribute and not being a card."""
    clip_positions = [i for i, b in enumerate(beats) if b.kind not in _CARD_KINDS]
    pairs = [(getattr(beats[i], "question_id", None),
              getattr(beats[i], "question_label", None)) for i in clip_positions]
    texts = overlays.plan_banner_texts(pairs)
    return {i: t for i, t in zip(clip_positions, texts) if t}
```

In `render_reel`, compute the map once before the loop (next to `subtitle_idx`):

```python
    subtitle_idx = first_point_index(beats)
    banner_by_idx = banner_texts_by_index(beats)
```

In the `else:  # clip / experience` branch of the render loop, render the banner PNG (best-effort) and pass it to `cut_clip`. Replace that branch's body:

```python
        else:  # clip / experience
            video_start, video_end = _clip_to_video(b, offset_ms)
            overlay_png = None
            banner_text = banner_by_idx.get(i)
            if banner_text:
                try:
                    overlay_png = os.path.join(tmp_dir, f"banner_{i:02d}.png")
                    overlays.render_question_banner(text=banner_text, out_path=overlay_png)
                except Exception:  # best-effort: never fail the reel over a banner
                    overlay_png = None
            await clips.cut_clip(
                recording_path=recording_path, out_path=seg,
                start_ms=video_start, end_ms=video_end, offset_ms=0,
                overlay_png=overlay_png)
            rendered.append((seg, b))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/render.py tests/reel/test_render.py
git commit -m "feat(reel): overlay the question banner on shown clips during render

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Director prompt — emit `question_label` per clip

**Files:**
- Modify: `prompts/v3/reel/director.txt`

**Interfaces:**
- Consumes: nothing (prompt text). The `<answers>` block in the document already prints `question_id=...` per answer ref, and the transcript exposes the agent's spoken questions, so the model has what it needs to paraphrase.
- Produces: a prompt that instructs a `question_label` on every clip/experience beat.

- [ ] **Step 1: Add a `<question_label>` instruction block**

In `prompts/v3/reel/director.txt`, inside `<selecting_clips>` (or as its own short block right after it), add guidance. Insert this block immediately before the closing `</selecting_clips>` tag:

```
QUESTION LABEL: every clip/experience beat MUST carry a `question_label` — a
SHORT paraphrase (6-10 words) of the question THIS clip answers, phrased as a
question (e.g. "New iPhones missing expected settings — where do you start?").
Base it on what the interviewer actually asked leading into this answer (the
agent's question, not the candidate's words). Do NOT restate the candidate's
answer, do NOT include a question id, and keep it tight enough to read in one
glance on screen. If two clips answer the same question, give them the same
label.
```

Also extend the `<rules>` line that lists output requirements so the label is not forgotten — append to the existing "Output at least one clip..." rule a sentence:

```
- Every clip/experience beat carries a `question_label` (see <selecting_clips>).
```

- [ ] **Step 2: Sanity-check the instruction is present and coherent**

Run: `grep -niE "question_label" prompts/v3/reel/director.txt`
Expected: at least two matches (the `<selecting_clips>` block + the `<rules>` line). Read the surrounding lines to confirm the prompt still flows.

- [ ] **Step 3: Commit**

```bash
git add prompts/v3/reel/director.txt
git commit -m "feat(reel): director emits a short question_label per clip

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5b: Ground the question label in the real interviewer question

**Why:** The director's document (`_build_document`) does NOT include the agent's spoken questions — only the candidate's answer words + the report's per-question `title`/`our_read`/`candidate_quote` (which are often empty). So a `question_label` would be fabricated from the answer. This task threads the interviewer's actual spoken question into the document per answer-run, and points the prompt at it, so the label paraphrases the REAL question.

**Files:**
- Modify: `app/modules/reel/transcript.py` (new pure helper)
- Modify: `app/modules/reel/director.py` (`_build_document` adds an `asked:` line)
- Modify: `prompts/v3/reel/director.txt` (point the QUESTION LABEL block at the `asked:` field)
- Test: `tests/reel/test_transcript.py`, `tests/reel/test_director.py`

**Interfaces:**
- Produces: `questions_by_run(transcript: list[dict]) -> list[str | None]` — the interviewer's question text immediately preceding each answer run, in run order (index aligns with `answer_runs(...)` `ref`). Consecutive agent turns before a run are joined; a run with no preceding agent turn yields `None`.
- `_build_document` emits an `asked: <question>` line (truncated to 280 chars) under each answer that has one.

- [ ] **Step 1: Write failing tests for `questions_by_run`**

Add to `tests/reel/test_transcript.py` (extend the existing import; the `_cand`/`_agent` helpers already exist in that file — `_agent(text="...")` sets the agent turn's `text`):

```python
from app.modules.reel.transcript import answer_runs, questions_by_run


def test_questions_by_run_captures_preceding_agent_question():
    tr = [_agent("What is your Intune triage?"), _cand("t-1", 100, [("a", 0, 1)])]
    assert questions_by_run(tr) == ["What is your Intune triage?"]


def test_questions_by_run_joins_consecutive_agent_turns():
    tr = [_agent("Sure —"), _agent("Walk me through enrollment."),
          _cand("t-1", 100, [("a", 0, 1)])]
    assert questions_by_run(tr) == ["Sure — Walk me through enrollment."]


def test_questions_by_run_one_entry_per_run_in_order():
    tr = [_agent("Q1?"), _cand("t-1", 100, [("a", 0, 1)]),
          _cand("t-2", 200, [("b", 0, 1)]),         # continuation -> same run
          _agent("Q2?"), _cand("t-3", 300, [("c", 0, 1)])]
    assert questions_by_run(tr) == ["Q1?", "Q2?"]
    assert len(questions_by_run(tr)) == len(answer_runs(tr))


def test_questions_by_run_none_when_no_preceding_agent():
    assert questions_by_run([_cand("t-1", 100, [("a", 0, 1)])]) == [None]
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_transcript.py -q`
Expected: FAIL — `ImportError: cannot import name 'questions_by_run'`.

- [ ] **Step 3: Add `questions_by_run` to `transcript.py`**

Add this pure function to `app/modules/reel/transcript.py` (next to `answer_runs`):

```python
def questions_by_run(transcript: list[dict]) -> list[str | None]:
    """Interviewer question text immediately preceding each answer run, in run
    order (aligned to ``answer_runs`` ``ref``). Consecutive agent turns before a
    run are joined; a run with no preceding agent turn yields ``None``."""
    out: list[str | None] = []
    pending: list[str] = []
    in_run = False
    for turn in transcript:
        if turn.get("speaker") != "candidate":
            txt = (turn.get("text") or
                   " ".join(str(w.get("text", "")) for w in (turn.get("words") or []))).strip()
            if txt:
                pending.append(txt)
            in_run = False
            continue
        if turn.get("turn_ref") is None:
            continue
        if not in_run:
            out.append(" ".join(pending).strip() or None)
            pending = []
            in_run = True
    return out
```

- [ ] **Step 4: Write a failing test for the document `asked:` line**

Add to `tests/reel/test_director.py`:

```python
def test_document_includes_asked_question_line():
    from app.modules.reel.director import _build_document
    tr = [
        {"speaker": "agent", "turn_ref": "a", "span": {"start_ms": 0, "end_ms": 1},
         "text": "Walk me through enrollment.", "words": []},
        {"speaker": "candidate", "turn_ref": "t-1", "question_id": "q1",
         "span": {"start_ms": 100, "end_ms": 101},
         "words": [{"text": "first", "start_ms": 0, "end_ms": 100}]},
    ]
    doc = _build_document(candidate_name="A", role_title="R", verdict="advance",
                          verdict_reason=None, why_positive=None, strengths=[],
                          question_scorecards=[], signal_scorecards=[], transcript=tr)
    assert "asked: Walk me through enrollment." in doc
```

- [ ] **Step 5: Run to verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_director.py::test_document_includes_asked_question_line -q`
Expected: FAIL — the document has no `asked:` line yet.

- [ ] **Step 6: Wire `asked:` into `_build_document`**

In `app/modules/reel/director.py`, the `_build_document` import of transcript helpers (the `answer_runs` / `is_pause_before` import at the top of the file) — add `questions_by_run` to it. Then in `_build_document`, just before the `lines.append("<answers>")` line, compute:

```python
    questions = questions_by_run(transcript)
```

And inside the `for run in answer_runs(transcript):` loop (the block that currently appends `answer ref=... | question_id=...` then `words: ...`), add the `asked:` line right after the `answer ref=...` line:

```python
        lines.append(f"answer ref={run.ref} | question_id={run.question_id}")
        asked = questions[run.ref] if run.ref < len(questions) else None
        if asked:
            lines.append(f"asked: {asked[:280]}")
        lines.append("words: " + " ".join(parts))
```

- [ ] **Step 7: Run both test files green**

Run: `docker compose exec -T nexus python -m pytest tests/reel/test_transcript.py tests/reel/test_director.py -q`
Expected: PASS.

- [ ] **Step 8: Point the prompt at the `asked:` field**

In `prompts/v3/reel/director.txt`, find the `QUESTION LABEL:` block (added in Task 5). It currently says to base the label on "what the interviewer actually asked leading into this answer (the agent's question, not the candidate's words)." Replace that sentence so it references the new field explicitly:

```
Base it on the `asked:` line of THIS answer in <answers> — that is the
interviewer's actual question. (If an answer has no `asked:` line, infer the
question from the answer.) Do NOT restate the candidate's answer.
```

Keep the rest of the block (6-10 words, phrased as a question, no question id, same label for clips on the same question) intact.

- [ ] **Step 9: Commit**

```bash
git add app/modules/reel/transcript.py app/modules/reel/director.py prompts/v3/reel/director.txt tests/reel/test_transcript.py tests/reel/test_director.py
git commit -m "feat(reel): ground the clip question label in the real interviewer question

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Full suite + live regen + frame verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full reel suite**

Run: `docker compose exec -T nexus python -m pytest tests/reel -q`
Expected: PASS — all reel tests green (director, overlays, clips, render, actor, cards, etc.).

- [ ] **Step 2: Restart the vision worker (no hot-reload)**

Run: `docker compose up -d --force-recreate nexus-vision-worker`
Expected: container recreated and `Up` (`docker compose ps nexus-vision-worker`).

- [ ] **Step 3: Regenerate the EMM reel**

From `backend/nexus/`, reset + enqueue with force (the same path used previously):

```bash
docker compose exec -T nexus python - <<'PY'
from app import brokers  # configure the RedisBroker before send
from app.modules.reel.actors import generate_session_reel
import asyncio
from sqlalchemy import text
from app.database import get_bypass_session
SID="14a15eb8-9084-4925-8573-71a6f06745d2"; TID="903bb4dd-7cc7-436f-ad6b-68e4f05a6ce4"
async def reset():
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{TID}'"))
        await db.execute(text("UPDATE session_reels SET status='pending', generation_error=NULL, generation_started_at=NULL, version=version+1 WHERE session_id=:s AND tenant_id=:t"), {"s": SID, "t": TID})
asyncio.run(reset())
generate_session_reel.send(SID, TID, "reel-qbanner-verify", True)
print("enqueued")
PY
```

Poll the DB until `status='ready'`:

```bash
docker compose exec -T nexus python - <<'PY'
import asyncio, logging; logging.disable(logging.INFO)
from sqlalchemy import text
from app.database import get_bypass_session
async def go():
    async with get_bypass_session() as db:
        await db.execute(text("SET LOCAL app.current_tenant='903bb4dd-7cc7-436f-ad6b-68e4f05a6ce4'"))
        r=(await db.execute(text("SELECT status, generation_error FROM session_reels WHERE session_id='14a15eb8-9084-4925-8573-71a6f06745d2'"))).mappings().first()
        print(r['status'], '|', (r['generation_error'] or '')[:300])
asyncio.run(go())
PY
```

Expected: `ready` with no error (re-run the poll until ready; the render takes ~1–2 min).

- [ ] **Step 4: Pull a frame from a banner-bearing clip and confirm it reads**

The chapters give each clip's `start_ms`. Download the reel and grab a frame a couple seconds into the FIRST clip (its `start_ms` from the chapters + ~2s), via the vision worker (has ffmpeg + storage creds):

```bash
docker compose exec -T nexus-vision-worker python - <<'PY'
import asyncio
from app.storage import get_object_storage
async def go():
    s=get_object_storage()
    await s.download_to_path("reels/903bb4dd-7cc7-436f-ad6b-68e4f05a6ce4/14a15eb8-9084-4925-8573-71a6f06745d2.mp4","/tmp/reel.mp4")
asyncio.run(go())
PY
# grab a frame ~2s into the first clip (adjust the -ss seconds to a clip's start+2s from the chapters)
docker compose exec -T nexus-vision-worker bash -lc 'ffmpeg -y -ss 7 -i /tmp/reel.mp4 -frames:v 1 /tmp/banner_frame.png 2>/dev/null && echo ok'
CID=$(docker compose ps -q nexus-vision-worker)
docker cp "$CID:/tmp/banner_frame.png" /tmp/banner_frame.png
echo "frame at /tmp/banner_frame.png"
```

Visually confirm (open the PNG): the top-center `Q …` banner reads cleanly over the candidate footage, wrapped to ≤2 lines, legible against the scrim. Then confirm a same-question follow-up clip (a clip whose chapter follows another clip under the same point with no new question) has NO banner. Use the `extract_reel.py` chapter/EDL dump to see which clips share a question.

- [ ] **Step 5: Final commit (only if a verification fixup was needed)**

```bash
git add -A
git commit -m "test(reel): verify question banner end-to-end

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** LLM source + per-clip label (Task 1 schema + Task 5 prompt); deterministic show-on-change dedup (Task 2 `plan_banner_texts`); transparent PNG overlay via ffmpeg (Task 2 renderer + Task 3 `cut_clip`); render wiring + best-effort failure (Task 4); styling (Task 2 renderer, verified Task 6); no migration / no bank dependency (nothing touches them). All spec items map to a task.
- **Type consistency:** `ReelBeat.question_label: str|None` (Task 1) → read in `_resolve_clip` (Task 1) and emitted by the prompt (Task 5). `ValidatedBeat.question_id`/`question_label` (Task 1) → consumed by `banner_texts_by_index` (Task 4) via `getattr`. `plan_banner_texts(list[tuple[str|None,str|None]]) -> list[str|None]` (Task 2) → consumed by `banner_texts_by_index` (Task 4). `render_question_banner(*, text, out_path, ...)` (Task 2) → called in `render_reel` (Task 4). `cut_clip(..., overlay_png=...)` (Task 3) → called in `render_reel` (Task 4). Names/signatures consistent across tasks.
- **No placeholders:** every code step shows real code; every run step has a command + expected result.
- **Best-effort guarantee:** Task 4 wraps banner render in try/except so a banner failure never fails the reel (spec constraint). The ffmpeg overlay itself, if it failed, would surface as a `cut_clip` RuntimeError — but the overlay command is deterministic and validated by Task 3's arg test; a malformed PNG can't be produced because `render_question_banner` either writes a valid PNG or raises (caught) before `cut_clip` is called.
