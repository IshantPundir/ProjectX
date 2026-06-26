# Candidate Reel — drop the intro, open on the first strength

**Date:** 2026-06-26
**Status:** Approved (brainstorming → ready for implementation plan)
**Module:** `backend/nexus/app/modules/reel/`
**Branch:** `feat/reel-drop-intro`

## Problem

The Candidate Reel is a ~45–60s AI-directed highlight video that lets an agency
pitch a top candidate to a client in seconds. Stakeholder feedback: the first
~10–11 seconds — the **title card** and the **match card** — are irrelevant. A
recruiter's attention window is a few seconds; the reel must get to the
candidate's actual strengths immediately.

Concretely, today's structure is:

```
title  (0–3s)    name · role · hook        "Let me show you why X stood out."
match  (3–11.5s) "Strong match: …"         "X fits this role well. …"
point  (11.5s+)  ★ first strength          → candidate clip
…
outro
```

The title + match beats are the ~11s of "intro" to remove.

## Decision

Change the reel shape from:

```
title → match → (point → clip[+clip]) × N → outro
```

to:

```
(point → clip[+clip]) × N → outro
```

The reel opens **cold on the first strength card**, which carries a small
name+role tag, then immediately cuts to the candidate's own words.

This is a **permanent product change for all reels going forward** — a
prompt + schema change, not a per-tenant toggle.

### On-screen identity

Dropping both cards removes all on-screen candidate identity (the reel travels
standalone — it is the downloadable MP4 and the public `/recordings/<token>`
share, not only viewed inside the report page chrome). To preserve identity
without spending intro seconds:

- A small **subtitle is rendered on the FIRST strength card only**: `FirstName ·
  Role Title` (e.g. `Punar · EMM Engineer`). Subsequent cards carry no name.
- The subtitle is **deterministically rendered by the card renderer** from the
  already-loaded `candidate_name` + `role_title` — it is NOT authored by the
  director LLM. Identity is a known fact; injecting it removes any chance of
  hallucination or drift.

## Approach — schema-enforced removal

Chosen over a prompt-only approach: removing `title`/`match` from the EDL schema
makes it **structurally impossible** for the director LLM to emit them (the
Responses API structured output is constrained to the `BeatKind` Literal), and
leaves no dead beat-kinds or dead rendering branches behind. This matches the
enterprise / no-hacks bar — it is the clean "this is the product now" change,
not a runtime filter over an unchanged schema.

## Change set

All paths under `backend/nexus/`.

### 1. `prompts/v3/reel/director.txt`
Rewrite the structure section:
- Remove the `title` beat and the `§1 MATCH` beat (and the optional
  match-attached `experience` clip guidance).
- The reel now opens directly with the strengths: `(point → clip[+clip]) × N →
  outro`. Emit ~3 differentiating strengths (the heart of the reel) then the
  outro.
- Keep `<selecting_clips>`, `<narration>`, and `<rules>` essentially as-is. The
  "≥1 clip/experience beat" rule still holds.
- The consolidated "why this matches the role" synthesis that the match card
  carried is **dropped** per the stakeholder call — the reel leads with specific
  strengths, and the full report (shipped alongside) carries the fit case.

> Operational note: `prompts/` is read fresh per-session by `PromptLoader`, but
> the reel renderer/director runs in the long-lived `nexus-vision-worker`, which
> has **no hot-reload**. Restart it after editing.

### 2. `reel/director.py`
- Remove `"title"` and `"match"` from the `BeatKind` Literal (line ~52).
- Remove `"title"` and `"match"` from `_CARD_FLOOR_MS` (line ~43).
- `LEAD_CARDS` becomes `{"point"}` (drop `"match"`) (line ~51).
- Trim logic (`_trim_to_budget`, ~line 200): already pins only
  `beats[0].kind == "title"` (leading) and `beats[-1].kind == "outro"`
  (trailing). With no title, `title = []`, so the entire body is
  strength-budgeted — slightly more candidate voice. No structural change needed
  beyond the Literal/floor/lead-card edits; verify the grouping still holds when
  the first body beat is a `point`.
- `experience` stays a valid clip kind (it is in `TIMED_KINDS`); it is simply no
  longer described as "after match" in the prompt.

### 3. `reel/cards.py`
- Add an optional `subtitle: str | None = None` param to `render_card`.
- In the `point` branch, when `subtitle` is set, draw it small and soft
  (`_INK_SOFT`) below the strength phrase.
- Delete the `title` and `match` render branches (dead once the schema cannot
  produce them). Keep the generic fallback and `outro`.

### 4. `reel/actors.py` (and `render.py` if it owns card rendering)
- Thread `candidate_name` + `role_title` (already loaded in `_load_inputs`) into
  the render step.
- Apply the subtitle (`{first_name} · {role_title}`) to the **first** rendered
  `point` card only; all other cards render with no subtitle.
- `first_name = candidate_name.split()[0]` when present; degrade gracefully when
  `candidate_name` or `role_title` is missing (omit the missing part; omit the
  separator/subtitle entirely if both are absent).

### 5. Chapters
The chapter list is derived from the EDL beats. With no title/match, the first
chapter becomes the first strength automatically — no dedicated code change.

## Backward compatibility

- Generation is atomic per actor run; stored historical `edl` JSONB is never
  re-validated against `BeatKind`, so removing `title`/`match` from the Literal
  does not break existing reels. `ReelChapter.kind` is typed `str` (not the
  Literal) on the wire, so old reels' `title`/`match` chapters still render in
  the player.
- No DB migration. No schema/table change. `session_reels` is untouched.
- **No question-bank dependency.** Reel generation reads only `session_reports`,
  `sessions.session_evidence_json`, `candidates`, and `job_postings`. Reels can
  be (re)generated for sessions whose original question bank no longer exists.

## Testing

- `reel` director/validator tests: assert the new shape — no `title`/`match`
  beats survive; the first beat is a `point`; `≥1` clip beat present; budget trim
  still respects the outro pin.
- `cards.py` tests: `render_card(kind="point", subtitle=…)` draws the subtitle;
  `subtitle=None` renders unchanged; removed `title`/`match` branches no longer
  referenced.
- Actor/render test: subtitle is applied to the first point card only, derived
  from `candidate_name`+`role_title`, with graceful degradation when either is
  missing.
- Manual: regenerate the live reel
  (`POST /api/reports/session/{id}/reel/regenerate`) for the EMM session and
  visually confirm the cold open + first-card name tag.

## Out of scope

- Persistent lower-third identity (considered, rejected in favor of the
  first-card tag).
- Any change to outro, clip selection/timing, TTS, captions, eligibility, or the
  report/recording pipeline.
- Per-tenant configurability of the reel shape.
