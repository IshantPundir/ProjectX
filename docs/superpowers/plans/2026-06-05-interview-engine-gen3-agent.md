# Interview Engine Gen-3 — Agent Implementation Plan (audio in → LLM → audio out)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the gen-2 interview engine with a clean gen-3 agent that runs a live screening end-to-end
(`audio → Ear → bridge ∥ brain → mouth → audio`), writing the new append-only evidence contract.

**Architecture:** Four layers — **Ear** (Silero VAD + Smart Turn v3 + LiveKit MultilingualModel fused
under LiveKit *manual* turn control), **Mouth** (called twice/turn: an immediate context-aware *bridge*
∥ the brain, then the real line voicing the Directive), **Brain** (async control plane, lean
per-turn output, append-only notes). Collector-not-judge; leak-proof mouth; deterministic question
resolver + time-budget.

**Tech Stack:** Python 3.13 · LiveKit Agents (manual turn detection) · Silero VAD · Smart Turn v3 (ONNX,
`pipecat`/standalone) · LiveKit `turn_detector` MultilingualModel · Deepgram STT (en-IN + keyterm) ·
Sarvam TTS · OpenAI `gpt-5.x-mini` via `app/ai` · Pydantic v2 · pytest · asyncpg/SQLAlchemy · Dramatiq.

**Source of truth:** `docs/superpowers/specs/2026-06-05-interview-engine-gen3-design.md`. Canonical
(validated) schemas: `tmp/interview_engine_research/{evidence_contract,brain_input,directive}.py`.
Prompts: `tmp/interview_engine_research/15-gen3-prompts.md`.

**Testing cadence (per product owner):** automated tests every task; **a single manual talk-test at the
end (Phase F)** — no mid-build talk sessions. The `[VALIDATE]` tuning items (Ear thresholds, bridge
latency feel, brain elicitation, budget constants) are tuned in that final talk-test, with knobs exposed
as config so tuning needs no code change.

**Scope:** the AGENT only. Runs on the **existing** question-bank data (resolver treats the flat bank as
all-`core`). The two-tier bank generator and the reporting-input rewrite are **separate later plans**.
Report scoring is skipped during agent testing via the existing `AUTO_SCORE_SESSION_REPORTS=false` toggle.

---

## Roadmap

| Phase | Builds | Live-testable? |
|---|---|---|
| **A** | Teardown of gen-2 engine + v3 prompts; new contracts; module skeleton; Smart Turn factory | imports/boot only |
| **B** | The Ear — manual turn control, VAD + Smart Turn + MultilingualModel fusion ladder, gated cue | unit-tested |
| **C** | The loop — bridge ∥ brain → mouth, Directive, append-only note accumulation | unit-tested |
| **D** | The Brain — input builder (cache-split), prompt v4, structured output, resolver + time-budget | unit-tested |
| **E** | The Mouth — persona + per-act + bridge prompts, leak validation | unit-tested |
| **F** | Wire end-to-end, persist `SessionEvidence`, provenance pass, **manual talk-test + tune** | **talk-test** |

> Phase A is fully detailed below. Phases B–F are concrete task lists (files + responsibility + interfaces
> + test intent + impl shape); each task expands to bite-sized TDD steps at execution time
> (subagent-driven). They carry no placeholders — every interface references the pinned schemas/prompts.

---

## File & deletion map

**Delete (gen-2, this plan):**
- `app/modules/interview_engine/{triage/,brain/,mouth/,controller.py,directive.py,coverage.py,audit.py,result_builder.py,audio_metrics.py,turn_taking/,event_log/}`
- `prompts/v3/engine/` (entire tree)
- `app/modules/interview_runtime/results.py` + the `SessionResult`/`SteeringObservation` parts of `interview_runtime/schemas.py`
- *(NOT deleted here — separate bank plan: `question_bank` generator + `prompts/v1|v2/question_bank_*`. NOT deleted — reporting; it's a later plan. Bank DB data stays.)*

**Create:**
- `app/modules/interview_runtime/evidence.py` — `SessionEvidence` + parts + wire enums (from `evidence_contract.py`)
- `app/modules/interview_engine/contracts.py` — `BrainTurnInput/Output`, `Directive`, `MouthTurnInput`, `BridgeRequest`, `BrainSessionContext` (from `brain_input.py` + `directive.py`)
- `app/modules/interview_engine/ear/{__init__.py,vad_gate.py,smart_turn.py,fusion.py,ladder.py}`
- `app/modules/interview_engine/brain/{__init__.py,service.py,input_builder.py,resolver.py,policy.py}`
- `app/modules/interview_engine/mouth/{__init__.py,service.py,persona.py,bridge.py}`
- `app/modules/interview_engine/notes.py` — append-only note accumulator + provenance pass
- `prompts/v4/engine/{brain.system.txt, mouth/_persona.txt, mouth/<act>.txt, mouth/bridge.txt}` (from doc 15)
- `tests/interview_engine_v3/...` mirroring the above

**Modify/rewrite:**
- `app/modules/interview_engine/agent.py` — rewrite the per-turn loop; keep the LiveKit entrypoint + worker/broker bootstrap
- `app/modules/interview_engine/__init__.py` — export the new public surface
- `app/ai/realtime.py` — add `build_smart_turn()`; keep `build_vad`/`build_turn_detector`/`build_stt_plugin`/`build_tts_plugin`
- `app/modules/interview_runtime/service.py` — replace `record_session_result` → `record_session_evidence`
- `app/modules/interview_runtime/__init__.py` + `schemas.py` — re-export `SessionEvidence`; drop `SessionResult`

---

## Phase A — Teardown + contracts + module skeleton

### Task A1: Delete gen-2 engine internals + v3 prompts

**Files:** delete the paths in the deletion map (engine internals + `prompts/v3/engine/`).

- [x] **Step 1:** `git rm -r` the gen-2 engine internal modules + `prompts/v3/engine/`. Leave
      `agent.py`, `__init__.py`, `__main__.py`, `README.md`, `AGENT_ARCHITECTURE.md` in place (rewritten later).
      (Also removed gen-2 `tests/interview_engine/` subtree — tested the deleted internals.)
- [x] **Step 2:** Temporarily reduce `app/modules/interview_engine/__init__.py` to export nothing
      LiveKit-bearing (so the FastAPI app still imports). Comment body: `# gen-3 rebuild in progress`.
- [x] **Step 3:** Run `docker compose run --rm nexus python -c "import app.main"` → Expected: imports
      clean (no reference to deleted modules). Fix any stray imports (e.g. `interview_runtime`, `reporting`,
      `reel`, `vision`) that referenced deleted engine symbols — repoint or stub. (None needed — nothing
      outside the engine package imported engine internals.)
- [x] **Step 4:** Commit: `git commit -m "chore(engine): tear down gen-2 engine internals + v3 prompts"` (70936098)

### Task A2: The `SessionEvidence` wire contract

**Files:** Create `app/modules/interview_runtime/evidence.py`; Test `tests/interview_engine_v3/test_evidence_contract.py`.

- [ ] **Step 1: Write the failing test** — port the validated end-to-end build + the retraction/provenance
      asserts from the spike (the exact asserts already run green against `evidence_contract.py`):

```python
from app.modules.interview_runtime.evidence import (
    SessionEvidence, SignalEvidence, EvidenceNote, QuestionRecord, TranscriptTurn,
    SessionMeta, TimeSpan, Provenance, EvidenceStance, EvidenceTexture, QuestionTier,
    QuestionOutcome, ThreadClosure, CoverageState, CompletionReason, SignalType, SignalPriority, Speaker,
)

def test_timespan_rejects_reversed():
    import pytest
    with pytest.raises(Exception):
        TimeSpan(start_ms=5, end_ms=1)

def test_append_only_retraction_keeps_original():
    notes = [
        EvidenceNote(seq=1, turn_ref="t-2", signal="SOAP/XML", stance=EvidenceStance.supports,
                     texture=EvidenceTexture.thin, quote="used SOAP a bit",
                     span=TimeSpan(start_ms=0, end_ms=900), from_question_id="q", via_probe=False),
        EvidenceNote(seq=2, turn_ref="t-3", signal="SOAP/XML", stance=EvidenceStance.contradicts,
                     texture=EvidenceTexture.thin, quote="actually no",
                     span=TimeSpan(start_ms=1000, end_ms=2000), from_question_id="q", via_probe=True,
                     retracts_seq=1),
    ]
    assert [n.seq for n in notes] == [1, 2]
    assert notes[1].retracts_seq == 1
```

- [x] **Step 2:** Run `docker compose run --rm nexus pytest tests/interview_engine_v3/test_evidence_contract.py -v` → Expected: FAIL (module missing).
- [x] **Step 3:** Create `evidence.py` by promoting `tmp/interview_engine_research/evidence_contract.py`'s
      `SessionEvidence` half (everything except `BrainTurnOutput`/`SignalObservation`, which go to the engine
      `contracts.py`). Keep the shared enums here (this is the wire contract). Code is already written + validated.
- [x] **Step 4:** Run the test → Expected: PASS.
- [x] **Step 5:** Commit: `git commit -m "feat(runtime): add gen-3 SessionEvidence append-only contract"` (ae9db898)

### Task A3: The engine-internal contracts (`BrainTurnInput/Output`, `Directive`, mouth inputs)

**Files:** Create `app/modules/interview_engine/contracts.py`; Test `tests/interview_engine_v3/test_engine_contracts.py`.

- [ ] **Step 1: Write the failing test** — build a `BrainTurnOutput` (probe + advance-with-preference) and a
      `MouthTurnInput` continuing from a bridge; assert `target_question_id` is absent and
      `preferred_next_signal`/`probe_index`/`just_said` work (the asserts already run green against the spike files).
- [x] **Step 2:** Run → FAIL.
- [x] **Step 3:** Create `contracts.py` by promoting `brain_input.py` (`BrainSessionContext`,
      `BrainTurnInput`, `SignalObservation`, `SignalRead`, `BankQuestionIndex`, `SignalSpec`, `BudgetPhase`,
      `ActiveQuestionRubric`, `WindowTurn`) + `directive.py` (`Directive`, `DirectiveAct`, `DirectiveTone`,
      `MouthTurnInput`, `BridgeRequest`) + `BrainTurnOutput`/`SignalObservation`. Import shared enums
      (`CoverageState`, `EvidenceStance`, `EvidenceTexture`, `SignalType`, `SignalPriority`, `TimeSpan`) from
      `interview_runtime.evidence` (single source).
- [x] **Step 4:** Run → PASS.
- [x] **Step 5:** Commit: `git commit -m "feat(engine): add gen-3 brain/mouth/directive contracts"` (ad303e0d)

### Task A4: `record_session_evidence` (persistence) + migration

**Files:** Modify `app/modules/interview_runtime/service.py`, `schemas.py`, `__init__.py`; Create migration
`migrations/versions/0054_session_evidence.py`; Test `tests/interview_engine_v3/test_record_evidence.py`.

- [ ] **Step 1: Write the failing test** — `record_session_evidence(db, evidence, tenant_id=...)` on a
      seeded `active` session: asserts the row transitions to `completed`, `result_status="ok"`, and the
      stored evidence round-trips (`SessionEvidence.model_validate(row.session_evidence_json)` equals input).
      Idempotent on re-call.
- [x] **Step 2:** Run → FAIL.
- [x] **Step 3:** Migration `0054`: add `sessions.session_evidence_json JSONB NULL`. **DEVIATION (deep-audit
      2026-06-05):** the gen-2 result columns (`raw_result_json`/`transcript`/`questions_asked`/`probes_fired`)
      are STILL READ by `reporting/actors.py`, `reel/actors.py`, `vision/actors.py` — modules the product owner
      explicitly scoped OUT (separate later rewrite plans). Dropping them now would break those modules without
      touching them. So **A4 is additive-only**: add the column + ORM attr; DEFER the gen-2 column drop +
      `record_session_result`/`SessionResult`/`SteeringObservation`/`results.py` removal to the reporting-rewrite
      plan (which removes their readers). Provide a rollback (drop column). Implement `record_session_evidence`
      in `service.py` (atomic update gated on `state='active'`, bypass-RLS + explicit `tenant_id`, audit row,
      commit-then-return; NO report enqueue — the old scorer can't read SessionEvidence), mirroring the
      `record_session_result` transaction shape. Export it from `__init__.py`. Add the `session_evidence_json`
      ORM column to `app/modules/session/models.py`.
- [x] **Step 4:** Run `docker compose run --rm nexus alembic upgrade head` then the test → PASS. (alembic 0053→0054 clean; 5 new tests + 71 existing interview_runtime tests green; `import app.main` OK)
- [x] **Step 5:** Commit: `git commit -m "feat(runtime): persist SessionEvidence + migration 0054"` (362ce39b; reviewer APPROVED, added 3 branch tests)

### Task A5: `build_smart_turn()` factory

**Files:** Modify `app/ai/realtime.py`, `app/ai/config.py`; Test `tests/interview_engine_v3/test_realtime_factories.py`.

- [ ] **Step 1: Write the failing test** — `build_smart_turn()` returns an object exposing
      `predict(audio: np.ndarray, sample_rate=16000) -> dict` with keys `prediction` (0|1) and `probability`
      (float). Test with a 1s silence array → asserts the keys + types (not the value).
- [x] **Step 2:** Run → FAIL.
- [x] **Step 3:** Add `engine_smart_turn_model` (default `pipecat-ai/smart-turn-v3`) to `AIConfig`. Implement
      `build_smart_turn()` in `realtime.py` (lazy import of the ONNX runtime + the model; a thin wrapper whose
      `predict()` does: resample→16k mono, clip/zero-pad-front to ≤8s, Whisper feature-extractor normalize,
      ONNX run, sigmoid → `{"prediction": int(p>0.5), "probability": float(p)}`). Bake the model in the
      Dockerfile `download-files` step (note in the task; mirrors the Silero bake).
      (Deviations, verified against the real repo: actual ONNX file is `smart-turn-v3.1-cpu.onnx`;
      `scipy` is not in the nexus image so resampling uses a numpy `np.interp` fallback — 16k is the hot path.)
- [x] **Step 4:** Run → PASS. (model downloads + runs on 1s silence; `import app.main` stays ML-lib-free)
- [x] **Step 5:** Commit: `git commit -m "feat(ai): add Smart Turn v3 factory in realtime"` (c4edd94b; session+extractor cached, lazy imports verified)

### Task A6: Engine module skeleton (boots, no behavior)

**Files:** Rewrite `app/modules/interview_engine/agent.py` (entrypoint + bootstrap only), `__init__.py`;
Test `tests/interview_engine_v3/test_engine_imports.py`.

- [ ] **Step 1: Write the failing test** — (a) `import app.main` does NOT import livekit (grep the loaded
      modules) — preserves the lazy-import invariant; (b) `from app.modules.interview_engine import run` works
      lazily; (c) the new `ear`/`brain`/`mouth`/`notes` packages import.
- [x] **Step 2:** Run → FAIL.
- [x] **Step 3:** Rewrite `agent.py`: keep the worker/broker bootstrap + `entrypoint`/`_run_entrypoint`
      (loads `SessionConfig` via `build_session_config`, binds structlog, heartbeat, failure funnel) and a
      `run()` that connects + waits for participant + builds the `AgentSession` (manual turn detection, STT,
      mouth LLM, TTS, VAD) then `await _drive(...)` (stub raising `NotImplementedError` for now). Create empty
      `ear/`, `brain/`, `mouth/` packages + `notes.py` with the class signatures (no bodies). Update `__init__.py`.
      (Bonus: the stricter no-livekit-in-FastAPI test exposed a pre-existing leak — `session/livekit.py`'s
      top-level `from livekit import api` — now lazified. agent.py 1190→~258 lines.)
- [x] **Step 4:** Run → PASS. (29 tests green incl. tests/session; `import app.main` livekit-free; engine imports lazily)
- [x] **Step 5:** Commit: `git commit -m "feat(engine): gen-3 module skeleton + bootstrap (no behavior yet)"` (ea52f626)

---

## Phase B — The Ear (manual turn control + fusion)

> All Ear logic is pure + unit-testable with fake inputs; tuning knobs are config, tuned in Phase F.

- [x] **B1 — `ear/ladder.py` (pure fusion ladder).** ✅ 186a151e — 23 table-driven tests; knobs `ear_*` in AIConfig (F3-tuned). Input: `{vad_silence_ms, smart_turn_prob, text_eou_prob}`
      → output `EarDecision` enum `{commit, wait, hold_cue}`. Implement the §4 fusion rule (both-complete→commit;
      both-incomplete→wait/hold; disagree→ voice-finished-but-text-unsure→commit, text-complete-but-voice-going→wait).
      Thresholds from `AIConfig` (`ear_smart_turn_commit_thr`, `ear_text_commit_thr`, `ear_min_silence_ms`,
      `ear_hold_cue_ms`). **Tests:** table-driven over the 4 quadrants + the two disagreement cases + the
      hold-cue timing. *(Pure logic — fully bite-sized TDD.)*
- [x] **B2 — `ear/smart_turn.py` (turn-audio buffer + predict).** ✅ 02e20e37 — `TurnAudioBuffer` (tail≤8s, reset,
      injectable detector, empty→0.0 contract); 5 tests.
- [x] **B3 — `ear/vad_gate.py` + MultilingualModel invocation.** ✅ d21162ce — `SpeechActivity` pause tracker (pure) +
      `text_eou_probability(model, chat_ctx)` graceful-None wrapper. **[VERIFY-at-build] RESOLVED:**
      `EOUModelBase.predict_end_of_turn(chat_ctx, timeout=3) -> float` is public + works in-job (needs job ctx);
      wrapper returns None on any failure → Smart-Turn-only. 18 tests incl. ladder-None integration.
- [x] **B4 — Ear orchestration in `agent.py` (manual turn control).** ✅ 9cb924ac — split into livekit-free `Ear`
      orchestrator (`ear/orchestrator.py`, 5 tests: commit-on-done, cue-once-on-thinking, wait-under-floor,
      buffer-reset) + agent.py glue (`_EarAgent.stt_node` audio tee, `setup_ear` poll loop + barge-in, `build_ear`).
      Telemetry → structlog `engine.ear.eou` (event_log deleted; DEVIATION). Real-event behavior `# F3-VALIDATE`.
      Full gen-3 suite 59 green.

---

## Phase C — The loop (bridge ∥ brain → mouth + notes)

- [x] **C1 — `notes.py` accumulator.** ✅ b762b746 + fix 0df99a70 (realigned `SignalObservation` to canonical
      `quote_span`, dropped a drifted LLM `quote` str — anti-hallucination). `NoteLog.append`/`to_session_evidence`; 8 tests. `NoteLog.append(SignalObservation, turn_ref, utterance, span,
      question_id, via_probe)` builds immutable `EvidenceNote`s (assigns `seq`, fills quote from the
      `quote_span`); `retraction` appends with `retracts_seq`. `to_session_evidence(...)` assembles
      `SessionEvidence`. **Tests:** append order/seq, retraction keeps original, multi-signal turn → multiple notes.
- [x] **C2 — Provenance pass (`notes.py`).** ✅ 38bac550 — `compute_provenance` §7.1 (4 branches incl. truncated→not_reached,
      disclaim→probed_absent, rule-1-wins); pure, inputs unmutated; 17 tests; suite 84 green. `compute_provenance(signals, notes, questions)` implements the
      §7.1 rule (asked_directly / cross_credited / probed_absent / not_reached using own-question map +
      `closure`). **Tests:** the four branches incl. truncated→not_reached, disclaim→probed_absent, cross-credit.
- [x] **C3 — The turn loop.** ✅ bb376ea7 — built as livekit-free `loop.py` (`run_turn` + `Brain`/`Mouth`/`Voice`
      Protocols + `TurnContext` + `BrainDecision` contract + `CANNED_BRIDGE_FALLBACK`); DEVIATION: separate module
      (not inline agent.py) for testability — F1 wires it into `_drive`. 5 tests: parallel launch, bridge-first,
      just_said=bridge, notes appended, barge-in cancels both children. Suite 89 green.

---

## Phase D — The Brain (input + prompt + output + resolver)

- [x] **D1 — `brain/input_builder.py`.** ✅ d38b8c48 (rebuilt) + **contracts realignment b07bcf82** — `build_session_context`
      (cache prefix) + `CoverageProjection` (signal_reads/uncovered weight-ranked/knockout_pending) + `build_turn_input`
      + render_prefix(byte-identical)/render_suffix(DATA-fenced). ⚠️ Caught + fixed systemic A3 contracts.py drift
      (ActiveQuestionRubric/BrainTurnOutput.composed_say/SignalRead.established_quote/BridgeRequest/cache-split). 25 tests.
- [x] **D2 — `prompts/v4/engine/brain.system.txt`.** ✅ 492959e5 — verbatim from doc 15 §2 (job-agnostic, no template vars);
      config bumped `engine_brain_prompt_version=v4` + `brain:v4`; load+job-agnostic test. Suite 127 green. Build `BrainSessionContext` once (cached prefix: role + full signals +
      compact bank index) + `BrainTurnInput` per turn (active rubric, on_the_floor, utterance, evidence_so_far
      from the runtime coverage projection, transcript window, budget_phase, uncovered_signals, knockout_pending).
      Render to the stable-prefix→dynamic-suffix message list. **Tests:** prefix byte-identical across turns;
      suffix bounded; no rubric in the prefix.
- [ ] **D2 — `prompts/v4/engine/brain.system.txt`.** Author from doc 15 §2 (job-agnostic). **Test:** a
      `@prompt_quality` eval stub (real-API, opt-in) — defer assertions to Phase F tuning; here just load + render.
- [x] **D3 — `brain/service.py` (`ControlPlane.decide`).** ✅ 7571f197 — injectable LLM seam (instructor, mocked in
      tests) → BrainTurnOutput; projection update; move→act (8-way match/case) + say resolution (ask=resolver bank text,
      probe=verbatim follow-up, clarify/etc=scrubbed composed_say, repeat=on_the_floor, close=None+terminal) + gates
      (knockout blocks premature close, probe coerced). `build_control_plane(config)` assembles from SessionConfig. 8 tests.
- [x] **D4 — `brain/resolver.py` (deterministic next-question + time-budget).** ✅ 8cb6e889 — `resolve_next` + `compute_budget_phase`
      + `build_question_records`; mandatory-never-dropped, overflow-by-weight, skip-covered-overflow, truncation→not_reached,
      preference-only-with-slack; knobs `engine_close_reserve_s`/`engine_winding_down_s` (F3-tuned). 22 tests.
- [x] **D5 — `brain/policy.py`.** ✅ e7e62aa1 — `gate_knockout` (KnockoutTracker probe→check→confirm; blocks premature close),
      `scrub_composed_say` (literal known-rubric-string leak detection, NOT regex-intent), `coerce_probe_index`. All
      deterministic + never-raise. 48 tests.

---

## Phase E — The Mouth (persona + acts + bridge)

- [x] **E1 — `prompts/v4/engine/mouth/{_persona.txt,<act>.txt,bridge.txt}`.** ✅ 7b2b3c3c — §3/§4/§4b verbatim +
      authored redirect/repeat in-style; all 8 acts + persona + bridge; config v4/mouth:v4. (Fixed a `prompts/v4/mouth/`→
      `prompts/v4/engine/mouth/` layout deviation for consistency with the brain prompt.) 4 tests.
- [x] **E2 — `mouth/persona.py` + `mouth/service.py` (`real_line`).** ✅ bc2d8240 — `[persona|per-act|dynamic suffix]`
      render; verbatim shortcut for ask/probe/repeat; injectable mouth-LLM seam for clarify/redirect/reassure/answer_meta/close;
      `validate_no_leak`; persona `str.replace` substitution. Structurally rubric-free. 7 tests.
- [x] **E3 — `mouth/bridge.py` (`bridge`).** ✅ b2a612b1 — `BridgeComposer.bridge` gist-mirror (utterance+openers only,
      DATA-fenced); canned fallback on error/timeout/empty (never raises); reuses `CANNED_BRIDGE_FALLBACK`. 6 tests. Suite 222.

---

## Phase F — Wire end-to-end + persist + manual talk-test

- [x] **F1 — Replace the `agent.py` `_drive` stub** with the real loop. ✅ 0106ca76 — livekit-free `SessionDriver`
      (`driver.py`: opener → handle_turn[bridge∥brain→mouth+notes] → finalize[provenance + `record_session_evidence`])
      + agent.py glue. `BrainDecision.next_question_id` added for active-question threading. Integration test: 3-turn
      scripted session → well-formed persisted SessionEvidence (notes, provenance, transcript, completion). Suite 223.
- [x] **F1.5 — Correct live glue to LiveKit's OFFICIAL manual-turn pattern** (from push_to_talk.py). ✅ 6309afb3 —
      `commit_user_turn()` RETURNS the transcript (was: per-segment `user_input_transcribed` event = wrong);
      `_EarAgent.on_user_turn_completed → StopResponse()` suppresses the built-in auto-reply (was: double-speak);
      session started with `_EarAgent` (audio-tee + StopResponse active); `stt_node` tees the INPUT audio stream;
      poll loop owns commit→`driver.handle_turn`. Suite 226. StopResponse verified `from livekit.agents`.
- [x] **F2 — Boot + smoke.** ✅ b793ce32 — `test_engine_boot.py` (full agent import + drive wiring + stub-gone);
      **nexus-engine image rebuilt (Smart Turn + Silero + turn-detector baked), worker boots + prewarms Silero +
      REGISTERS with LiveKit Cloud (agent `Punar`, India West), no import/model errors.** Restarted on the corrected glue.
- [ ] **F3 — MANUAL TALK-TEST (the single live session) — USER'S TURN.** Engine is live + registered. Run a real
      session against the existing JD/bank (`ce6dad9a…` / stage `2ea4f4a3…`). Expect to surface the `# F3-VALIDATE`
      live-API items (event/attr names, `commit_user_turn` kwargs, AudioFrame attrs, timing) + tune config (no code):
      Ear thresholds (`ear_*`), bridge latency, brain elicitation, budget (`engine_close_reserve_s`/`engine_winding_down_s`).
- [ ] **F4 — Commit the tuned config** + a short `README.md`/`AGENT_ARCHITECTURE.md` refresh for gen-3.

---

## Self-review (against the spec)

- **Spec coverage:** Ear (§4)→B; bridge+loop+notes (§6,§7)→C; brain+resolver+budget (§5,§8)→D; mouth (§6)→E;
  contract+provenance (§7)→A2/C1/C2; wire+persist (§3)→F. Bank (§8 generation) + reporting (§7.2) are
  **out of scope** (separate plans, per the agreed order) — noted, not dropped.
- **Placeholders:** none — every interface references the validated `.py` schemas / doc-15 prompts. Phases B–F
  are concrete task lists to expand to bite-sized at execution (large-plan handling), not vague TODOs.
- **Type consistency:** enums/contracts come from one source (`interview_runtime/evidence.py` + engine
  `contracts.py`); `BrainTurnOutput` has no `target_question_id` (dropped); `record_session_evidence` (not
  `record_session_result`) used in A4 + F1.

## `[VALIDATE]` items resolved in F3 (config-only, no code change)
Ear thresholds + disagreement weights · bridge first-audio (and whether to add the instant micro-beat) ·
brain elicitation feel/anti-grind · time-budget `close_reserve`/`winding_down`.
