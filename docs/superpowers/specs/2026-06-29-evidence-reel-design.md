# Evidence Reel — reframe the Candidate Reel as verdict-evidence (all verdicts)

**Date:** 2026-06-29
**Status:** Approved (design)
**Module:** `backend/nexus/app/modules/reel/` + recruiter app + public recordings page
**Supersedes the framing in:** `prompts/v3/reel/director.txt` (the "persuasive pitch" model)

---

## 1. Problem & intent

Today the Candidate Reel is hard-wired as a one-directional **pitch**: the director
prompt opens with *"makes the case … for why THIS candidate fits THIS role"*, it is
eligible only for `advance`/`borderline` verdicts, and it is fed **only positive**
report material (`why_positive`, `strengths`). That framing is correct for an
**Approved** candidate but wrong for the product as a whole.

The reel should be reframed as the **video evidence behind the report's verdict** —
the same AI-directed highlight format, but its job is to *show why this verdict was
reached*, in the candidate's own words:

- **Advance** — why the candidate fits (today's behaviour, retained).
- **Borderline** — the case **both ways**: where they met the bar and where they fell
  short, so the recruiter can make the always-required human decision.
- **Reject** — the evidence behind the call: the highest-weight unmet signals, tripped
  red-flags, and thin/absent answers, grounded in the candidate's actual words.

The reel **still never scores, ranks, or changes the verdict** — it voices the verdict
the report already reached. It remains recruiter-facing only (never shown to
candidates) and recruiter-triggered (manual Generate, unchanged).

### Decisions locked in brainstorming
- **Eligibility:** all three verdicts (reject becomes eligible).
- **Borderline framing:** balanced — both sides, alternating met-bar and gap beats.
- **Narration voice:** Approved keeps the warm "pitch" voice; Borderline/Reject use a
  **neutral evidence-narrator** voice (Arjun stays the speaker; calm, factual,
  non-judgmental; reports evidence, never editorializes or sells).
- **Naming:** user-facing rename to **"Evidence Reel"** (verdict-neutral). Backend
  module stays `reel/`.
- **Prompt architecture:** Approach A — one verdict-aware prompt with shared rules +
  a per-verdict framing section. New version dir `prompts/v4/reel/director.txt`.

---

## 2. Architecture (what changes, what doesn't)

The reel pipeline is: **director (LLM → EDL) → validate_edl (pure guards) → render
(ffmpeg) → R2**, triggered by the `generate_session_reel` actor.

**Unchanged** (verdict-neutral, no edits needed):
- Clip resolution / word-index timing / budget trim (`director.validate_edl`,
  `timing.py`).
- Render pipeline (`render.py`, `clips.py`, `captions.py`, `tts.py`) — the EDL beat
  schema (`point`/`clip`/`outro`) already expresses everything the new framings need.
- Actor lifecycle, R2 upload, sync calibration (`actors.py` heavy-work path).

**Changes:**
1. **Eligibility** (`service.py`) — drop the verdict allowlist.
2. **Director inputs** (`director.py` `_build_document` + `generate_edl`; `actors.py`
   `_load_inputs` already loads everything) — feed the **full** report data, fix the
   broken signal field mapping.
3. **Prompt** — new `prompts/v4/reel/director.txt`, verdict-aware; bump
   `reel_director_prompt_version` → `v4`.
4. **Cards** (`cards.py`) — polarity-aware point glyph (`★`/`✓`/`△`), neutral tint for
   gaps.
5. **UI** — rename "Highlight Reel" → "Evidence Reel" in recruiter app + public page;
   verdict-aware subtitle; fix ineligible-reason copy.
6. **Tests** — eligibility table-tests, `_build_document` content assertions,
   opt-in prompt-quality fixtures.

---

## 3. Eligibility (`reel/service.py`)

Remove `_ELIGIBLE_VERDICTS` and the verdict branch. New rule:

```python
def eligibility_decision(*, report_status, verdict, recording_key):
    if report_status != "ready":
        return False, "Report is not ready yet."
    if not recording_key:
        return False, "Session recording is not ready yet."
    return True, None
```

`verdict` stays a parameter only if still needed elsewhere; otherwise drop it from the
signature and the `check_eligibility` SQL keeps selecting it for the director, not the
gate. (We still need `verdict` loaded for the director, so `check_eligibility` is
unaffected — only the decision logic loses the gate.) Table-tests updated: a `reject`
verdict with a ready report + recording is now **eligible**; report-not-ready and
recording-missing still block.

---

## 4. Director inputs — use the whole report (`director.py`, `actors.py`)

`actors._load_inputs` already SELECTs `verdict, verdict_reason, summary,
question_scorecards, signal_scorecards`. No new DB reads. The work is in
`generate_edl` / `_build_document` (and extracting two more fields in the actor).

### 4.1 Fields to add to the serialized `<report>` document

From `summary`:
- `decision.headline` — the one-line verdict headline (currently unused).
- `decision.why_positive.body` (already passed) **and** `decision.why_negative.body`
  (NEW).
- `quick_summary` (NEW) — the report's own neutral synopsis.
- `strengths[]` (already) — title + detail.
- `concerns[]` (NEW) — title + detail + **severity** (`deal_breaker|major|moderate`).
- `methodology.charity_flags[]` (NEW, advisory) — places the report already extended
  benefit-of-the-doubt; the reel must **not** harden a charity-flagged read into a
  firm claim in either direction.

### 4.2 Fix the signal block + enrich it

`signal_scorecards` are `SignalAssessmentOut` dumps. The current code reads
`s.get('final_state')` / `s.get('grade')` — **those keys do not exist** (the schema
uses `level` / `score`), so the signal block currently emits `state: None | grade:
None`. Fix the mapping and add the rich fields:

Per signal, emit: `signal_label` (fallback `signal`), `weight`, `knockout`, `priority`,
`provenance` (`asked_directly|cross_credited|probed_absent|not_reached`), `level`
(`strong|solid|thin|absent|not_reached`), `score`, `level_basis`, and the report's
`evidence[]` snippets. Keep the weight-desc sort. This is the spine of the
Reject/Borderline "which must-haves were/weren't met" story.

### 4.3 Enrich the per-question block

The question scorecards (`QuestionOut`) carry far more than is used. Add to each
question line: `level` (`strong|solid|thin|absent|not_reached`), `closure`
(`satisfied|tapped_out|absent|truncated`), `difficulty`, `red_flags_tripped[]`,
`listen_for_hits[]`, `score`. Keep `our_read` + `candidate_quote` (the clip-location
hint). These let the director locate **shortfall** moments (a `thin`/`absent` answer, a
tripped red-flag) exactly the way it locates strengths today.

### 4.4 `generate_edl` signature

Add `why_negative`, `concerns`, `quick_summary`, `decision_headline`,
`charity_flags` parameters (the actor extracts them from `summary` and passes them).
`verdict` is already passed; it becomes load-bearing (selects the framing branch in the
prompt — the prompt reads it from the document).

---

## 5. Prompt — `prompts/v4/reel/director.txt` (verdict-aware)

One prompt, bump `reel_director_prompt_version` to `v4`. Structure:

### Shared rules (apply to every verdict)
- **Identity:** the reel is verdict-evidence — it *voices* the report's verdict using
  the candidate's own words. It never scores/ranks/changes the verdict.
- **Clip selection** (carried over verbatim from v3): reference an answer `ref` +
  `[in_word, out_word]`, capture the substance not just the topic sentence, end on a
  complete thought at `//` pauses, never feature a moment twice, every clip carries a
  6–10 word `question_label`.
- **Anti-fabrication — mirrored for both sides (CRITICAL):** every beat — strength OR
  shortfall — must be **defensible against the report and the transcript**. Never
  invent a strength, never invent or overstate a weakness, never firm a hedge in either
  direction, never select a gratuitous "bad moment" that isn't material to the verdict.
  A shortfall beat must trace to a real `concern` / `why_negative` point / tripped
  red-flag / `thin`/`absent` signal or question. Respect `charity_flags` — do not
  harden a charitably-read moment.
- **Output:** ≥1 clip/experience beat or the reel can't build.

### Per-verdict framing section (selected by `verdict`)

- **`advance`** — retained pitch structure: `(★ strength → clip[+clip]) × ~3 → outro`.
  Warm, confident narration (the v3 voice). Outro = warm CTA to the full report.
- **`borderline`** — **balanced**: alternate met-bar and gap beats so the 50/50 is
  visible, e.g. `(✓ met → clip) (△ gap → clip) (✓ met → clip) (△ gap → clip) → outro`.
  Open on whichever side the report's `decision.headline` leads with. Neutral
  evidence-narrator voice. Outro = neutral "this one needs your call — full report
  inside," no verbal verdict stamp.
- **`reject`** — **evidence-for-shortfall**: lead on the highest-weight unmet signals
  (`level` ∈ `thin|absent`, high `weight`, `knockout`/high `priority` first), tripped
  red-flags, and `absent`/`thin` answers — each grounded in the candidate's own words
  via a clip. A brief `✓` acknowledgement beat is allowed where the report genuinely
  credits a strength (honest, not perfunctory). Neutral evidence-narrator voice. Outro
  = neutral pointer to the full report.

### Card glyph / polarity
`point.on_screen_text` leads with a polarity glyph: `★` (strength, advance),
`✓` (met-bar), `△` (gap/shortfall). The narration matches the glyph's polarity.

### Narration voice block
Two modes, selected by verdict:
- **Pitch (advance):** the existing warm-colleague voice ("Here's the part I liked…").
- **Neutral evidence-narrator (borderline/reject):** calm, factual, specific, never
  warm-selling and never mocking. e.g. *"On distributed systems, the detail stayed at
  a high level — here's the moment."* / *"Where they were strongest was incident
  response — listen."* Names the signal/gap concretely; reports, doesn't judge.

---

## 6. Cards / render (`cards.py`, `overlays.py`)

Minimal. The point-card renderer tints by glyph: `★`/`✓` in the existing accent;
`△` in a **neutral** (not alarmist red) tone — this is evidence, not an indictment.
No timing/clip/validation/EDL-schema changes. `render.py` untouched.

---

## 7. UI rename (recruiter app + public recordings page)

User-facing label **"Highlight Reel" → "Evidence Reel"** everywhere; verdict-aware
subtitle where a blurb renders.

Recruiter app (`frontend/app`):
- `components/dashboard/reports/ReelCard.tsx` — title/label; ineligible-reason copy no
  longer says "only for advancing/borderline" (it now reflects report/recording gates).
- `components/dashboard/reports/ImmersiveHeader.tsx` — the Generate button label.
- `components/dashboard/reports/theater/ReelTheater.tsx` — theater title/label.

Public page (`frontend/session`):
- `components/recordings/PublicRecordingsView.tsx` — reel/full-session toggle label.
- `components/recordings/theater/ReelTheater.tsx` — theater title/label.

Verdict-aware subtitle copy: advance → *"Why this candidate fits"*; borderline →
*"The case both ways"*; reject → *"The evidence behind this call."*

---

## 8. Tests

- **`reel/service.py` eligibility** (table-tests): `reject` + ready report + recording
  → eligible; report-not-ready → blocked; recording-missing → blocked.
- **`director._build_document`** (pure, unit): given a fixture report dict, asserts the
  serialized document contains `why_negative`, each `concern` + its `severity`,
  `quick_summary`, the **corrected** signal `level`/`score` (not null), `provenance`,
  and per-question `level`/`closure`/`red_flags_tripped`.
- **`director.validate_edl`** (pure): unchanged behaviour still holds with the new
  beat polarities (a `△` point is just a `point` beat).
- **Prompt-quality** (opt-in `-m prompt_quality`, real API, manual-validated as today):
  one borderline + one reject fixture; assert the EDL selects ≥1 shortfall beat that
  resolves to a real answer, and narration stays neutral (no "selling" verbs on
  reject). These are developer talk-test aids, not CI gates.

---

## 9. Ops / rollout notes

- **`reel_director_prompt_version` → `v4`** in `AIConfig` (env-driven; the
  `prompt_cache_key` already includes the version so the cache rolls cleanly).
- The reel actor runs in **`nexus-vision-worker`**, which has **no hot-reload** —
  restart it after the prompt/code change:
  `docker compose up -d --force-recreate nexus-vision-worker`.
- No DB migration (no schema change). No new env vars.
- No change to the public-recordings envelope shape — the reel is already presigned
  into `PublicRecordingsEnvelope`; only its content/label changes.

---

## 10. Out of scope (YAGNI)

- Auto-generating reels (stays recruiter-triggered).
- Showing reels to candidates (stays recruiter-only).
- Any change to verdict/scoring logic (the reel consumes, never computes).
- Per-verdict prompt files (Approach B, rejected) — one verdict-aware prompt.
- Render/timing/clip-engine changes beyond the card glyph tint.
