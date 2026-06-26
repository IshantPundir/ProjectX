# Candidate Reel — Drop Intro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the reel's title + match intro cards so the reel opens cold on the first strength card (carrying a name+role tag), then the candidate's voice.

**Architecture:** Schema-enforced removal — `title`/`match` are deleted from the EDL `BeatKind` Literal so the director LLM structurally cannot emit them. The director prompt is rewritten to open on strengths. The candidate name+role is rendered deterministically as a subtitle on the first strength card only, sourced from already-loaded report data (never LLM-authored). No DB migration, no question-bank dependency.

**Tech Stack:** Python 3.13, Pydantic v2, Pillow (cards, vision image only), ffmpeg (render), OpenAI Responses API (director). Tests are pure-function pytest in the lean nexus image.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-26-reel-drop-intro-design.md`.
- Permanent change for ALL reels — no per-tenant toggle.
- Subtitle text format: `FirstName · Role Title` (e.g. `Punar · EMM Engineer`). Degrade gracefully: only one part present → show just that part; neither present → no subtitle.
- Subtitle appears on the FIRST strength (`point`) card ONLY; all other cards carry no name.
- The reel renderer/director runs in the long-lived `nexus-vision-worker`, which has **NO hot-reload** — restart it after any code/prompt change before manual verification.
- No new dependencies. Cross-module imports go through public APIs (intra-`reel` deep imports are fine).
- `tests/reel/test_no_gen2_eventlog.py` scans reel source for forbidden markers — do not introduce gen-2 event-log markers.
- Run reel tests with: `docker compose run --rm nexus pytest tests/reel -q` (or `.venv/bin/python -m pytest tests/reel -q` from `backend/nexus/` if the venv has deps). All commands below assume CWD = `backend/nexus/`.

---

### Task 1: Remove `title`/`match` from the director EDL schema + budget logic

**Files:**
- Modify: `app/modules/reel/director.py` (lines ~43, ~51, ~52)
- Test: `tests/reel/test_director.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `BeatKind = Literal["experience", "point", "clip", "outro"]` (no `title`/`match`); `LEAD_CARDS = {"point"}`; `_CARD_FLOOR_MS` without `title`/`match` keys. `ReelBeat(kind="title")` / `ReelBeat(kind="match")` now raise `pydantic.ValidationError` at construction. `validate_edl` / `ValidatedEdl` / `NoClipBeatsError` signatures unchanged.

- [ ] **Step 1: Update the two budget tests + the zero-clip + match-estimate tests to the new shape (write failing tests first)**

In `tests/reel/test_director.py`, replace the body of `test_over_budget_drops_trailing_point_groups_keeping_one` so it no longer builds `title`/`match` beats:

```python
def test_over_budget_drops_trailing_point_groups_keeping_one(monkeypatch):
    # Mechanism-preserved: with the total budget configured LOW (80s), seven
    # ~15s clips far exceed it, forcing trailing point-groups (card + clip) to
    # drop. agents between -> separate runs (refs 0..6). No title/match intro.
    monkeypatch.setattr(settings, "reel_max_total_ms", 80_000)
    n = 7
    transcript = []
    for i in range(n):
        transcript += [_cand(f"t-{i}", 16), _agent()]
    beats = []
    for i in range(n):
        beats += [ReelBeat(kind="point", on_screen_text=f"p{i}"), _clip(i, 0, 15)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    kinds = [b.kind for b in vedl.beats]
    assert kinds[0] == "point" and kinds[-1] == "outro"
    clips = _by_kind(vedl, "clip")
    assert 1 <= len(clips) < 7
    assert len(_by_kind(vedl, "point")) == len(clips)
    assert vedl.duration_ms <= settings.reel_max_total_ms
```

Replace the body of `test_no_clips_dropped_at_default_high_budget` the same way (drop the `title`/`match` lines):

```python
def test_no_clips_dropped_at_default_high_budget():
    # At the relaxed default (~1 h), many clips all survive — none dropped — so
    # the candidate's full evidence is shown. No title/match intro.
    assert settings.reel_max_total_ms >= 3_600_000
    n = 7
    transcript = []
    for i in range(n):
        transcript += [_cand(f"t-{i}", 16), _agent()]
    beats = []
    for i in range(n):
        beats += [ReelBeat(kind="point", on_screen_text=f"p{i}"), _clip(i, 0, 15)]
    beats.append(ReelBeat(kind="outro", on_screen_text="o"))
    vedl = validate_edl(ReelEdlOut(beats=beats), transcript)
    assert len(_by_kind(vedl, "clip")) == n
    assert len(_by_kind(vedl, "point")) == n
```

Replace `test_zero_clip_beats_raises` (it used a `title` beat that will no longer be a valid kind):

```python
def test_zero_clip_beats_raises():
    edl = ReelEdlOut(beats=[ReelBeat(kind="point", on_screen_text="p"),
                            ReelBeat(kind="outro", on_screen_text="o")])
    with pytest.raises(NoClipBeatsError):
        validate_edl(edl, [_cand("t-1", 5)])
```

Replace `test_match_card_duration_estimated_from_narration` (match is gone) with the equivalent for a `point` card:

```python
def test_point_card_duration_estimated_from_narration():
    narration = " ".join(["word"] * 22)   # 22 / 2.75 = 8s, above the point floor
    edl = ReelEdlOut(beats=[
        ReelBeat(kind="point", on_screen_text="p", narration_text=narration),
        _clip(0, 0, 5)])
    point = _by_kind(validate_edl(edl, [_cand("t-1", 6)]), "point")[0]
    assert point.duration_ms >= 8000
```

Add a new test asserting the removed kinds are rejected by the schema (add the import at the top of the file: `from pydantic import ValidationError`):

```python
@pytest.mark.parametrize("dead_kind", ["title", "match"])
def test_removed_intro_kinds_are_rejected_by_schema(dead_kind):
    with pytest.raises(ValidationError):
        ReelBeat(kind=dead_kind, on_screen_text="x")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/reel/test_director.py -q`
Expected: FAIL — `test_removed_intro_kinds_are_rejected_by_schema` fails (title/match still accepted), and the rewritten budget tests assert `kinds[0] == "point"` which currently is also true so those may pass; the parametrized rejection test is the key failing one.

- [ ] **Step 3: Edit `director.py` to remove the intro kinds**

In `app/modules/reel/director.py`:

Line ~43 — drop the `title`/`match` floors:
```python
_CARD_FLOOR_MS = {"point": 3_500, "outro": 4_000}
```

Line ~51 — lead card is now `point` only:
```python
LEAD_CARDS = {"point"}                  # a card that leads a drop-group of clips
```

Line ~52 — remove `title`/`match` from the Literal:
```python
BeatKind = Literal["experience", "point", "clip", "outro"]
```

Leave `_fit_budget`'s `title = [beats[0]] if beats and beats[0].kind == "title" else []` line as-is — with no title kind it always evaluates to `[]`, which is correct (only the outro stays pinned). No other change needed in `_fit_budget`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reel/test_director.py -q`
Expected: PASS (all director tests green).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/director.py tests/reel/test_director.py
git commit -m "feat(reel): remove title/match from EDL schema and budget logic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Identity-tag helper + first-card subtitle rendering in `cards.py`

**Files:**
- Modify: `app/modules/reel/cards.py`
- Test: `tests/reel/test_cards.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `format_identity_tag(candidate_name: str | None, role_title: str | None) -> str | None` — pure; returns `"FirstName · Role Title"`, or just one part, or `None`.
  - `render_card(*, kind, out_path, on_screen_text=None, subtitle: str | None = None, width=CARD_W, height=CARD_H) -> str` — the `point` branch draws `subtitle` (when set) below the strength phrase; `title`/`match` branches removed.

- [ ] **Step 1: Write failing tests for `format_identity_tag`**

Add to `tests/reel/test_cards.py` (extend the existing import line):

```python
from app.modules.reel.cards import format_identity_tag, wrap_to_width


def test_identity_tag_full_name_and_role():
    assert format_identity_tag("Punar Singh", "EMM Engineer") == "Punar · EMM Engineer"


def test_identity_tag_uses_only_first_name():
    assert format_identity_tag("Asha Rao Kumar", "Backend Engineer") == "Asha · Backend Engineer"


def test_identity_tag_name_only_when_role_missing():
    assert format_identity_tag("Punar Singh", None) == "Punar"
    assert format_identity_tag("Punar Singh", "  ") == "Punar"


def test_identity_tag_role_only_when_name_missing():
    assert format_identity_tag(None, "EMM Engineer") == "EMM Engineer"
    assert format_identity_tag("   ", "EMM Engineer") == "EMM Engineer"


def test_identity_tag_none_when_both_missing():
    assert format_identity_tag(None, None) is None
    assert format_identity_tag("  ", "") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/reel/test_cards.py -q`
Expected: FAIL — `ImportError: cannot import name 'format_identity_tag'`.

- [ ] **Step 3: Implement `format_identity_tag` and the subtitle in `cards.py`**

Add the pure helper near `wrap_to_width` in `app/modules/reel/cards.py`:

```python
def format_identity_tag(candidate_name: str | None, role_title: str | None) -> str | None:
    """Build the first-card identity subtitle: ``"FirstName · Role Title"``.

    Pure + deterministic — identity is a known fact, never LLM-authored. Degrades
    gracefully: only one part present → just that part; neither → ``None``.
    """
    first = (candidate_name or "").strip().split()
    name = first[0] if first else ""
    role = (role_title or "").strip()
    parts = [p for p in (name, role) if p]
    return " · ".join(parts) if parts else None
```

Change the `render_card` signature to accept `subtitle`:

```python
def render_card(*, kind: str, out_path: str, on_screen_text: str | None = None,
                subtitle: str | None = None,
                width: int = CARD_W, height: int = CARD_H) -> str:
```

Replace the `if kind == "title": … elif kind == "match": … elif kind == "point":` chain so the dead `title`/`match` branches are gone and `point` draws the subtitle. The new chain:

```python
    if kind == "point":
        star = font(_FONT_BOLD, 96)
        sw = text_w("★", star)
        draw.text(((width - sw) / 2, 170), "★", font=star, fill=_ACCENT_SOFT)
        phrase = text.lstrip("★ ").strip() or text
        y = centered_block(phrase, font(_FONT_BOLD, 54), top=320, fill=_INK)
        if subtitle:
            centered_block(subtitle, font(_FONT_REG, 30), top=y + 22, fill=_INK_SOFT)
    elif kind == "outro":
        centered_block(text, font(_FONT_BOLD, 50), top=230, fill=_INK)
        _cta_pill(draw, font(_FONT_BOLD, 30), "▶  Watch full interview", width, y=470)
        _paste_wordmark(Image, img, y=600, target_w=220)
    else:  # generic fallback
        centered_block(text, font(_FONT_BOLD, 52), top=300, fill=_INK)
```

Update the module docstring's first line (it lists `title/match/point/outro`) to: `Card beats (point/outro) render to a PNG …`. The now-unused `_eyebrow`, `_accent_rule`, and `_paste_wordmark` helpers: `_paste_wordmark` is still used by `outro`; delete `_eyebrow` and `_accent_rule` (only `title`/`match` used them) to avoid dead code.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reel/test_cards.py -q`
Expected: PASS (the pure-helper tests; Pillow drawing is verified manually in Task 6).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/cards.py tests/reel/test_cards.py
git commit -m "feat(reel): identity-tag helper + first-card subtitle; drop title/match cards

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Thread `identity_tag` through `render_reel` (subtitle on first point card only)

**Files:**
- Modify: `app/modules/reel/render.py`
- Test: `tests/reel/test_render.py`

**Interfaces:**
- Consumes: `cards.render_card(..., subtitle=...)` from Task 2.
- Produces:
  - `first_point_index(beats: list) -> int | None` — pure; index of the first beat whose `kind == "point"`, else `None`.
  - `render_reel(*, beats, recording_path, offset_ms, tmp_dir, out_path, tts_enabled=True, identity_tag: str | None = None)` — passes `subtitle=identity_tag` to `render_card` for the first `point` card only.

- [ ] **Step 1: Write failing tests for `first_point_index`**

Add to `tests/reel/test_render.py` (extend the import):

```python
from app.modules.reel.render import (
    _clip_to_video,
    build_card_segment_cmd,
    build_concat_cmd,
    first_point_index,
)


class _KindBeat:
    def __init__(self, kind):
        self.kind = kind


def test_first_point_index_finds_first_point():
    beats = [_KindBeat("point"), _KindBeat("clip"), _KindBeat("point"), _KindBeat("outro")]
    assert first_point_index(beats) == 0


def test_first_point_index_skips_leading_non_points():
    beats = [_KindBeat("clip"), _KindBeat("point"), _KindBeat("outro")]
    assert first_point_index(beats) == 1


def test_first_point_index_none_when_no_point():
    beats = [_KindBeat("clip"), _KindBeat("outro")]
    assert first_point_index(beats) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/reel/test_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'first_point_index'`.

- [ ] **Step 3: Implement `first_point_index` + thread `identity_tag` in `render.py`**

In `app/modules/reel/render.py`:

Remove `title`/`match` from the card constants (validated EDLs no longer contain them):
```python
_CARD_FLOOR_S = {"point": 3.0, "outro": 4.0}
```
```python
_CARD_KINDS = ("point", "outro")
```

Add the pure helper (near `_chapter_label`):
```python
def first_point_index(beats: list) -> int | None:
    """Index of the first ``point`` beat (gets the identity subtitle), or None."""
    for i, b in enumerate(beats):
        if b.kind == "point":
            return i
    return None
```

Update `render_reel`'s signature to add `identity_tag`:
```python
async def render_reel(*, beats: list, recording_path: str, offset_ms: int,
                      tmp_dir: str, out_path: str, tts_enabled: bool = True,
                      identity_tag: str | None = None
                      ) -> tuple[str, list[dict]]:
```

Compute the first-point index once before the render loop, and pass the subtitle only for that beat. Replace the `cards.render_card(...)` call inside the loop:
```python
    subtitle_idx = first_point_index(beats)
    for i, b in enumerate(beats):
        seg = os.path.join(tmp_dir, f"seg_{i:02d}.mp4")
        if b.kind in _CARD_KINDS:
            png = os.path.join(tmp_dir, f"card_{i:02d}.png")
            cards.render_card(kind=b.kind, out_path=png,
                              on_screen_text=b.on_screen_text or "",
                              subtitle=identity_tag if i == subtitle_idx else None)
```
(The rest of the loop body — TTS, `card_segment`, clip cutting, chapters, concat — is unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reel/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/render.py tests/reel/test_render.py
git commit -m "feat(reel): render identity subtitle on the first strength card

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Build the identity tag in the actor and pass it to `render_reel`

**Files:**
- Modify: `app/modules/reel/actors.py` (in `_build_and_upload`, the `render.render_reel(...)` call ~line 186)
- Test: `tests/reel/test_actor.py`

**Interfaces:**
- Consumes: `cards.format_identity_tag` (Task 2), `render_reel(..., identity_tag=...)` (Task 3).
- Produces: the actor computes `identity_tag = cards.format_identity_tag(inp["candidate_name"], inp["role_title"])` and forwards it to `render.render_reel`.

- [ ] **Step 1: Write the failing actor test**

In `tests/reel/test_actor.py`, the `_inputs` helper already sets `candidate_name="Asha"`, `role_title="Backend Engineer"`. Add a new test (the `_patched` fixture already captures `render_kwargs`):

```python
@pytest.mark.asyncio
async def test_identity_tag_passed_to_render(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, 0, tzinfo=UTC)
    _patched["inputs"] = _inputs(_evidence("2026-06-14T10:00:00.500000Z"),
                                 recording_started_at=rec_start)
    await actors._build_and_upload(uuid4(), uuid4(), "corr-1", actors.logger.bind())
    assert _patched["render_kwargs"]["identity_tag"] == "Asha · Backend Engineer"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reel/test_actor.py::test_identity_tag_passed_to_render -q`
Expected: FAIL — `KeyError: 'identity_tag'` (the actor does not pass it yet).

- [ ] **Step 3: Implement the wiring in `actors.py`**

Ensure `cards` is imported at the top of `app/modules/reel/actors.py` (alongside the other `from app.modules.reel import ...`); add `cards` to that import if absent:
```python
from app.modules.reel import cards, render, timing
```
(Match the existing import style in the file; only add `cards` if it is not already imported.)

In `_build_and_upload`, just before the `render.render_reel(...)` call, build the tag and pass it:
```python
        identity_tag = cards.format_identity_tag(
            inp["candidate_name"], inp["role_title"])
        out_path = os.path.join(tmp, "reel.mp4")
        _, chapters = await render.render_reel(
            beats=vedl.beats, recording_path=rec_path, offset_ms=final_offset,
            tmp_dir=tmp, out_path=out_path, identity_tag=identity_tag,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reel/test_actor.py -q`
Expected: PASS (the new test + all existing actor tests).

- [ ] **Step 5: Commit**

```bash
git add app/modules/reel/actors.py tests/reel/test_actor.py
git commit -m "feat(reel): pass deterministic identity tag from actor to renderer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Rewrite the director prompt to open on strengths

**Files:**
- Modify: `prompts/v3/reel/director.txt`

**Interfaces:**
- Consumes: nothing (prompt text).
- Produces: a prompt whose structure is `(point → clip[+clip]) × N → outro` with no `title`/`match` beats.

- [ ] **Step 1: Rewrite the `<the_pitch>` structure section**

In `prompts/v3/reel/director.txt`, replace the structure line and the `title` + `§1 MATCH` blocks (lines ~12–26) so the reel opens on strengths. Keep `§2 STRENGTHS` (renumber to `§1`), the `outro` block, the PACING line, and the `<selecting_clips>` / `<narration>` / `<rules>` sections. The new structure header + opening:

```
    §1 strengths: (point → clip[+clip]) × N  →  outro

- §1 STRENGTHS — the reel OPENS here, on the candidate's DIFFERENTIATING strengths
           (the ones that make this candidate stand out). There is NO title or
           intro card — the first thing the recruiter sees is a strength and then
           the candidate's own words. Emit ~3 strengths (this is the heart of the
           reel). For each, a `point` card then 1-2 `clip` beats:
             • point.on_screen_text = "★ " + a short strength phrase; point.narration_text
               = ONE short sentence (~14 words) — see <narration>. The FIRST point's
               narration may name the candidate to orient the viewer (e.g. "Watch how
               Punar begins with the right controls…").
             • clip = the candidate's real words evidencing it (see <selecting_clips>).
               Use a second clip only when a different moment genuinely strengthens
               the same point.
```

Keep the existing `outro` block. Update the PACING line if it references the match card (it currently says "your narration is brief framing between clips" — leave as-is; it is still accurate). In `<rules>`, the DO-NOT-REPEAT clause mentions "a point made in §1 must not reappear as a §2 strength" — simplify it to: `no two §1 points may make the same argument.` Leave the `experience` clip kind available but do not instruct a dedicated experience beat (it is now just a clip variant).

- [ ] **Step 2: Sanity-check the prompt has no lingering title/match instructions**

Run: `grep -niE "title card|match card|§1 match|§2" prompts/v3/reel/director.txt`
Expected: no matches (empty output). If `§2` still appears, finish renumbering to `§1`.

- [ ] **Step 3: Commit**

```bash
git add prompts/v3/reel/director.txt
git commit -m "feat(reel): director prompt opens on strengths, no title/match intro

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Full suite + live manual verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full reel test suite**

Run: `docker compose run --rm nexus pytest tests/reel -q`
Expected: PASS — all reel tests green, including `tests/reel/test_no_gen2_eventlog.py`.

- [ ] **Step 2: Restart the vision worker (no hot-reload) so it picks up the code + prompt**

Run: `docker compose up -d --force-recreate nexus-vision-worker`
Expected: container recreated and healthy (`docker compose ps nexus-vision-worker`).

- [ ] **Step 3: Regenerate the live EMM reel and verify the cold open + name tag**

Trigger a regenerate for session `14a15eb8-9084-4925-8573-71a6f06745d2` via the recruiter report UI ("regenerate" on the Highlight video), or `POST /api/reports/session/14a15eb8-9084-4925-8573-71a6f06745d2/reel/regenerate`. Watch the worker:

Run: `docker compose logs -f nexus-vision-worker | grep -i reel`
Expected: `reel.actor.edl_validated` then a `ready` reel. Then play the reel and visually confirm:
- The reel opens directly on the first strength card (★ …) — NO title card, NO "Strong match" card.
- The first strength card shows the small `Punar · …` subtitle under the strength phrase; subsequent cards have no name tag.
- The outro is unchanged.

- [ ] **Step 4: Confirm chapters reflect the new shape**

Run: `.venv/bin/python /tmp/claude-1000/-home-ishant-Projects-ProjectX/5d87543c-91c2-4695-ab0f-ef21c8ee0370/scratchpad/extract_reel.py 2>&1 | sed -n '/CHAPTERS/,/EDL BEATS/p'`
Expected: the first chapter `kind` is `point` (no `title`/`match` chapters).

- [ ] **Step 5: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test(reel): verify cold-open reel end-to-end

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** prompt rewrite (Task 5), schema removal (Task 1), deterministic first-card subtitle (Tasks 2–4), chapters fall out (verified Task 6), no migration / no question-bank dependency (unchanged — nothing touches them). All spec change-set items map to a task.
- **Type consistency:** `format_identity_tag(candidate_name, role_title) -> str | None` (Task 2) is consumed verbatim in Task 4; `first_point_index(beats) -> int | None` and `render_reel(..., identity_tag=...)` (Task 3) are consumed in Task 4; `render_card(..., subtitle=...)` (Task 2) is consumed in Task 3. `BeatKind` is narrowed once in Task 1 and not re-referenced elsewhere.
- **No placeholders:** every code step shows the actual code; every run step shows the command + expected result.
