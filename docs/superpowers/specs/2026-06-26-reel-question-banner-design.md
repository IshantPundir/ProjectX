# Candidate Reel — question banner on candidate clips

**Date:** 2026-06-26
**Status:** Approved (brainstorming → ready for implementation plan)
**Module:** `backend/nexus/app/modules/reel/`
**Branch:** `feat/reel-drop-intro` (continues the reel-UX line of work)
**Follows:** `2026-06-26-reel-drop-intro-design.md`

## Problem

The Candidate Reel shows a strength card (`★ …`) then a clip of the candidate
answering. A viewer sees the answer but not **what was asked** — they lack the
context that makes the answer land. Stakeholders want the question on-screen so
the clip is self-explanatory: an agency can drop the reel in front of a client
and the client immediately understands "this is the candidate answering THIS".

## Decision

Burn a short **question banner** at the top-center of each candidate clip,
showing the question that clip answers. The banner:

- Uses a **short LLM-authored paraphrase** of the question (~6–10 words), NOT the
  verbatim spoken question. Rationale: the report's stored `question_text` is
  often empty (it depends on bank text that may have changed), and the agent's
  actual spoken questions are long and split across a lead + several probes —
  too long for an overlay and messy to map to a specific clip. The director LLM
  already reads the full transcript (agent questions included) while selecting
  the clip, so it is perfectly placed to emit a crisp question label.
- Appears **only when the question changes** from the previously-shown clip. Two
  consecutive clips answering the same question → the banner shows on the first
  and the second clip stays clean (less repetition). Suppression is
  **deterministic on `question_id`**, not on the label string (the LLM only
  writes the creative paraphrase; code decides show/suppress, so phrasing drift
  cannot break dedup).
- **Persists for the whole clip** as steady context.

### Styling

- Top-center, over a subtle dark gradient **scrim** across the top ~160px so
  white text reads over bright footage.
- A small violet `Q` prefix + the question in white (DejaVu, ~34px), wrapped to
  ≤2 lines, centered. Brand palette consistent with the cards
  (`_INK` white, `_ACCENT` violet `#6C5CD0`).

## Architecture / data flow

1. **Director (LLM)** emits a new optional `question_label` per `clip`/`experience`
   beat — the question that clip answers, paraphrased to ~6–10 words, phrased as a
   question (never the answer).
2. **`validate_edl`** resolves each clip to its answer-run (it already does this
   for word timing). It threads the run's `question_id` AND the LLM
   `question_label` onto the resolved `ValidatedBeat`.
3. **Render** walks the beats in order. For each clip beat it applies a pure
   decision: show the banner iff this clip's `question_id` differs from the
   previously-shown clip's `question_id` (a `None` question_id falls back to
   label-string comparison). When showing, it renders a transparent banner PNG
   and composites it onto the cut clip.
4. The banner is a **transparent full-frame PNG** (Pillow, brand-styled) overlaid
   via an ffmpeg `overlay` filter inside `cut_clip` — reusing Pillow (not ffmpeg
   `drawtext`) gives clean word-wrapping and sidesteps special-character escaping
   (`:`, `?`, `—`, quotes). Clip A/V params (codec, fps, scale) are unchanged.

## Components (files)

All under `backend/nexus/`.

| File | Change |
|---|---|
| `prompts/v3/reel/director.txt` | Add a `<question_label>`-style instruction: each clip beat carries a crisp ~6–10 word `question_label` — the question the clip answers, paraphrased, phrased AS a question, never restating the answer. Keep it grounded in what the agent actually asked. |
| `reel/director.py` | Add `question_label: str \| None = None` to `ReelBeat`. In `_resolve_clip`, thread the run's `question_id` and the beat's `question_label` onto the returned `ValidatedBeat` (add both fields to the `ValidatedBeat` dataclass). `edl_to_dict` carries them so they persist in the `edl` JSONB. |
| `reel/overlays.py` *(new)* | `render_question_banner(*, text, out_path, width, height) -> str` — a transparent PNG with the top scrim + `Q` prefix + wrapped white question text. Reuses `cards.wrap_to_width`. Pillow imported lazily (lean-image safe). |
| `reel/clips.py` | `cut_clip` gains optional `overlay_png: str \| None = None`; when set, the ffmpeg command adds the PNG as a second input and an `overlay` filter. Output codec/params unchanged. |
| `reel/render.py` | A pure `banner_plan(beats) -> dict[int, str]` (or inline decision) mapping each clip beat index to the banner text to show (empty/absent = suppressed), based on `question_id` change. In `render_reel`, render the banner PNG for shown clips and pass `overlay_png` to `cut_clip`. |
| tests | director: `question_label` parsed + `question_id`/`question_label` threaded onto the validated clip. `overlays`/`render`: the pure show/suppress decision (first clip shows; same-question next clip suppressed; different-question shows; `None` qid falls back to label compare). `clips`: `cut_clip` cmd includes the overlay input+filter when `overlay_png` is given, and is unchanged when it is not. Banner PNG drawing is verified by a rendered frame (manual, like the cold-open subtitle). |

## Error handling / edge cases

- **No `question_label`** (LLM omitted it) → no banner for that clip (graceful;
  the clip still renders).
- **`question_id` is `None`** on a run → cannot dedup by id; fall back to
  suppressing only when the label string exactly matches the previous shown
  clip's. (Worst case: a banner repeats — acceptable, never a crash.)
- **Overlay render fails** → the banner is best-effort: a Pillow/ffmpeg failure
  on the overlay must NOT fail the reel. If the banner PNG can't be produced, cut
  the clip without it (log + continue).
- Banner text is wrapped + truncated to ≤2 lines so a long label can't overflow.

## Backward compatibility

- No DB migration. `question_label`/`question_id` live inside the existing `edl`
  JSONB. Old reels simply have no banner data; nothing re-validates stored EDLs.
- No question-bank dependency — the label comes from the transcript via the LLM.
- `cut_clip`'s new param is optional and defaulted, so existing callers/tests are
  unaffected unless they opt in.

## Testing

- Director validator: a clip beat's `question_label` survives validation and the
  resolved `ValidatedBeat` carries both `question_id` (from the run) and
  `question_label`.
- Pure banner decision: ordered clip beats → correct show/suppress per the
  question-change rule, including the `None`-qid label fallback.
- `cut_clip` arg builder: overlay input + `overlay` filter present iff
  `overlay_png` is passed; absent otherwise; output params identical.
- Full reel suite stays green.
- Manual: regenerate the EMM reel; pull a frame from a banner-bearing clip and
  confirm the top-center `Q: …` reads cleanly over the footage; confirm a
  same-question follow-up clip has no banner.

## Out of scope

- Per-word/caption burning on clips (previously removed; not reintroduced).
- Showing the question on the strength cards (cards keep the `★ strength`).
- Any change to clip selection/timing, the cold-open/subtitle work, eligibility,
  TTS, or the reporting pipeline.
