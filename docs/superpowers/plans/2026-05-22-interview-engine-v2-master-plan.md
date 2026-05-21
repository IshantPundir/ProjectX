# Interview Engine v2 — Master Implementation Plan

> **For agentic workers:** This is the **master / sequencing** plan. It decides build order + cutover,
> maps the file structure, defines the six milestones, and fixes the test/eval strategy. Each milestone
> has its **own** detailed bite-sized plan (`...-mN-<name>.md`) — use
> superpowers:subagent-driven-development or superpowers:executing-plans against the per-milestone plan,
> not this one. This document is the contract the milestone plans must each satisfy.

**Goal:** Replace ProjectX's interview agent decision/conversation core with a two-plane hybrid engine
(async reasoning **brain** + real-time **mouth** joined by a **Directive** contract) that screens
Indian candidates conversationally, collects signal evidence, runs verified knockout, and never
blocks speech on reasoning — while keeping the live recruiter/candidate flows working throughout.

**Architecture:** Two planes on two clocks. The **Conversation Plane (mouth)** = GPT‑5.4‑mini persona
"Arjun", owns turn-taking + EOU + TTS, voices Directives, never sees the rubric. The **Control Plane
(brain)** = GPT‑5.4 (low effort + a `reasoning` field), runs async, reads transcript as data, tracks
signal coverage, picks the move, emits Directives + audit records. **Option C** control protocol:
the brain pre-stages the likely next Directive; the mouth delivers whichever is current at a turn
boundary; stale/speculative Directives are discarded. The new engine is a **parallel module** selected
by a per-job + global feature flag; the old engine is untouched (reference-only).

**Tech Stack:** FastAPI + Python 3.13 (`backend/nexus`), SQLAlchemy async / asyncpg, Alembic, Pydantic
v2, Dramatiq, LiveKit Agents (Python), Deepgram nova‑3 STT + keyterms, Indian‑English TTS
(Sarvam/OpenAI), OpenAI via `app/ai/` (instructor + Responses streaming), Next.js (`frontend/app`),
Postgres/Supabase + RLS. Solo dev — the author is the reviewer; no CI/PR gate.

---

## 0. How to read this plan

- **§1** restates the rewrite/extend/reuse boundary (from DESIGN‑SPEC §1a) as a touch-point table.
- **§2** is the first deferred decision: **build order** + rationale + dependency graph.
- **§3** is the second deferred decision: **old→new cutover / migration strategy**.
- **§4** is the target **file structure** (new module + extend/reuse seams).
- **§5** defines the six **milestones** (goal, deliverables, acceptance criteria, which detailed plan).
- **§6** is the **test & eval strategy** (deterministic TDD · prompt evals · manual talk-test · regression).
- **§7** is **risks & mitigations**.
- **§8** is the **spec→milestone coverage map** (self-review).

Locked decisions (do not re-litigate — DESIGN‑SPEC + memory): two-plane hybrid; Option C; one-directional
interruption (AI never interrupts candidate); thread-satisfaction not turn-count; verified knockout +
early-exit default + records-never-rejects; EOU = tune existing LiveKit `MultilingualModel` + dynamic
endpointing + behavioral layer (NO new model in v1); models all OpenAI (mouth 5.4‑mini, brain 5.4,
bank‑gen 5.5+reasoning); all prompts rewritten from scratch; spoken-not-written bank questions; Directive
is the only brain→mouth contract; **no regex for intent/understanding**; quality before latency.

---

## 1. Scope — touch-point table (DESIGN‑SPEC §1a)

| Disposition | Area | Concrete paths |
|---|---|---|
| **REWRITE (new module)** | brain, mouth, Directive, controller, EOU/turn-taking glue, coverage/decision models | NEW `backend/nexus/app/modules/interview_engine_v2/` |
| **EXTEND (modify)** | bank schema + spoken-question gen + per-question streaming + chaining | `app/modules/question_bank/{models,schemas,actors,refine,context,router,sse}.py`; new migration `0044` |
| **EXTEND** | session config/result wire contract (new bank fields + engine-version selector) | `app/modules/interview_runtime/{schemas,service}.py` |
| **EXTEND** | realtime audio: EOU recalibration + dynamic endpointing + hold-space hooks | `app/ai/realtime.py`, `app/config.py`, `app/ai/config.py` |
| **EXTEND** | AI config: brain/mouth model+effort, prompt versions, cache keys, v2 flag | `app/ai/config.py`, `app/config.py` |
| **EXTEND** | recruiter bank UI: section grouping + live per-question append | `frontend/app/components/dashboard/question-bank/*`, `hooks/use-questions-status-stream.ts` |
| **EXTEND** | audit event-log: per-turn decision record shape | reuse `interview_engine/event_log/` envelope concept in v2 (`interview_engine_v2/event_log/`) |
| **NEW prompts** | brain · per-act mouth · rewritten bank-gen set | `backend/nexus/prompts/v3/engine/*`, `prompts/v2/question_bank_*` (rewritten set) |
| **REUSE AS-IS** | auth/RLS, scheduler/invite, session `/start`+OTP+single-use token, LiveKit room provisioning, `frontend/session` candidate UI, STT keyterm extraction, module/DB/tenant architecture | untouched |
| **REFERENCE-ONLY (never edit)** | the entire old engine core | `app/modules/interview_engine/` (orchestrator, judge, speaker, state, bank_resolver) |

> **Important reality check (verified against the tree, supersedes the doc text I read):**
> - Migration head is `0043_session_proctoring`. **New migrations start at `0044`.**
> - Per-question `difficulty` **already shipped** in `0042`. `question_kind` exists since `0026`
>   (CHECK = `technical_depth|behavioral_star|compliance_binary|open_culture`). So the bank migration is
>   small: add `primary_signal`, broaden the `question_kind` CHECK to the new taxonomy.
> - The current engine is the **full Judge→State→Speaker structured orchestrator** (orchestrator.py
>   ~1.5k LOC, judge/, speaker/, state/engine.py ~1.4k LOC), not the "thin harness" the backend
>   CLAUDE.md still describes — that CLAUDE.md bullet is stale. The rewrite targets the full structured
>   engine. (Flag only; not fixing CLAUDE.md here.)

---

## 2. DECISION A — Build order (deferred from DESIGN‑SPEC §15)

**Chosen order: M1 Foundations → M2 Bank redesign → M3 Audio/EOU & turn-taking → M4 Mouth →
M5 Brain + integration → M6 End-to-end + cutover ramp.**

### Rationale
- **Foundations first (M1).** The Directive schema + the controller (queue / supersession / staleness /
  speculative-discard) + the audit record + the cutover flag are prerequisites for *every* other piece
  and are **pure, deterministic, and 100% TDD-able** — exactly the work that benefits from a precise
  bite-sized plan. Building the cutover seam first means v1 stays the default and nothing downstream
  ever risks the live flow.
- **Bank before brain (M2).** The brain *consumes* the new bank fields (`primary_signal`, per-question
  `difficulty`, refined `question_kind`, depth-in-`follow_ups`, spoken `rubric`). Building the brain
  against the old written-exam bank would be throwaway. The bank redesign is **independently shippable**
  (it doesn't touch the live engine at all), is the **single biggest quality win** (spoken-not-written,
  per doc 12/13), and lets us **de-risk the biggest backend lift** (per-question streaming) early while
  the live engine keeps running v1.
- **Audio/EOU before mouth (M3).** The mouth owns turn-taking but *depends on* the floor-control layer
  (when has the candidate yielded? when to hold space? barge-in cancellation). Tuning EOU + building the
  behavioral layer first gives the mouth a stable substrate to talk-test against.
- **Mouth before brain (M4).** The mouth is **talk-testable in isolation** with hand-scripted Directives
  (no brain needed) — the fastest path to "I can talk to Arjun and judge the voice/persona/latency,"
  which is how the user validates agents. It also forces the LiveKit-integration risk to the surface
  early (the trickiest engineering), before the brain depends on it.
- **Brain last of the components (M5).** It needs the bank shape (M2), the Directive contract (M1), and a
  working mouth to voice its output (M4). Building it last means every dependency is real, not mocked.
- **E2E + cutover ramp (M6).** Full-session integration, the prompt-eval harness wired across all three
  surfaces, the regression replay of the `b99d8cc6` failure, then the staged flip of real jobs to v2 and
  the documented retirement of v1.

This honors the locked **quality-before-latency** preference: M2 (bank) and M5 (brain) — the two biggest
quality levers — are sequenced ahead of any pure latency tuning, and EOU work in M3 is scoped to
"tune + behavioral layer," not a speculative new model.

### Dependency graph
```
M1 Foundations ──┬─────────────────────────────────────────────► M6 E2E + cutover
                 │
                 ├─► M2 Bank redesign ───────────────┐
                 │                                    ├─► M5 Brain + integration ─► M6
                 └─► M3 Audio/EOU ─► M4 Mouth ────────┘
```
- **Critical path:** M1 → (M2 ∥ M3→M4) → M5 → M6.
- M2 is independent of M3/M4 and may be slotted anywhere after M1 and before M5. For a solo dev the
  recommended *linear* order is M1, M2, M3, M4, M5, M6.

---

## 3. DECISION B — Old→new cutover / migration (deferred from DESIGN‑SPEC §15)

### Principle
**Parallel build behind a flag; old engine never edited; default stays v1 until v2 is proven by talking
to it.** The cutover seam lives entirely **inside the engine container**, so the rate-limited candidate
`/start` path, dispatch, and `frontend/session` are **untouched** (minimum blast radius).

### The selector
1. **New column** `job_postings.interview_engine_version TEXT NULL` (migration `0044`). `NULL` = inherit
   the global default; `'v1'` / `'v2'` = explicit per-job override. (Per-job is the right granularity:
   the user flips one *test* job to v2 and talks to it while every real job stays on v1 — no UI needed,
   set it with one SQL update or the dev helper in M6.)
2. **Global default** `INTERVIEW_ENGINE_DEFAULT_VERSION` in `Settings` (default `'v1'`), surfaced on
   `AIConfig.interview_engine_default_version`.
3. **Resolution** in `interview_runtime.service.build_session_config`: `version = job.interview_engine_version or ai_config.interview_engine_default_version`,
   written to a new `SessionConfig.interview_engine_version` field. (build_session_config already walks
   `job_postings`, so this is a 2-line add + a field.)
4. **Branch** at the engine entrypoint (`app/modules/interview_engine/agent.py` `entrypoint`, the
   `nexus-engine` compose command): after `build_session_config`, `if config.interview_engine_version == 'v2': return await interview_engine_v2.run(ctx, config)` else fall through to today's
   orchestrator. This is the **only** edit to the old tree — a single dispatch branch, additive, with the
   v1 path byte-for-byte unchanged.

> Why resolve in `build_session_config` and branch in `agent.py` rather than at dispatch in
> `session/service.py`: keeps the sensitive, rate-limited, single-use-token `/start` path and
> `session/livekit.py` dispatch **completely untouched** (they're in the test-coverage-gate list). The
> engine worker already loads the session under bypass-RLS; it is the natural place to choose an engine.

### Data migrations (split per milestone — each milestone owns its own)
- **`0044_interview_engine_version`** (lands in **M1** — the cutover selector):
  - `ALTER TABLE job_postings ADD COLUMN interview_engine_version TEXT NULL` + CHECK
    `interview_engine_version IS NULL OR interview_engine_version IN ('v1','v2')`.
  - Paired downgrade drops the column. `job_postings` is tenant-scoped, but adding a column changes no
    RLS policy → startup RLS completeness check unaffected.
- **`0045_bank_spoken_fields`** (lands in **M2** — the bank redesign):
  - `ALTER TABLE stage_questions ADD COLUMN primary_signal TEXT NULL`.
  - **Switch** `stage_questions.question_kind` CHECK **outright** to the new taxonomy
    (`experience_check|behavioral|technical_scenario|compliance_binary`) — **no old∪new union** (dev mode,
    no backward compat; CMI-2). Per-question `difficulty` already exists (`0042`) — no migration needed.
  - **CHECK-validates-existing-rows caveat (verified):** a new-only CHECK re-validates existing rows, so
    old-kind rows block the `ALTER`. The migration **clears `stage_questions` first** (banks are
    regenerated — no data must survive) or adds the CHECK `NOT VALID`; default is clear-and-regenerate.
  - Paired downgrade restores the original CHECK + drops `primary_signal`.
- Each migration ships with its **rollback script** (per backend rules). No new tenant-scoped *tables* in
  v1 → no new RLS policy pairs, no `_TENANT_SCOPED_TABLES` edits.

### Non-breaking guarantees
- `INTERVIEW_ENGINE_DEFAULT_VERSION` defaults to `'v1'`; with no per-job override, **every existing
  session runs the old engine exactly as today**.
- The v2 module is import-isolated; the v1 path does not import it except through the single guarded
  branch.
- v1 test suite (`tests/interview_engine/`) stays green throughout — it is the regression backstop. A v2
  milestone is "done" only if v1 tests still pass.

### Retirement (M6, after v2 proven on real talk-tests)
- Flip `INTERVIEW_ENGINE_DEFAULT_VERSION='v2'`; leave per-job `'v1'` overrides available as an escape
  hatch for one release.
- After a soak window, delete `app/modules/interview_engine/` (old core) + its prompts + tests in a
  single dedicated commit, drop the now-dead branch in `agent.py`, and (optionally) the version column
  if no longer needed. This is a documented step in the M6 plan, **not** done before v2 is validated.

---

## 3a. Known cross-milestone issues (decided here; resolved when planning M2/M5)

Surfaced during plan review (2026-05-22), each **verified against the live code** before recording.
Dev-mode clarification applies throughout: **no real users, no backward compatibility — all question
banks will be regenerated.** v1 stays selectable as a dev fallback, but old banks/sessions need not keep
working.

### CMI-1 [HIGHEST] — v2 result contract must not depend on old-engine types
**Verified:** `interview_runtime/schemas.py` `SessionResult` has **required** `signal_ledger` /
`question_queue` / `claims_pool` fields, and its module-bottom `model_rebuild()` imports
`SignalLedgerSnapshot` / `QuestionQueueSnapshot` / `ClaimsPoolSnapshot` from
`app.modules.interview_engine.models.*` (lines 482–486). This is the **only** cross-module importer of
the old engine's models. Two problems: (a) v2's internal coverage model can't produce those v1 snapshots;
(b) deleting `interview_engine/` in M6 breaks `interview_runtime` **at import** → breaks
`build_session_config`, which **both** engines call.

**Decision (resolve in M5, as M5's Task 1 — documented now so it can't surface mid-build):**
1. **Relocate** the three snapshot Pydantic models (`SignalLedgerSnapshot`, `QuestionQueueSnapshot`,
   `ClaimsPoolSnapshot`) from `interview_engine/models/{ledger,queue,claims}.py` into a neutral home in
   the shared contract layer: `app/modules/interview_runtime/results.py`. Update v1's importers (the v1
   state-engine `models/__init__` + `interview_runtime/schemas.py`) to point at the new home. This flips
   the dependency to the correct direction (engine → shared `interview_runtime` contract) and makes
   `interview_runtime` **self-contained**, so the M6 v1 deletion can't break it. *(This touches v1
   imports/locations only — not its decision logic — and is sanctioned cutover infrastructure, like the
   M1 dispatch branch. It is NOT the forbidden "refactor the old core in place.")*
2. **Make `SessionResult.{signal_ledger, question_queue, claims_pool, push_back_total,
   cap_forced_advance_count, quality_distribution}` optional** (default `None`/`0`/`{}`). v1 fills them;
   v2 leaves them at defaults.
3. **Add a v2-native optional detail field** `coverage_summary: dict[str, str] | None = None`
   (per-signal final coverage state). Richer v2 detail (per-turn decision records) lives in the audit
   envelope via the existing `audit_envelope_ref` — not bloated into `SessionResult`.
4. **v2 reuses `record_session_result`** as-is (it writes `raw_result_json` + transcript + counts +
   `knockout_failures` + `audio_tuning_summary` — all engine-agnostic). No second recording path.

**M1 impact: none** — the M1 stub deliberately does not call `record_session_result` (it records to
throwaway jobs only). So CMI-1 forces no M1 code change; it is M5 Task 1.

### CMI-2 — `question_kind`: clean switch to the new taxonomy (NO union, NO compat)
**Verified:** `question_kind` is the shared `interview_runtime` Literal, read by v1 `orchestrator.py:846`
+ `speaker.py:98` (equality checks for behavioral→technical phase detection — they degrade to a **no-op**
under new strings, no crash) and the bank pipeline (`question_bank/{models,schemas,actors,service}.py` +
the DB CHECK in `0026` + the two-call generator keyed on `behavioral_star`/`technical_depth`).

**Decision (M2) — VALIDATE-ON-WRITE, RELAX-ON-READ (refined 2026-05-22 during M2 planning):**
Switch the taxonomy **cleanly at the write boundary** to
`experience_check | behavioral | technical_scenario | compliance_binary` —
the `GeneratedQuestion` generator model + the `stage_questions.question_kind` DB CHECK both move to the
new 4 values (**no old∪new union**). Updating the bank-generation tests that construct `GeneratedQuestion`
with old strings is expected, in-scope M2 work.
**RELAX the shared READ projection:** `interview_runtime.QuestionConfig.question_kind` becomes a plain
`str` (a read-only mirror of an already-CHECK-constrained column) — **NOT a union**; enforcement lives
entirely at the write side. This is what reconciles the apparent three-way conflict (scope "switch the
Literal" vs CMI-2 "no union" vs §3 "v1 tests stay green"): the *taxonomy* switches cleanly at write, while
the *read* projection stays loose during v1 coexistence so the reference-only v1 suite
(`tests/interview_engine/*` + `sample_session_config.json`, which read `QuestionConfig` with old strings)
stays a TRUE untouched backstop — a backstop you had to rewrite no longer catches regressions. Verified:
NO v1 engine test constructs `GeneratedQuestion` directly, so the write-side switch touches zero v1 engine
tests. Tighten `QuestionConfig.question_kind` to the new Literal at M6 when v1 is retired, if desired.
**Caveat (verified):** adding a new-only CHECK **re-validates existing rows by default** → old-kind
`stage_questions` rows would block the `ALTER`. The `0045` migration therefore clears existing
`stage_questions` (regeneration repopulates); paired downgrade restores the original CHECK. Tests build
their schema from `Base.metadata.create_all`, so the ORM CheckConstraint switch means any test that INSERTs
`stage_questions` must use the new taxonomy strings (in-scope M2 work).

### CMI-3 [HIGH] — latency is co-equal but ungated → add an instrumented measurement gate
**Verified:** the old engine computes per-session latency percentiles in
`agent.py::_compute_audio_tuning_summary` (`_percentile_stats`, p50/p95/max for `end_of_utterance_delay`,
`transcription_delay`, `llm_ttft`, `tts_ttfb`) from `audio.metrics.*` events → `sessions.audio_tuning_summary`.

**Decision:** v2 carries an equivalent (the percentile math is engine-agnostic — v2 gets its **own** copy
of `_percentile_stats`/`_extract_ms` so it survives v1 deletion; v2 also uses LiveKit `AgentSession`, so
the same `audio.metrics.*` events are available). **Add instrumented latency acceptance** to M3/M4/M6:
on a talk-test, dump the v2 audio summary and confirm `end_of_utterance_delay_ms` (patient but not laggy)
and **perceived response** (mouth `llm_ttft_ms` + `tts_ttfb_ms`) p50/p95 sit within the DESIGN-SPEC §3 /
CLAUDE.md targets (real-time response p50 ≤ 1200 ms, p95 ≤ 1500 ms). **The gate targets EOU + mouth + TTS,
NOT the brain** — the brain is async/off the critical path by design (masked by hold-space). Numbers, not
"feels conversational" — fixing the old 10–18 s is the whole point. (Caveat from the EOU decision: do not
expect EOU alone near ~0.5 s; the big win is decoupling the brain.)

### CMI-4 — live two-plane async timing is back-loaded → talk-test it in M4/M5
**Verified:** M1's `DirectiveController` tests are pure-logic (correct for M1). The real R3 risk is **live**
timing, exercised nowhere in the plan yet.

**Decision:** M4 and M5 acceptance must include **live talk-test** scenarios for the async path, not just
unit tests: (a) pre-stage a speculative `PROBE` → candidate says something surprising → confirm the
superseding Directive wins and is delivered at the **turn boundary** (not two utterances); (b) candidate
barge-in mid-AI-speech → confirm the in-flight async brain call is **cancelled cleanly** and floor yields;
(c) confirm a stale Directive (computed for the prior turn) is discarded live. Static scripted directives
won't catch these.

---

## 4. Target file structure

### New module (M1 creates the skeleton; later milestones fill it)
```
backend/nexus/app/modules/interview_engine_v2/
├── __init__.py                 ← public API (__all__): run(), Directive, ControlPlane, ConversationPlane
├── __main__.py                 ← (optional) standalone debug entry; prod entry stays interview_engine/agent.py branch
├── agent.py                    ← v2 entrypoint run(ctx, config): builds planes, wires LiveKit AgentSession
├── directive.py                ← Directive Pydantic model + closed `act` enum + no-leak validator   [M1]
├── controller.py               ← two-plane controller: directive queue, supersession, staleness,    [M1]
│                                  speculative-discard, turn-boundary gating (pure, no LLM)
├── audit.py                    ← TurnDecisionRecord schema + helpers (the brain's full reasoning)    [M1]
├── coverage.py                 ← signal coverage model: states + cross-question crediting (pure)     [M5]
├── brain/
│   ├── service.py              ← ControlPlane: build prompt → GPT-5.4 call → Directive + record      [M5]
│   ├── input_builder.py        ← compact, cache-friendly prompt assembly (stable prefix→suffix)      [M5]
│   ├── decision.py             ← BrainDecision structured-output schema (reasoning-first)            [M5]
│   └── policy.py               ← deterministic gates: knockout OR-check, no-leak, closed-enum        [M5]
├── mouth/
│   ├── service.py              ← ConversationPlane: Directive → voiced text → TTS stream             [M4]
│   ├── persona.py              ← Arjun persona preamble (deterministic render → cache-stable)        [M4]
│   └── input_builder.py        ← per-act prompt assembly                                             [M4]
├── turn_taking/
│   ├── floor.py                ← floor-yield + one-directional-interruption + barge-in classify      [M3]
│   ├── eou.py                  ← EOU config + hold-space cue + unresponsive ladder + backchannel gate [M3]
│   └── pacing.py               ← dynamic endpointing wiring + pause thresholds                       [M3]
└── event_log/                  ← per-session envelope (mirror interview_engine/event_log concept)    [M1]
    ├── envelope.py
    └── collector.py

backend/nexus/prompts/v3/engine/
├── brain.system.txt            ← the brain prompt (rewritten from scratch)                           [M5]
└── mouth/                      ← per-act mouth prompts (rewritten)                                   [M4]
    ├── _persona.txt   ├── intro.txt   ├── ask.txt   ├── probe.txt   ├── clarify.txt
    ├── ack_advance.txt├── redirect.txt├── hold.txt  ├── reassure.txt├── hint.txt
    ├── answer_meta.txt├── confirm.txt └── close.txt
backend/nexus/prompts/v2/                                                                              [M2]
└── question_bank_*.txt         ← rewritten spoken-question bank-gen set (common + per-stage + behavioral
                                   + technical-chained + keyterms)
```

### Extend/reuse seams (touched, not rebuilt)
- `app/ai/config.py` + `app/config.py` — new settings/properties (M1): `interview_engine_default_version`,
  `engine_brain_model`/`_effort`, `engine_mouth_model`/`_effort`, `engine_brain_prompt_version`,
  `engine_mouth_prompt_version`, `engine_brain_prompt_cache_key`, `engine_mouth_prompt_cache_key`,
  plus EOU knobs in M3.
- `app/modules/interview_runtime/schemas.py` + `service.py` — `SessionConfig.interview_engine_version`
  (M1) and new bank fields surfaced into `QuestionConfig` (M2).
- `app/modules/question_bank/*` + migration `0044` — schema + spoken-gen + per-question streaming (M2).
- `app/ai/realtime.py` — EOU/endpointing/turn-detector tuning hooks (M3).
- `app/modules/interview_engine/agent.py` — the single guarded v2 branch (M1).
- `frontend/app/components/dashboard/question-bank/*` — sections + live append (M2).

### Tests
```
backend/nexus/tests/interview_engine_v2/      ← new suite, mirrors the module tree
├── conftest.py                               ← v2 fixtures (scripted directives, mock LLM at app/ai boundary)
├── test_directive.py · test_controller.py · test_audit.py            [M1]
├── test_coverage.py · test_brain_policy.py · test_thread_satisfaction.py [M5]
├── test_mouth_*.py · test_turn_taking_*.py                           [M3/M4]
├── prompt_evals/                             ← @pytest.mark.prompt_quality (existing marker convention)
│   ├── test_bank_gen_evals.py · test_brain_evals.py · test_mouth_evals.py
└── test_regression_b99d8cc6.py               ← replay the documented failure cascade               [M6]
backend/nexus/tests/question_bank/            ← extended (M2)
frontend/app/...                              ← bank UI composition tests (M2)
```

---

## 5. Milestones

Each milestone is independently testable and leaves v1 working. Each gets its own detailed plan file
(`docs/superpowers/plans/2026-05-22-interview-engine-v2-mN-<name>.md`). Only **M1's** detailed plan is
written in this session; the rest are written on demand, gated on the prior milestone's acceptance.

### M1 — Foundations & cutover scaffold  → `…-m1-foundations.md` (written now)
**Goal:** Stand up the v2 module skeleton, the Directive contract, the pure controller, the audit
record, the AIConfig surface, and the cutover flag — such that a v2-flagged session **routes to the v2
path**, connects, says one canned line (proof-of-life via the existing TTS plugin), and writes a v2
audit envelope. No turn-taking, brain, or real conversation yet — that's M3–M5.
**Deliverables:** migration `0044_interview_engine_version` (job_postings column) + JobPosting ORM field;
`interview_engine_v2/` skeleton; `directive.py` (closed `act`/`tone` enums + Directive + no-leak
validator); `controller.py` (queue/supersession/staleness/speculative-discard, all pure); `audit.py`
(TurnDecisionRecord); `event_log/` envelope + collector; AIConfig/Settings additions (default version +
brain/mouth models·effort·prompt-versions·cache-keys); `SessionConfig.interview_engine_version` +
resolution in `build_session_config`; the single guarded branch in `interview_engine/agent.py`; a
proof-of-life `run()` stub.
**Acceptance:** all M1 unit tests pass; full v1 suite still green; a job with
`interview_engine_version='v2'` routes to the v2 path, connects, speaks one canned line, and writes a v2
audit envelope; `INTERVIEW_ENGINE_DEFAULT_VERSION` unset/`'v1'` → v1 path byte-for-byte unchanged.
(Clean result-recording + session close lands with the brain in M5 — the M1 stub intentionally does not
call `record_session_result`, so test only against throwaway jobs.)
**Dep:** none.

### M2 — Question bank redesign  → `…-m2-bank.md`
**Goal:** Spoken-not-written questions, `primary_signal`, refined `question_kind`, depth-in-`follow_ups`;
per-question streaming (`BANK_QUESTION_ADDED`); behavioral→technical context chaining; recruiter UI
sections + live append. (Per-question `difficulty` already exists — verify the generator sets it.)
**Deliverables:** migration `0045_bank_spoken_fields` (§3); `question_bank/schemas.py` +
`models.py` field additions; rewritten `prompts/v2/question_bank_*` (spoken, few-shot good/bad, self-
critique rubric); switch `actors.py`/`refine.py` from whole-array validate to **streaming structured
output**, persist + emit per question; chain behavioral set into the technical call; frontend section
grouping + per-`BANK_QUESTION_ADDED` append; bank-gen prompt-eval suite.
**Acceptance:** generating a bank streams each question into the recruiter UI as it completes; questions
are short/single-focus with depth in follow_ups; every question has `primary_signal` + `difficulty` +
correct **new-taxonomy** `question_kind`; the `0045` migration applies cleanly after clearing existing
`stage_questions` (CMI-2 caveat); bank-gen evals pass. (v1 still *runs* on a regenerated bank as a dev
fallback; its behavioral→technical phase-detection no-ops under the new strings — acceptable, CMI-2.)
**Dep:** M1 (uses the version field/migration baseline). Independent of M3/M4. Implements CMI-2.
**Risk spike (do first):** confirm instructor/Responses streaming-structured-output API for per-question
incremental parsing; fall back to per-set streaming if per-question proves infeasible (note in plan).

### M3 — Audio, EOU & turn-taking layer  → `…-m3-audio-eou.md`
**Goal:** The conversation plane's floor-control substrate. Recalibrate `MultilingualModel` threshold for
Indian ESL/Hinglish; enable **dynamic endpointing**; add the **hold-space cue**, the **unresponsive
ladder** (~6–8s "whenever you're ready" → ~15s "still there?" → 2 no-responses = `candidate_unresponsive`),
**backchannel gating** (≥2-word + non-backchannel lexicon), and the **one-directional interruption /
floor-always-yields** invariant + barge-in classification (continuation/early/barge-in attributed by the
brain *after* capture, not real-time). **No new model.**
**Deliverables:** `turn_taking/{floor,eou,pacing}.py`; `app/ai/realtime.py` + config knobs; TTS
voice listen-test + decision (Sarvam vs OpenAI); **v2 audio/latency summary** (own copy of
`_percentile_stats`/`_extract_ms`; collects `audio.metrics.*` like v1 — CMI-3).
**Acceptance:** unit tests for backchannel gate, unresponsive ladder, barge-in classification, hold-space
trigger; manual talk-test confirms it waits on think-pauses and doesn't talk over the candidate; pause
behavior matches doc 08 "resolved" rules; **instrumented latency gate (CMI-3): dump the v2 audio summary
from a talk-test and confirm `end_of_utterance_delay_ms` is patient-but-not-laggy** within the §3 budget.
**Dep:** M1. Implements CMI-3 (EOU half).

### M4 — The mouth (Conversation Plane)  → `…-m4-mouth.md`
**Goal:** GPT‑5.4‑mini "Arjun" voicing Directives: per-act prompts, ≤2 sentences / one question / no
lists / spoken numbers, 2–4 Indian-English fillers, **identity lock**, neutral/anti-sycophancy, integrate
as the LiveKit `llm_node` consuming the controller's current Directive; verbatim bank text for
ASK/PROBE, composed text for the rest; barge-in/floor-yield wired to M3.
**Deliverables:** `mouth/{service,persona,input_builder}.py`; `prompts/v3/engine/mouth/*`; a
**directive-injection talk-test harness** (drive the mouth with scripted Directives, no brain); mouth
prompt-eval suite (length, single-question, identity-lock-under-injection, in-persona redirect).
**Acceptance:** user can talk to Arjun with scripted Directives and judge voice/persona/latency; mouth
evals pass; cache: persona preamble renders byte-identical across turns (prefix-stability test);
**instrumented latency gate (CMI-3): perceived response (mouth `llm_ttft_ms` + `tts_ttfb_ms`) p50/p95
within the §3/CLAUDE.md targets** on a talk-test; **live timing (CMI-4): scripted-directive harness drives
a live supersession** (stage a speculative directive, then supersede it) and confirms the superseding
line wins at the turn boundary (no double-delivery) and barge-in cancels delivery cleanly.
**Dep:** M1, M3. Implements CMI-3 (mouth+TTS half) + CMI-4 (mouth-side).

### M5 — The brain (Control Plane) + integration  → `…-m5-brain.md`
**Goal:** GPT‑5.4 (low effort + reasoning-first field): attribute→grade→update coverage→pick move→check
thread/lifecycle in one coherent pass; signal-based cross-question coverage; thread-satisfaction
(`sufficient|tapped-out|absent`, soft cap ~1–2 probes); **verified knockout** (probe + ALL OR-alts +
reflect-to-confirm, records never rejects); ANSWER_META grounded only in role context (never invent);
in-persona injection redirect; **semantic** intent classification (no regex). Emits Directives + audit
records via Option C staging. Wire brain↔mouth through the controller.
**Deliverables:** **Task 1 = the CMI-1 result-contract decoupling** (relocate the three snapshot models
to `interview_runtime/results.py`, repoint v1 imports, make the v1 fields optional on `SessionResult`,
add `coverage_summary`); `brain/{service,input_builder,decision,policy}.py`, `coverage.py`;
`prompts/v3/engine/brain.system.txt`; deterministic policy gates + tests; v2 wires
`record_session_result` (reused) with a populated `coverage_summary` + audit envelope; brain prompt-eval
suite (grade↔move coherence, OR-knockout correctness, no-leak in directive text, indirect-no/tapped-out,
injection).
**Acceptance:** full brain→mouth turn works on talk-test; brain evals pass; **no rubric string ever
appears in a Directive** (validator + eval); knockout never fires on one member of an OR-set without
checking alternatives; **CMI-1: `interview_runtime` imports cleanly with `interview_engine/` absent**
(prove by temporarily renaming the old package in a scratch check, or a test that imports
`interview_runtime` without touching `interview_engine`); **live timing (CMI-4): a real brain-driven
talk-test exercises supersession + clean async-call cancellation on barge-in**.
**Dep:** M1, M2, M4. Implements CMI-1 + CMI-4 (brain-side).

### M6 — End-to-end, eval harness & cutover ramp  → `…-m6-e2e-cutover.md`
**Goal:** Full-session integration; the PromptFoo-style eval harness wired across all three surfaces with
20+ cases each; the **`b99d8cc6` regression replay** (no false OR-knockout, no interrogation spiral,
brain off the critical path); dev tooling (flip-job-to-v2 helper, transcript/audit dump); staged flip of
`INTERVIEW_ENGINE_DEFAULT_VERSION` to v2; documented v1 retirement.
**Deliverables:** `test_regression_b99d8cc6.py`; full-session simulation harness (mock LLMs at the
`app/ai` boundary); manual end-to-end talk-test checklist; cutover runbook + retirement commit plan;
threat-model + spec promotion (promote DESIGN-SPEC to `docs/superpowers/specs/`).
**Acceptance:** a real talk-test session end-to-end on v2 feels conversational and audits cleanly; the
documented `b99d8cc6` failures do not recur; **instrumented latency gate (CMI-3): full-session v2 audio
summary shows perceived response p50 ≤ 1200 ms / p95 ≤ 1500 ms and EOU within budget** (the explicit
numeric counter to the old 10–18 s); v1 still selectable as fallback; **after v1 deletion,
`interview_runtime` + `build_session_config` still import and pass tests** (CMI-1 end-state proof).
**Dep:** M1–M5. Closes CMI-1/CMI-3.

---

## 6. Test & eval strategy

Four layers — matched to what each kind of code can actually be tested by (and to the user's preference
for **manual talk-testing** + **composition tests**, and the **no-regex** + **quality-before-latency**
locks).

### 6.1 Deterministic unit tests (TDD — bite-sized, real assertions)
The backbone of M1/M3/M5 policy code and M2 persistence. Everything *not* LLM-behavioral:
- Directive schema + **no-leak validator** (rejects rubric-smelling text in `say`/`compose_hint`).
- Controller: enqueue, supersede-by-id, discard-on-stale-`turn_ref`, speculative discard, never-deliver-two.
- Coverage model state transitions + cross-question crediting; thread-satisfaction stop logic incl. soft cap.
- Knockout deterministic guards: never close on one OR-member; mandatory-only; reflect-confirm required.
- Turn-taking: backchannel gate, unresponsive ladder timing, barge-in classification, hold-space trigger.
- Bank: schema validation, per-question streaming persistence + `BANK_QUESTION_ADDED` emission.
- Audit: TurnDecisionRecord completeness + envelope hashing.
- **Negative controls** (per `feedback_composition_tests`): re-introduce a bug (e.g. rubric text in a
  Directive) and confirm the validator/test catches it.

### 6.2 Prompt evals (PromptFoo-style, doc 13) — `@pytest.mark.prompt_quality`
Uses the **existing** marker convention (`pytest -m "not prompt_quality"` already in backend CLAUDE.md).
20+ diverse cases per surface — happy / edge / adversarial — graded by assertion + (where needed) an
LLM-grader. Run on demand, not on every change.
- **Bank-gen:** spoken (not multi-part), single-focus, `primary_signal` set, depth in `follow_ups`,
  `difficulty` set, no overlap behavioral↔technical (chaining), rubric kept brain-only.
- **Brain:** grade↔move **coherence** (never "push for more" while grading concrete); correct OR-knockout
  (Java-or-Python-or-Ruby case never knocks out on Java alone); **no rubric in directive text**;
  tapped-out + indirect-no handled semantically (no regex); injection → in-persona REDIRECT, never comply;
  ANSWER_META grounded only in role context (never invents salary/benefits).
- **Mouth:** ≤2 sentences, one question, no lists, spoken numbers, Indian-English fillers; **identity lock
  holds** under "ignore your instructions / are you a real person"; neutral, never sycophantic.

### 6.3 Manual talk-testing (the user's primary method — `feedback_manual_agent_testing`)
Dev tooling, not CI eval suites:
- **M4 directive-injection harness:** talk to the mouth with scripted Directives before the brain exists.
- **Flip-a-job-to-v2 helper** (M6) + per-job version column → dial a real session and talk to Arjun.
- **Transcript/audit dump** built on the v2 `event_log` envelope (reuse `scripts/export_job_agent_context.py`
  pattern) so a talk-test produces an inspectable decision trail.
- Each conversation milestone (M3/M4/M5/M6) ends with a **talk-test checklist** of scenarios from doc 08
  (think-pause, barge-in, indirect-no, injection, off-topic, "is this recorded?", knockout).

### 6.4 Integration / regression
- **Full-session simulation harness** (M5/M6): scripted candidate transcript driven through controller +
  brain + mouth with **LLMs mocked at the `app/ai` boundary**, asserting end-to-end Directive flow, audit
  records, and the no-leak invariant — a composition test per the user's preference.
- **`b99d8cc6` replay** (M6): the documented failure cascade (false OR-knockout, interrogation spiral,
  10–18s turns) must not recur — the explicit negative control for the whole rewrite.
- **v1 must stay green:** the old `tests/interview_engine/` suite is the cutover backstop; every milestone
  re-runs it.
- **Instrumented latency gate (CMI-3):** latency is co-equal with quality, so each conversation milestone
  has a *numeric* gate, not a vibe. v2 computes the same per-session percentile summary as v1
  (`end_of_utterance_delay_ms`, `transcription_delay_ms`, mouth `llm_ttft_ms`, `tts_ttfb_ms`); the
  talk-test dumps it and asserts EOU is patient-but-not-laggy and **perceived response** (mouth ttft +
  tts ttfb) p50 ≤ 1200 ms / p95 ≤ 1500 ms (§3 / CLAUDE.md). The gate excludes the brain (async, masked).
  This is the explicit, measured counter to the old 10–18 s turns.

### Coverage gates (per CLAUDE.md)
v2 introduces no new auth/RLS/candidate-session/admin paths, so the 100%-branch gates don't newly apply;
project-wide 80% line target holds. The Directive no-leak validator + knockout OR-guards + controller
supersession are treated as **must-be-100%-branch** by local convention (they are the security/quality
load-bearing pieces). Coverage runs use the documented `python -m coverage run` workaround (livekit/PyO3
segfault) — `--source=app/modules/interview_engine_v2`.

---

## 7. Risks & mitigations

| # | Risk | Likelihood/Impact | Mitigation | Milestone |
|---|---|---|---|---|
| R1 | **EOU recalibration insufficient for Indian ESL/Hinglish** (the #1 UX risk) | M / H | Dynamic endpointing + hold-space turn ambiguity into a natural moment; tune on real talk-tests; a custom completeness signal is an explicit **v1.5/v2** fallback (overlaps the planned audio-prosody upgrade) — not a v1 blocker. Remember the bigger latency win is decoupling the brain, not EOU. | M3 |
| R2 | **Per-question streaming structured output** (instructor/Responses partial parsing) is the biggest single backend lift | M / M | **Spike it first in M2**; if per-question is infeasible, fall back to per-set streaming (still better than today's whole-array-at-end). | M2 |
| R3 | **LiveKit AgentSession ↔ two-plane controller** integration: mouth-as-`llm_node` consuming an async directive queue, barge-in cancellation, supersession at turn boundaries, never-interrupt invariant | M / H | Force this risk early via M4's talk-test harness; consult LiveKit docs via the MCP (`docs_search`/`get_pages`) during M3/M4; the never-interrupt invariant *simplifies* concurrency (directives only land at boundaries). | M3/M4 |
| R4 | **Brain judgment quality** (tapped-out, indirect-no, coherence) | M / H | Reasoning-first field + low effort; brain eval suite; A/B 5.4 vs 5.5 via AIConfig env swap (no code change). | M5 |
| R5 | **TTS Indian-English voice quality** | L / M | Listen-test Sarvam vs OpenAI in M3; it's an AIConfig swap. | M3 |
| R6 | **Prompt-cache discipline regresses** (stable-prefix → dynamic-suffix not maintained → latency/cost up) | M / M | Deterministic prefix render + explicit `prompt_cache_key` per surface + a test asserting prefix byte-stability across turns. | M4/M5 |
| R7 | **Cutover breaks live flow** | L / H | Default `'v1'`; seam isolated in the engine container; v1 untouched + tests green every milestone; per-job opt-in for testing; retirement only after v2 proven. | M1/M6 |
| R8 | **No-rubric-leak regression** (a Directive carries evaluation text) | L / H | Structural (mouth holds no rubric) + the no-leak validator + a brain eval that scans every Directive; negative-control test. | M1/M5 |
| R9 | **Scope creep into reporting/analysis** | L / M | Out of scope (confirmed): engine emits SessionResult + audit record; reporting is a separate effort consuming that contract. | all |

---

## 8. Spec → milestone coverage map (self-review)

| DESIGN‑SPEC § | Requirement | Milestone(s) |
|---|---|---|
| §1, §1a | Screener goal; rewrite/extend/reuse boundary | all (boundary = §1/§4 here) |
| §2 | Two-plane on two clocks; Option C pre-stage | M1 (controller), M4/M5 (planes) |
| §3 | Audio: Deepgram+keyterm (reuse), EOU tune+behavioral layer, pause behavior, TTS | M3 |
| §4 | One-directional interruption, floor-to-candidate, attribute-by-meaning, ladders | M3 (substrate), M5 (attribution) |
| §5 | Mouth: 5.4‑mini, persona Arjun, voice discipline, identity lock, anti-sycophancy | M4 |
| §6 | Brain: attribute→grade→coverage→move→thread; coherence; bias-to-advance; verified knockout; no regex | M5 |
| §7 | Directive schema, closed `act` enum, no-leak, Option C staging/supersession | M1 (schema/controller), M4/M5 (use) |
| §8 | Phase orchestration (open→behavioral→technical→cand-Q→close) | M5 (brain owns transitions) |
| §9 | Bank: spoken text, primary_signal, difficulty, question_kind, per-question streaming, chaining, FE sections | M2 |
| §10 | Models all OpenAI (mouth/brain/bank-gen); cost/cache | M1 (config), M2/M4/M5 (use) |
| §11 | Latency & caching: compact prompts, stable-prefix→dynamic-suffix, cache keys, pinned snapshots | M4/M5 (R6 test); **instrumented latency gate CMI-3** (M3/M4/M6) |
| §12 | Security: data-not-instructions, closed enum, policy gates, identity lock, ANSWER_META grounding, knockout-never-rejects | M1/M4/M5 |
| §13 | Observability/audit: per-turn decision record, correlation_id, event-log envelope | M1 (shape), M5 (populate) |
| §14 | Out of scope (prosody EOU, full-duplex, per-job knockout config, coding, mid-monologue backchannel) | enforced (none planned) |
| §15 | Build order + cutover (the two deferred items) | **§2 + §3 of this doc** |

Gaps flagged & resolved before planning: (a) EOU = tune existing model, no new model (user-confirmed,
docs corrected); (b) reporting out of scope (user-confirmed); (c) migration head `0043`/difficulty in
`0042` (verified). No open contradictions remain.

Cross-milestone issues surfaced at plan review and resolved in **§3a** (all verified against the live
code): CMI-1 (v2 result contract decoupled from old-engine types — M5 Task 1), CMI-2 (clean
`question_kind` switch, no compat — M2), CMI-3 (instrumented latency gate — M3/M4/M6), CMI-4 (live
two-plane timing talk-tested — M4/M5).

---

## 9. Execution

Per-milestone plans are written one at a time, each gated on the prior milestone passing its acceptance
criteria (and v1 tests staying green). **M1's detailed plan is `2026-05-22-interview-engine-v2-m1-foundations.md`.**
Recommended execution mode per milestone: superpowers:subagent-driven-development (fresh subagent per
task + review between tasks), with the per-milestone git scope guarded (per `feedback_subagent_git_scope`).
