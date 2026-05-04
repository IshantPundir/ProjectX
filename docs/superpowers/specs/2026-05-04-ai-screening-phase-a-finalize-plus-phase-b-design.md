# AI Screening Agent — Phase A finalize + Phase B (StructuredInterviewAgent end-to-end)

**Status:** Draft v1
**Owner:** Engineering (solo)
**Date:** 2026-05-04

---

## Preamble

This spec is the actionable contract for the **next implementation session** on the AI Screening Agent. It is narrower than the upstream sources:

- **Authoritative design source:** `docs/ai-screening-agent/ai-screening-agent-design.md` — the *what* and the *why*. Coverage primitive, prompt design, edge cases, adversarial checklist.
- **Authoritative implementation source:** `docs/ai-screening-agent/ai-screening-agent-implementation.md` — the *where* and the *how*. Codebase orientation, phased build plan, file layout, vendor SDK rules.

**Conflict-resolution rule:** Where this spec disagrees with either source doc, **this spec wins for Phase A tail + Phase B**; the source docs win for everything else (Phases C–J, scoring philosophy, design principles, adversarial coverage). Any divergence here from the source docs is documented inline with a one-line rationale so future readers can see the deliberate departure rather than wondering about drift.

This spec covers **two scopes in one session**:
1. The remainder of Phase A (a small tail — most of Phase A has already shipped).
2. Phase B end-to-end: replace `GenericInterviewAgent` with `StructuredInterviewAgent`, wire the deterministic state machine, drive every utterance via hardcoded English strings through `await session.say(...)`, ship integration coverage.

Phase B uses **deliberately throwaway hardcoded utterances**. LLM-rendered Speech Agent is Phase C. The hardcoded strings are not a "fake of a later-phase feature" — they *are* the Phase B feature.

---

## 1. Verification report (what's already done in main)

Before proposing the work, the spec audit confirmed the following are already in `main` as of `2378df5`:

**Phase A — shipped:**
- `app/modules/interview_engine/orchestrator/{__init__.py, ledger.py, state.py, persistence.py}` — `SignalLedger`, `SignalState`, `EvidenceQuote`, `CoverageStatus`, `InterviewState`, `QuestionState`, `InterviewPhase`, `ExitMode`, `LedgerPersistence`, with phase-transition allowlist, append-only evidence + forward-only coverage invariants, sequence-number gap detection.
- `app/modules/interview_engine/speech/{__init__.py, safety.py}` — outcome / salary / scheduling regex categories with `matched_text_hash` audit-envelope convention.
- `app/modules/interview_engine/event_kinds.py` — every Phase A→J envelope event-kind constant declared in one registry.
- `app/modules/interview_engine/prompts/speech_agent/{intro.v1.txt, ask_question_standard.v1.txt, wrap_normal.v1.txt}` — three filled prompt templates (per design doc §7.2 / §7.3 / §7.13).
- `app/ai/prompts.py` — both `PromptLoader` and `TemplateLoader` (per-template versioning with `<base>/<role>/<name>.<version>.txt`, dev-mode mtime reload, `{{include:...}}` resolution, `sha256:<hex>` hash helper, `FileNotFoundError` on miss).
- `app/ai/config.py` — `evaluator_intent_model/effort`, `evaluator_disclaim_model/effort`, `evaluator_sufficiency_model/effort` properties with documented effort-gating contract.
- `app/config.py` — `evaluator_intent_model="gpt-5"`, `evaluator_disclaim_model="gpt-5"`, `evaluator_sufficiency_model="gpt-5.2"`. Corresponding `EVALUATOR_*_MODEL` entries in `.env.example`.
- `app/modules/interview_runtime/schemas.py` — `SignalMetadata` Pydantic model + `SessionConfig.signal_metadata: list[SignalMetadata]` field.
- `app/modules/interview_runtime/service.py` — `_project_signal_metadata(...)` projector + empty-signal-metadata fence + `signal_metadata=` populated in `build_session_config`.
- `app/modules/interview_runtime/__init__.py` — `SignalMetadata` re-exported.
- Tests: `tests/interview_engine/orchestrator/{test_ledger.py, test_state.py, test_persistence.py}`, `tests/interview_engine/speech/test_safety.py`, `tests/interview_engine/test_event_kinds.py`, `tests/interview_runtime/test_signal_metadata_plumbing.py`.

**Phase A — open carryforward (this spec finishes):**
- No `app/modules/interview_engine/speech/templates.py` (engine-scoped `TemplateLoader` instance binding) yet — but the underlying `TemplateLoader` class already exists in `app/ai/prompts.py`.
- `LedgerPersistence` module + class docstring does not yet name `app/pubsub.py::_get_client()` as the canonical client source.
- No prompt template stubs for the remaining ~11 templates — **and per the prior Phase A close-out's carryforward #8 the discipline is to NOT create stubs**: `TemplateLoader.get(...)` raises `FileNotFoundError` cleanly when a template is missing, so a Phase B/C/E/G/H/I caller can't accidentally rely on a missing stub. Empty stubs add noise without safety. Templates are filled in the phase that consumes them. This spec preserves that discipline.

**Phase B — not started:**
- `agent.py` still instantiates `GenericInterviewAgent`; no `StructuredInterviewAgent`, no `flow.py`, no orchestration loop. `AgentSession` still has `preemptive_generation={"enabled": True}` (the GenericInterviewAgent expects autogen).

---

## 2. Scope statement

### 2.1 In scope (this spec)

**Phase A tail:**
- Engine-scoped `TemplateLoader` instance binding.
- `LedgerPersistence` docstring update naming `app/pubsub.py::_get_client()` as the canonical client source.

**Phase B execution:**
- Replace `GenericInterviewAgent` with `StructuredInterviewAgent`.
- The three-layer guardrail from impl doc §4: `llm_node` override + `preemptive_generation={"enabled": False}` + inert system prompt.
- Single-utterance entry point: `StructuredInterviewAgent._say(text)` is the only call site for `session.say(...)` in `app/modules/interview_engine/`. Enforced by AST invariant test.
- Linear orchestration loop: walk `config.stage.questions` in position order, ask each as `mode="standard"`, advance unconditionally on the candidate's first transcribed utterance. No conditionals.
- All utterances are hardcoded English strings with simple `{name}` / `{role}` / `{minutes}` / `{question_text}` placeholder substitution. Lives in `_phase_b_utterances.py` with an explicit deletion criterion in its docstring.
- `LedgerPersistence` wired into the entrypoint via `app.pubsub._get_client()` and passed into `StructuredInterviewAgent`. Redis writeback fires on every phase change and at session close.
- Three exit modes wired and reachable in B: `completed`, `candidate_disconnected`, `error`. The fourth (`candidate_ended`) is wired in the close-handler mapping but **not reachable** in B — its trigger paths land in Phases H (knockout disclaim) and I (candidate-initiated end after pause-decline).

### 2.2 Out of scope (deferred to later phases)

- LLM-rendered utterances via `SpeechAgent.render(...)` (Phase C).
- Sufficiency Checker — including signal-driven question selection, follow-up budget, deepening probes, knockout-at-risk detection (Phases D / E / G).
- Intent Classifier — meta-request / off-topic / pause-request / silence routing (Phase F).
- Disclaim Classifier + confirmation turn + `KnockoutFailure` persistence + tenant-policy `record_only` vs `close_polite` branching (Phase H).
- Silence-tier policy + bounded reconnect + pause-request binary choice (Phase I).
- Hardening / adversarial pass / ledger-snapshot payload-shape spec (Phase J).
- Real-LLM `prompt_quality` tests on the three filled templates (Phase C, since Phase B doesn't render them via LLM).

### 2.3 Files NOT touched

`event_log/*`, `event_kinds.py` (already declares Phase B kinds), all of `interview_runtime/`, `app/ai/realtime.py`, `app/ai/config.py`, `app/ai/client.py`, `app/ai/prompts.py`, `tenant_settings/`, frontend, migrations, Alembic. Verified during the spec audit; no new migrations are required for any of A or B.

---

## 3. Architecture & wiring decisions for Phase B

### 3.1 The three-layer guardrail (load-bearing)

Every layer is non-optional. Removing any of the three creates a path where the realtime LLM could hallucinate speech in parallel with the orchestrator's `session.say(...)` and reach TTS.

**Layer 1 — hard guardrail.** `StructuredInterviewAgent.llm_node` is overridden to return an async generator that emits zero items:

```python
async def llm_node(self, *args, **kwargs):
    return
    yield
```

Form chosen for compatibility with `mypy --strict` + `ruff check`. Verified in spec audit:
- `pyproject.toml` has `strict = true` but no explicit `warn_unreachable = true`. Mypy's `strict` bundle does NOT include `warn_unreachable`, so the unreachable `yield` is not flagged.
- Ruff has no default rule that flags `return; yield` patterns.
- Canonical idiom in livekit-agents' own `examples/voice_agents/structured_output.py`.

If implementation surfaces any lint or type complaint, the silence is surgical and named — not blanket. Example form (only used if needed):

```python
# type: ignore[unreachable]  # async-generator contract: yield is unreachable
# but required to make this an async generator (Pattern 2 hard guardrail; see
# livekit-agents/examples/voice_agents/structured_output.py)
```

The verbose comment is deliberate. Future-me reading this in 18 months without context needs to understand it's load-bearing — not just "Claude's tidy-up tool removed a `noqa`."

**Layer 2 — defense in depth.** System prompt is the inert form: `"Wait for explicit instructions. Do not speak unless told."` Belt-and-suspenders against accidental removal of the `llm_node` override.

**Layer 3 — single utterance entry point.** Every agent utterance routes through `StructuredInterviewAgent._say(text)`:

```python
async def _say(self, text: str, *, allow_interruptions: bool = True) -> None:
    # safety check before TTS — never bypass
    safety = check_safety(text)
    if not safety.is_safe:
        # Hardcoded-string drift: vetted Phase-B strings should always pass.
        # If they don't, log every violation, emit envelope event, fall back
        # to a pre-validated static string, and continue. Never crash mid-call.
        # Phase C's retry/fallback inside SpeechAgent.render is a separate
        # concern (LLM output drift, not hardcoded-string drift).
        for v in safety.violations:
            self._collector.append(
                kind=SPEECH_SAFETY_VIOLATION,
                payload={
                    "category": v.category,
                    "pattern_name": v.pattern_name,
                    "matched_text_hash": _sha256_short(v.matched_text),
                },
                wall_ms=int(time.time() * 1000),
            )
        self._collector.append(
            kind=SPEECH_FALLBACK_USED,
            payload={"reason": "phase_b_hardcoded_safety_violation"},
            wall_ms=int(time.time() * 1000),
        )
        text = _PHASE_B_SAFETY_FALLBACK_TEXT  # imported from _phase_b_utterances
    await self.session.say(text, allow_interruptions=allow_interruptions)
```

Awaiting `session.say(...)` blocks until playback completes. The orchestrator's main loop relies on this for deterministic ordering: the candidate has heard the utterance before we wait for the next `UserInputTranscribedEvent(final=True)`. Fire-and-forget (`handle = session.say(...)` without `await`) is forbidden in the main loop.

`_PHASE_B_SAFETY_FALLBACK_TEXT` is defined at module level in `_phase_b_utterances.py` with a **module-import-time assertion** that it passes `check_safety`. If the fallback itself drifts and trips a safety rule, the import fails and the agent process cannot start — the right loudness for a load-bearing recovery path. See §4.3 for the constant + assertion.

The single-entry-point invariant is enforced by an AST test (§5.4).

### 3.2 AgentSession config delta

In `agent.py`, where `AgentSession` is built today:

| Setting | Today (GenericInterviewAgent) | Phase B (StructuredInterviewAgent) |
|---|---|---|
| `preemptive_generation` | `{"enabled": True}` | `{"enabled": False}` |
| `interruption.mode` | `"vad"` | `"vad"` (unchanged; LiveKit-Cloud-only adaptive interruption out of scope per CLAUDE.md Phase 6 rollback) |
| `endpointing` | dynamic, current min/max delays from settings | unchanged |
| `turn_handling.turn_detection` | `build_turn_detector()` | unchanged |
| `vad` | `ctx.proc.userdata["vad"]` | unchanged |

`preemptive_generation={"enabled": False}` is kept even though `llm_node` would short-circuit the autogen path before it fires — clarity matters more than minimalism in a load-bearing guardrail block.

### 3.3 Orchestration loop (Phase B linear scope)

Phase B's orchestrator implements:
- Linear question progression in `config.stage.questions` position order.
- Always `asked_mode="standard"` — no deepening (Phase G).
- Every candidate transcribed utterance is treated as substantive — no Intent Classifier (Phase F).
- No follow-ups — no Sufficiency Checker (Phase D/E).
- No knockout detection — no Disclaim Classifier (Phase H).
- No silence handling beyond LiveKit's defaults — no silence-tier policy (Phase I).
- No reconnect protocol (Phase I).
- Three reachable exit modes: `completed`, `candidate_disconnected`, `error`.
- One non-reachable-but-wired exit mode: `candidate_ended` (knockout + candidate-initiated end paths land in H / I).

**Sequence on `on_enter` (called by LiveKit `AgentSession.start(...)`):**

1. `state.transition(InterviewPhase.CONSENT)` — emits `phase_changed` event with payload `{old: CONNECTING, new: CONSENT, reason: "wizard_consent_already_captured"}`. **Important:** CONSENT is a real state machine step, not a workaround. Per design doc §6.1 and the `InterviewPhase.CONSENT` enum comment, consent is captured in the pre-room wizard before the agent dispatches. The agent's CONSENT phase is a brief audit-recordable acknowledgment that consent exists; it is not waited-on for any candidate input. Future Phase F may add behavior here (e.g., re-confirming consent at session start) — leaving the phase intact preserves that integration point.
2. `state.transition(InterviewPhase.INTRO)` — emits `phase_changed`.
3. `await self._say(<intro_string>)` — hardcoded English from `_phase_b_utterances.py` with `{name}`, `{role}`, `{minutes}` substitution.
4. `state.transition(InterviewPhase.MAIN_LOOP)` — emits `phase_changed`.
5. **For each `QuestionConfig` in `config.stage.questions`:**
   - Set `QuestionState.asked_at = now()`, `asked_mode = "standard"`.
   - **The orchestrator (not the state model) calls** `await self._persistence.write_state(self._state)` after every `state.transition(...)` invocation. This is best-effort, non-blocking, never raises. The architectural split is intentional: `state.py` is pure data-layer (no `persistence.py` import); `persistence.py` is I/O-layer; `structured_agent.py` is the only layer that calls both. Don't be tempted to hook persistence into `state.transition()` itself — that would couple data layer to I/O in the wrong direction.
   - Emit `orchestrator.question_asked` envelope event with payload `{question_id, position, mode: "standard"}`.
   - `await self._say(<ask_string with question.text>)`.
   - Wait for one `UserInputTranscribedEvent(final=True)`. Treat unconditionally as substantive.
   - Record the candidate's transcript on the `QuestionState` (one `TranscriptEntry` per question, plus accumulating into `full_transcript` for the SessionResult).
   - Set `QuestionState.completed_at = now()`. Compute `elapsed_seconds`.
   - Emit `orchestrator.question_completed` envelope event with payload `{question_id, elapsed_seconds, followups_asked: 0}`. Note: `coverage_at_close` is **omitted** in B; added in Phase D when Sufficiency Checker lands. Audit envelope JSON-object additions are forward-compatible.
   - `await self._persistence.write_ledger(ledger)` (best-effort; ledger has no real updates in B but the path executes).
6. After last question: `state.transition(InterviewPhase.NORMAL_WRAP)` — emits `phase_changed`.
7. `await self._say(<wrap_string>)`.
8. `state.transition(InterviewPhase.CLOSED)`, `state.set_exit_mode(ExitMode.COMPLETED, ended_at=now())` — emits `phase_changed` and `orchestrator.exit` (payload `{exit_mode, reason: "all_questions_completed"}`).
9. Existing `_handle_close` runs: emits `orchestrator.ledger.snapshot` with the (all-zeros, all-`none`) ledger, `persistence.gaps_detected` from `_persistence.detect_gaps(...)`, then `session.close`. Persists `SessionResult`. Publishes `session_outcome="completed"`.

**Total `phase_changed` events on a happy path:** 5 — `CONNECTING→CONSENT`, `CONSENT→INTRO`, `INTRO→MAIN_LOOP`, `MAIN_LOOP→NORMAL_WRAP`, `NORMAL_WRAP→CLOSED`.

### 3.4 ExitMode → SessionOutcome mapping

Wired via the orchestrator's `_end_outcome` field (the existing pattern in `agent.py`):

| Trigger | `ExitMode` set by orchestrator | `SessionOutcome` published | Reachable in B? |
|---|---|---|---|
| All questions completed normally | `COMPLETED` | `"completed"` | yes |
| `participant_disconnected` fires before close | `TECHNICAL_FAILURE` | `"candidate_disconnected"` | yes |
| Unhandled exception in close path | `TECHNICAL_FAILURE` | `"error"` | yes |
| Knockout-confirmed early exit | `KNOCKOUT_EXIT` | `"candidate_ended"` (with `knockout_failures` non-empty) | wired, **unreachable in B** |
| Candidate chose to end during pause-decline | `CANDIDATE_INITIATED_EXIT` | `"candidate_ended"` (with `knockout_failures` empty) | wired, **unreachable in B** |

Wired-but-unreachable means: the close-handler mapping logic knows how to publish those outcomes, but no Phase-B code path triggers them.

**Who drives the final `state.transition(InterviewPhase.CLOSED)`?**

- **Happy path:** the orchestrator's main loop drives `MAIN_LOOP → NORMAL_WRAP → CLOSED` itself (steps 6–8 in §3.3). The close handler runs *after* state is already CLOSED.
- **Disconnect / error path:** the existing `_wire_participant_disconnect` callback only stamps `agent._end_outcome = "candidate_disconnected"` — it does NOT call `state.transition()`. The orchestrator's main loop is awaiting `UserInputTranscribedEvent(final=True)` and never fires for the disconnected candidate, so the loop doesn't progress phase either. **The close handler (`_handle_close`) is responsible for the final `state.transition(InterviewPhase.CLOSED)` and `state.set_exit_mode(ExitMode.TECHNICAL_FAILURE, ended_at=...)`** when it observes `state.phase != InterviewPhase.CLOSED` at close time. `_LEGAL_TRANSITIONS` allows `MAIN_LOOP → CLOSED`, `INTRO → CLOSED`, `CONSENT → CLOSED`, and `CONNECTING → CLOSED` directly — the disconnect path takes whichever direct edge is legal from current phase.

This split keeps the disconnect callback minimal (it can fire from any thread/task and only mutates a single field) and gives `_handle_close` the single responsibility of finalizing state machine wind-down.

### 3.5 SessionResult population

- `question_results` — one `QuestionResult` per `QuestionConfig`, in original position order.
  - For each asked question: `was_skipped=False`, `probes_fired=0`, `observations=[]`, `transcript_entries` carries one entry with the candidate's verbatim final transcript.
  - For questions never reached (e.g., disconnect mid-session): `was_skipped=True`, `probes_fired=0`, `observations=[]`, `transcript_entries=[]`.
- `full_transcript` — built from accumulated turn records on the agent (mirrors the `audio.stt.transcribed` envelope events).
- `knockout_failures` — empty list. Always.
- `duration_seconds`, `questions_asked`, `questions_skipped`, `total_probes_fired=0`, `completed_at` — populated as the existing `_build_session_result` does.

### 3.6 Envelope events emitted in Phase B

Sourced from `event_kinds.py` (already declared up-front, no string drift):

- `ORCHESTRATOR_PHASE_CHANGED` — every `state.transition()` call.
- `ORCHESTRATOR_QUESTION_ASKED` — `{question_id, position, mode: "standard"}`.
- `ORCHESTRATOR_QUESTION_COMPLETED` — `{question_id, elapsed_seconds, followups_asked: 0}` (no `coverage_at_close` in B).
- `ORCHESTRATOR_EXIT` — `{exit_mode, reason}`.
- `ORCHESTRATOR_LEDGER_SNAPSHOT` — emitted at close with the (all-zeros, all-`none`) ledger. Confirms the snapshot path executes before Phase D fills it with real data.
- `PERSISTENCE_GAPS_DETECTED` — emitted at close with `_persistence.detect_gaps(...)` result. Likely `{state_gap: 0, ledger_gap: 0}` for a clean session; the path executing is what's being verified.
- `SPEECH_SAFETY_VIOLATION` — emitted only on a hardcoded-utterance regression that fails `check_safety`. Phase B's hardcoded strings are designed to not trip safety; an emission means the strings drifted and the test surface caught it.

`ORCHESTRATOR_FOLLOWUP_ASKED`, `EVALUATOR_*`, `SPEECH_RENDERED`, `SPEECH_FALLBACK_USED`, `ORCHESTRATOR_KNOCKOUT_*`, `ORCHESTRATOR_SILENCE_TIER`, `ORCHESTRATOR_RECONNECT`, `SYSTEM_VERSIONS` — declared in `event_kinds.py` but **not emitted in B**. They light up in their respective phases (E, F, C, H, I, J).

### 3.7 Redis persistence wiring

Per the `app/pubsub.py::_get_client()` pattern verified in the spec audit:

```python
# in agent.py entrypoint(...), after building the EventCollector.
# Reusing app.pubsub's process-level memoized Redis client per Phase A
# close-out Flag 5; LedgerPersistence is the second legitimate consumer.
# No separate engine pool needed. The leading-underscore convention is
# acknowledged but accepted for this scope; promoting to a public name
# is deferred to a separate small commit if a third consumer appears.
from app.pubsub import _get_client as _get_redis_client
persistence = LedgerPersistence(
    client=_get_redis_client(),
    tenant_id=tenant_id_str,
    session_id=session_id,
)
```

One process-level memoized `aioredis.Redis` client is shared across all per-session `LedgerPersistence` instances. Not a per-session client, not a Dramatiq-broker reconstruction — the same shared client `app/pubsub.py` already exposes for fanout.

`LedgerPersistence.write_state` is called by the orchestrator (`structured_agent.py`) on every `state.transition()` call site. `write_ledger` is called once per question completion. Both are best-effort and never raise. Failures log at warning. `detect_gaps(...)` is called inside `_handle_close` and the result is emitted as `PERSISTENCE_GAPS_DETECTED`. The state-model layer (`state.py`) does NOT import persistence — see §3.3 for the architectural-split rationale.

The `LedgerPersistence` module + class docstring is updated as part of the Phase A tail to name `app/pubsub.py::_get_client()` explicitly.

---

## 4. File layout

### 4.1 Phase A tail

**Create:**

| Path | Purpose |
|---|---|
| `app/modules/interview_engine/speech/templates.py` | ~10-line module binding. Imports `TemplateLoader` from `app.ai.prompts`, computes `ENGINE_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"`, exports a singleton `template_loader` constructed with `reload_on_change=settings.environment == "development"`. |
| `tests/interview_engine/speech/test_template_loader_binding.py` | Three cases. (a) `template_loader.get("speech_agent", "intro", "v1")` returns non-empty body. (b) `template_loader.get("speech_agent", "intro", "v999")` raises `FileNotFoundError`. (c) `template_loader._reload` reflects the dev/prod flag derived from `settings.environment`. |

**Modify:**

| Path | Change |
|---|---|
| `app/modules/interview_engine/speech/__init__.py` | Re-export `template_loader` alongside the existing safety re-exports. |
| `app/modules/interview_engine/orchestrator/persistence.py` | Module docstring + `LedgerPersistence.__init__` docstring updated to spell out the `app.pubsub._get_client()` reuse contract: "Clients are obtained from `app.pubsub._get_client()` — a process-level memoized `aioredis.Redis` shared across all per-session `LedgerPersistence` instances. Do not construct a fresh client per session." No code change. |

### 4.2 Phase B

**Create:**

| Path | Purpose |
|---|---|
| `app/modules/interview_engine/structured_agent.py` | The new `StructuredInterviewAgent(Agent)` class. Holds `InterviewState`, `SignalLedger`, `LedgerPersistence`, `EventCollector`. Overrides `llm_node` (Form A). Owns `_say(text)`. Drives the orchestration loop on `UserInputTranscribedEvent(final=True)`. |
| `app/modules/interview_engine/orchestrator/flow.py` | Pure functions. `pick_next_question(state, config) -> QuestionConfig \| None` — Phase B walks `config.stage.questions` in position order. `evaluate_exit_condition(state, config) -> ExitMode \| None` — returns `COMPLETED` when `pick_next_question` is None, else None. |
| `app/modules/interview_engine/_phase_b_utterances.py` | Throwaway hardcoded English strings. Top-of-file docstring is the deletion criterion. See §4.3 for the exact docstring text. |
| `tests/interview_engine/orchestrator/test_flow.py` | Pure unit tests for `pick_next_question` and `evaluate_exit_condition`. |
| `tests/interview_engine/test_structured_agent_integration.py` | Integration test with mocked LiveKit transport. Two cases: happy path + disconnect mid-session. See §5.3 for assertion detail. |
| `tests/interview_engine/test_say_call_sites.py` | AST invariant scan. Walks every `.py` under `app/modules/interview_engine/`; finds every `Call` whose `func` is an `Attribute` with `.attr == "say"` and whose base resolves to a name containing `"session"`; asserts every match's line falls inside the body of the `_say` method on `StructuredInterviewAgent`. |

**Modify:**

| Path | Change |
|---|---|
| `app/modules/interview_engine/agent.py` | Replace `GenericInterviewAgent(...)` instantiation with `StructuredInterviewAgent(...)`. Flip `preemptive_generation` to `False`. Construct `LedgerPersistence(client=_get_client(), …)` and pass into the agent. Remove the cold-start "candidate speaks first" comment. The `_build_system_prompt` is replaced by the inert string from §3.1. Keep `_wire_session_observability`, `_wire_close_handler`, `_wire_participant_disconnect` exactly as-is. |
| `app/modules/interview_engine/__init__.py` | No-op: keep `server` re-export. `StructuredInterviewAgent` stays internal. |

**Deferred (per scope refinement, per impl doc tail-of-Phase-B):**

| Path | Why deferred |
|---|---|
| `app/modules/interview_engine/orchestrator/question_selection.py` | Phase B's selection is 3 lines of "walk in position order" and lives inside `flow.py::pick_next_question`. Priority + mandatory-first + knockout-first selection has no signal data to consume in B. Reintroduced in **Phase E** alongside Sufficiency Checker. |
| `app/modules/interview_engine/orchestrator/time_budget.py` | Compression / extension triggers have no inputs in B (no Sufficiency Checker → no signal coverage). Adding the file in B would be 100% dormant. Reintroduced in **Phase E**. |

Both deferrals are tracked in §6 (carryforward) so they cannot be silently lost.

### 4.3 `_phase_b_utterances.py` shape

The module exports four strings and asserts the safety-fallback string at import time:

```python
"""Throwaway hardcoded utterances for Phase B.

DELETE this file when Phase C ships the LLM-rendered Speech Agent. The
orchestrator's call sites (in structured_agent.py) get repointed to
speech.deliveries.render_<template> at that time. If you are reading this
in Phase C or later, this file should not exist.

Phase B uses these literal strings to exercise the orchestrator's flow
end-to-end before the Speech Agent class is built. The candidate
experience sounds robotic; that is intended. Phase C replaces every call
site with `await speech_agent.render(template, version, inputs)` →
`await session.say(rendered.text)`, with the same single-entry-point
discipline.

Four strings ship in Phase B:
- INTRO with placeholders: {name}, {role}, {minutes}
- ASK_QUESTION_STANDARD with: {question_text}
- WRAP_NORMAL (no placeholders)
- _PHASE_B_SAFETY_FALLBACK_TEXT — load-bearing recovery path used by
  StructuredInterviewAgent._say when an utterance fails check_safety.

All four are designed to pass `speech.safety.check_safety` cleanly. The
fallback's safety is enforced at module import time (see assertion
below) so a regression that would crash the agent in production fails
loudly during boot instead.
"""
from app.modules.interview_engine.speech.safety import check_safety

INTRO = "Hi {name}, I'll be running a short technical screen for the {role} role today. We'll be about {minutes} minutes. Take your time, and feel free to ask me to repeat anything. Let's get started."
ASK_QUESTION_STANDARD = "Got it. Next question: {question_text}"
WRAP_NORMAL = "That's everything from my side. The recruiting team will be in touch with next steps."
_PHASE_B_SAFETY_FALLBACK_TEXT = "Let me ask you about something else. The recruiting team will follow up with you."

# Module-import-time assertion: a safety regression in the fallback itself
# would otherwise allow the agent to limp on into production. Failing the
# import is the right loudness for a load-bearing recovery path.
assert check_safety(_PHASE_B_SAFETY_FALLBACK_TEXT).is_safe, (
    "_PHASE_B_SAFETY_FALLBACK_TEXT failed check_safety — fix the string before "
    "the agent process is allowed to start."
)
```

The exact wording of the four strings is not load-bearing on this spec — implementation may tune them as long as (a) all four pass `check_safety` and (b) the placeholder set on each is exactly as documented above so the orchestrator's `.format(...)` call sites don't drift.

---

## 5. Test plan

### 5.1 Phase A tail

`tests/interview_engine/speech/test_template_loader_binding.py`:
- `test_loads_intro_v1` — `template_loader.get("speech_agent", "intro", "v1")` returns non-empty body.
- `test_missing_version_raises_file_not_found` — `template_loader.get("speech_agent", "intro", "v999")` raises `FileNotFoundError`.
- `test_reload_flag_reflects_environment` — with `settings.environment` patched to `"development"`, a freshly-instantiated `template_loader` has `_reload=True`; with `"production"` (or any other value), `_reload=False`.

### 5.2 Phase B unit tests

`tests/interview_engine/orchestrator/test_flow.py`:
- `test_pick_next_returns_first_unasked` — fresh state, three questions; returns the position-0 question.
- `test_pick_next_skips_completed` — position-0 has `completed_at` set; returns position-1 question.
- `test_pick_next_returns_none_when_all_complete` — all three questions have `completed_at` set; returns None.
- `test_pick_next_empty_questions_returns_none` — `config.stage.questions=[]`; returns None.
- `test_evaluate_exit_returns_completed_when_pick_next_none` — all questions completed; returns `ExitMode.COMPLETED`.
- `test_evaluate_exit_returns_none_during_progress` — at least one question not yet completed; returns None.

### 5.3 Phase B integration test

`tests/interview_engine/test_structured_agent_integration.py`. Two cases. Both use mocked LiveKit transport (no real room, no real STT/TTS) but the real `StructuredInterviewAgent` class.

**Case A — happy path.** Scripted candidate sends N transcribed utterances (one per question), where N = `len(config.stage.questions)`. (Phase B treats every utterance as substantive unconditionally because there is no Intent Classifier yet — Phase F. The wording "transcribed utterances" matches what is actually happening at the framework level: `UserInputTranscribedEvent(final=True)` fires N times.)

Assertions:
- `SessionResult.question_results` has exactly N entries, all with `was_skipped=False`, `probes_fired=0`, exactly one `transcript_entries` per question.
- `SessionResult.exit_mode` maps to `"completed"`.
- `session_outcome` participant attribute reads `"completed"`.
- Envelope events:
  - **First event:** `orchestrator.phase_changed` with payload representing `CONNECTING→CONSENT`.
  - **Last event:** `session.close`. (The close-handler sequence is `orchestrator.exit → orchestrator.ledger.snapshot → persistence.gaps_detected → session.close → write to disk`. `session.close` is therefore strictly last in the envelope.)
  - **Middle (counts-only multiset, no order constraint):**
    - `orchestrator.phase_changed × 5` (CONNECTING→CONSENT, CONSENT→INTRO, INTRO→MAIN_LOOP, MAIN_LOOP→NORMAL_WRAP, NORMAL_WRAP→CLOSED).
    - `orchestrator.question_asked × N`.
    - `orchestrator.question_completed × N`.
    - `orchestrator.exit × 1`.
    - `orchestrator.ledger.snapshot × 1`.
    - `persistence.gaps_detected × 1`.

The set-membership assertion (first-ordered, last-ordered, middle as multiset) is deliberate. Strict ordering of all events is brittle when async Redis writebacks fire concurrently with envelope appends; counts + first-and-last anchors prove the path executed without flaking on interleaving.

**Case B — disconnect mid-session.** Scripted candidate answers Q1 then disconnects. Simulated by directly invoking the registered `participant_disconnected` callback (the one wired by `_wire_participant_disconnect`) with a mock participant object after the `UserInputTranscribedEvent` for Q1, then triggering the close handler.

**Test mechanics:** after asserting Q1 was processed, cancel the orchestrator's main-loop task (which is awaiting the `UserInputTranscribedEvent` for Q2 that will never fire), then directly invoke the close handler. Cancellation is the test's stand-in for LiveKit's participant-timeout-driven close; without it the awaiting task hangs the test. Verify that `state.phase` immediately after cancellation is `MAIN_LOOP` (not yet `CLOSED`) and that the close handler observes `_end_outcome="candidate_disconnected"` and performs the direct `MAIN_LOOP→CLOSED` transition itself, then `set_exit_mode(ExitMode.TECHNICAL_FAILURE, ended_at=...)`.

**Safety-fallback variant:** a third sub-case asserts that injecting a deliberately-unsafe string into `_say(...)` (test-only override of one Phase B utterance) emits both `SPEECH_SAFETY_VIOLATION` and `SPEECH_FALLBACK_USED` envelope events, and that the candidate hears the `_PHASE_B_SAFETY_FALLBACK_TEXT` string instead of crashing the session. This covers the §3.1 Layer 3 fallback path.

Assertions:
- `SessionResult.question_results[0].was_skipped=False`, `transcript_entries` has 1 entry.
- `SessionResult.question_results[1:].was_skipped=True`, `transcript_entries=[]`.
- `SessionResult.exit_mode` maps to `"candidate_disconnected"`.
- `session_outcome` participant attribute reads `"candidate_disconnected"`.
- Envelope events: first event `phase_changed: CONNECTING→CONSENT`; last event `session.close`. Middle multiset: `phase_changed × 4` (CONNECTING→CONSENT, CONSENT→INTRO, INTRO→MAIN_LOOP, **MAIN_LOOP→CLOSED**), `question_asked × 1`, `question_completed × 1`, `exit × 1` with `exit_mode=technical_failure`, `ledger.snapshot × 1`, `gaps_detected × 1`. NORMAL_WRAP is not traversed: the close handler takes the direct `MAIN_LOOP→CLOSED` edge (legal per `_LEGAL_TRANSITIONS`) when it observes the disconnect-stamped `_end_outcome`. NORMAL_WRAP is reachable only when the orchestrator's main loop completes naturally — which it doesn't here, since the loop is blocked on `UserInputTranscribedEvent` for Q2 that never fires.

### 5.4 AST invariant test

`tests/interview_engine/test_say_call_sites.py`:
- `test_session_say_only_called_inside_structured_agent_say` — walks every `.py` under `app/modules/interview_engine/` (skipping `__pycache__/`), parses with `ast.parse`, finds every `Call` whose `func` is an `Attribute` with `.attr == "say"` and whose base name contains `"session"`. Asserts each match's line number falls inside the body of `StructuredInterviewAgent._say` (resolved via separate AST walk to find that method's `lineno` and `end_lineno`). On failure: error message names the offending file, line, and the surrounding function so triage takes seconds.

Precedent: `tests/test_module_boundaries.py` is the existing AST-walk invariant test in this codebase. The new test follows the same pattern (`ast.parse` + `ast.walk` + assertion + helpful failure message).

---

## 6. Acceptance criteria

### Phase A tail — done when ALL of:

- C1, C2 landed.
- `pytest tests/interview_engine/speech/test_template_loader_binding.py` green.
- All previously-green `tests/interview_engine/` tests still green.
- `mypy --strict app/modules/interview_engine/speech/` clean on new files.
- `ruff check app/modules/interview_engine/speech/` clean.
- `tests/test_module_boundaries.py` still green (`templates.py` re-exports through `__init__.py`).

### Phase B — done when ALL of:

- C3, C4, C5 (squashed agent + entrypoint), C6 (test_flow), C7 (integration test), C8 (AST invariant) landed.
- All Phase B tests green.
- All Phase A tests still green (no regression on orchestrator / state / ledger / persistence / safety).
- `mypy --strict app/modules/interview_engine/` clean.
- `ruff check app/modules/interview_engine/` clean.
- `tests/test_module_boundaries.py` green.
- **Manual smoke gate (happy path)** — real LiveKit room, you-as-candidate. Agent speaks intro. Walks through every question in `stage.questions`. Records each answer. Plays the wrap utterance. Disconnects cleanly. `SessionResult` row in Postgres reflects the run. `engine-events/<session_id>.json` envelope contains the expected event kinds with the first / last / multiset shape from §5.3 Case A. `session_outcome` participant attribute reads `"completed"`.
- **Manual smoke gate (disconnect)** — real LiveKit room, you-as-candidate. After Q2 (or any mid-session question), refresh the browser tab to disconnect. `SessionResult` shows partial completion, `session_outcome` reads `"candidate_disconnected"`, audit envelope contains the `participant_disconnected` event followed by the close path.
- After both smoke gates pass, the close-out commit lands documenting any fix commits and the carryforward list (§7).

### What does NOT gate Phase B:

- LLM-rendered utterances (Phase C).
- Knockout / disclaim / silence / pause-request / reconnect handling (Phase H/I).
- `prompt_quality`-marked real-LLM tests on the three filled templates.
- Coverage targets beyond the project default. The interview engine path is not in CLAUDE.md's "100% branch" list (which targets auth / RLS / candidate-session / admin-app); the engine targets the standard 80% line.

---

## 7. Carryforward (must be reflected in Phase B close-out commit)

The close-out commit lands AFTER the manual smoke gate passes, and after any fix commits the smoke gate forces. It describes what shipped, not what was predicted.

The close-out commit message body must explicitly list:

1. **Files deferred to Phase E** — `orchestrator/question_selection.py` (priority/mandatory-first/knockout-first selection), `orchestrator/time_budget.py` (compression/extension triggers). Both reintroduced when Sufficiency Checker provides the data they consume.
2. **`_phase_b_utterances.py` deletion gate** — must be deleted as part of the Phase C diff that introduces `speech/agent.py` + `speech/deliveries.py`. The tripwire docstring inside the file communicates this to future readers and to future-Claude-Code.
3. **Open product/legal item from design doc §8.1** — "Candidate asks if it's an AI?" transparency policy. Phase B doesn't need a decision; **Phase F (Intent Classifier) does**, since the routing depends on whether this is a `meta_request` subtype, an `off_topic` subtype, or a dedicated template. Surfaces as a TODO in the Phase F spec when that work begins.
4. **Three-layer guardrail provenance** — confirms whether the `llm_node` Form A choice held under `mypy --strict` + `ruff check` without any silencer. If a `# type: ignore[<code>]` was needed, the close-out names which code was triggered and why the silence is targeted.
5. **Smoke-gate observations** — any rough edges discovered in real-LiveKit testing that did not justify a fix commit but are worth flagging. Examples: audible TTS latency on the hardcoded strings (expected; Phase C's pre-buffering helps), audio pipeline metrics that look anomalous, any `audio.pipeline.error` envelope events seen.

---

## 8. Commit sequence

Numbered slots are the planned, pre-gate commits. Post-gate commits (zero or more fix commits + the close-out) are described but not numbered, since their count and exact subject lines depend on what the smoke gate surfaces.

| # | Commit (codebase convention: `<type>(<scope>): <description>`) | Phase | Atomic |
|---|---|---|---|
| C1 | `docs(interview_engine): document _get_client() reuse contract in LedgerPersistence` | A tail | yes |
| C2 | `feat(interview_engine): bind engine-scoped TemplateLoader instance` | A tail | yes |
| C3 | `feat(interview_engine): add orchestrator/flow.py for Phase B linear progression` | B prep | yes |
| C4 | `feat(interview_engine): add hardcoded-utterance stub for Phase B` | B prep | yes (includes `_PHASE_B_SAFETY_FALLBACK_TEXT` + module-import-time assertion per §4.3) |
| C5 | `feat(interview_engine): swap GenericInterviewAgent → StructuredInterviewAgent end-to-end` | B core | yes (squashed: structured_agent.py + agent.py edits + LedgerPersistence wiring + system-prompt swap + preemptive_generation flip) |
| C6 | `test(interview_engine): unit tests for orchestrator/flow.py` | B test | yes |
| C7 | `test(interview_engine): integration test for StructuredInterviewAgent SessionResult shape` | B test | yes (includes happy path + disconnect + safety-fallback sub-case per §5.3) |
| C8 | `test(interview_engine): AST invariant on session.say single-entry-point` | B test | yes |

**Smoke gate (not a commit):** Manual happy-path + disconnect runs in a real LiveKit session per §6 acceptance criteria. The gate must pass before the close-out commit is allowed to land.

**Post-gate commits (unnumbered, count depends on smoke-gate findings):**
- Zero or more `fix(interview_engine): <description>` commits, one per fix surfaced by the smoke gate. Each is small, surgical, and atomic. If the smoke gate passes cleanly with no fixes, this category is empty.
- Exactly one close-out commit: `docs(ai-screening): Phase A finalize + Phase B close-out — StructuredInterviewAgent shipped`. Mirrors the established `docs(ai-screening): ...` scope (commits `de33731`, `9d4a65a`, `23e0583`). Body lists every carryforward item from §7 explicitly — including any deferrals or behavioral nuances discovered during the smoke gate. The close-out describes what shipped, not what was predicted; it lands AFTER all fix commits.

C5 squashes the new class introduction and the entrypoint swap into one atomic commit. Solo-dev posture, no production exposure yet — flag-gating is not justified, and a class-without-call-site snapshot in git history is the kind of thing that gets stale silently if the next commit stalls.

---

## 9. Anti-patterns (specific to this work — reject)

- Adding empty prompt template stubs for the remaining ~11 templates. `TemplateLoader` raises `FileNotFoundError` cleanly; stubs add noise without safety. Templates are filled in the phase that consumes them.
- Constructing a fresh `aioredis.Redis` client per `LedgerPersistence` instance. Use `app.pubsub._get_client()` — process-level memoized client.
- Calling `session.say(...)` from anywhere other than `StructuredInterviewAgent._say`. The AST invariant test catches this.
- Fire-and-forget `session.say(...)` without `await` in the main loop. Reserved patterns (e.g., starting a `say()` then `handle.interrupt()` on a corrective signal) are explicit, post-MVP, and must be commented inline. None of those patterns exist in B.
- Blanket `# noqa` or `# type: ignore` on the `llm_node` override. Surgical, named, with rationale.
- Putting `coverage_at_close: "n/a (phase B)"` magic strings in the `orchestrator.question_completed` payload. Omit the field; add it in Phase D.
- Asserting strict total ordering of envelope events in the integration test. First / last / multiset is the contract.
- Asserting only 4 (or 3) `phase_changed` events. The CONSENT phase is real and traversed; 5 events is the correct count.
- Skipping the smoke gate. The integration test exercises mocked transport; only a real LiveKit room exercises the end-to-end TTS / STT / interruption stack against the structural change.
- Touching anything in §2.3's "Files NOT touched" list.

---

## 10. Definition of "done" for this spec's scope

This work is complete when:

1. Phase A tail acceptance criteria (§6) all pass.
2. Phase B acceptance criteria (§6) all pass, including both manual smoke gates.
3. The close-out commit (§7, §8 final row) has landed and its message body lists every carryforward item explicitly.
4. The next session can begin Phase C from a clean slate: orchestrator + state machine running real LiveKit interviews end-to-end with deterministic flow, awaiting only the LLM-rendered Speech Agent layer to replace the throwaway `_phase_b_utterances.py`.

---

*End of spec v1.*
