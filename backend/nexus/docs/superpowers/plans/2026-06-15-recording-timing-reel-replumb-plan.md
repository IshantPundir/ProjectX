# Recording Timing, Word-Timed Transcript & Reel Re-plumb — implementation plan

> Execute with superpowers:subagent-driven-development. Fresh subagent per task; two-stage review
> (spec then quality) for medium+ tasks, combined review for small mechanical ones. NO implementer
> subagents in parallel.

**Spec:** `docs/superpowers/specs/2026-06-15-recording-timing-reel-replumb-design.md`
**Branch:** `feat/recording-timing-reel-replumb` (off `main`). Work LOCAL; commit per task; do NOT push.
End commit messages with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

**Goal:** Fix the four gen-2→gen-3 timing breakages end-to-end and delete the dead gen-2 timing code:
RC-1 report capsules (asked_at_ms), RC-2 "This moment" verdict (closure+grade), RC-3 reel re-plumb,
RC-4 session-relative candidate spans + word-timed transcript.

## Conventions (every task)
- Tests run IN the container, from `backend/nexus`:
  ```
  docker compose up -d nexus
  docker compose exec -T nexus python -m pytest <suite> -m "not prompt_quality" -q
  ```
- Engine tests live in `tests/interview_engine_v3/`. Touched suites to gate green:
  `tests/interview_engine_v3`, `tests/interview_runtime`, `tests/reporting`, `tests/reel`.
  The full backend suite has ~25 PRE-EXISTING unrelated failures — gate ONLY the touched suites.
- Frontend (`frontend/app`): `npm run test` (vitest), `npm run lint`, `npm run type-check`.
- Line numbers are hints — locate symbols by name with `grep -n` before editing.
- Git discipline: NO `git stash` / `reset --hard` / `checkout <other files>` / rebase / amend.
  Each task: `git add` ONLY its listed files; commit once. If an out-of-scope file is needed, STOP.
- Engine/worker have NO hot-reload: `docker compose up -d --force-recreate nexus-engine nexus-worker`
  before any live test.

## Pre-flight
- [ ] Confirm branch `feat/recording-timing-reel-replumb`. Commit the spec + this plan first:
  `git add docs/superpowers/specs/2026-06-15-recording-timing-reel-replumb-design.md docs/superpowers/plans/2026-06-15-recording-timing-reel-replumb-plan.md && git commit -m "docs(timing): spec + plan for recording-timing & reel re-plumb"`
- [ ] Green baseline:
  `docker compose exec -T nexus python -m pytest tests/interview_engine_v3 tests/interview_runtime tests/reporting tests/reel -m "not prompt_quality" -q` (record counts).

---

## Phase A — Engine durable contract (RC-4 + word timing)

### Task 1 — Session-relative assembler clock (RC-4)
**Files:** `app/modules/interview_engine/agent.py`; test `tests/interview_engine_v3/test_assembler_clock.py` (new).
- TDD: assert a `TurnAssembler` given an injected clock anchored at session start produces
  session-relative spans (turn at t0+10s → `span.start_ms ≈ 10000`, NOT ~1e9). (The assembler already
  takes `clock`; the test pins the contract; the agent wiring change makes prod use it.)
- Impl: in `agent.py` capture `t0_monotonic = time.monotonic()` adjacent to `started_at = datetime.now(UTC)`
  (~line 237); pass `clock=lambda: time.monotonic() - t0_monotonic` to `TurnAssembler(...)` (replace raw
  `time.monotonic` at ~line 496). Keep `max(0, …)` clamps downstream.
- Verify engine suite green. Combined review (small).

### Task 2 — Capture STT word timings via `stt_node` (words plumbing part 1)
**Files:** `app/modules/interview_engine/agent.py`; tests `tests/interview_engine_v3/test_word_capture.py` (new).
- Research-confirmed: `livekit.agents.stt.SpeechData.words: list[TimedString] | None` (each `TimedString`
  has `.start_time`/`.end_time` in stream seconds), populated by the Deepgram plugin. Provider-agnostic.
- TDD: a unit test over a small pure helper `_words_from_speech_event(ev) -> list[RawWord]` that extracts
  `(str(w), w.start_time, w.end_time, conf)` from `alternatives[0].words` (empty/None-safe).
- Impl: override `_EngineAgent.stt_node(self, audio, model_settings)` to wrap `Agent.default.stt_node(...)`,
  tap `FINAL_TRANSCRIPT` `SpeechEvent`s, accumulate the latest turn's `RawWord`s on the agent instance,
  and expose them to `on_user_turn_completed` (which then passes text + words to the assembler). Keep the
  empty-words path graceful (words default `[]`). Confirm exact `stt_node` signature/event enum via the
  livekit-docs MCP (`docs_search`/`code_search`) before coding.
- Verify engine suite green. SPLIT review (medium; LiveKit integration).

### Task 3 — `AssembledTurn.words` + assembler word-merge + driver populate (words plumbing part 2)
**Files:** `app/modules/interview_engine/turn_source.py`, `turn_assembler.py`, `driver.py`;
tests `tests/interview_engine_v3/test_assembler_words.py` (new) + extend driver tests.
- TDD: assembler buffers per-fragment word lists and concatenates on flush; fragment N>0 word times are
  offset by `(fragment_first_at - turn_first_at)*1000` so merged `words[]` are turn-relative + monotonic;
  empty-words fragment safe. Driver: `TranscriptTurn.words == turn.words`.
- Impl: add `words: list[WordTiming] = field(default_factory=list)` to `AssembledTurn`; `submit_fragment`
  gains a `words` arg; buffer alongside text; merge with offset on `_flush`. `agent.py` passes captured
  words into `submit_fragment`. `driver.py:~522` use `words=turn.words`.
- Use `transcript_timing.relative_words` to turn `RawWord`→`WordTiming` (turn-relative) at the capture/
  submit boundary. Verify engine suite green. SPLIT review (medium; touches the assembly core).

---

## Phase B — Reporting (RC-1 + RC-2)

### Task 4 — `asked_at_ms` from agent transcript spans (RC-1) + delete dead helper
**Files:** `app/modules/interview_runtime/transcript_timing.py` (delete `question_asked_at_ms`; remove
`scoring/transcript.py` gen-2 remnants only if unused), `app/modules/reporting/service.py`;
tests `tests/reporting/test_asked_at_ms.py` (new).
- TDD: a pure helper (place in reporting) `asked_at_ms_by_question(transcript) -> dict[str,int]` picks the
  earliest AGENT `TranscriptTurn` per `question_id` → `span.start_ms`. Report builder sets
  `QuestionOut.asked_at_ms` from it (replace hardcoded `None` at service.py:~291).
- Confirm `question_asked_at_ms` (gen-2 fields `role`/`timestamp_ms`) has no remaining callers, then DELETE
  it (zero stale code). Grep first.
- Verify reporting suite green. SPLIT review (medium; touches the report contract + a deletion).

### Task 5 — `QuestionOut.closure` (RC-2)
**Files:** `app/modules/reporting/schemas.py`, `app/modules/reporting/service.py`;
tests extend `tests/reporting/...`.
- TDD: `QuestionOut.closure: str | None` populated from `qr.closure` (satisfied/tapped_out/truncated/none).
- Verify reporting suite green. Combined review (small).

---

## Phase C — Reel re-plumb onto gen-3 (RC-3)

### Task 6 — `reel/timing.py` gen-3 mapper; delete gen-2 functions
**Files:** `app/modules/reel/timing.py`; tests `tests/reel/test_timing_gen3.py` (new), update existing.
- TDD: pure `recording_offset_ms(started_at, recording_started_at)` (≈0) + `session_ms_to_video_ms(ms, offset)`.
  Word/turn span → video_ms mapping with a known offset.
- Impl: DELETE `ENGINE_DISPATCH_KIND`, `engine_t0_wall`, `wall_anchor`, `speaking_intervals`, `answer_span`,
  `measure_pipeline_lag`, `recording_speech_intervals` (grep callers first; remove only if unused after the
  re-plumb). Replace with the gen-3 mapper. Verify reel suite green. SPLIT review (medium; deletes a subsystem).

### Task 7 — `reel/actors.py` load SessionEvidence (not event-log)
**Files:** `app/modules/reel/actors.py`; tests update `tests/reel/...`.
- TDD: actor `_load_inputs` reads `SessionEvidence` (same source as reporting) + recording fields; no
  `_resolve_events`/`raw_result_json.audit_envelope_ref`. Actor runs a fixture session to an EDL without raising.
- Impl: delete `_resolve_events` + event-log loading; pass word-timed transcript + recording offset onward.
  Verify reel suite green. SPLIT review (medium).

### Task 8 — `reel/director.py` + `render.py` consume gen-3 word-indexed transcript
**Files:** `app/modules/reel/director.py`, `app/modules/reel/render.py`; tests update `tests/reel/...`.
- TDD: director word-index→ms resolves against `SessionEvidence.transcript[].words`; `render.prepare_anchor`/
  `_clip_to_video` use the gen-3 mapper (no `events`). EDL validates against a word-timed fixture.
- Verify reel suite green. SPLIT review (medium).

---

## Phase D — Frontend (RC-2 render; RC-1 verify)

### Task 9 — `QuestionOut.closure` type + render in ThisMomentPanel
**Files:** `frontend/app/lib/api/reports.ts`, `frontend/app/components/dashboard/reports/theater/ThisMomentPanel.tsx`;
tests `frontend/app` vitest for the panel.
- TDD: panel renders closure + level (+ existing `our_read`) as the agent's read when a question is selected.
- `npm run test`, `npm run lint`, `npm run type-check` green. Combined review (small).

### Task 10 — Verify capsules on served app (RC-1, user-assisted)
- After re-scoring `bcf61d0f` (or a fresh session), confirm capsules seek + highlight and the verdict shows.
  Curl/playback per repo norms. (Acceptance is the user's live check.)

---

## Phase E — Guards + full verification

### Task 11 — No-stale-code guards + cross-suite green
**Files:** tests `tests/reel/test_no_gen2_eventlog.py` (new) + a reporting guard.
- Guards: grep asserts `reel/` has no `engine.v2.dispatched`/`audio.user.state`/`turn.captured`;
  `question_asked_at_ms` is gone; `SpeechData.words` capture present in agent.py.
- Full: `docker compose exec -T nexus python -m pytest tests/interview_engine_v3 tests/interview_runtime tests/reporting tests/reel -m "not prompt_quality" -q` PASS.
- Dead-code sweep: `grep -rn "engine.v2.dispatched\|audio.user.state\|turn.captured" app/modules/reel` → nothing.
- Restart engine+worker; user runs the live acceptance (capsules + verdict + reel generation + session-relative
  candidate spans in new evidence). Combined review.

## Done criteria
- All tasks committed; touched suites green; gen-2 event-log timing deleted from `reel/`;
  `question_asked_at_ms` deleted. Candidate transcript spans session-relative; `words[]` populated.
- Live (user): report capsules seek/highlight, "This moment" shows closure+grade, reel generates.
