# AI Screening Agent — Implementation Prompt

> **Purpose of this document.** This is the implementation prompt for re-attaching a structured AI Screening Agent to the existing `interview_engine` plumbing in Nexus. It is intended to be passed to a coding agent (Claude Code) through the brainstorming skill, alongside the separately-attached design document (`ai-screening-agent-design.md`).
>
> **Read order:**
> 1. The design document — for *what* we are building and *why*.
> 2. This document — for *where* it goes in the codebase, *how* it integrates, and the *phased build plan*.
> 3. Root `CLAUDE.md` and `backend/nexus/CLAUDE.md` — for cross-cutting rules.

---

## 1. Mission

The current `app/modules/interview_engine/agent.py` is a **clean-slate generic LLM harness** (the structured controller was deleted on 2026-05-04). All supporting plumbing was deliberately preserved:

- `SessionConfig` is fetched at session start via `build_session_config`.
- `SessionResult` (with `KnockoutFailure[]`, `QuestionResult[]`, `TranscriptEntry[]`) is persisted at session end via `record_session_result`.
- `EventCollector` + `EventLogSink` write the audit envelope.
- `tenant_settings.engine_knockout_policy` already supports `record_only` and `close_polite` modes.
- `app.ai.realtime` factories build STT/LLM/TTS plugins from `AIConfig`.
- The candidate frontend reads `session_outcome` participant attribute.

**Your job:** Replace the `GenericInterviewAgent` with a `StructuredInterviewAgent` that implements the architecture in the design document — Orchestrator, SignalLedger, Speech Agent (templated), Sufficiency Checker (instructor), Intent Classifier (instructor), Disclaim Classifier (instructor), and the full state machine — while reusing every piece of preserved plumbing.

This is not a rewrite. It is a re-attachment. Do not touch the realtime factories, the event log envelope, the SessionConfig schema, the database, or any module outside `interview_engine` unless explicitly required by an acceptance criterion in this document.

---

## 2. Where everything lives (codebase orientation)

### 2.1 The agent process itself

| Path | Role | Touch in this work? |
|---|---|---|
| `backend/nexus/app/modules/interview_engine/agent.py` | LiveKit `AgentServer` entrypoint, prewarm, `entrypoint()`, current `GenericInterviewAgent` | **YES — replace `GenericInterviewAgent`** |
| `backend/nexus/app/modules/interview_engine/__init__.py` | Re-exports `server` | Update only if you add new public symbols |
| `backend/nexus/app/modules/interview_engine/event_log/` | `EventCollector`, envelope, `LocalFileSink`, `S3Sink`, factory | **DO NOT MODIFY** — extend kinds via `kind=` param only |

### 2.2 Plumbing we consume (do not modify)

| Path | What it gives us |
|---|---|
| `backend/nexus/app/modules/interview_runtime/__init__.py` | `SessionConfig`, `SessionResult`, `QuestionConfig`, `QuestionResult`, `KnockoutFailure`, `SteeringObservation`, `TranscriptEntry`, `build_session_config`, `record_session_result` |
| `backend/nexus/app/modules/interview_runtime/schemas.py` | Wire-format Pydantic models. Note: `KnockoutFailure.reason` runs `_scrub_pii` validator — emails/phones get `[redacted]`. |
| `backend/nexus/app/ai/realtime.py` | `build_stt_plugin()`, `build_llm_plugin()`, `build_tts_plugin()`, `build_turn_detector()` — the **only** blessed import site for `livekit.plugins.*` |
| `backend/nexus/app/ai/config.py` | `ai_config.interview_llm_model`, `interview_stt_model`, `interview_tts_voice`, etc. Read model IDs only via this. |
| `backend/nexus/app/ai/client.py` | `get_openai_client()` — for batch (non-realtime) LLM calls. **Use this for Sufficiency Checker, Intent Classifier, Disclaim Classifier.** |
| `backend/nexus/app/modules/tenant_settings/` | `get_tenant_settings(tenant_id)` returns `TenantSettings(engine_knockout_policy, engine_agent_name)`. |
| `backend/nexus/app/database.py` | `get_bypass_session()` — async context manager, RLS-bypassing session. The agent uses this because it has no Supabase user context. |
| `backend/nexus/app/config.py` | `settings.engine_*` — endpointing, VAD, log toggles, agent name |
| `backend/nexus/app/modules/audit/` | `log_event(...)` for audit table writes |

### 2.3 What we'll add (new files)

All new code lives in `backend/nexus/app/modules/interview_engine/`:

```
app/modules/interview_engine/
├── agent.py                    [MODIFY — entrypoint stays, swap GenericInterviewAgent → StructuredInterviewAgent]
├── __init__.py                 [MODIFY — export StructuredInterviewAgent if needed]
├── event_log/                  [DO NOT MODIFY]
├── orchestrator/               [NEW]
│   ├── __init__.py
│   ├── state.py                [InterviewState, phase enum, transitions]
│   ├── ledger.py               [SignalLedger, SignalState, EvidenceQuote]
│   ├── flow.py                 [main loop logic — pure functions, no I/O]
│   ├── question_selection.py   [pick next question; deepening vs standard vs skip]
│   ├── time_budget.py          [compression/extension mode logic]
│   └── persistence.py          [Redis async writeback, sequence numbers]
├── speech/                     [NEW]
│   ├── __init__.py
│   ├── agent.py                [SpeechAgent — LLM call wrapper]
│   ├── templates.py            [load + version templates from prompts/]
│   ├── safety.py               [disallowed-phrase regex, length cap]
│   └── deliveries.py           [public API: render_intro(), render_ask_question(), etc.]
├── evaluators/                 [NEW]
│   ├── __init__.py
│   ├── intent_classifier.py    [instructor + pydantic schema]
│   ├── disclaim_classifier.py  [instructor + pydantic schema]
│   ├── sufficiency_checker.py  [instructor + pydantic schema]
│   └── output_schemas.py       [shared structured output models]
├── prompts/                    [NEW — versioned text files]
│   ├── speech_agent/
│   │   ├── intro.v1.txt
│   │   ├── ask_question_standard.v1.txt
│   │   ├── ask_question_deepening.v1.txt
│   │   ├── ask_followup.v1.txt
│   │   ├── ask_followup_dynamic.v1.txt
│   │   ├── meta_response.v1.txt
│   │   ├── polite_deflection.v1.txt
│   │   ├── confirmation_turn.v1.txt
│   │   ├── pause_request_decline.v1.txt
│   │   ├── gentle_prompt.v1.txt
│   │   ├── resume_from_state.v1.txt
│   │   ├── wrap_normal.v1.txt
│   │   ├── wrap_knockout_exit.v1.txt
│   │   └── wrap_candidate_initiated_exit.v1.txt
│   ├── intent_classifier.v1.txt
│   ├── disclaim_classifier.v1.txt
│   └── sufficiency_checker.v1.txt
└── structured_agent.py         [NEW — the new LiveKit Agent class wiring it all]
```

Tests live under `backend/nexus/tests/interview_engine/` mirroring the source tree. Use existing fixtures from `tests/conftest.py` (e.g., `db`, `make_assignment_with_stage`).

---

## 3. Mapping design components to this codebase

| Design doc concept | This codebase realization |
|---|---|
| **Orchestrator** | `interview_engine/orchestrator/` — pure code, no LLM. State machine drives `StructuredInterviewAgent`. |
| **SignalLedger** | In-memory dataclass in `orchestrator/ledger.py`. Async writeback to Redis via `orchestrator/persistence.py`. Final state lands in `SessionResult.question_results` + a new envelope event kind `orchestrator.ledger.snapshot`. **No new DB table** — the audit envelope and SessionResult already capture everything. |
| **InterviewState** | Dataclass in `orchestrator/state.py`. Lives in the `StructuredInterviewAgent` instance. Persisted to Redis on every phase change for reconnect support. |
| **Speech Agent** | `speech/agent.py` — uses `app.ai.client.get_openai_client()` for non-realtime LLM (Sufficiency, Intent, Disclaim). For utterance generation, the speech path goes through the realtime `Agent` system prompt. **See §6 for the architectural decision on this.** |
| **Sufficiency Checker, Intent Classifier, Disclaim Classifier** | `evaluators/*.py` — each is an `instructor`-wrapped OpenAI call returning a Pydantic model. Use `app.ai.client.get_openai_client()`. |
| **Pre-canned content** | Already in `SessionConfig.stage.questions` (each a `QuestionConfig` with `text`, `signal_values`, `follow_ups`, `positive_evidence`, `red_flags`, `rubric`, `evaluation_hint`). **Do not regenerate or modify questions at runtime.** |
| **Audit envelope** | Already exists. Hook every Orchestrator decision, every LLM call (template, version, model, latency, tokens), every state transition into `EventCollector.append(kind=..., payload=...)`. |
| **HandoffPackage to Report Builder** | The existing `SessionResult` already covers ~90% of this. Use `QuestionResult.observations` (`SteeringObservation`) for evidence quotes per question. Use `KnockoutFailure[]` for the knockout exit record. Use `full_transcript` for the verbatim transcript. **Do not add new tables.** |
| **Three exit modes** | Map onto existing `SessionOutcome = Literal["completed", "candidate_ended", "candidate_disconnected", "error"]`. Mapping: `completed` → COMPLETED; `candidate_ended` → CANDIDATE_INITIATED_EXIT or KNOCKOUT_EXIT (distinguished via `KnockoutFailure[]` non-empty); `candidate_disconnected` → TECHNICAL_FAILURE. |

---

## 4. Critical architectural decision: how is the Speech Agent wired?

**This is the single most important integration decision and must be resolved before Phase B.**

The design document treats the Speech Agent as a service that takes (state, template, inputs) and produces an utterance. But the realtime LiveKit `AgentSession` runs its own LLM that listens to STT, generates, streams to TTS — there's no obvious hook to inject "say exactly this string" mid-conversation without fighting the framework.

### The two viable patterns

**Pattern 1: System prompt + per-turn instructions via `Agent` class**
The realtime LLM stays in charge of speaking. We control what it says by mutating the `Agent`'s instructions dynamically at each state transition. The Orchestrator decides "ask question 3 standard" → updates the `Agent`'s instruction → the realtime LLM produces that utterance on the next turn.

- **Pro:** Works with the framework. Latency is lowest.
- **Con:** The realtime LLM has agency we don't want. It might add hints, change scope, refuse to ask, or hallucinate. Hard to enforce per-template MUST-NOT rules at the realtime LLM level.

**Pattern 2: External Speech Agent + `session.say(text)` injection**
We pre-generate the exact utterance using a non-realtime call to OpenAI (with the template, full MUST-NOT rules, output validation), then call `session.say(text)` to make the LiveKit session speak that exact string. The realtime LLM is bypassed for agent speech entirely; it only exists to handle the STT→LLM→TTS plumbing.

Looking at the `livekit-agents` API, this is feasible: `AgentSession.say(text)` exists for exactly this purpose.

- **Pro:** Full control. Every utterance goes through our template + safety regex + version. Cheaper (no per-turn realtime LLM cost on agent turns). Auditable.
- **Con:** Slightly higher latency (one extra non-realtime LLM call). Need to manage `say()` lifecycle correctly to avoid race conditions with auto-generation.

### Decision

**Use Pattern 2.** It is the only pattern that satisfies the design doc's hard rules:
- "Speech Agent never receives the rubric" — only enforceable with an external call we control.
- "Disallowed-phrase regex on every Speech Agent output" — only enforceable on string output we hold.
- "Every utterance goes through a versioned template" — only enforceable when we mint the string ourselves.

The realtime LLM is configured to **not auto-respond** — instead, our Orchestrator drives every agent utterance via `session.say()` with a string produced by the Speech Agent.

### Three-layer guardrail (non-negotiable)

`preemptive_generation={"enabled": False}` alone is **not sufficient** — it disables only *speculative* LLM execution before end-of-turn. After the user's turn is confirmed, the framework still runs the LLM by default (per `https://docs.livekit.io/reference/agents/turn-handling-options/` and *Agent speech and audio*: "Only the LLM runs preemptively — TTS waits until the turn is confirmed."). A "do not speak unless told" system prompt is a *soft* guardrail — it depends on the LLM faithfully following instructions and produces zero tokens at best, hallucinated speech at worst.

Pattern 2 therefore needs **three layers**, in order of increasing strength:

1. **Hard guardrail — override `Agent.llm_node`** to return an empty / no-op stream. This is the load-bearing layer: with `llm_node` overridden, the framework's autogen path becomes a no-op regardless of what the system prompt says or what the LLM tries to produce. There is no race window where a hallucinated autogen response could reach TTS in parallel with our `session.say()`. LiveKit's own `examples/voice_agents/structured_output.py` uses the same pattern (override `llm_node` and `tts_node` for full control).
2. **Defense in depth — system prompt** of the form `"Wait for explicit instructions. Do not speak unless told."`. Belt-and-suspenders: if the `llm_node` override is ever removed by mistake, the system prompt is a second line of defense.
3. **Single utterance entry point — `await session.say(text)`** routed through `structured_agent.py`'s utterance dispatcher, which is the sole site that runs `safety.check_safety()` before TTS playout. Every agent utterance — hardcoded in Phase B, LLM-rendered in Phase C+ — converges here.

Concretely:
- Override `Agent.llm_node` in `StructuredInterviewAgent` to yield nothing (e.g. an `async def llm_node(self, *args, **kwargs): return; yield  # noqa` that satisfies the framework's async-generator contract while emitting zero chunks). Verify against the LiveKit version pinned in `pyproject.toml` (`livekit-agents>=1.5.4,<2`) before Phase B; the `structured_output.py` example is the reference implementation.
- Build the `AgentSession` with `preemptive_generation={"enabled": False}` (kept for clarity even though `llm_node` would short-circuit before that fires).
- The no-op `Agent`'s instructions are `"Wait for explicit instructions. Do not speak unless told."`.
- When the Orchestrator decides to speak, it calls `speech_agent.render_<template>(...)` → gets a string → utterance dispatcher → `safety.check_safety(text)` → `await session.say(text, allow_interruptions=...)`.
- **Always `await` `session.say()` in the orchestrator main loop.** `AgentSession.say()` returns a `SpeechHandle`; awaiting it blocks until playback completes (or an interruption fires). This gives the Orchestrator deterministic ordering: the candidate has heard the utterance before we wait for `UserInputTranscribedEvent(final=True)`. Fire-and-forget (`handle = session.say(...)` without `await`) is forbidden in the main loop. Reserved patterns (e.g., starting a `say()` then `handle.interrupt()` on a corrective signal) are explicit, post-MVP, and must be commented inline.
- Listen for `UserInputTranscribedEvent` (final=True) → run Intent Classifier → route → Orchestrator → next utterance via `await session.say()`.

This is the inversion of the current `GenericInterviewAgent` pattern. The `session.say()` API was confirmed against `livekit-agents` source (`livekit-agents/voice/agent_session.py`) and the canonical `frontdesk_agent.py` / `multi_agent.py` examples (the latter notes inline: *"awaiting it will ensure the message is played out before returning"*). The `llm_node` override pattern was confirmed against `examples/voice_agents/structured_output.py`. No further spike required for the API itself; manual smoke-test in Phase B remains gated by acceptance criteria.

---

## 5. Codebase constraints (must comply)

These are pulled from `backend/nexus/CLAUDE.md` and observable patterns. Violating these breaks tests, CI, or production guarantees.

### 5.1 Vendor SDK imports
- Realtime LiveKit plugins (`livekit.plugins.*`) — **only** in `app/ai/realtime.py`. No direct imports from anywhere else, including the new structured agent. Use the existing `build_stt_plugin()` / `build_llm_plugin()` / `build_tts_plugin()` / `build_turn_detector()`.
- `livekit.agents` (the `Agent`, `AgentSession`, `JobContext` etc.) — used in `agent.py` already; pattern is fine to extend in the new `structured_agent.py`.
- OpenAI SDK — for batch calls, **only** through `app.ai.client.get_openai_client()`. Wrap with `instructor` for structured outputs.
- Vendor exception types — never reference by name across module boundaries; use the typed sentinels in `app/ai/errors.py` (or extend it if needed).

### 5.2 Module boundaries
- `tests/test_module_boundaries.py` enforces public-API imports only. New evaluator/orchestrator/speech submodules must export through their `__init__.py`. Cross-module callers (e.g., `agent.py` importing from `orchestrator/`) use the package, not deep paths.

### 5.3 Tenancy + RLS
- Engine runs on `get_bypass_session()` — RLS bypassed because there's no Supabase user.
- **Every query in new code that touches DB must explicitly filter by `tenant_id`.** The bypass-session is a footgun. The pattern in `interview_runtime/service.py` is the model.
- Never log raw transcript content with `tenant_id` in the same line at INFO level unless `engine_log_user_transcripts=True`. Default is metadata-only.

### 5.4 Realtime LLM model selection
- `ai_config.interview_llm_model` is the speech-path LLM (used by `build_llm_plugin`). For the structured agent's "no-op" Agent, this is unused at runtime (we don't auto-generate) but still configured.
- Chat models (`*-chat-latest`) reject `reasoning_effort` parameter — sending it 400s every turn. Reasoning models (`gpt-5.1`, `o3`, `o4-mini`, `gpt-5-pro`) accept it. The factory already gates this on `ai_config.interview_reasoning_effort` being non-empty. **Don't break this.**

### 5.5 Evaluator model selection
- Sufficiency Checker / Intent Classifier / Disclaim Classifier are **batch** OpenAI calls, not realtime. They are not bound by the `interview_*` config keys.
- Add new `AIConfig` properties for these:
  - `evaluator_intent_model`
  - `evaluator_disclaim_model`
  - `evaluator_sufficiency_model`
  - (Optional) `evaluator_intent_effort`, etc.
- Default values via `app/config.py` `Settings` class. Add the env vars to `.env.example`.

### 5.6 instructor library
- `instructor` is already a dependency (>=1.15,<2). **`app.ai.client.get_openai_client()` already returns an `instructor.AsyncInstructor` (mode=`TOOLS_STRICT`)** — do NOT additionally wrap with `instructor.from_openai()`. Evaluator code (Sufficiency, Intent, Disclaim) calls `client.chat.completions.create(response_model=YourPydanticModel, ...)` directly on the client returned by `get_openai_client()`. See `app/modules/jd/actors.py`, `app/modules/question_bank/actors.py` for canonical examples — they use the pre-wrapped client the same way.
- `TOOLS_STRICT` mode enforces OpenAI function-calling with strict schema validation; on schema-validation failure, `instructor` raises `InstructorRetryException` (from `instructor.core`) after exhausting `max_retries` (defaults to instructor's per-call default — pass `max_retries=` to `chat.completions.create()` to override).
- All evaluator output schemas must be Pydantic models with strict validation.

### 5.7 Logging
- `structlog` with contextvars bound at session entry: `session_id`, `tenant_id`, `correlation_id`. The current `agent.py` does this — preserve it.
- Every LLM call logs: `template_name`, `template_version`, `model`, `latency_ms`, `tokens_in`, `tokens_out`, `parsed_ok`.
- Every Orchestrator decision logs at INFO with `decision`, `reason`, `current_phase`, `current_question_id`.
- Use namespaced log names: `interview-engine.orchestrator`, `interview-engine.speech`, `interview-engine.evaluator.intent`, etc.

### 5.8 Tests
- pytest-asyncio, real Postgres in test container.
- Use `tests/conftest.py` fixtures: `db`, `create_test_client`, `create_test_user`, `create_test_org_unit`, `make_assignment_with_stage`.
- Tests against real LLMs are marked `@pytest.mark.prompt_quality` and run nightly, not per-PR. Use these for real-LLM smoke tests on prompt changes.
- Unit tests for evaluators **must** mock the OpenAI client; do not hit real APIs in default test runs.
- For state machine logic, test pure functions in `orchestrator/flow.py` without LiveKit at all.

### 5.9 Migrations
- **No new migrations should be required for v1.** Everything we need is already in `SessionResult`, `KnockoutFailure`, `tenant_settings`, and the audit envelope. If a phase looks like it needs a migration, stop and re-check the design — it almost certainly doesn't.

### 5.10 Files NOT to touch
- Anything under `app/modules/` outside `interview_engine`, `tenant_settings` (read-only), `interview_runtime` (read-only), `audit` (read-only).
- Frontend code (`frontend/session/`).
- `event_log/` internals.
- Database schema, Alembic migrations.
- `app/ai/realtime.py`, `app/ai/config.py` (extend `AIConfig` only by adding new properties at the bottom).

---

## 6. Data model adjustments

### 6.1 SignalLedger — in-memory only

Following the design doc but realized as a Python dataclass that lives in the `StructuredInterviewAgent` instance:

```python
# orchestrator/ledger.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.modules.interview_runtime import QuestionConfig

CoverageStatus = Literal["none", "partial", "sufficient", "failed"]

@dataclass
class EvidenceQuote:
    quote: str
    turn_id: str
    source_question_id: str
    strength: Literal["weak", "strong"]
    timestamp: datetime

@dataclass
class SignalState:
    signal_value: str          # The string from QuestionConfig.signal_values
    weight: int                # We'd have to derive from JobPostingSignalSnapshot — see §6.2
    is_knockout: bool
    priority: Literal["required", "preferred"]
    coverage: CoverageStatus = "none"
    confidence: float = 0.0
    evidence_quotes: list[EvidenceQuote] = field(default_factory=list)
    last_updated_turn: str | None = None
    notes: list[str] = field(default_factory=list)

@dataclass
class SignalLedger:
    signals: dict[str, SignalState]   # keyed by signal_value (strings, since that's what QuestionConfig has)
    sequence_number: int = 0
    
    def update(self, signal_value: str, **changes) -> None:
        ...
        self.sequence_number += 1
```

### 6.2 Where do `weight`, `is_knockout`, `priority` come from?

Critical: `SessionConfig.signals` is a flat `list[str]` (signal values only) per `interview_runtime/schemas.py`. The structured signal metadata (weight, knockout, priority) lives in `JobPostingSignalSnapshot.signals` as a `list[dict]` — but `build_session_config` doesn't currently expose this richer form to the engine.

**Two options:**

**Option A (recommended): Extend `SessionConfig` to include signal metadata.**
Add a new field `SessionConfig.signal_metadata: list[SignalMetadata]` where `SignalMetadata` mirrors what's in `JobPostingSignalSnapshot.signals`. Update `build_session_config` to populate it. This is a small, additive, non-breaking change.

```python
# in interview_runtime/schemas.py — ADD:
class SignalMetadata(BaseModel):
    value: str
    type: Literal["competency", "experience", "credential", "behavioral", "logistic"]
    priority: Literal["required", "preferred"]
    weight: int = Field(ge=1, le=3)
    knockout: bool
    stage: str
    evaluation_method: str
    evaluation_hint: str | None = None

class SessionConfig(BaseModel):
    ...
    signals: list[str]                    # keep for backward compat (already used)
    signal_metadata: list[SignalMetadata] # NEW — full metadata
```

This is an exception to "no migrations" and "don't touch interview_runtime" — it's an additive Pydantic field, no DB change. Approved as part of Phase A.

**Option B (avoid):** Have the engine refetch the snapshot. Adds a query, breaks the in-process service contract, harder to test.

### 6.3 Evidence storage in SessionResult

The existing `QuestionResult.observations: list[SteeringObservation]` is the natural home for evidence quotes per question. `SteeringObservation` already has `answer_summary`, `wants_to_probe`, `candidate_disengaged`, `notes`. Use `notes` for the evidence quote string and primary signal coverage status; use `wants_to_probe` for the in-call follow-up decision; use `candidate_disengaged` for the candidate_initiated_exit signal.

Do NOT add new fields to `SteeringObservation` for v1. If the Report Builder needs more structure, it can re-derive from `full_transcript`.

For a richer ledger snapshot at session end, write a single audit envelope event:
```python
collector.append(
    kind="orchestrator.ledger.snapshot",
    payload={"signals": [serialized SignalState dicts]},
)
```

The Report Builder reads the envelope JSON file (existing pattern) for the rich state, the DB row for queryable summary.

---

## 7. Phased implementation plan

Each phase is independently testable, mergeable, and produces a working system at a higher fidelity than the previous. **Do not skip ahead.** Resist the temptation to wire up the Disclaim Classifier early — it's the highest-stakes branch and benefits from a stable foundation.

### Phase A — Foundations (no LLMs, no flow yet)

**Goal:** Data models, persistence, prompt loader, signal metadata wire-through. The agent process still runs the existing `GenericInterviewAgent`. Nothing user-visible changes.

**Files to create:**
- `app/modules/interview_engine/orchestrator/__init__.py`
- `app/modules/interview_engine/orchestrator/ledger.py` — `SignalLedger`, `SignalState`, `EvidenceQuote`, `CoverageStatus`
- `app/modules/interview_engine/orchestrator/state.py` — `InterviewPhase` enum, `InterviewState` dataclass with reconnect counter, time-budget fields, prompt versions, model versions, etc.
- `app/modules/interview_engine/orchestrator/persistence.py` — `LedgerPersistence` with async fire-and-forget Redis writes, sequence number gap detection
- `app/modules/interview_engine/speech/__init__.py`
- `app/modules/interview_engine/speech/templates.py` — `load_template(role: str, name: str, version: str = "v1") -> str`. File-based loader, caches once per process. Validates `{placeholder}` syntax.
- `app/modules/interview_engine/speech/safety.py` — `DISALLOWED_PHRASES` regex compilation, `check_safety(text) -> SafetyResult`. Includes outcome words ("passed", "failed", "rejected", "advanced", "best of luck", "unfortunately", "thanks for your interest"), specific salary numbers (regex), specific scheduling commitments.
- `app/modules/interview_engine/prompts/speech_agent/intro.v1.txt` — full text per design doc §7.2
- `app/modules/interview_engine/prompts/speech_agent/ask_question_standard.v1.txt` — full text per design doc §7.3
- `app/modules/interview_engine/prompts/speech_agent/wrap_normal.v1.txt` — full text per design doc §7.13
- (Other prompt template files: empty stubs with placeholder comments only — they get filled in subsequent phases)

**Files to modify:**
- `app/modules/interview_runtime/schemas.py` — add `SignalMetadata`, add `signal_metadata` field to `SessionConfig` (per §6.2 Option A).
- `app/modules/interview_runtime/service.py` — populate `SessionConfig.signal_metadata` from `snapshot.signals` in `build_session_config`.
- `app/modules/interview_runtime/__init__.py` — re-export `SignalMetadata`.
- `app/ai/config.py` — add `evaluator_intent_model`, `evaluator_disclaim_model`, `evaluator_sufficiency_model` properties.
- `app/config.py` — add corresponding `Settings` fields with reasonable defaults.
- `.env.example` — document new env vars.

**Tests to write:**
- `tests/interview_engine/orchestrator/test_ledger.py` — ledger updates, sequence number monotonicity, evidence append-only.
- `tests/interview_engine/orchestrator/test_state.py` — phase transitions valid/invalid, dataclass invariants.
- `tests/interview_engine/orchestrator/test_persistence.py` — Redis writeback (use `fakeredis` or mock), gap detection on reconstruction.
- `tests/interview_engine/speech/test_templates.py` — loads correctly, caches, raises on missing placeholder, raises on missing file.
- `tests/interview_engine/speech/test_safety.py` — every disallowed phrase regex hits; every safe phrase passes; case-insensitive where appropriate.
- `tests/interview_runtime/test_signal_metadata_plumbing.py` — `build_session_config` populates `signal_metadata` from snapshot; matches signal values.

**Acceptance criteria:**
- All new tests pass.
- Existing tests still pass (especially `test_session_service.py`, `test_jd_router.py`, `test_record_session_result_*`).
- Mypy strict passes on all new files.
- `module_boundaries` test still green (new submodules export through `__init__.py`).
- The `GenericInterviewAgent` still runs end-to-end (no behavioral changes yet).

---

### Phase B — Orchestrator skeleton (deterministic, hardcoded utterances)

**Goal:** Replace `GenericInterviewAgent` with `StructuredInterviewAgent`. State machine drives interview. Utterances are **hardcoded English strings** for now — no LLM-generated speech. End-to-end interview with a fixed list of questions. All three exit modes work. SessionResult populated correctly.

**Decision to verify before starting (§4):** Spike `session.say(text)` to confirm Pattern 2 works.

**Files to create:**
- `app/modules/interview_engine/structured_agent.py` — `StructuredInterviewAgent(Agent)`. Holds `InterviewState`, `SignalLedger`, references to `EventCollector`. Subscribes to `UserInputTranscribedEvent` to drive the loop. Does NOT use Speech Agent yet (Phase C). Uses hardcoded strings like `"Hi {name}, I'll be running a short technical screen for the {role} role today. We'll be about {n} minutes. Feel free to take your time. Let's get started."`.
- `app/modules/interview_engine/orchestrator/flow.py` — pure functions: `pick_next_question(state, ledger, config) -> QuestionConfig | None`, `should_compress(state, time_remaining) -> bool`, `should_extend(state, ledger) -> bool`, `evaluate_exit_condition(...) -> ExitMode | None`. No I/O, no LLMs, fully unit-testable.
- `app/modules/interview_engine/orchestrator/question_selection.py` — selection policy (priority-ordered, mandatory-first, knockout-first within mandatory).
- `app/modules/interview_engine/orchestrator/time_budget.py` — compression/extension logic.

**Files to modify:**
- `app/modules/interview_engine/agent.py`:
  - Replace `GenericInterviewAgent(...)` instantiation with `StructuredInterviewAgent(...)`.
  - Set `preemptive_generation={"enabled": False}` on the AgentSession.
  - Remove the cold-start "candidate speaks first" comment — the structured agent speaks first (the intro).
  - The `_build_system_prompt` becomes a no-op guardrail: `"You are an inert assistant. Do not speak unless explicitly instructed via session.say(). If you do generate output, output an empty string."`
- `app/modules/interview_engine/__init__.py` — re-export `StructuredInterviewAgent` (optional; only if tests need it).

**Event log kinds to add (via `collector.append(kind=..., payload=...)`):**
- `orchestrator.phase_changed` — `{"old_phase": ..., "new_phase": ..., "reason": ...}`
- `orchestrator.question_asked` — `{"question_id", "position", "mode": "standard|deepening|skipped"}`
- `orchestrator.question_completed` — `{"question_id", "elapsed_seconds", "followups_asked", "coverage_at_close"}`
- `orchestrator.followup_asked` — `{"question_id", "followup_index_or_dynamic", "target"}`
- `orchestrator.exit` — `{"exit_mode", "reason"}`

**Hardcoded utterance dispatcher** (temporary, to be replaced in Phase C):
A simple `dict[Template, str]` with `{name}`, `{role}`, `{n}` placeholder substitution. This is throwaway code; comment as such.

**Tests:**
- `tests/interview_engine/orchestrator/test_flow.py` — pick_next, compression triggers, extension triggers, all exit conditions. Pure unit tests, no LiveKit.
- `tests/interview_engine/orchestrator/test_question_selection.py` — mandatory before optional, knockout-first within mandatory, signal already covered → skipped.
- `tests/interview_engine/test_structured_agent_integration.py` — integration test that drives a fake transcript through the agent and asserts: SessionResult has correct question_results count, exit_mode mapped correctly, audit envelope contains all expected event kinds. Use `make_assignment_with_stage` fixture. Mock the LiveKit transport but use the real `StructuredInterviewAgent` class.

**Acceptance criteria:**
- A scripted "candidate" sending hardcoded substantive answers progresses through every question and produces a complete SessionResult.
- A scripted candidate sending one substantive answer then disconnecting produces `exit_mode = candidate_disconnected` and the partial SessionResult is persisted.
- Audit envelope contains every state transition.
- Manual smoke test (you, in a real LiveKit session, speaking as candidate) completes a full interview.

---

### Phase C — Speech Agent (LLM-generated utterances)

> **Superseded by Phase C design spec (2026-05-05).** The implementation details below were the original Phase C plan; they were revised before build began. The authoritative source is `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-design.md` (ARCH-D: streaming + pre-render, prompt-only safety enforcement (no regex layer)). The phase's *goal* and *acceptance criteria* below are still accurate; the *files to create*, *event log kinds*, and *render pipeline* are revised by the spec.

**Goal:** Replace hardcoded utterances with LLM-generated ones. Every agent utterance is produced by the Speech Agent — a class that takes a template name + inputs, calls OpenAI with `stream=True`, exposes a `SpeechRenderHandle` whose joined async iterator is consumed by `session.say(handle.commit())` for token-streamed TTS playout. Per-template prompt MUST-NOT rules enforce safety; manual session review and eval harness regression tests verify (no regex layer at runtime).

**Files to create:**
- `app/modules/interview_engine/speech/agent.py`:
  ```python
  class SpeechAgent:
      def __init__(self, client, model: str): ...
      
      async def render(
          self,
          template_name: str,
          template_version: str,
          inputs: dict,
      ) -> SpeechRenderResult:
          # 1. Load template via load_template
          # 2. Substitute {placeholder} with inputs
          # 3. Call OpenAI (no instructor — we want plain text output)
          # 4. Validate: length, safety regex
          # 5. Retry once on safety violation with stricter "remove the word X" instruction
          # 6. On second violation, return fallback static utterance
          # 7. Log everything (template, version, model, latency, tokens, retries, parsed_ok)
          # 8. Return SpeechRenderResult(text, was_fallback, retries, ...)
  
  class SpeechRenderResult(BaseModel):
      text: str
      was_fallback: bool
      retries: int
      template_version: str
      model: str
      latency_ms: int
      tokens_in: int
      tokens_out: int
  ```
- `app/modules/interview_engine/speech/deliveries.py` — typed wrappers around `SpeechAgent.render()` for each template, with the right inputs:
  ```python
  async def render_intro(
      speech_agent: SpeechAgent,
      *, candidate_first_name: str, role_title: str, target_duration_minutes: int,
  ) -> SpeechRenderResult:
      return await speech_agent.render("intro", "v1", {
          "candidate_first_name": candidate_first_name,
          "role_title": role_title,
          "target_duration_minutes": target_duration_minutes,
      })
  ```
  One function per template. Type signatures enforce required inputs.

**Files to modify:**
- `app/modules/interview_engine/structured_agent.py` — replace hardcoded utterance lookups with `await render_<template>(...)` calls. On `was_fallback=True`, log a warning event.
- All prompt template files in `prompts/speech_agent/*.v1.txt` — fill in remaining ones from design doc §7.

**Static fallback utterances** (per template, used when LLM fails / safety fails twice):
- `intro`: `"Hi, I'll be running a short technical screen with you today. We'll be about 15 minutes. Let's get started."`
- `ask_question_standard`: just the raw `question_text` from the QuestionConfig.
- `wrap_normal`: `"That's everything from my side. The recruiting team will be in touch with next steps."`
- (one per template; pick the safest minimal version.)

**Event log kinds:**
- `speech.rendered` — `{"template": "...", "version": "v1", "model": "...", "latency_ms": ..., "tokens_in": ..., "tokens_out": ..., "was_fallback": false, "retries": 0}`
- `speech.safety_violation` — `{"template": "...", "violation_pattern": "...", "raw_output_hash": "..."}` (do NOT log raw violating text in metadata mode)
- `speech.fallback_used` — `{"template": "...", "reason": "safety_max_retries|llm_error|timeout"}`

**Tests:**
- `tests/interview_engine/speech/test_speech_agent.py` — mocked OpenAI client. Test: happy path, retry on safety violation, fallback after second violation, length cap enforcement, placeholder substitution.
- `tests/interview_engine/speech/test_deliveries.py` — each `render_*` function passes correct inputs to `SpeechAgent.render`.
- `tests/interview_engine/speech/prompt_quality/` — `@pytest.mark.prompt_quality` real-LLM tests. Run a representative input through each template, assert no safety violations, assert length limit, assert key invariants (e.g., `intro` does not contain digits referring to question count). These run nightly, not per-PR.

**Acceptance criteria:**
- A full happy-path interview now sounds natural (you'll notice in a manual test).
- All MUST-NOT rules from the templates verified by manual review of 5 sessions: no leaked rubric content, no outcome language in wraps, no examples given on `meta_response`.
- `was_fallback` rate < 5% on real sessions over 50-session sample. (You won't have this much data on day one; track it as a metric for week 1.)

---

### Phase D — Sufficiency Checker (shadow mode)

**Goal:** After every substantive candidate turn, run the Sufficiency Checker. Log its output. **Do NOT use it for flow decisions yet.** This is observation-only mode — used to tune the prompt against real outputs.

**Files to create:**
- `app/modules/interview_engine/evaluators/output_schemas.py`:
  ```python
  class EvidenceQuoteOut(BaseModel):
      signal_value: str
      quote: str
      strength: Literal["weak", "strong"]
  
  class SufficiencyOutput(BaseModel):
      primary_signal_coverage: Literal["none", "partial", "sufficient"]
      primary_signal_confidence: float = Field(ge=0.0, le=1.0)
      evidence_quotes: list[EvidenceQuoteOut]
      incidental_signals_covered: list[EvidenceQuoteOut]
      red_flags_observed: list[str]
      knockout_at_risk: bool
      suggested_action: Literal["ask_followup", "move_on"]
      followup_target: str | None = None
      rationale: str
  ```
- `app/modules/interview_engine/evaluators/sufficiency_checker.py`:
  ```python
  class SufficiencyChecker:
      def __init__(self, client, model: str, template_version: str): ...
      
      async def check(
          self,
          *,
          question: QuestionConfig,
          candidate_transcript: str,
          other_signals_summary: list[str],
      ) -> SufficiencyOutput:
          # 1. Load prompt via load_template("sufficiency_checker", version)
          # 2. Render with question + candidate_transcript + other_signals_summary
          # 3. Call instructor with response_model=SufficiencyOutput
          # 4. Verify each evidence_quote.quote substring is present in candidate_transcript
          #    — if not, drop that quote and log warning (anti-hallucination)
          # 5. Log everything
          # 6. Return validated output
  ```
- `prompts/sufficiency_checker.v1.txt` — full text per design doc §7.18.

**Files to modify:**
- `app/modules/interview_engine/structured_agent.py` — after `UserInputTranscribedEvent(final=True)` and intent → substantive (still hardcoded as "always substantive" in Phase D since Intent Classifier is Phase F), call the sufficiency checker. Log its output. **Do NOT use it to decide follow-ups yet** — flow continues per Phase B logic (always move on).

**Event log kind:**
- `evaluator.sufficiency.checked` — `{"question_id": ..., "primary_signal_coverage": ..., "confidence": ..., "knockout_at_risk": ..., "evidence_count": ..., "rationale": ..., "model": ..., "latency_ms": ..., "verified_quotes": ..., "discarded_quotes_hallucinated": ...}`

**Tests:**
- `tests/interview_engine/evaluators/test_sufficiency_checker.py` — mocked client. Test: schema validation, quote verification (real quotes pass, fake quotes dropped), retry on schema validation failure, log structure.
- `tests/interview_engine/evaluators/prompt_quality/test_sufficiency_checker_real.py` — `@pytest.mark.prompt_quality`. Hand-crafted strong/partial/none answers against known questions; assert classification correct.

**Acceptance criteria:**
- Logs from a real session show sufficiency results that match your manual judgment ≥ 80% of the time.
- Hallucinated-quote rate < 1% on real sessions (sufficiency checker invented a quote that wasn't in the transcript). Tracked as `discarded_quotes_hallucinated` in event log.
- No flow changes — interview behaves identically to Phase C from the candidate's perspective.

---

### Phase E — Sufficiency Checker (active for follow-ups)

**Goal:** Use the Sufficiency Checker output to drive follow-up decisions. Update the SignalLedger with evidence. Match `followup_target` against canned `QuestionConfig.follow_ups`; fall through to dynamic only when no canned matches. Enforce follow-up budget.

**Files to modify:**
- `app/modules/interview_engine/orchestrator/flow.py` — add `decide_followup(suff: SufficiencyOutput, question: QuestionConfig, followups_used: int, max_followups: int) -> FollowupDecision`. Pure function.
- `app/modules/interview_engine/orchestrator/question_selection.py` — add `match_canned_followup(target: str, follow_ups: list[str]) -> int | None` — best-match index by substring/keyword overlap. If overlap above threshold, use canned; else None → dynamic.
- `app/modules/interview_engine/structured_agent.py` — after sufficiency check: update ledger, decide follow-up, render `ask_followup` or `ask_followup_dynamic` accordingly.
- Fill in `prompts/speech_agent/ask_followup.v1.txt` and `prompts/speech_agent/ask_followup_dynamic.v1.txt`.

**Constants to add:**
- `MAX_FOLLOWUPS_PER_QUESTION = 2`
- `CANNED_FOLLOWUP_MATCH_THRESHOLD = 0.5` (or whatever overlap metric you choose; tune)

**Tests:**
- `test_flow.py` — `decide_followup`: budget exhausted → move_on; partial coverage + budget remaining → ask_followup; sufficient → move_on.
- `test_question_selection.py` — `match_canned_followup`: clear match returns index; ambiguous returns None; case-insensitive.
- Integration test: scripted vague-then-specific candidate triggers exactly one follow-up, second answer is sufficient, moves on.

**Acceptance criteria:**
- "Vague-but-fluent" candidate persona (manually tested) gets 1–2 follow-ups, never 0, never escapes with `sufficient`.
- "Strong specific" candidate persona gets 0 follow-ups.
- Follow-up budget never exceeded.

---

### Phase F — Intent Classifier

**Goal:** Replace the "always substantive" assumption with a real intent classifier. Route every candidate turn correctly. Meta requests, off-topic, pause requests, silence handled cleanly.

**Files to create:**
- `evaluators/output_schemas.py` — add:
  ```python
  IntentKind = Literal["substantive", "meta_request", "off_topic", "disclaim", "pause_request", "silence"]
  MetaSubtype = Literal["repeat", "rephrase", "example"]
  
  class IntentOutput(BaseModel):
      intent: IntentKind
      confidence: float = Field(ge=0.0, le=1.0)
      subtype: MetaSubtype | None = None
      reasoning: str
  ```
- `evaluators/intent_classifier.py` — `IntentClassifier.classify(question: QuestionConfig, candidate_transcript: str) -> IntentOutput`. Mirrors sufficiency checker pattern. Bias to "substantive" on parse failure or confidence < 0.6.
- `prompts/intent_classifier.v1.txt` — full text per design doc §7.16.

**Files to modify:**
- `structured_agent.py` — on `UserInputTranscribedEvent(final=True)`:
  1. Run Intent Classifier.
  2. Route per design doc §6:
     - `substantive` → existing path (Sufficiency Checker → ledger → flow decision).
     - `meta_request` → render `meta_response` template, no state advance, increment meta-counter.
     - `off_topic` → render `polite_deflection`, no state advance.
     - `pause_request` → render `pause_request_decline`, await binary choice.
     - `disclaim` → Phase H (for now: log and treat as substantive).
     - `silence` → Phase I (for now: ignore, wait).
- Fill in `meta_response.v1.txt`, `polite_deflection.v1.txt`, `pause_request_decline.v1.txt`.

**Event log kind:**
- `evaluator.intent.classified` — `{"intent": ..., "confidence": ..., "subtype": ..., "reasoning": ..., "model": ..., "latency_ms": ...}`

**Tests:**
- Mocked classifier tests for each routing path.
- Real-LLM `prompt_quality` tests: classify mixed turns ("can you repeat — actually never mind, I think it's about validators") as `substantive`. Classify clear meta as `meta_request`.
- Integration: meta-counter caps at 2 per question; subsequent meta requests get a "let's try with what we've got" wrap-and-redirect.

**Acceptance criteria:**
- Asking "can you repeat that?" no longer gets scored by the Sufficiency Checker.
- Asking "what does this pay?" gets a polite deflection and the original question is re-prompted.
- Asking for a break gets the binary-choice template.

---

### Phase G — Deepening probes

**Goal:** When a signal is already covered with strong evidence (incidental coverage from a prior question), the next question targeting that signal becomes a *deepening probe* rather than a redundant re-ask.

**Files to modify:**
- `orchestrator/question_selection.py` — when picking next question:
  - If all signals already at `failed` → skip.
  - If any signal at `sufficient` with strong evidence → mode = `deepening`.
  - Else → mode = `standard`.
- `structured_agent.py` — when mode is `deepening`, render `ask_question_deepening` with:
  - `original_question_text`: from QuestionConfig
  - `candidate_prior_evidence_summary`: short summary derived from `SignalLedger.signals[sig].evidence_quotes` (concatenate strong quotes, truncate). **Do this with a tiny separate LLM call OR by simple concatenation of the quote strings.** Recommendation: simple concatenation for v1 — avoids another LLM round-trip.
  - `gap_description`: a short string describing what wasn't covered. For v1, derive heuristically: "specific tools or steps not mentioned" or similar generic phrasing. Phase G can grow into LLM-driven gap targeting later.
- Fill in `ask_question_deepening.v1.txt` per design doc §7.4.

**Event log kind:**
- `orchestrator.deepening_probe` — `{"question_id": ..., "covered_signals": [...], "gap_description": ...}`

**Tests:**
- Integration: candidate gives strong evidence on Signal A in Q1; the next question targeting Signal A (Q3) is rendered with deepening template; the original question text is NOT spoken verbatim.
- Adversarial: deepening template never contains tool names the candidate didn't mention. Real-LLM `prompt_quality` test with hand-crafted scenarios.

**Acceptance criteria:**
- Manual test with a strong candidate: at least one deepening probe fires, feels natural, doesn't make the candidate repeat themselves.
- Safety test: scripted prior-evidence summary referencing only "ScriptRunner" never produces a deepening probe mentioning Bitbucket.

---

### Phase H — Disclaim Classifier + knockout flow

**Goal:** Detect knockout disclaim with paranoid triple-gate. Confirmation turn before exit. Respect `tenant_settings.engine_knockout_policy`. Persist `KnockoutFailure[]` in `SessionResult`.

**Files to create:**
- `evaluators/output_schemas.py` — add:
  ```python
  class DisclaimOutput(BaseModel):
      is_disclaim: bool
      confidence: float = Field(ge=0.0, le=1.0)
      evidence_quote: str
      reasoning: str
  ```
- `evaluators/disclaim_classifier.py` — `DisclaimClassifier.check(signal_value: str, candidate_transcript: str) -> DisclaimOutput`. Bias to is_disclaim=False on parse failure. Verify evidence_quote substring is in transcript; on failure, force is_disclaim=False.
- `prompts/disclaim_classifier.v1.txt` — full text per design doc §7.17.
- `prompts/speech_agent/confirmation_turn.v1.txt` — full text per design doc §7.9.
- `prompts/speech_agent/wrap_knockout_exit.v1.txt` — full text per design doc §7.14.

**Files to modify:**
- `structured_agent.py` — Disclaim path:
  1. Triggered when Intent=disclaim OR (Sufficiency.knockout_at_risk AND signal is knockout AND coverage stayed `none` after follow-up budget exhausted).
  2. Run Disclaim Classifier with the specific knockout signal_value.
  3. If `is_disclaim=True AND confidence ≥ 0.85 AND evidence_quote verified in transcript`:
     - Look up `tenant_settings.engine_knockout_policy`:
       - `record_only`: append KnockoutFailure to ledger; continue interview as if signal is `failed`. Do NOT enter confirmation.
       - `close_polite`: enter `KNOCKOUT_CONFIRMATION` phase. Render `confirmation_turn` with the requirement description.
  4. In `KNOCKOUT_CONFIRMATION` phase, on next candidate turn:
     - Re-classify intent: if substantive correction (e.g., "wait, I do have some experience") → cancel exit, mark signal as `partial` with new evidence, return to `MAIN_LOOP`.
     - If confirmed disclaim → transition to `EARLY_EXIT_WRAP` → render `wrap_knockout_exit` → close.
     - If ambiguous → ask one clarifying question (cap: one). If still ambiguous → default to **continue** (false-negative is safer than false-positive).
- Populate `SessionResult.knockout_failures` from the orchestrator's accumulated list at session close.

**Event log kinds:**
- `evaluator.disclaim.checked` — `{...}`
- `orchestrator.knockout_confirmation_entered` — `{"signal_value": ..., "policy": ...}`
- `orchestrator.knockout_confirmed` / `orchestrator.knockout_corrected` / `orchestrator.knockout_ambiguous_continue`
- `orchestrator.knockout_failure_recorded` — `{"signal_value": ..., "reason": ..., "occurred_at_ms": ...}`

**Tests:**
- Mocked classifier tests: triple-gate works; below-threshold disclaim does not trigger; verified-quote requirement enforced.
- Integration test (record_only policy): clear disclaim → KnockoutFailure recorded → interview continues → SessionResult has correct `knockout_failures`.
- Integration test (close_polite policy): clear disclaim → confirmation turn → confirmed → early exit with wrap_knockout_exit. `exit_mode = candidate_ended`.
- Integration test: candidate "corrects" during confirmation → resumes main loop with new evidence; KnockoutFailure NOT recorded.
- Adversarial test: weak answer to knockout signal does NOT trigger disclaim path (only explicit disclaim does).
- Adversarial test: STT mis-transcription "I have used Jira" → "I haven't used Jira" — confirmation turn catches it.

**Acceptance criteria:**
- Run all 14 personas from design doc §15 manually. The "Knockout fisherman" (says yes to having experience but no specifics) does NOT trigger early exit. The "Knockout disclaimer" (explicit "I haven't used X") does, with confirmation.
- KnockoutFailure rows appear in DB for `record_only` policy without ending interview.
- The PII scrubber on `KnockoutFailure.reason` confirmed (test injects a phone number into a synthetic disclaim quote, asserts `[redacted]` in stored row).

---

### Phase I — Silence, pause, reconnect

**Goal:** Robustness layer. Silence tier policy; pause request decline mechanism; bounded reconnect.

**Files to create:**
- `orchestrator/silence_policy.py` — silence tier definitions, dispatch logic.

**Files to modify:**
- `structured_agent.py`:
  - Subscribe to `UserStateChangedEvent` (already imported) — track candidate speaking/listening/silent.
  - On extended silence (15s+), render `gentle_prompt`. On sustained (30s+), one more prompt. On critical (60s+), transition to `TECHNICAL_FAILURE`.
  - Subscribe to participant disconnect (already imported as `_wire_participant_disconnect`) — extend it: hold for 60s; on rejoin, increment `reconnect_count`; if exceeded `max_reconnects=2`, transition to `TECHNICAL_FAILURE`.
  - On rejoin, render `resume_from_state` with the current question text.
- Fill in `gentle_prompt.v1.txt`, `resume_from_state.v1.txt`, `pause_request_decline.v1.txt`, `wrap_candidate_initiated_exit.v1.txt`.
- Pause request handling (already routed in Phase F, now wire the binary-choice follow-through):
  - After `pause_request_decline` rendered, listen for next turn.
  - Re-classify intent. If substantive answer to "continue or end?":
    - "Continue" / "keep going" → resume current question (re-ask via `ask_question_standard`).
    - "End" / "I need to stop" → transition to `CANDIDATE_INITIATED_WRAP` → render `wrap_candidate_initiated_exit`.
    - Ambiguous → re-prompt with the binary choice once. Cap: one re-prompt.

**Event log kinds:**
- `orchestrator.silence_tier` — `{"tier": "extended|sustained|critical"}`
- `orchestrator.reconnect` — `{"reconnect_count": ..., "max_reconnects": ...}`
- `orchestrator.candidate_initiated_exit_choice` — `{"choice": "continue|end|ambiguous"}`

**Tests:**
- Silence tier escalation (mocked timers).
- Reconnect counter; technical failure transition on exceeded.
- Pause request → continue → resume.
- Pause request → end → CANDIDATE_INITIATED_WRAP → SessionResult has `exit_mode=candidate_ended` AND empty `knockout_failures` (distinguishes from knockout exit).

**Acceptance criteria:**
- Manual test: walk away from mic for 30s; agent gently prompts. Walk away for 60s; technical failure exit fires.
- Manual test: ask for a 5-min break; decline + binary choice fires; choosing "end" produces clean `CANDIDATE_INITIATED_WRAP` exit.
- Manual test: refresh browser tab mid-interview; rejoin; agent welcomes back and re-asks current question.
- Manual test: refresh 3 times; third refresh exceeds reconnect cap; technical-failure exit fires.

---

### Phase J — Hardening + adversarial pass

**Goal:** Production-grade defenses. Manual eval against the design doc §15 checklist. Comprehensive observability.

**Files to modify:**
- `structured_agent.py`:
  - Defense in depth: every Speech Agent rendered output passes through `safety.check_safety()` before `session.say()`. (Already enforced inside SpeechAgent.render, but assert again here to catch any direct say() calls.)
  - On any LLM call timeout (per AIConfig timeout, default 8s) + 1 retry: degrade gracefully. For Speech Agent: use static fallback. For evaluators: log and assume conservative defaults (sufficiency → partial; intent → substantive; disclaim → not_disclaim).
  - Add a global "model versions snapshot" event at session start: `system.versions` payload includes prompt versions in use, model IDs, ledger schema version.

**New tests / artifacts:**
- `docs/eval/adversarial_checklist.md` — paste design doc §15 checklist as a manual test plan. Run before any prompt or model change ships.
- `docs/eval/miscall_log_template.md` — template for logging miscall observations (input, expected, actual, hypothesis, resolution). Per design doc §13.4.

**Final review pass:**
- Code review against every "Hard rule" / "MUST NOT" in the design doc — spot any violations.
- Every public symbol exported through the right `__init__.py`.
- `tests/test_module_boundaries.py` still green.
- Mypy strict still passes.
- Ruff clean.
- Event log envelope from a representative session reviewed end-to-end: every state transition logged, every LLM call logged, every safety violation logged.

**Acceptance criteria:**
- Full adversarial checklist passes (manual run-through).
- 5 representative sessions reviewed: no rubric leakage, no outcome language, no improper auto-decisions.
- Cost-per-session metric tracked; within order-of-magnitude target ($0.30–$0.60 v1).

---

## 8. Cross-cutting rules (apply to every phase)

These are pulled from the design document plus codebase discipline. They are non-negotiable.

1. **Tenant_id at every DB boundary.** Bypass-RLS does not absolve you of tenant filtering. Every query in new code includes `WHERE tenant_id = :tenant_id`.
2. **No rubric content in Speech Agent prompts.** Ever. The Speech Agent's input is template + delivery context only — no `positive_evidence`, no `red_flags`, no `evaluation_hint`, no SignalLedger.
3. **Output schema validation on every LLM call.**
   - **Speech Agent (Phase C and on):** length cap is lenient — measured post-hoc on the completed text, logged on `speech.rendered`, never retried, never fallback-triggered. Retries fire only on OpenAI API errors (timeout, 5xx, pre-first-token disconnect); one retry, then fallback to a static per-template utterance. Mid-stream disconnects after the first token is yielded are non-recoverable — the partial utterance plays through and the cause is logged on `speech.stream_interrupted`.
   - **Evaluators (Phase D and on):** instructor + Pydantic. On validation failure: retry once with stricter instruction; on second failure: fall back to conservative output.
4. **Evidence quote verification.** Every quote returned by Sufficiency Checker or Disclaim Classifier must be a substring of the actual candidate transcript. Hallucinated quotes are dropped silently and counted as a metric.
5. **Outcome-neutral language.** Enforced by template prompt's MUST-NOT rules (the gate); verified by manual session review and eval harness regression tests. The disallowed-phrase list in §7.13 of the design doc applies to all Speech Agent templates as MUST-NOT rules in the prompt. There is no regex layer at runtime. See design doc §11.5 (re-amended 2026-05-05) for the three-layer safety model.
6. **Versioned prompt templates as files.** Never as Python string literals. Loaded via `load_template(role, name, version)`. Version recorded in event log on every render.
7. **Log every LLM call.** Template, version, model, latency, tokens, validation status, retries. The audit envelope is the source of truth for "why did the agent do X."
8. **No new DB tables for v1.** All state lives in `InterviewState` (memory + Redis), `SessionResult` (DB), audit envelope (file/S3). If you reach for a migration, stop and reconsider.
9. **No direct vendor SDK imports outside `app/ai/realtime.py` and `app/ai/client.py`.** Module boundary tests will catch this.
10. **No silent state advance on non-substantive turns.** Meta requests, off-topic deflections, pause requests, silence — none of these advance the question pointer or update the ledger.
11. **Knockout exit is a triple-gate.** Intent flag → Disclaim Classifier (≥0.85, verified quote) → Confirmation turn (close_polite policy only). Any single gate vetoes the exit.
12. **No outcome decisions in the live agent.** Agent collects, agent closes. Period.
13. **No PII in logs at INFO level.** `engine_log_user_transcripts=False` is the default for a reason.

---

## 9. Anti-patterns (specific to this codebase — Claude Code may try these; reject)

- **Adding fields to `SteeringObservation`.** Use `notes` for evidence; do not extend the schema for v1.
- **Creating a new `interview_*` module alongside `interview_engine` and `interview_runtime`.** The naming is confusing enough; the new code goes inside `interview_engine`.
- **Replacing the existing audit envelope with a new event store.** Hook into `EventCollector.append(kind=...)` — do not rewrite the envelope system.
- **Calling OpenAI directly in evaluators.** Use `instructor.from_openai(get_openai_client())` — the project standard.
- **Importing `livekit.plugins.openai` etc. in the new structured agent.** Use `app.ai.realtime` factories.
- **Adding `reasoning_effort` to OpenAI calls for chat models.** They reject it. Gate on `ai_config.*_effort` being non-empty (mirror existing pattern).
- **Auto-applying tenant_settings overrides outside the documented sites.** `engine_agent_name` override applies in agent prompts only (per `tenant_settings/schemas.py` docstring); LiveKit routing label stays on env value.
- **Introducing a new `/api/internal/*` HTTP boundary.** Phase 3 retired this. Use in-process function calls (`build_session_config`, `record_session_result`).
- **Adding new migrations for SignalLedger / observation tables.** All ledger state lives in memory + audit envelope; final summary in existing `SessionResult`.
- **Modifying frontend code.** This is a backend-only change. The frontend already handles `session_outcome` participant attribute updates.
- **Using `BackgroundTasks` for the Sufficiency Checker / Intent Classifier calls.** They are part of the per-turn synchronous loop in the agent process. Don't background them.

---

## 10. Definition of "done" for this work

The full v1 is complete when:

1. All 10 phases (A–J) have shipped, with their acceptance criteria met.
2. A solo developer (you) has manually run all 14 adversarial personas from design doc §15 and recorded results in the miscall log.
3. The miscall log has at least 30 entries from real testing, and every "high-severity" entry has been resolved (prompt update, pipeline fix, or accepted-known-limitation with rationale).
4. A representative real session's audit envelope JSON has been reviewed end-to-end and every event makes sense.
5. The cost-per-session metric is within order-of-magnitude of target ($0.30–$0.60).
6. Mypy strict, ruff, and `tests/test_module_boundaries.py` are all green.
7. No new Alembic migration was required.
8. The frontend `session_outcome` events still work (no regressions on the candidate UX).
9. The SessionResult populated by the structured agent contains: every QuestionConfig represented in `question_results` (with correct `was_skipped`, `probes_fired`, `observations`, `transcript_entries`), the full transcript, accurate timing, and any KnockoutFailure rows for `record_only` policy.
10. The Report Builder team (you, future) has a documented contract for what's in the audit envelope — list of event kinds, payload shapes per kind. Add this as `docs/interview_engine/event_kinds.md` in Phase J.

---

## 11. Order-of-operations summary for Claude Code

When you (the coding agent) pick up this task:

1. Read the design document end-to-end.
2. Read this implementation prompt end-to-end.
3. Read root `CLAUDE.md` and `backend/nexus/CLAUDE.md`.
4. Read `app/modules/interview_engine/agent.py` end-to-end (current clean-slate state).
5. Read `app/modules/interview_runtime/schemas.py` and `service.py`.
6. Read `app/ai/realtime.py` and `app/ai/config.py`.
7. The §4 `session.say()` API spike was completed on 2026-05-04 against the `livekit-agents>=1.5.4,<2` source: the method exists, returns a `SpeechHandle`, and `await`-ing it blocks until playback completes. No further API spike needed; a manual end-to-end smoke test in a real LiveKit session remains a Phase B acceptance criterion.
8. **Begin Phase A.** Do not proceed to B until A's acceptance criteria are met.
9. After each phase: run all tests. Run mypy strict. Run ruff. Run `test_module_boundaries.py`. Manual smoke test in a real LiveKit session.
10. When in doubt: ask, do not guess. The cost of a clarifying question is much lower than the cost of a wrong assumption that propagates through three phases.

---

*End of implementation prompt.*
