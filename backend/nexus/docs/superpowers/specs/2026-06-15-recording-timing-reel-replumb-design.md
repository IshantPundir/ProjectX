# Recording Timing, Word-Timed Transcript & Reel Re-plumb — design

**Date:** 2026-06-15
**Status:** Design approved (root-cause investigation complete; 4 RCs evidence-backed) — pending plan + implementation
**Modules:** `interview_engine/`, `interview_runtime/`, `reporting/`, `reel/` (backend) + `frontend/app` (ReviewTheater)

---

## 1. Summary

The recruiter report's video player and the Candidate Reel were built on the **gen-2 engine's
clock + event-log envelope**. The gen-3 engine ("Path A+") emits a different durable contract
(`SessionEvidence`) and no longer writes the event-log envelope, so four things broke. This spec
fixes all four end-to-end and removes the dead gen-2 timing code:

- **RC-1** Report capsules never seek/highlight — `asked_at_ms` is hardcoded `None`.
- **RC-2** "This moment" panel shows no agent verdict — `QuestionOut` lacks closure/grade.
- **RC-3** Reel crashes — it reads the gen-2 event-log envelope that gen-3 doesn't emit.
- **RC-4** Candidate transcript/note spans are on a raw `time.monotonic()` clock (~1.231e9 ms),
  not session-relative; and candidate `words[]` is never populated.

Decisions (locked with the user): do **all four** in one pass; the reel uses **STT word timings**
(emit them from the engine, word-index clips); the verdict panel **surfaces existing
closure + grade** (no new LLM call).

---

## 2. Root causes (evidence)

| RC | Symptom | Root cause (file:line) | Evidence |
|---|---|---|---|
| RC-1 | Capsules don't seek/highlight | `reporting/service.py:291` hardcodes `asked_at_ms=None`. The helper `interview_runtime/transcript_timing.question_asked_at_ms` reads gen-2 fields `entry["role"]`/`entry["timestamp_ms"]` (gen-3 uses `speaker`+`span`) and is unused. | All 6 questions in the persisted report have `asked_at_ms=None`. Frontend (`ReviewTheater`/`timeline-model`/`useTheaterState`) correctly reads `asked_at_ms`; with null it has no marker positions. Recording is clock-aligned with the session (3.5 ms apart), so populated `asked_at_ms` seeks correctly. |
| RC-2 | No agent verdict in "This moment" | `reporting/schemas.py QuestionOut` has no per-question closure/verdict field; `ThisMomentPanel.tsx` renders only Q+A+`our_read`. | Engine records `SessionEvidence.questions[].closure` (satisfied/tapped_out) and the report computes per-question `level`. Not surfaced. |
| RC-3 | Reel errors, no row persisted | `reel/timing.py:27/32/46/68` reads gen-2 events `engine.v2.dispatched`/`audio.user.state`/`turn.captured`; `reel/actors.py:_resolve_events` returns `[]`; `engine_t0_wall()` `next(...)` raises `StopIteration` in `render.prepare_anchor`. | No `engine-events/<id>.json` for gen-3 sessions; gen-3 has no event-log emitter (confirmed: zero emitters of those `kind`s). |
| RC-4 | Candidate spans on wrong clock; no word timing | `turn_assembler.py:129` spans use `int(self._first_at*1000)` where clock=`time.monotonic` (`agent.py:496`); agent lines use session-relative `(now-started_at)` (`driver.py:335`). Candidate `words[]` never populated (`driver.py:522`); `on_user_turn_completed` (`agent.py:164`) only reads `text_content`. | Candidate spans ≈1.231e9 ms; agent spans 0..648420 ms (= 648 s duration). |

The recording clock is trustworthy: `sessions.recording_started_at` (17:45:17.436) vs session
`started_at` (17:45:17.433) ⇒ **~3.5 ms offset**, `recording_duration_seconds == session duration`.
Once all transcript spans are session-relative, `video_ms ≈ session_ms + (started_at −
recording_started_at)` (tens of ms) — no pipeline-lag cross-correlation needed.

---

## 3. Design

### 3.1 Engine — session-relative clock (RC-4)
Anchor the assembler clock to session start so candidate spans match agent lines.
- In `agent.py`, capture `t0_monotonic = time.monotonic()` at the same instant `started_at =
  datetime.now(UTC)` is taken (~line 237), and pass `clock=lambda: time.monotonic() - t0_monotonic`
  to `TurnAssembler` (replacing the raw `time.monotonic` at line 496). Result: `_first_at`/`_last_at`
  are seconds-since-session-start ⇒ `span` ms are session-relative, consistent with agent lines
  (both "ms since session start", within a few ms).
- No change to `driver.py` agent-line stamping (already session-relative). Note spans inherit the
  candidate span (`notes.py:112`) and are therefore fixed transitively.
- Defensive: keep `max(0, …)` clamps.

### 3.2 Engine — word-timed transcript (reel decision)
Emit per-word timing on candidate `TranscriptTurn.words[]` (currently always `[]`).
- **Capture (implementation-time research item):** word timings are on the LiveKit STT
  `SpeechEvent` final alternatives, NOT on `on_user_turn_completed`'s `new_message` (text only).
  The engine must tap the STT stream. Confirm the exact mechanism via the **livekit-docs MCP**
  (`docs_search`/`code_search`): likely an `stt_node` override on `_EngineAgent` (or a
  `RecognitionUsage`/transcript event listener) that accumulates `(text, start_s, end_s, conf)`
  word tuples for the in-progress turn and hands them to `on_user_turn_completed`.
- **Thread through assembly:** `AssembledTurn` gains `words: list[WordTiming]`. `TurnAssembler`
  buffers per-fragment word lists alongside text and concatenates them on flush. Per-fragment words
  are already turn-relative (first word = 0 via `transcript_timing.relative_words`); when merging
  fragment N>0, offset its word times by `(fragment_start - first_fragment_start)` so the merged
  `words[]` are relative to the assembled turn's first word. Keep `words` aligned with `span`.
- **Driver:** populate `TranscriptTurn(words=turn.words)` (replaces `words or []` at `driver.py:522`).
- `WordTiming` (interview_runtime/models) is unchanged: `{text, start_ms, end_ms, confidence}`,
  ms-from-turn-first-word. This is the durable-contract addition (additive; default `[]` preserved
  for non-STT turns).

### 3.3 Backend reporting — asked_at_ms (RC-1) + verdict (RC-2)
- **RC-1:** In `reporting/service.py`, derive `asked_at_ms` per question from the gen-3 evidence
  transcript: the earliest **agent** `TranscriptTurn` whose `question_id == qr.question_id`, using
  `span.start_ms` (session-relative). Replace the hardcoded `asked_at_ms=None` (line 291). Add a
  small pure helper (testable) + use `evidence_adapter`'s transcript. **Delete** the dead
  gen-2 `question_asked_at_ms` (and `scoring/transcript.py` gen-2 remnants if unused) — zero stale code.
- **RC-2:** Add to `QuestionOut`: `closure: str | None` (satisfied/tapped_out/truncated/none) and
  reuse existing `level`. Populate `closure` from `qr.closure`. Frontend renders closure + level +
  `our_read` as "the agent's read" in `ThisMomentPanel`. No new LLM call.

### 3.4 Reel — re-plumb onto gen-3 (RC-3)
Remove the gen-2 event-log dependency entirely; drive timing from `SessionEvidence` + `sessions`.
- **Source of truth:** `SessionEvidence.transcript` (word-timed, session-relative after 3.1/3.2)
  + `sessions.recording_started_at` / `started_at` / `recording_duration_seconds` /
  `recording_s3_key`.
- **`reel/timing.py`:** delete `engine_t0_wall`, `ENGINE_DISPATCH_KIND`, `speaking_intervals`,
  `answer_span`, `measure_pipeline_lag` (all gen-2/event-log). Keep/replace with:
  `recording_offset_ms = int((started_at - recording_started_at).total_ms())` (≈0), and a pure
  `turn/word → video_ms` mapper: `video_ms = session_ms + recording_offset_ms`. Candidate clip spans
  come from `transcript[].span` (turn) refined by `words[]` (word-indexed boundaries for the
  director's chosen quote). `recording_speech_intervals` (ffmpeg) is no longer needed for
  calibration — remove unless used elsewhere.
- **`reel/actors.py`:** stop loading the event-log envelope (`_resolve_events`, `raw_result_json`
  `audit_envelope_ref`); load `SessionEvidence` instead (same source the report uses). Pass
  word-timed transcript + recording offset to the director/render.
- **`reel/director.py`:** the word-indexed transcript now comes from `SessionEvidence.transcript[].words`
  (was the gen-2 envelope). `validate_edl` word-index→ms resolution maps against the merged `words[]`.
- **`reel/render.py`:** `prepare_anchor` / `_clip_to_video` use the new mapper (no `events`).
- Eligibility unchanged (report ready + advance/borderline verdict + recording exists).

### 3.5 Frontend — render the verdict (RC-2) + verify capsules (RC-1)
- `lib/api/reports.ts QuestionOut`: add `closure?: string | null`.
- `ThisMomentPanel.tsx`: when a question is selected, render closure + level (+ existing `our_read`)
  as the agent's read.
- No other frontend change needed for capsules — once `asked_at_ms` is non-null the existing
  seek/highlight logic works. Verify with the served app (`curl`/playback) per repo norms.

---

## 4. Durable-contract changes (additive only)
- `SessionEvidence.transcript[].words[]` — now populated for candidate turns (was always `[]`).
- `SessionEvidence.transcript[].span` — candidate spans now session-relative (was monotonic). No
  schema change; a data-correctness fix. **Existing persisted sessions keep their old (monotonic)
  candidate spans** — only re-run/future sessions are clean. The report's `asked_at_ms` uses AGENT
  spans (already sane) so RC-1 fixes existing sessions on re-score.
- `QuestionOut.closure` (report schema) + `closure?` (frontend type) — additive.
- No DB migration (all JSONB / response-shape additions).

---

## 5. Invariants preserved
- **Collector-not-judge:** timing/word emission is factual; no scoring change. Report still grades.
- **Lean engine output:** one new transcript sub-field (`words`) already in the schema; spans corrected.
- **No regex / no hacks:** word timings come from STT structured output, not parsing.
- **Engine is the only LiveKit importer:** STT word capture stays in `agent.py` (the sole
  livekit-importing module) and `app/ai/realtime.py` plugin layer.
- **Zero stale code:** delete every gen-2 event-log timing path (reel + transcript_timing helper).

---

## 6. Test strategy (TDD)
- **Engine clock (RC-4):** unit test — assembler with an injected fake clock anchored at t0 yields
  session-relative spans; a turn at session+10s → span.start_ms≈10000 (not ~1e9). Driver test:
  candidate + agent spans on the same scale.
- **Word assembly:** assembler merges per-fragment words with correct offsets; merged `words[]`
  monotonic, aligned to span; empty-words fragment safe.
- **asked_at_ms (RC-1):** pure helper picks earliest agent turn per question_id → span.start_ms;
  report builder populates `QuestionOut.asked_at_ms`; a re-score of an existing session yields
  non-null values (the agent spans are already persisted).
- **Verdict (RC-2):** `QuestionOut.closure` populated from `qr.closure`; frontend renders it.
- **Reel (RC-3):** pure mapper `session_ms→video_ms` with a known offset; director word-index→ms
  against a word-timed transcript fixture; actor loads `SessionEvidence` (not the event-log) and
  produces an EDL without raising; a fixture session yields a persisted `session_reels` row.
- **No-stale-code guards:** grep asserts no `engine.v2.dispatched`/`audio.user.state`/`turn.captured`
  remain in `reel/`; `question_asked_at_ms` removed.
- **Live verification (user):** re-score `bcf61d0f` → capsules seek/highlight + verdict shows;
  run a fresh session → reel generates; confirm candidate spans session-relative in new evidence.

Touched suites to gate green: `tests/interview_engine_v3`, `tests/interview_runtime`,
`tests/reporting`, `tests/reel` (+ frontend `vitest` for the player). Engine/worker have no
hot-reload → `docker compose up -d --force-recreate nexus-engine nexus-worker` before live test.

---

## 7. Risks
| Risk | Mitigation |
|---|---|
| LiveKit STT word-timing API differs from assumption | Resolve via livekit-docs MCP before coding the capture; isolate capture in `agent.py`; words default `[]` so a miss degrades gracefully (reel falls back to turn-span clips). |
| Clock anchor skew (monotonic vs datetime) | Both are session-relative within a few ms; video frames are ~33 ms. Acceptable; documented. |
| Reel re-plumb regresses clip selection | Keep director EDL contract; only swap the timing source. Pure-function tests on the mapper + word-index resolution. |
| Existing sessions' candidate spans stay monotonic | Documented; RC-1 (capsules/verdict) fixes existing sessions on re-score; reel correctness is for new sessions. |
| Removing reel `recording_speech_intervals`/ffmpeg path breaks a caller | Grep callers first; remove only if unused. |
