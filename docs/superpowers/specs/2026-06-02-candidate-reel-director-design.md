# Candidate Reel — Reel Director Design

- **Date:** 2026-06-02
- **Status:** Approved brainstorm 2026-06-02. **Revised to the fit-pitch model (v2)
  2026-06-02** after the first clips-only render proved the timing/selection seam but
  read as a Q&A recap rather than a persuasive "why this candidate fits the role" pitch.
  See §1a for the v2 narrative model (it supersedes the question-driven spine).
- **Refines:** `2026-06-01-candidate-reel-design.md` §7.1 (Director I/O + EDL validation) and
  `2026-06-02-candidate-reel-phase2-build-design.md` §2 (build step 2 = Director). This document
  locks the Director's **clip-reference contract**, **EDL schema**, **validation guardrails
  (incl. duration policy)**, and **prove flow**. It does not re-decide any locked product/architecture
  choice (narrative model §4, `session_reels` §6, render stages §7.3, API §7.4 all stand).
- **Test session:** `5e004a4d-e1a5-4165-9fd8-1e2e6a6438f6` (word-timed; report exists in DB;
  transcript fixture at `backend/nexus/tests/fixtures/candidate_reel/session_5e004a4d_transcript.json`).

---

## 1. The clean seam (why the Director is simple)

The clips-only core already proved the timing model end-to-end (`timing.py` + `spike.py`,
operator-confirmed). The renderer maps a candidate sub-clip to video via:

```
ans_start_video = timing.answer_span(events, speaking, commit)[0] + wall_anchor − pipeline_lag
clip_video      = [ans_start_video + in_ms_rel, ans_start_video + out_ms_rel]
```

where `in_ms_rel`/`out_ms_rel` are **turn-relative word ms** (first word = 0, the coordinate of
`words[]`). The spike's `TRIMS = [(commit, trim_start_rel, trim_end_rel, label), …]` is exactly this.

**Consequence:** the Director is a **pure transcript-space selector**. It never sees video ms, VAD
spans, the wall anchor, or pipeline lag — those live entirely in `timing.py`/renderer. The Director
emits `(source_turn_ref = commit, in_word, out_word)`; the existing renderer turns that into video.
The three brainstorm tensions (EDL↔VAD reconciliation, full-narrative-vs-core, word-snap↔timing)
all collapse into this one seam.

```
report (session_reports, DB) ─┐
                              ├─► director.generate_edl()  ─► ReelEdlOut  (raw LLM, word indices)
word-timed transcript (words)─┘            │
                                           ▼
                              director.validate_edl()   (pure, TDD'd)
                                           │  resolve in_word/out_word → in_ms/out_ms
                                           │  reject hallucinations · fit ≤60s · ≥1 clip
                                           ▼
                                     ValidatedEdl  ─► clip/experience beats = (commit, in_ms, out_ms, caption)
                                           ▼
                                  spike renderer (existing) ─► MP4   ← operator watches
```

## 1a. Narrative model — the fit-pitch (v2, supersedes the Q&A spine)

The reel is a **persuasive argument that this candidate fits THIS role**, narrated by the
AI (Arjun), stitching evidence from across the whole session — not a per-question recap.
The report already did the analysis (a thesis in `verdict_reason`, a `why_positive`
paragraph, named `strengths`, and JD `signal_scorecards`); the Director's job is to **voice
that fit-case as a video**, never invent the "why."

**Spine:**
```
title
→ §1  match    — ONE consolidated, narrated "why this is a great match", grounded in the
                  role's must-have signals. The first thing the recruiter hears → a full
                  picture of fit. Narration-led; MAY include one short establishing clip.
→ §2  (point → clip[+clip]) × N   — the report's DIFFERENTIATING strengths, each: a claim
                  card + Arjun narration on WHY it's strong, then 1–2 evidence clips pulled
                  from ANYWHERE in the session (sub-parts of different answers are fine).
→ outro        — the REAL verdict + "▶ Watch full interview" CTA.
```

**Beat kinds (v2):** `title · match · experience · point · clip · outro` (the v1 `ask`/`credit`
are renamed to `match`/`point`). Render types are unchanged: **cards** = `title`/`match`/
`point`/`outro` (Arjun narration over a Pillow card); **clips** = `experience`/`clip`
(candidate's real audio + burned captions). The locked audio architecture (D5) is untouched.

**§1 vs §2 split, dedup, and coverage are the LLM's job, not hard rules** (the split is a
semantic judgment; per the no-pattern-match rule the LLM does it better than a topic
classifier). The prompt instructs: §1 synthesizes the baseline must-haves into one match
beat; §2 features the *differentiators*; **do not repeat a point across §1/§2; do not skip a
strong strength.** Deterministic validation stays **structural only** (§5) — plus one cheap
structural backstop: a clip that **duplicates an already-used span** (same turn + overlapping
`[in_ms,out_ms]`) is dropped, so the LLM's semantic dedup has a guard without rigid rules.

**Honesty = honest-positive (a product invariant, not a toggle).** Feature genuine strengths
persuasively; never fabricate or overstate. The **outro carries the real verdict** (a
borderline candidate stays borderline). The reel aids the *required* human review — it never
spins. The Borderline-human-review invariant is intact.

## 2. Data contract (from the fixture)

A candidate transcript turn:

| Field | Meaning | Director use |
|---|---|---|
| `timestamp_ms` | `turn.captured` **commit** (e.g. `203401`) | the `source_turn_ref` |
| `start_ms`/`end_ms` | commit-lagged | **NOT used** for video; ignored by the Director |
| `words[]` | `{text, start_ms, end_ms, confidence}`, **turn-relative** (first word `start_ms=0`), accurate | the index space the Director selects from |
| `question_id` | links to report `questions[].question_id` | maps a turn to its report scorecard |

Report ground truth (`ReportRead`): `verdict`, `summary`-level `strengths`, per-question
`questions[]` (`question_id`, `title`, `question_text`, `status_badge`, `our_read`,
`candidate_quote`, `asked_at_ms`), `signal_assessments[]`. `candidate_quote` is **cleaned /
non-verbatim** — used only as a *hint* for which slice of the answer to clip, never matched literally
(no regex/fuzzy matching — consistent with the no-pattern-match rule). The Director locates the real
span by **word index** in `words[]`.

## 3. Clip-reference contract — word indices (locked)

The LLM emits `source_turn_ref` (commit) + `[in_word, out_word]` **integer indices** into that turn's
`words[]`. Validation derives `in_ms = words[in_word].start_ms`, `out_ms = words[out_word].end_ms`.

Rationale (chosen over raw-ms-+-snap from §7.1): snapping is **free by construction** (indices *are*
word boundaries); hallucination is an **out-of-bounds index** (hard reject); the LLM does no ms
arithmetic; no fuzzy matching. Same `[in_ms, out_ms]` reaches the renderer either way — this only
changes what the LLM emits and how validation derives ms.

## 4. EDL schema (full narrative now)

The Director emits the **complete** narrative EDL (all beat kinds) from day one — the real contract,
no schema rework when cards/TTS land (build steps 3–5). Only `clip`/`experience` beats carry timing
and are validated/renderable today; to prove on `5e004a4d` we filter to those and feed the spike.

```python
class ReelBeat(BaseModel):
    kind: Literal["title", "match", "experience", "point", "clip", "outro"]
    source_turn_ref: int | None = None   # commit (timestamp_ms); REQUIRED for clip/experience
    in_word: int | None = None           # index into turn.words[]; clip/experience only
    out_word: int | None = None
    on_screen_text: str | None = None    # card copy (title/match/point/outro)
    caption: str | None = None           # optional hint; renderer uses words[] as caption truth
    narration_text: str | None = None    # Arjun TTS script for card beats

class ReelEdlOut(BaseModel):
    beats: list[ReelBeat]
```

Canonical sequence (v2 §1a): `title → match → [experience] → (point → clip[+clip])×N → outro`.
`match`/`point` are lead cards; `experience`/`clip` are timed (`TIMED_KINDS`).

**Validated output** (`ValidatedEdl`): the surviving beats with `in_ms`/`out_ms` resolved on timed
beats, a per-beat `duration_ms` (measured for clips, estimated for cards), total `duration_ms`, and
drop/trim bookkeeping for the audit trail.

## 5. Validation guardrails (pure functions — the table-tests)

All operate on `(ReelEdlOut, transcript) → ValidatedEdl`; no ffmpeg/LLM; run in the lean `nexus` image.

1. **Turn-ref check.** `source_turn_ref` must equal some candidate turn's `timestamp_ms`. Else drop
   the beat (a card beat without a ref is fine; a clip/experience beat without a valid ref is dropped).
2. **Word-index bounds.** Require `0 ≤ in_word ≤ out_word < len(words)`. Out-of-bounds = hallucination
   → drop the beat. Derive `in_ms = words[in_word].start_ms`, `out_ms = words[out_word].end_ms`.
3. **Duplicate-span guard (structural dedup backstop).** Resolving timed beats in order, drop a
   clip whose `(source_turn_ref, [in_ms,out_ms])` overlaps an already-kept clip's span (keep the
   first). The LLM owns semantic dedup; this catches identical/overlapping evidence only.
4. **Duration budget ≤ 60s, target ~45s.**
   - Clip/experience `duration_ms = out_ms − in_ms` (measured).
   - Card beats get an **estimated** duration (the real render recomputes): `max(floor,
     narration_words / SPEAK_WPS)` with `SPEAK_WPS ≈ 2.75` (~165 wpm, Arjun); floors title 3s /
     match 4s / point 3.5s / outro 4s.
   - **Grouping:** a drop-unit is a **lead card (`match`/`point`) + its following clips** (a point
     and its 1–2 evidence clips drop together); a clip before any lead card (e.g. a §1 `experience`)
     forms its own group.
   - **Fit order (deterministic):** (a) trim any clip over `CLIP_SOFT_CAP_MS` (~12000) inward by
     lowering `out_word`; (b) if still over 60s, drop whole **trailing groups** (lowest priority =
     last → trailing §2 points drop before the §1 `match`, which is first). **Always keep `title`,
     `outro`, and ≥1 clip-bearing group.**
5. **≥1 clip beat survives, or fail honestly.** Zero surviving clip/experience beats → raise/return a
   failure (the actor sets `status=failed`, no MP4).

Constants (`CLIP_SOFT_CAP_MS`, `MAX_TOTAL_MS=60000`, `TARGET_MS≈45000`, `SPEAK_WPS`, `LEAD_CARDS=
{match,point}`) live as module constants in `director.py`.

## 6. LLM call, prompt, config

- Mirror `app/modules/reporting/scoring/judge.py`: `get_raw_openai_client().responses.parse(
  text_format=ReelEdlOut)`, effort-gated `reasoning={"effort": …}`, `prompt_cache_key`,
  `set_llm_span_attributes`, structlog. **No PII in logs** — never log transcript text, quotes,
  `narration_text`, or `on_screen_text`; log `session_id`, beat counts, cache usage only.
- **AIConfig** (`app/ai/config.py`) gains, mirroring report_scorer (env-driven, never hardcoded):
  `reel_director_model`, `reel_director_effort`, `reel_director_prompt_version`,
  `reel_director_prompt_cache_key_prefix`, with matching `Settings` fields + `.env.example` entries.
- **Prompt** `prompts/v3/reel/director.txt`, read via `PromptLoader(version=…).get("reel/director")`.
  **Context-before-document** (house rule): (1) report context — role title, `verdict`,
  `verdict_reason`, `why_positive`, `strengths`, `signal_scorecards` (the JD must-haves with
  weight + state), per-question scorecards; THEN (2) the document — candidate turns serialized as
  **indexed words** `{turn_ref, question_id, words:[idx:text]}`. The prompt directs the **fit-pitch**
  (§1a): build §1 as ONE consolidated `match` beat grounded in the must-have signals; build §2 from
  the *differentiating* strengths, each a `point` + 1–2 evidence clips referenced by
  `source_turn_ref` + `[in_word,out_word]`; **don't repeat across §1/§2, don't skip a strong
  strength**; write card copy + Arjun narration that says *why each moment shows fit*; honest-positive
  (outro carries the real verdict); keep it ~45s.

## 7. Prove flow (build step 2 acceptance)

- `python -m app.modules.reel.director <session_id>` (dev main): load report from DB (bypass-RLS +
  explicit `tenant_id`, like the report actor) + transcript from the fixture → `generate_edl` →
  `validate_edl` → print the EDL + write `tmp/edl_<session>.json`.
- Extend `spike.py` to optionally load its `TRIMS` from `tmp/edl_<session>.json` (clip/experience
  beats → `(commit, in_ms, out_ms, label)`) instead of the hardcoded list → render → **operator
  watches the MP4**. The success gate is the operator's (manual-agent-testing preference); the
  emitted EDL is inspected first.

## 8. Testing

- **TDD (pure functions):** `tests/reel/test_director.py` table-tests every guardrail — valid
  selection resolves to correct ms; hallucinated `source_turn_ref` dropped; out-of-bounds
  `in_word`/`out_word` dropped; `in_word > out_word` dropped; >60s trimmed then group-dropped in the
  defined order while preserving title/outro/≥1 clip; card-duration estimate; zero-clip → failure.
  Runs in the lean `nexus` image (no ffmpeg, no live LLM).
- **LLM quality = manual** (operator runs the Director on `5e004a4d`, inspects the EDL, watches the
  rendered MP4). No automated eval (per the manual-agent-testing preference); a `@prompt_quality`
  opt-in eval may be added later, consistent with the engine's prompt-eval pattern.

## 9. Security / compliance

- No new external service. Director is an `app/ai` call (OpenAI, already in the data path).
- No raw PII in logs (transcript/quotes/narration/on-screen text never logged).
- The reel remains a **viewing aid, never a decision** — the Director selects highlights; it does not
  score, rank, or alter any verdict (Borderline-human-review invariant untouched).
