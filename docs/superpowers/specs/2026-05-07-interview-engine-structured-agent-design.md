# Interview Engine — Structured Agent Design

**Status:** Draft for review
**Date:** 2026-05-07
**Author:** Ishant + Claude (collaborative brainstorming)
**Scope:** Replace the placeholder `GenericInterviewAgent` with a structured forensic interviewer composed of a Judge LLM, deterministic Python State Engine, Speaker LLM, and audit envelope.

---

## 1. What this is, and what this is not

This spec documents the **locked architecture** for the new interview engine agent. Architectural decisions originated in the user's detailed build prompt and were refined through three rounds of brainstorming (confirmed in this conversation). The spec does **not** introduce new architectural decisions — it captures the locked design and the integration plan against the existing nexus codebase.

The goal: by the end of implementation, a candidate can join a session and receive a structured screening interview that asks questions from a pre-generated bank, probes follow-ups based on rubric coverage, handles disclosures and redirects, and produces a forensic-grade audit trail plus a typed `SessionResult` for the post-session Report Builder.

**This is NOT a generic voice agent.** The agent is constrained: it does not invent questions, does not score candidates (scoring lives in the Report Builder), and does not freely roam. It executes a controlled process while feeling natural to the candidate.

---

## 2. Goals and non-goals

**Goals (v1):**

- Per-turn pipeline: STT → Judge → State Engine → Speaker → TTS, with structural rubric isolation between Judge (sees rubric, decides) and Speaker (no rubric, speaks).
- Append-only SignalLedger of evidence observations + denormalized per-signal coverage snapshots.
- QuestionQueue with per-question state machine (mandatory enforcement, hard advance, no return).
- CandidateClaimsPool capped at 50 entries (drop-oldest).
- Self-healing on Judge output failures (validation degrades gracefully and logs loudly).
- Forensic-grade audit envelope per session (file or S3) with content-hashed prompts and replay determinism.
- Typed `SessionResult` extension carrying ledger + queue + claims for the Report Builder.
- Crash recovery via periodic checkpoint to `sessions.engine_checkpoint`.
- Frontend participant-attribute publishing (current_question_index, total_questions, time_remaining_seconds, session_outcome).
- Prompt-injection resistance and rubric-leak resistance verified by manual test cases.

**Non-goals (v1, deferred):**

- Filler audio to mask Judge latency.
- Per-session STT keyterm extraction (deferred behind a hook seam — see §10.5).
- Real-time scoring and probe selection beyond bank-controlled probes (Phase 3D `analysis` module — separate effort).
- Post-session report compilation (Phase 3D `reporting` module — separate effort).
- Tenant-level persona configuration (`engine_persona_id` on TenantSettings — deferred; v1 uses a single hardcoded `DEFAULT_PERSONA`).
- Automated CI eval suite for the agent (per the user's stated preference for manual testing of AI agents).
- `gpt-5.4` (full size) for Judge or Speaker — both default to `gpt-5.4-mini` in v1.

---

## 3. Existing landscape

The following scaffolding is already in place and is built **on**, not replaced:

| Asset | Location | Role in new design |
|---|---|---|
| `AgentServer`, entrypoint, prewarm | `app/modules/interview_engine/agent.py` | Kept. `agent.py` becomes thin: server + entrypoint + LiveKit hooks. Lifecycle delegated to orchestrator. |
| Audio pipeline factories | `app/ai/realtime.py` | Kept unchanged. STT/TTS/VAD/turn-detector/noise-cancellation factories continue to be sole entry points. |
| `SessionConfig`, `SessionResult`, `KnockoutFailure`, `TranscriptEntry` | `app/modules/interview_runtime/schemas.py` | `SessionConfig` unchanged. `SessionResult` **extended** (see §7). `KnockoutFailure` and `TranscriptEntry` unchanged. |
| `build_session_config`, `record_session_result` | `app/modules/interview_runtime/service.py` | Unchanged signatures. `record_session_result` continues to be the only writer of session result data. |
| `EventCollector`, `EventLogEnvelope`, sinks | `app/modules/interview_engine/event_log/` | Kept. New event kinds added; no schema changes to envelope structure. |
| `event_kinds.py` | `app/modules/interview_engine/event_kinds.py` | Extended with new kinds (see §6.4). |
| `PromptLoader` | `app/ai/prompts.py` | Used for both Judge and Speaker system prompts. |
| `set_llm_span_attributes()` | `app/ai/tracing.py` | Used on every Judge and Speaker call. |
| `get_openai_client()` | `app/ai/client.py` | **Not used** by the engine — engine uses a separate `AsyncOpenAI` client (see §6.5 / §6.6). The instructor batch client stays for JD/bank pipelines only. |
| Tenant settings | `app/modules/tenant_settings/` | `engine_agent_name` and `engine_knockout_policy` consumed. `engine_persona_id` is **not** added in v1. |
| Question bank | `app/modules/question_bank/` | Read-only. `follow_ups: list[str]` accessed by array index for `probe_id`. |

**Replaced wholesale:**

- `GenericInterviewAgent` internals (in `agent.py`). The class becomes a thin LiveKit `Agent` subclass that delegates `on_enter`, `on_user_turn_completed`, and close to the new orchestrator.
- `_build_system_prompt`, `_build_session_result` placeholder helpers in `agent.py` are deleted.
- `QuestionResult` Pydantic class in `interview_runtime/schemas.py` is **deprecated and removed** from `SessionResult` (replaced by `question_queue: QuestionQueueSnapshot` — see §7). Verified consumers: only the placeholder agent we are replacing + 2 test files passing `question_results=[]`. Removal is safe.
- `SteeringObservation` is **marked deprecated** in code (kept readable for legacy `raw_result_json` blobs; new sessions never emit it). The new `Observation` type lives in `app/modules/interview_engine/models/judge.py` and is the only observation type new code emits.

---

## 4. Per-turn pipeline (locked)

```
candidate audio → STT → utterance text
                              ↓
                  ┌───────────────────────┐
                  │  Judge (LLM, ~600ms)  │  ← sees rubric, claims, last 8 turns, active question
                  │  structured output    │     emits: thought, observations, claims, next_action,
                  └───────────────────────┘            next_action_payload, turn_metadata
                              ↓
                  ┌───────────────────────┐
                  │   State Engine        │  ← deterministic, no LLM
                  │  validate, mutate     │     writes ledger, mutates queue/claims, applies hard rules
                  │  resolve bank text    │     resolves Speaker input
                  └───────────────────────┘
                              ↓
                  ┌───────────────────────┐
                  │  Speaker (LLM, ~400ms)│  ← NO rubric, sees: bank text, persona, recent turns,
                  │  streaming Responses  │     claims pool, instruction kind
                  └───────────────────────┘     streams tokens
                              ↓
                  Cartesia TTS plugin (sentence-tokenized streaming) → candidate audio
```

**Structural property:** Judge has rubric + decisions. Speaker speaks. State Engine is the firewall — it sees both sides and prepares isolated contexts for each. This is enforced by the prompt-construction code (separate input builders per role) and by the fact that the Speaker's input never carries rubric content.

**Latency target:** time-to-first-audio after candidate finishes ≈ 1200–1800ms. Acceptable for v1.

**Interruption policy:** `allow_interruptions=True` on `session.say()`. Top interviewers don't expect candidates to wait politely; barge-in is natural and welcome.

**No filler audio in v1** — deferred per build prompt.

**Why two LLM calls and not one (re-stated for the record):** Rubric isolation requires structurally separating rubric-aware reasoning from candidate-facing speech. Bank-controlled probe selection requires the Judge to pick a probe by ID and the State Engine to look it up — the Speaker only rephrases what the State Engine hands it. A single-call agent cannot enforce these boundaries.

---

## 5. Module layout

```
app/modules/interview_engine/
├── agent.py                        # SLIM: AgentServer + entrypoint + LiveKit hooks only
├── orchestrator.py                 # NEW: per-turn pipeline (Judge → State Engine → Speaker)
├── state/
│   ├── __init__.py                 # re-exports StateEngine + snapshot types
│   ├── engine.py                   # StateEngine — owns ledger, queue, claims, lifecycle
│   ├── ledger.py                   # SignalLedger (entries + snapshots + next_seq)
│   ├── queue.py                    # QuestionQueue (per-question state machine)
│   ├── claims.py                   # CandidateClaimsPool (capped 50, drop-oldest)
│   ├── lifecycle.py                # SessionLifecycle FSM + KnockoutFailures + TimeBudget
│   └── checkpoint.py               # serialize/deserialize for sessions.engine_checkpoint
├── judge/
│   ├── __init__.py                 # re-exports JudgeService
│   ├── service.py                  # call OpenAI Responses (structured), retry policy, parse
│   ├── input_builder.py            # assemble input from State Engine snapshots
│   └── fallback.py                 # synthetic JudgeOutput synthesizers
├── speaker/
│   ├── __init__.py                 # re-exports SpeakerService
│   ├── service.py                  # call OpenAI Responses (streaming), AsyncIterable[str]
│   ├── input_builder.py            # assemble input + persona + bank text
│   ├── persona.py                  # DEFAULT_PERSONA + resolution logic
│   └── instructions.py             # InstructionKind enum + per-kind context helpers
├── bank_resolver.py                # pure function — JudgeOutput → bank text + InstructionKind
├── frontend_attributes.py          # NEW: constants + AttributePublisher (diffing wrapper)
├── audit_events.py                 # NEW: payload schemas for new event kinds
├── stt_factory.py                  # NEW: build_stt_plugin_for_session(SessionConfig) -> STT
├── models/
│   ├── __init__.py                 # re-exports ALL model classes (per Round 3.6 requirement)
│   ├── judge.py                    # JudgeOutput, NextAction, NextActionPayload, Observation,
│   │                               #   ClaimEntry (Judge-emitted shape), TurnMetadata
│   ├── speaker.py                  # SpeakerInput, InstructionKind
│   ├── ledger.py                   # LedgerEntry, SignalSnapshot, CoverageState,
│   │                               #   SignalLedgerSnapshot
│   ├── queue.py                    # QuestionState, QuestionQueueSnapshot
│   └── claims.py                   # ClaimEntry (canonical), ClaimsPoolSnapshot
├── event_log/                      # EXISTING — unchanged
├── event_kinds.py                  # EXISTING — extended
├── __init__.py                     # EXISTING
└── __main__.py                     # EXISTING
```

**Re-export discipline:** `models/__init__.py` re-exports every model class so consumers do `from app.modules.interview_engine.models import JudgeOutput, Observation, LedgerEntry, …` without knowing which sub-file each class lives in.

**Prompts:** under `backend/nexus/prompts/v1/engine/` — `judge.system.txt` and `speaker.system.txt`. Loaded via existing `PromptLoader.get("engine/judge.system")` and `PromptLoader.get("engine/speaker.system")` (the loader supports nested paths).

**Cross-module public-API discipline:** `app/modules/interview_engine/__init__.py` re-exports the `server` symbol only — it has no other consumers. Internal modules import each other freely (intra-module).

---

## 6. Component design

### 6.1 Pydantic models module (`models/`)

Single typed contract layer. Every other component imports from here.

#### `models/judge.py` (Judge output schema — locked)

```python
class NextAction(StrEnum):
    advance = "advance"
    probe = "probe"
    clarify = "clarify"                      # candidate asks "what do you mean by X?"
    repeat = "repeat"                        # candidate asks "can you repeat that?"
    redirect_off_topic = "redirect_off_topic"
    redirect_abusive = "redirect_abusive"
    safe_redirect_injection = "safe_redirect_injection"
    acknowledge_no_experience = "acknowledge_no_experience"
    polite_close = "polite_close"
    end_session = "end_session"

class CoverageTransition(StrEnum):
    # Forward progression
    none_to_partial = "none→partial"
    partial_to_partial = "partial→partial"   # probe pulled more evidence; still partial
    partial_to_sufficient = "partial→sufficient"
    none_to_sufficient = "none→sufficient"   # rare but legal — single-utterance answer hits enough anchors

    # Failure terminal (set when candidate discloses no experience or knockout)
    none_to_failed = "none→failed"
    partial_to_failed = "partial→failed"
    sufficient_to_failed = "sufficient→failed"
    failed_to_failed = "failed→failed"       # idempotent re-disclosure across turns

    # Backwards transitions (sufficient→partial, etc.) are NEVER legal.
    # No "strong" state — answer-quality grading lives in the post-session Report Builder.

class Observation(BaseModel):
    signal_value: str
    anchor_id: int = Field(ge=0)             # index into positive_evidence list
    evidence_quote: str = Field(min_length=1, max_length=500)  # verbatim from utterance
    coverage_transition: CoverageTransition

class ClaimEntry(BaseModel):
    claim_topic: str = Field(max_length=40)
    claim_text: str = Field(max_length=200)
    source_quote: str = Field(min_length=1, max_length=500)  # verbatim

class AdvancePayload(BaseModel):
    kind: Literal["advance"] = "advance"
    target_question_id: str

class ProbePayload(BaseModel):
    kind: Literal["probe"] = "probe"
    probe_id: str                            # array index of follow_ups, e.g. "0", "1", "2"
    probe_rationale: str = Field(max_length=200)

class ClarifyPayload(BaseModel):
    kind: Literal["clarify"] = "clarify"
    # No fields; the Speaker uses recent_turns + active question text to compose
    # a brief, rubric-free explanation of what the candidate asked about.

class RepeatPayload(BaseModel):
    kind: Literal["repeat"] = "repeat"
    # No fields; the State Engine resolves the most-recent agent transcript entry
    # and the orchestrator delivers it via session.say() WITHOUT a Speaker LLM call.

class RedirectOffTopicPayload(BaseModel):
    kind: Literal["redirect_off_topic"] = "redirect_off_topic"

class RedirectAbusivePayload(BaseModel):
    kind: Literal["redirect_abusive"] = "redirect_abusive"

class SafeRedirectInjectionPayload(BaseModel):
    kind: Literal["safe_redirect_injection"] = "safe_redirect_injection"

class AcknowledgeNoExperiencePayload(BaseModel):
    kind: Literal["acknowledge_no_experience"] = "acknowledge_no_experience"
    failed_signal_value: str

class PoliteClosePayload(BaseModel):
    kind: Literal["polite_close"] = "polite_close"
    reason: str  # e.g. "knockout_recorded", "all_mandatory_complete", "judge_fallback_no_advance_target"

class EndSessionPayload(BaseModel):
    kind: Literal["end_session"] = "end_session"
    initiated_by: Literal["candidate_initiated", "agent_initiated"]

NextActionPayload = Annotated[
    Union[AdvancePayload, ProbePayload, ClarifyPayload, RepeatPayload,
          RedirectOffTopicPayload, RedirectAbusivePayload, SafeRedirectInjectionPayload,
          AcknowledgeNoExperiencePayload, PoliteClosePayload, EndSessionPayload],
    Field(discriminator="kind"),
]

class TurnMetadata(BaseModel):
    candidate_disclosed_no_experience: bool = False
    candidate_disclosed_knockout: bool = False
    candidate_off_topic: bool = False
    candidate_abusive: bool = False
    candidate_attempted_injection: bool = False
    candidate_wants_to_end: bool = False

class JudgeOutput(BaseModel):
    thought: str = Field(max_length=600)     # internal reasoning, NOT spoken
    observations: list[Observation] = Field(default_factory=list, max_length=10)
    candidate_claims: list[ClaimEntry] = Field(default_factory=list, max_length=5)
    next_action: NextAction
    next_action_payload: NextActionPayload
    turn_metadata: TurnMetadata = Field(default_factory=TurnMetadata)

    @model_validator(mode="after")
    def _check_discriminator_alignment(self) -> "JudgeOutput":
        if self.next_action.value != self.next_action_payload.kind:
            raise ValueError(
                f"next_action {self.next_action} does not match payload kind "
                f"{self.next_action_payload.kind}"
            )
        return self
```

#### `models/speaker.py` (Speaker input schema)

```python
class InstructionKind(StrEnum):
    deliver_first_question = "deliver_first_question"
    deliver_question = "deliver_question"
    deliver_probe = "deliver_probe"
    clarify = "clarify"                       # rubric-free explanation of active question term
    repeat = "repeat"                         # bypass Speaker LLM; replay cached agent utterance
    redirect_off_topic = "redirect_off_topic"
    redirect_abusive = "redirect_abusive"
    safe_redirect_injection = "safe_redirect_injection"
    acknowledge_no_experience = "acknowledge_no_experience"
    polite_close = "polite_close"

class SpeakerInput(BaseModel):
    instruction_kind: InstructionKind
    bank_text: str | None                    # main question text, probe text, or None for canned redirects
    last_candidate_utterance: str | None     # None on session start
    recent_turns: list[TranscriptEntry]      # last 8 turns max
    claims_pool_snapshot: list[ClaimEntry]
    persona_name: str                        # resolved at runtime
    failed_signal_value: str | None = None   # set when instruction_kind == acknowledge_no_experience
```

#### `models/ledger.py`

```python
class CoverageState(StrEnum):
    none = "none"
    partial = "partial"
    sufficient = "sufficient"
    failed = "failed"                        # terminal — set when candidate discloses no
                                              # experience or knockout for this signal.
    # Note: no "strong" state. Answer-quality grading lives in the post-session Report Builder.

class LedgerEntry(BaseModel):
    seq: int
    turn_id: str                             # UUID v4 from the originating turn
    signal_value: str
    anchor_id: int                           # for failure entries, anchor_id is -1 sentinel
    evidence_quote: str                      # for failure entries, the candidate's disclosure quote
    coverage_before: CoverageState
    coverage_after: CoverageState
    recorded_at_ms: int                      # monotonic from session start

class SignalSnapshot(BaseModel):
    signal_value: str
    coverage: CoverageState
    anchors_hit: list[int] = Field(default_factory=list)  # unique anchor_ids
    last_observation_seq: int | None = None

class SignalLedgerSnapshot(BaseModel):
    entries: list[LedgerEntry]
    snapshots: dict[str, SignalSnapshot]    # keyed by signal_value
    next_seq: int
```

#### `models/queue.py`

```python
class QuestionStatus(StrEnum):
    pending = "pending"
    active = "active"
    completed = "completed"
    skipped = "skipped"                      # only for non-mandatory; mandatory cannot be skipped

class QuestionState(BaseModel):
    question_id: str
    position: int
    is_mandatory: bool
    status: QuestionStatus
    main_asked_at_turn: int | None = None
    probes_asked_ids: list[str] = Field(default_factory=list)
    probes_remaining_ids: list[str] = Field(default_factory=list)
    anchors_hit_ids: list[int] = Field(default_factory=list)
    time_spent_ms: int = 0
    turn_count: int = 0

class QuestionQueueSnapshot(BaseModel):
    questions: list[QuestionState]
    active_index: int | None                 # None before first question is delivered
```

#### `models/claims.py`

```python
class ClaimEntry(BaseModel):
    claim_topic: str = Field(max_length=40)
    claim_text: str = Field(max_length=200)
    source_quote: str = Field(min_length=1, max_length=500)
    captured_at_turn: int
    captured_at_seq: int

class ClaimsPoolSnapshot(BaseModel):
    entries: list[ClaimEntry]                # capped at 50, oldest dropped first
```

The Judge-emitted `ClaimEntry` (in `models/judge.py`) is a narrower shape (no `captured_at_*`); the State Engine canonicalizes to the `models/claims.py` shape on ingest.

### 6.2 State Engine (`state/engine.py`)

`StateEngine` is the deterministic Python core. No LLM calls. Heavily unit-testable.

**Public API (sketch):**

```python
class StateEngine:
    def __init__(self, *, session_config: SessionConfig, time_budget_seconds: float, knockout_policy: KnockoutPolicy):
        ...

    # Initialization
    def initialize_for_session_start(self) -> JudgeOutput:
        """Synthesize the first JudgeOutput (next_action: advance to position 0). Returns it
        so the orchestrator can record it as a synthetic Judge call in the audit envelope."""

    # Per-turn entry point
    def process_judge_output(
        self, *, turn_id: str, judge_output: JudgeOutput, candidate_utterance_text: str | None,
    ) -> StateEngineDecision:
        """Validate, mutate, resolve. Returns a StateEngineDecision the orchestrator hands to Speaker."""

    # Snapshots (read-only views for prompts and persistence)
    def ledger_snapshot(self) -> SignalLedgerSnapshot: ...
    def queue_snapshot(self) -> QuestionQueueSnapshot: ...
    def claims_snapshot(self) -> ClaimsPoolSnapshot: ...
    def lifecycle_snapshot(self) -> LifecycleSnapshot: ...

    # Checkpoint (full in-memory state)
    def to_checkpoint(self) -> EngineCheckpoint: ...
    @classmethod
    def from_checkpoint(cls, checkpoint: EngineCheckpoint, session_config: SessionConfig) -> "StateEngine": ...
```

`StateEngineDecision` carries: the resolved Speaker input (`SpeakerInput`), the validation warnings/errors that occurred (for audit), the new lifecycle state (active / closing / closed), and the publish-ready frontend attribute values.

**Hard rules enforced by State Engine (override LLM):**

1. **Mandatory questions cannot be skipped.** If Judge emits `advance` while there is an unanswered mandatory question that is not the active question, validation fails: drop the advance, fall back to "advance to next pending mandatory."
2. **`next_action: end_session` is blocked** unless: (a) a `KnockoutFailure` has been recorded this session, OR (b) all mandatory questions are completed, OR (c) `time_budget.exhausted` is genuinely true. Otherwise fall back to "advance to next pending mandatory" or "polite_close" if none remain.
3. **Hard advance: once a question advances, no return.** Late additions to prior questions are recorded in the transcript only and handled post-session by the Report Builder.
4. **Time budget is soft.** Time tracking is informational. The agent never cuts the candidate off mid-utterance.
5. **All state mutations are sequence-numbered** (`LedgerEntry.seq`, monotonic from 1). Replay determinism: given the audit envelope, the State Engine's final state can be reconstructed by replaying all `state.mutation` events in seq order.

**Self-healing on Judge output failures (locked from build prompt):**

| Failure | State Engine response |
|---|---|
| Illegal coverage transition (e.g., `sufficient → partial`) | Drop the ledger update for that observation, record `validation_warning` in audit, continue. |
| Invalid `probe_id` (not in active question's `follow_ups`) | Fall back to first unused `follow_up`, or `advance` if none remain. Log warning. |
| Invalid `target_question_id` (e.g., advancing to a completed question) | State Engine picks the next pending mandatory question itself. Log warning. |
| Mismatched discriminator (caught by Pydantic `model_validator`) | Reject entire output, fall back to "advance to next pending mandatory" with canned acknowledgment. Log error. |
| Malformed JSON / Pydantic parse failure | Same fallback, log error with raw response. |
| **No advance target available** (all mandatory done, fallback can't advance) | Fall back to `next_action: polite_close` with `reason: "judge_fallback_no_advance_target"`. |

The system never crashes on Judge output. It degrades and logs.

### 6.3 Bank text resolver (`bank_resolver.py`)

Pure function: given a `JudgeOutput` (post-State-Engine validation) and the current `SessionConfig` + `QuestionQueueSnapshot`, return:

```python
class ResolvedBankText(BaseModel):
    instruction_kind: InstructionKind
    bank_text: str | None                    # main question text, probe text, or None
    failed_signal_value: str | None = None
```

Lookup logic by `next_action`:

| `next_action` | bank_text source |
|---|---|
| `advance` | `active_question.text` (after queue moves to target) |
| `probe` | `active_question.follow_ups[int(probe_id)]` |
| `acknowledge_no_experience` | None (canned scaffold in Speaker) |
| `redirect_*`, `safe_redirect_injection` | None (canned scaffold in Speaker) |
| `polite_close` | None (canned close scaffold in Speaker) |
| `end_session` | None — Speaker emits no further utterance; State Engine transitions lifecycle |

The State Engine calls this resolver after validation and hands the result to the orchestrator as part of `StateEngineDecision`.

### 6.4 Audit envelope writer (`audit_events.py` + extended `event_kinds.py`)

Wraps existing `EventCollector`. Defines new event kinds and their payload schemas.

**New event kinds added to `event_kinds.py`:**

```python
# Engine turn loop
TURN_STARTED = "turn.started"                # STT finalized, before Judge
JUDGE_CALL = "judge.call"                    # real Judge LLM call
JUDGE_SYNTHETIC = "judge.synthetic"          # session-start synthetic JudgeOutput
JUDGE_FALLBACK = "judge.fallback"            # canned fallback after retry+parse failure
JUDGE_VALIDATION = "judge.validation"        # State Engine self-heal events (warning/error)
STATE_MUTATION = "state.mutation"            # one per ledger/queue/claim mutation
SPEAKER_CALL = "speaker.call"                # Speaker LLM call (input + final utterance)
SPEAKER_CACHED = "speaker.cached"            # repeat flow — no LLM call; replays cached prior agent utterance
SPEAKER_OUTPUT = "speaker.output"            # final assembled utterance text (post-stream)
SPEAKER_ERROR = "speaker.error"              # streaming TTFT failure; canned recovery delivered
TURN_COMPLETED = "turn.completed"            # turn end marker

# Lifecycle
LIFECYCLE_TRANSITION = "lifecycle.transition"
CHECKPOINT_WRITTEN = "checkpoint.written"

# Frontend
FRONTEND_ATTRIBUTE_PUBLISHED = "frontend.attribute.published"
```

**Payload schema sketches (full Pydantic in `audit_events.py`):**

- `JudgeCallPayload`: `turn_id`, `model: str` (snapshot), `prompt_hash`, `input_summary` (lengths, not raw), `output: JudgeOutput`, `latency_ms`, `usage: {prompt_tokens, completion_tokens}`.
- `JudgeSyntheticPayload`: `turn_id`, `output: JudgeOutput`, `reason: "session_start"`.
- `JudgeFallbackPayload`: `turn_id`, `reason: Literal["timeout","parse_error","validation_error","no_advance_target"]`, `original_failure_context: dict` (raw response if parse_error, exception type if timeout, validation list if validation_error), `synthesized_output: JudgeOutput`.
- `JudgeValidationPayload`: `turn_id`, `level: Literal["warning","error"]`, `code: str`, `details: dict`.
- `StateMutationPayload`: `turn_id`, `seq`, `kind: Literal["ledger.append","queue.advance","queue.probe","queue.complete","claims.add","claims.drop_oldest","lifecycle.transition","knockout.recorded"]`, `before: dict | None`, `after: dict`.
- `SpeakerCallPayload`: `turn_id`, `model: str`, `prompt_hash`, `instruction_kind`, `bank_text_present: bool`, `latency_ms_first_token`, `latency_ms_total`, `usage`, `final_utterance: str`.
- `SpeakerCachedPayload`: `turn_id`, `instruction_kind: "repeat"`, `source_turn_id: str` (the prior turn whose agent utterance was replayed), `final_utterance: str`.
- `SpeakerErrorPayload`: `turn_id`, `model: str`, `error_class: str`, `error_message: str` (truncated 500 chars), `recovery_utterance: str`.
- `FrontendAttributePayload`: `turn_id | None`, `attribute_name`, `value`.

**Content hashing for prompts:** at envelope construction, the orchestrator computes `controller_prompt_hash` (the orchestrator's own constants, if any) and `task_prompt_hashes = {"judge": sha256_of_judge_system_prompt, "speaker": sha256_of_speaker_system_prompt}`. Hashes change → audit envelope shows the prompt version delta.

**Redaction modes:** Candidate utterance text is **NOT redacted** in either `metadata` or `full` mode — the utterance is the audit-grade artifact required for replay determinism and forensic capability. PII concerns (names, emails, phones inside knockout disclosures) are handled separately by `KnockoutFailure._scrub_pii`. The `metadata` mode redacts only ancillary details that aren't load-bearing for replay (e.g. STT vendor metadata, raw token counts) — never the utterance itself. `state.mutation.before/after` keeps everything (audit-critical). `speaker.call.final_utterance` and `speaker.cached.final_utterance` are full text in both modes (the agent's voice is not PII).

**Raw STT transcript per turn (Round 3.5 requirement):** the audit envelope's `TURN_STARTED` payload carries both `stt_text_raw` (verbatim Deepgram output) and `stt_text_used` (what the Judge saw — may differ if we add post-STT cleanup later). For v1 these are identical, but the schema split lets us add cleanup transparently and grep transcripts to diagnose STT mangling without code changes.

### 6.5 Judge module (`judge/service.py`)

Calls OpenAI Responses API with structured output (Pydantic schema → JSON Schema → response_format).

**Public API:**

```python
class JudgeService:
    def __init__(self, *, openai_client: AsyncOpenAI, model: str, system_prompt: str,
                 system_prompt_hash: str, retry_wait_ms: int = 250, total_budget_ms: int = 3000):
        ...

    async def call(self, *, turn_id: str, input_payload: JudgeInputPayload,
                   correlation_id: str, tenant_id: str) -> JudgeCallResult:
        """Returns either a successful JudgeOutput or a fallback synthesis with reason."""
```

**`JudgeCallResult`** carries: `judge_output: JudgeOutput`, `is_fallback: bool`, `fallback_reason: str | None`, `original_failure_context: dict | None`, `latency_ms`, `usage`, `model_used`.

**Retry/fallback policy (locked from Round 2.3 + corrections):**

| Failure | Action | Wait |
|---|---|---|
| Network/timeout/5xx/rate-limit | One retry. Hard wall-clock budget 3s total. | Flat 250ms before retry. |
| JSON parse / schema validation fail | NO retry. Fall back. | — |
| Schema-valid but rule violation (handled in State Engine, not Judge) | — | — |

**Wall-clock cancellation:** the orchestrator wraps the Judge call in `asyncio.wait_for(call, timeout=3.0)`. On timeout, the in-flight task is cancelled. If a response arrives after cancellation, it is discarded — no late parsing.

**Fallback synthesis:** when Judge fails after retry, `JudgeService` synthesizes a fallback `JudgeOutput`:

- If next pending mandatory exists → `next_action: advance`, `target_question_id: <next pending mandatory>`, no observations, no claims, `thought: "judge_fallback_<reason>"`.
- If no pending mandatory exists → `next_action: polite_close`, `reason: "judge_fallback_no_advance_target"`, no observations, no claims.

The fallback synthesizer lives in `judge/fallback.py` and is also used by the State Engine for the "no advance target" branch.

**Tracing:** every Judge call sets OTel span attributes via `set_llm_span_attributes(prompt_name="engine/judge.system", prompt_version="v1", tenant_id=..., correlation_id=..., turn_id=..., model=...)`.

**OpenAI client:** a separate `AsyncOpenAI` instance from the existing instructor batch client. Created once in the orchestrator and shared between Judge and Speaker (both use Responses API but in different modes — structured output vs streaming). Configured with the same timeout / max_retries=0 (the engine handles retries) / httpx client pattern as `app/ai/client.py`.

### 6.6 Speaker module (`speaker/service.py`)

Calls OpenAI Responses API in streaming mode. Returns an `AsyncIterable[str]` of token deltas.

**Public API:**

```python
class SpeakerService:
    def __init__(self, *, openai_client: AsyncOpenAI, model: str, system_prompt: str,
                 system_prompt_hash: str):
        ...

    async def stream(self, *, turn_id: str, speaker_input: SpeakerInput,
                     correlation_id: str, tenant_id: str) -> SpeakerStreamHandle:
        """Returns a handle whose .stream() yields tokens as they arrive, and .final_text()
        returns the assembled utterance after the stream completes."""
```

**`SpeakerStreamHandle`** behavior:

- `async def stream(self) -> AsyncIterable[str]`: yields token deltas. The orchestrator passes this directly to `session.say(stream(), allow_interruptions=True, add_to_chat_ctx=True)`.
- `async def final_text(self) -> str`: returns the full assembled utterance after stream completes. Used by the orchestrator to write the `SPEAKER_OUTPUT` audit event and append a `TranscriptEntry(role="agent", text=…)`.
- `latency_ms_first_token: int`, `latency_ms_total: int`: measured.
- `usage`: prompt+completion token counts.

**Anti-leak rules** are enforced primarily by the system prompt (see §8.2). The Speaker's input never carries rubric content — the input builder writes only: `bank_text`, `recent_turns`, `claims_pool_snapshot`, `persona_name`, `instruction_kind`, `failed_signal_value`. No rubric, no anchor IDs, no positive_evidence, no red_flags, no signal_metadata, no evaluation_hint.

**No retries on Speaker.** A streaming TTFT failure is bubbled up as a `speaker.error` event in the audit envelope; the orchestrator falls back to `session.say("I apologize — could you say that again?", …)` and reuses the candidate's prior utterance for the next Judge call.

### 6.7 Frontend attributes (`frontend_attributes.py`)

```python
ATTR_CURRENT_QUESTION_INDEX = "current_question_index"
ATTR_TOTAL_QUESTIONS = "total_questions"
ATTR_TIME_REMAINING_SECONDS = "time_remaining_seconds"
ATTR_SESSION_OUTCOME = "session_outcome"

class AttributePublisher:
    def __init__(self, room: rtc.Room):
        self._room = room
        self._last_values: dict[str, str] = {}

    async def publish(self, **attrs: str | int) -> dict[str, str]:
        """Diffs against last values; pushes only changed attributes via
        room.local_participant.set_attributes({}). Returns the dict actually pushed."""
```

**Cadence:**

- `total_questions` — published once in `on_enter`, after State Engine init.
- `current_question_index` — published every turn at the end of `on_user_turn_completed`, only on change.
- `time_remaining_seconds` — published every turn, floored to whole seconds.
- `session_outcome` — published once in close handler.

The orchestrator calls `AttributePublisher.publish(...)` and writes a `FRONTEND_ATTRIBUTE_PUBLISHED` audit event for each attribute actually pushed.

### 6.8 STT factory seam (`stt_factory.py`)

```python
def build_stt_plugin_for_session(session_config: SessionConfig) -> deepgram.STT:
    """Hook seam for per-session STT customization (keyterms, language, etc.).
    v1: returns the global build_stt_plugin() unchanged. Future: extracts technical terms
    from session_config.signal_metadata + question texts and passes via Deepgram keyterm."""
    return build_stt_plugin()
```

The entrypoint calls `build_stt_plugin_for_session(config)` instead of `build_stt_plugin()` directly. v1 is a no-op pass-through; future keyterm work changes only this function.

### 6.9 Per-turn orchestrator (`orchestrator.py`)

`InterviewOrchestrator` owns the LiveKit `Agent` subclass behavior. The slim agent class delegates to it.

**Public API:**

```python
class InterviewOrchestrator:
    def __init__(self, *, session_config: SessionConfig, tenant_settings: TenantSettings,
                 state_engine: StateEngine, judge: JudgeService, speaker: SpeakerService,
                 attr_publisher: AttributePublisher, event_collector: EventCollector,
                 correlation_id: str):
        ...

    async def on_enter(self, agent: Agent) -> None:
        """Synthesize first-turn JudgeOutput, run State Engine, stream Speaker,
        publish total_questions + initial current_question_index + time_remaining."""

    async def on_user_turn_completed(self, agent: Agent, turn_ctx: ChatContext,
                                     new_message: ChatMessage) -> None:
        """Run Judge → State Engine → Speaker. Stream Speaker into session.say().
        Raise StopResponse() to suppress the framework's default reply."""

    async def on_close(self, agent: Agent, audio_tuning_summary: dict) -> SessionResult:
        """Drain in-flight, build SessionResult, return it for record_session_result."""

    # Checkpointing
    async def maybe_checkpoint(self, db: AsyncSession) -> None:
        """Called after every turn. Writes engine_checkpoint if 10 turns OR 30s elapsed since last."""
```

**`on_user_turn_completed` flow:**

1. Generate `turn_id = uuid4()`. Append `TURN_STARTED` event with `stt_text_raw` and `stt_text_used` (= `new_message.text_content`).
2. Build `JudgeInputPayload` from State Engine snapshots + active question rubric + last 8 turns + utterance.
3. `judge_result = await judge.call(turn_id=…, input_payload=…)`. Append `JUDGE_CALL` (or `JUDGE_FALLBACK`) event.
4. `decision = state_engine.process_judge_output(turn_id=…, judge_output=judge_result.judge_output, candidate_utterance_text=…)`. Append `STATE_MUTATION` events for each mutation; append `JUDGE_VALIDATION` events for each warning/error.
5. If `decision.lifecycle == "closing"`: stream speaker (e.g. polite_close), then call `agent.session.shutdown(...)` after speech completes.
6. Else if `decision.speaker_input.instruction_kind == InstructionKind.repeat`:
   - **Bypass Speaker LLM entirely.** State Engine has resolved `decision.cached_utterance` from the most-recent agent transcript entry.
   - Call `await agent.session.say(decision.cached_utterance, allow_interruptions=True, add_to_chat_ctx=False)`. (`add_to_chat_ctx=False` because the text is already in the chat context from the original turn — re-adding it would create a duplicate.)
   - Append `SPEAKER_CACHED` event with `source_turn_id` = the originating turn's id and `final_utterance` = the cached text. **Do not** append a new `TranscriptEntry` — the utterance was already recorded under its original `turn_id`.
7. Else (the normal Speaker path):
   - `stream_handle = await speaker.stream(turn_id=…, speaker_input=decision.speaker_input)`.
   - `await agent.session.say(stream_handle.stream(), allow_interruptions=True, add_to_chat_ctx=True)`.
   - After say completes: append `SPEAKER_OUTPUT` with `stream_handle.final_text()`. Append a `TranscriptEntry(role="agent", text=final_text, …)` to the running transcript.
   - On streaming error before TTFT: append `SPEAKER_ERROR` event; deliver canned recovery (`"I apologize — could you say that again?"`) via `session.say(...)`; reuse the candidate's prior utterance for the next Judge call.
8. Publish frontend attributes (current_question_index, time_remaining_seconds).
9. `await self.maybe_checkpoint(db)`.
10. Append `TURN_COMPLETED` event.
11. **`raise StopResponse()`** to suppress the framework's default LLM/TTS reply.

**`on_enter` flow** (no `StopResponse` — `on_enter` doesn't trigger a default reply):

1. `synthetic_judge = state_engine.initialize_for_session_start()`. Append `JUDGE_SYNTHETIC` event.
2. `decision = state_engine.process_judge_output(turn_id=session_start_turn_id, judge_output=synthetic_judge, candidate_utterance_text=None)`. (`SpeakerInput.instruction_kind = deliver_first_question`.) Append `STATE_MUTATION` events.
3. Publish `total_questions`, `current_question_index=0`, `time_remaining_seconds`.
4. `stream_handle = await speaker.stream(turn_id=…, speaker_input=decision.speaker_input)`.
5. `await agent.session.say(stream_handle.stream(), allow_interruptions=True, add_to_chat_ctx=True)`.
6. Append `SPEAKER_OUTPUT` and the agent's `TranscriptEntry`.
7. Append `TURN_COMPLETED`.

**`AgentSession` config from the orchestrator:**

- `turn_handling = {"preemptive_generation": {"enabled": False}}` — preemptive runs the framework's LLM speculatively, which is pure waste when we're not using it.
- All other audio config (`build_interruption_options`, `build_vad`, `build_turn_detector`, `build_noise_cancellation`) unchanged from current `agent.py`.

### 6.10 Session lifecycle (`state/lifecycle.py`)

```python
class LifecycleState(StrEnum):
    pre_start = "pre_start"
    active = "active"
    closing = "closing"
    closed = "closed"

class LifecycleSnapshot(BaseModel):
    state: LifecycleState
    knockout_failures: list[KnockoutFailure]
    time_budget_total_seconds: float        # = SessionConfig.stage.duration_minutes * 60
    time_elapsed_seconds: float
    last_outcome: SessionOutcome | None
```

**Time budget source.** `time_budget_total_seconds` is derived from `SessionConfig.stage.duration_minutes * 60` — the stage's contractual budget. `QuestionConfig.estimated_minutes` is **not** summed; that field is per-question allocation guidance for the bank generator, not an aggregate stage budget. Summing it would inflate the budget when the bank contains optional questions.

**State transitions:**

- `pre_start → active` — on `on_enter`, after first synthetic Judge advance.
- `active → closing` — on Judge `next_action: end_session` (validated), `polite_close`, time exhaustion (informational only — does not force termination), or `record_session_result` triggered externally.
- `closing → closed` — on `agent.session.shutdown()` completion.

**Outcome resolution** (extends current 4-value enum to 6, matching frontend):

| Trigger | `session_outcome` |
|---|---|
| All mandatory completed, Judge `end_session: agent_initiated` | `completed` |
| Knockout recorded + `polite_close` per `engine_knockout_policy="close_polite"` | `knockout_closed` |
| `time_budget.exhausted` AND lifecycle drove a `polite_close` | `time_expired` |
| Judge `end_session: candidate_initiated` | `candidate_ended` |
| Candidate disconnected (LiveKit `participant_disconnected`) | `candidate_disconnected` |
| Idle timeout (existing nudge → give-up flow) | `candidate_unresponsive` |
| Unhandled exception in orchestrator | `error` |

The 6-value frontend enum (`frontend/session/components/interview/lib/session-outcome.ts`) is now fully consumed.

### 6.11 Crash recovery / checkpointing (`state/checkpoint.py`)

```python
class EngineCheckpoint(BaseModel):
    schema_version: int = 1
    session_id: str
    ledger: SignalLedgerSnapshot
    queue: QuestionQueueSnapshot
    claims: ClaimsPoolSnapshot
    lifecycle: LifecycleSnapshot
    last_audit_seq_flushed: int              # the seq of the last audit envelope event flushed to file
    captured_at_ms: int

def serialize(engine: StateEngine, last_audit_seq: int) -> dict: ...
def deserialize(payload: dict, session_config: SessionConfig) -> StateEngine: ...
```

**Cadence:** every 10 candidate turns OR every 30 seconds since last checkpoint, whichever first. Checkpoint write goes to `sessions.engine_checkpoint JSONB` (new column, see §7).

**Crash recovery flow:** on a server-side process restart with an active session row, an operator-driven recovery script can load `sessions.engine_checkpoint`, deserialize into a fresh `StateEngine`, and replay any audit-envelope events past `last_audit_seq_flushed`. The audit envelope is the immutable per-turn log; the checkpoint is the resumption snapshot.

**Mid-conversation `/rejoin` does NOT consume `engine_checkpoint` in v1.** The existing Phase 3C.2 `/rejoin` endpoint always restarts a fresh agent session; the candidate-side rejoin flow is a clean restart. Mid-conversation resume is post-MVP. The `engine_checkpoint` column still earns its keep for two reasons: (a) crash recovery as described above, and (b) forensic debugging — operators can inspect a session's mid-flight state without parsing the full audit envelope.

---

## 7. SessionResult schema extension + checkpoint migration

### 7.1 SessionResult new shape

`app/modules/interview_runtime/schemas.py` — `SessionResult` extended:

```python
class SessionResult(BaseModel):
    # Existing fields (unchanged):
    session_id: str
    job_title: str
    stage_id: str
    stage_type: str
    candidate_name: str
    duration_seconds: float = Field(ge=0)
    questions_asked: int = Field(ge=0)
    questions_skipped: int = Field(ge=0)
    total_probes_fired: int = Field(ge=0)
    full_transcript: list[TranscriptEntry]
    completed_at: str
    knockout_failures: list[KnockoutFailure] = Field(default_factory=list)
    audio_tuning_summary: dict[str, object] | None = Field(default=None)

    # NEW fields:
    signal_ledger: SignalLedgerSnapshot      # imported from interview_engine.models
    question_queue: QuestionQueueSnapshot
    claims_pool: ClaimsPoolSnapshot
    audit_envelope_ref: str | None = None    # path or s3 URI; None when sink is "none"

    # REMOVED field:
    # question_results: list[QuestionResult]  ← deleted; QuestionResult class also deleted
```

`QuestionResult` and its `observations: list[SteeringObservation]` field are removed entirely. `SteeringObservation` is marked `@deprecated` in code (kept defined so legacy `raw_result_json` blobs still parse), and is no longer a field on any current schema.

**Migration risk:** old `raw_result_json` rows written by the placeholder agent contain `question_results: [...]` and no `signal_ledger`. The post-session Report Builder is not yet built, so no live consumer breaks. Tests pass `question_results=[]` — they'll switch to passing the new fields with empty defaults.

**Cross-module imports:** `interview_runtime/schemas.py` imports `SignalLedgerSnapshot`, `QuestionQueueSnapshot`, `ClaimsPoolSnapshot` from `app.modules.interview_engine.models`. This is a cross-module deep import to `models` which is **explicitly allowed** by the public-API discipline (`tests/test_module_boundaries.py` allows `models` cross-module — see root CLAUDE.md). No new exception needed.

**Import direction note.** Today's pattern is `interview_engine` → `interview_runtime` (engine imports runtime schemas). This change adds a runtime → engine import for the snapshot types, creating a bidirectional dependency between the two modules. Concrete handling:

- Both files use `from __future__ import annotations` so type hints are strings at import time.
- `interview_runtime/schemas.py` uses `TYPE_CHECKING` for the engine import to keep it out of the runtime import path:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from app.modules.interview_engine.models import (
          SignalLedgerSnapshot, QuestionQueueSnapshot, ClaimsPoolSnapshot,
      )
  ```
- Pydantic v2's `model_rebuild()` is called once at module load on `SessionResult` to resolve the forward refs.
- This pattern is standard for cross-module Pydantic schemas; it does not create a runtime cycle.

If the build encounters mapper/forward-ref issues despite this, the fallback is to move the three snapshot types into a new shared module `app/modules/interview_engine/snapshots.py` that both runtime and engine import — but the TYPE_CHECKING approach is preferred and tried first.

### 7.2 `record_session_result` extension

`app/modules/interview_runtime/service.py::record_session_result` already serializes the entire `SessionResult` to `raw_result_json: JSONB`. No code change needed — the new fields will be carried automatically.

The `Session` model columns `transcript`, `questions_asked`, `probes_fired`, `knockout_failures`, `audio_tuning_summary` remain individually denormalized. We do **not** add denormalized columns for `signal_ledger` / `question_queue` / `claims_pool` in v1 — the Report Builder reads them from `raw_result_json`. If query patterns later require it, we add denormalized columns or a JSONB GIN index in a follow-up migration.

### 7.3 New Alembic migration: `0029_engine_checkpoint`

```sql
-- Up
ALTER TABLE sessions ADD COLUMN engine_checkpoint JSONB NULL;
COMMENT ON COLUMN sessions.engine_checkpoint IS
  'Last per-turn snapshot for crash recovery. Written every 10 turns or 30s.';
-- No RLS change: sessions table already has tenant_isolation + service_bypass policies.

-- Down
ALTER TABLE sessions DROP COLUMN engine_checkpoint;
```

Migration file: `migrations/versions/0029_engine_checkpoint.py`. Head moves from `0028_audio_tuning_summary` → `0029_engine_checkpoint`. CLAUDE.md migration list updated.

---

## 8. Prompts

### 8.1 Judge system prompt — requirements (locked from build prompt)

The Judge prompt (`prompts/v1/engine/judge.system.txt`) must include:

- **Role:** forensic evidence extractor for a structured screening interview. NOT a conversationalist.
- **Output schema:** every field of `JudgeOutput` documented inline. Pin output language to English. Bullet points over paragraphs.
- **Anti-leak rules:** NEVER reveal rubric content in `thought` (audited but should still not articulate "the rubric says X"). Do not produce content that, if leaked, would coach a candidate.
- **Probe selection rules:**
  - Only pick a probe whose ID is in the active question's `follow_ups` (probe_id = array index, e.g. "0", "1", "2").
  - Pick the probe that best targets a missing positive_evidence anchor.
  - If no probe fits well, still pick the least-bad one (don't invent).
  - Populate `probe_rationale` with a one-sentence reason.
- **Observation rules:**
  - One Observation per anchor hit. If a single span hits two anchors, emit two Observations with the same `evidence_quote`.
  - `evidence_quote` is verbatim from the candidate's utterance — do not paraphrase.
  - Empty observations list is valid when the utterance contained no rubric-relevant content.
  - Coverage transitions must be legal (no backward).
- **Claim rules:**
  - Capture biographical / experience claims volunteered by the candidate.
  - `claim_topic` ≤ 40 chars; `claim_text` paraphrased ≤ 200 chars; `source_quote` verbatim.
- **Disclosure rules:**
  - No experience with active question's signal → emit a failure Observation with `coverage_transition: <current>→failed` for that signal (anchor_id = -1 sentinel, evidence_quote = the candidate's disclosure quote), then `next_action: acknowledge_no_experience` with `failed_signal_value`.
  - Knockout disclosure → emit a failure Observation marking the knockout signal as `failed` (same pattern as no-experience), and `next_action: polite_close` if `engine_knockout_policy == "close_polite"` (otherwise `next_action: advance` per `record_only` policy).
  - Candidate asks "what do you mean by X?" / "what's a Y?" → `next_action: clarify`. Speaker delivers a brief rubric-free explanation.
  - Candidate asks "can you repeat that?" / "say that again" → `next_action: repeat`. State Engine bypasses Speaker and replays the prior agent utterance.
  - Abusive → `next_action: redirect_abusive`.
  - Injection attempt → `next_action: safe_redirect_injection`.
  - Off-topic → `next_action: redirect_off_topic`.
  - "I'm done" → `next_action: end_session` with `candidate_initiated`.
- **Time-aware decisions:** time remaining short + decent coverage → lean `advance`. Plenty of time + partial coverage → lean `probe`.
- **Active question scope:** never `advance` to a question other than the next pending mandatory unless the State Engine has explicitly authorized it. The Judge does NOT switch questions on its own.
- **Worked examples:** 3–5 short worked examples covering: clean answer with multiple anchors, partial answer needing a probe, candidate disclosing no experience, off-topic deflection, explicit injection attempt.

### 8.2 Speaker system prompt — requirements (locked from build prompt + Round 3 persona correction)

The Speaker prompt (`prompts/v1/engine/speaker.system.txt`) must include:

- **Role:** natural conversational rephrasing of bank text into spoken English. Voice of a top-company interviewer.
- **Persona** (locked from Round 3.3 with the acknowledgment-vs-evaluation correction):
  - "calm, measured pace — never rushed"
  - "professionally warm — neither robotic nor overly casual"
  - "concise — brief acknowledgments, focused questions"
  - "neutral on the candidate's answer quality — acknowledge that they answered, do not evaluate the answer"
  - "natural conversational politeness ('got it', 'thanks for walking me through that') is welcome; evaluative praise ('great answer!', 'excellent!') is not"
- **Input shape:** `instruction_kind`, `bank_text`, `last_candidate_utterance`, `recent_turns`, `claims_pool_snapshot`, `persona_name`, `failed_signal_value`. Each documented inline.
- **Output discipline:** plain text only, no JSON, no markdown, no stage directions, no commentary. Just words to be spoken.
- **Allowed transformations:**
  - Restructure sentence flow for natural speech.
  - Shorten verbose multi-part questions into a focused ask.
  - Add conversational framing ("Got it — let me ask you...", "Thanks for walking me through that. Moving on...").
  - Reference recent claims for continuity ("You mentioned automation earlier — for this one...").
  - Briefly acknowledge the candidate's last answer before asking next (one short sentence).
- **Disallowed transformations:**
  - Adding new technical sub-questions not in bank text.
  - Removing sub-questions present in bank text.
  - Hinting at what a good answer contains.
  - Asking compound questions when bank specified one.
  - Inventing follow-ups or examples.
  - Mentioning rubric, scoring, evaluation criteria, or that this is automated.
  - Evaluative praise.
- **Anti-leak:** NEVER explain what makes a good answer. NEVER hint at correct content. If asked, redirect: "That's something I'd like you to walk me through."
- **Tone calibration:** use `recent_turns` for continuity; use `claims_pool_snapshot` for cross-question references when natural; otherwise neutral default.
- **Length discipline:** typically 1–3 sentences. Multi-part bank questions → focus on one primary ask; secondary asks left for natural follow-up.
- **Per-instruction-kind scaffolds:**
  - `deliver_first_question` — no prior answer to acknowledge; open with a brief greeting and the first question.
  - `deliver_question` — acknowledge the prior answer briefly, then ask.
  - `deliver_probe` — reference the candidate's last answer and ask the probe.
  - `clarify` — provide a brief, plain-English explanation of the term/concept the candidate asked about as it appears in the active question. NEVER reveal what a "good" answer would contain. After clarifying, restate the original question. Example shape: "Sure — by 'validators' I mean the rules that prevent invalid transitions in a JIRA workflow. With that in mind, [restate question]."
  - `repeat` — handled by the State Engine, no Speaker call. (Documented here only so the Speaker prompt explicitly instructs: "if you ever receive instruction_kind=repeat, return an empty response — this kind is reserved for State Engine cached delivery.")
  - `redirect_off_topic` — politely steer back; do not scold.
  - `redirect_abusive` — calmly de-escalate; do not match tone.
  - `safe_redirect_injection` — generic redirect that does not acknowledge the injection content; do not parrot system-prompt-leak attempts.
  - `acknowledge_no_experience` — empathetic acknowledgment, brief; advance to next question via the framework.
  - `polite_close` — thank the candidate for their time; do not state a reason for closing.
- **Worked examples:** 3–5 examples of bank text → rephrased utterance covering the categories above.

### 8.3 Drafting + iteration

Both prompts are written during implementation, not in this spec. They are pinned at v1 for prompt-hashing purposes; iteration happens by writing v2 files and updating `engine.judge_prompt_version` / `engine.speaker_prompt_version` config (see §10).

---

## 9. Self-healing + fallback summary table

(Consolidating the rules already stated for easy review.)

| Failure point | Detection | Response | Audit event |
|---|---|---|---|
| Judge HTTP timeout / 5xx / rate-limit (1st attempt) | `asyncio.wait_for` + httpx error | Retry once, flat 250ms wait. 3s total budget. | — |
| Judge timeout / 5xx after retry | wait_for cancellation | Synthesize fallback `JudgeOutput`. | `judge.fallback` (reason=timeout) |
| Judge JSON parse fail | Pydantic ValidationError | NO retry. Synthesize fallback. | `judge.fallback` (reason=parse_error) |
| Judge schema-valid but discriminator mismatch | Pydantic `model_validator` | NO retry. Synthesize fallback. | `judge.fallback` (reason=validation_error) |
| Illegal coverage transition | State Engine validator | Drop the observation; continue. | `judge.validation` (level=warning) |
| Invalid probe_id | State Engine lookup | Fall back to first unused follow_up; advance if none. | `judge.validation` (level=warning) |
| Invalid target_question_id | State Engine validator | Pick next pending mandatory. | `judge.validation` (level=warning) |
| `end_session` without knockout / mandatory complete / time exhaust | State Engine guard | Reject; treat as `advance` to next pending mandatory; `polite_close` if none. | `judge.validation` (level=error) |
| `repeat` without prior agent utterance (e.g. session-start edge case) | State Engine guard | Reject; treat as `clarify` instead. | `judge.validation` (level=warning) |
| Backward coverage transition (e.g. `sufficient → partial`) | State Engine validator | Drop the observation; continue. Already covered by "Illegal coverage transition" row. | `judge.validation` (level=warning) |
| Failure transition for already-failed signal (`failed → failed` from idempotent re-disclosure) | Legal — write entry but no state change | Write the LedgerEntry for audit fidelity; SignalSnapshot.coverage stays `failed`. | (no validation event) |
| No advance target available (fallback can't advance) | Fallback synthesizer | Synthesize `polite_close` with `reason="judge_fallback_no_advance_target"`. | `judge.fallback` (reason=no_advance_target) |
| Speaker streaming TTFT failure | OpenAI stream error | Speak canned recovery (`"I apologize — could you say that again?"`); reuse candidate utterance for next Judge call. | `speaker.error` |

---

## 10. Configuration

### 10.1 New env vars (added to `.env.example` + `app/config.py::Settings`)

```bash
# Engine — Judge model
ENGINE_JUDGE_MODEL=gpt-5.4-mini-2026-03-17     # Dated snapshot string for audit replay.
                                                # Verify in OpenAI dashboard before merge — if the
                                                # actual current snapshot has a different date suffix,
                                                # use that.
ENGINE_JUDGE_TOTAL_BUDGET_MS=3000
ENGINE_JUDGE_RETRY_WAIT_MS=250

# Engine — Speaker model
ENGINE_SPEAKER_MODEL=gpt-5.4-mini-2026-03-17   # Same pinning requirement.
ENGINE_SPEAKER_MAX_OUTPUT_TOKENS=200            # Hard cap on Speaker output length.

# Engine — checkpoint
ENGINE_CHECKPOINT_TURNS=10
ENGINE_CHECKPOINT_SECONDS=30

# Engine — claims pool
ENGINE_CLAIMS_POOL_MAX=50

# Engine — recent turns context
ENGINE_RECENT_TURNS_WINDOW=8

# Engine — prompt versions (for hashing + display)
ENGINE_JUDGE_PROMPT_VERSION=v1
ENGINE_SPEAKER_PROMPT_VERSION=v1
```

Existing `ENGINE_*` vars (unchanged): `ENGINE_AGENT_NAME`, `ENGINE_EVENT_LOG_*`, `ENGINE_LOG_AUDIO_EVENTS`, `ENGINE_LOG_USER_TRANSCRIPTS`, `ENGINE_ENDPOINTING_*`, `ENGINE_IDLE_*`.

**Stale env vars to remove from `.env.example`:** `ENGINE_MAX_PROBES_PER_QUESTION`, `ENGINE_TIME_WARNING_THRESHOLD`, `INTERVIEW_ENGINE_JWT_SECRET` (its consumer was retired in migration `0025`). These are leftovers from the removed structured agent; they have no `Settings` field today and can be deleted in this same PR for hygiene.

### 10.2 `AIConfig` extension

`app/ai/config.py::AIConfig` gains:

```python
@property
def engine_judge_model(self) -> str: ...
@property
def engine_speaker_model(self) -> str: ...
```

Realtime LLM/STT/TTS plugin config remains unchanged.

### 10.3 Persona

`DEFAULT_PERSONA` (locked from Round 3.3) lives in `app/modules/interview_engine/speaker/persona.py`. Resolution at runtime:

```python
def resolve_persona_name(*, tenant_settings: TenantSettings, settings: Settings) -> str:
    return (tenant_settings.engine_agent_name
            or settings.engine_agent_name
            or "the interviewer")
```

`engine_persona_id` is **not added** to `TenantSettings` in v1. Tenant-level persona configuration is post-MVP.

### 10.4 TTS voice

Unchanged. `INTERVIEW_TTS_VOICE` (Cartesia voice ID) continues to drive `build_tts_plugin()`.

### 10.5 STT keyterms

Skipped in v1 (Round 3.5 decision). Hook seam at `stt_factory.build_stt_plugin_for_session(session_config)` returns the global plugin unchanged. Audit envelope captures `stt_text_raw` and `stt_text_used` per turn so degradation can be diagnosed empirically before investing in keyterm extraction.

---

## 11. Test plan

### 11.1 Layer 1 — Pure-Python unit tests (no LiveKit, no OpenAI)

Coverage target: 100% branch on State Engine, Ledger, Queue, Claims, Lifecycle, Checkpoint, BankResolver, Judge fallback synthesizer, Speaker input builder. Aspirational global target stays 80% line per root CLAUDE.md.

Test files:

- `tests/interview_engine/state/test_ledger.py` — append, snapshot consistency, illegal transition rejection, seq monotonicity.
- `tests/interview_engine/state/test_queue.py` — mandatory enforcement, hard advance, probe tracking, time accounting, status transitions.
- `tests/interview_engine/state/test_claims.py` — drop-oldest at cap 50, ordering preserved.
- `tests/interview_engine/state/test_lifecycle.py` — outcome resolution for all 6 enum values, knockout policy branching.
- `tests/interview_engine/state/test_checkpoint.py` — round-trip serialize/deserialize, schema_version forward-compat, replay-past-checkpoint sanity.
- `tests/interview_engine/state/test_engine.py` — process_judge_output happy paths + every self-healing branch.
- `tests/interview_engine/judge/test_fallback.py` — synthesizer correctness for every reason + no-target branch.
- `tests/interview_engine/judge/test_input_builder.py` — assembled prompt input from snapshots + active question.
- `tests/interview_engine/speaker/test_input_builder.py` — anti-leak: assert no rubric / anchor / positive_evidence / red_flags content ever appears in SpeakerInput.
- `tests/interview_engine/speaker/test_persona.py` — resolution precedence (tenant_setting > settings > default).
- `tests/interview_engine/test_bank_resolver.py` — every NextAction → ResolvedBankText mapping.
- `tests/interview_engine/test_audit_events.py` — payload schema validation per kind.
- `tests/interview_engine/test_frontend_attributes.py` — diffing wrapper, publish only on change.

### 11.2 Layer 2 — Mocked-LLM integration tests

`tests/interview_engine/test_orchestrator.py` exercises the full per-turn pipeline with a fake `JudgeService` (returns canned `JudgeOutput`s) and a fake `SpeakerService` (returns canned async iterators). LiveKit framework not invoked — orchestrator methods are called directly with synthetic `ChatMessage` objects.

Assertions per test:

- Audit envelope has the expected event sequence with monotonic seq numbers.
- `SessionResult` carries the expected ledger / queue / claims state.
- Frontend attributes published exactly the expected diffs.
- Self-heal path triggers the right validation event when fed a bad JudgeOutput.

Fixtures (`tests/interview_engine/conftest.py`):

- `make_session_config(...)` — builds a SessionConfig with N questions, M signals, customizable knockout flag.
- `make_question(...)` — builds a `QuestionConfig` with bank text + follow_ups + rubric.
- `make_judge_output(action=..., ...)` — builds typed JudgeOutputs for canned scripts.
- `sample_session_config.json` — checked-in fixture under `tests/interview_engine/fixtures/`.

### 11.3 Layer 3 — Manual end-to-end (you, the user)

Real LiveKit, real OpenAI, real STT/TTS, real DB. Per the user's "Manual testing for AI agents" memory, this is the primary verification path. Acceptance criteria from the build prompt drive what to test.

### 11.4 Dev tool: `scripts/run_engine_dry.py` (Round 3.7 scenario harness)

Replaces the LiveKit + audio path with direct method calls. Two modes:

**Mode A — bare list:** scripted utterances, no assertions.

```yaml
# scenarios/quick_smoke.yaml
session_config_fixture: tests/interview_engine/fixtures/sample_session_config.json
candidate_responses:
  - utterance: "I worked on automation for two years."
  - utterance: "I've never used JQL."
  - utterance: "I'd rather not answer that."
```

**Mode B — scenario with assertions** (for regression accumulation):

```yaml
# scenarios/anchor_coverage.yaml
session_config_fixture: tests/interview_engine/fixtures/sample_session_config.json
candidate_responses:
  - utterance: "I led a team that automated regression for ScriptRunner..."
    expected_next_action: probe
    expected_observations_count: 2
    notes: "checks anchor 0 hit; expects probe on probe_id=0"
  - utterance: "We added validators in JIRA workflow with post-functions."
    expected_next_action: advance
    expected_observations_count: 1
    notes: "anchor 2 hit; queue advances"
```

Output: prints final `SessionResult` JSON + a pass/fail summary of assertions. Lets prompt iteration run in seconds without audio.

The script lives at `backend/nexus/scripts/run_engine_dry.py`. v1 ships with at least 3 scenario files: a happy-path interview, a knockout flow, and a prompt-injection attempt.

### 11.5 Test coverage gates (per root CLAUDE.md)

Adding paths to the gated list:

- `app/modules/interview_engine/state/` — 100% branch.
- `app/modules/interview_engine/judge/fallback.py` — 100% branch (every fallback reason path).
- `app/modules/interview_engine/speaker/input_builder.py` — 100% branch (anti-leak guarantees).
- `app/modules/interview_engine/orchestrator.py` — at least the pipeline-success and each fallback-event-emission path.

---

## 12. Acceptance criteria (from build prompt, restated)

The user can:

1. Start a test session with a real `SessionConfig` loaded from a job.
2. The agent delivers the first mandatory question, naturally rephrased.
3. Speak (or type) candidate answers; the agent probes follow-ups, advances questions, handles disclosures.
4. Cause edge cases and see them handled gracefully:
   - Inject prompt → agent redirects, doesn't leak.
   - Disclose no experience → agent acknowledges, marks signal failed, advances.
   - Go off-topic → agent redirects.
   - Ask "what are you looking for?" → agent declines without leaking.
   - Say "I'm done" → agent gracefully ends.
5. End the session and see a `SessionResult` with:
   - Full SignalLedger (event log + snapshots).
   - Full QuestionQueue final state.
   - Full ClaimsPool.
   - Full audit envelope with all Judge calls, validations, mutations, Speaker calls.
   - All sequence numbers monotonically increasing.
   - All entries traceable by `turn_id`.
6. Replay determinism: given the audit envelope, the State Engine final state can be reconstructed deterministically.
7. Latency: time-to-first-audio after candidate finishes ≤ 2 seconds in typical conditions.

---

## 13. Resolved decisions from review

Items flagged during initial drafting, resolved by the user during spec review (2026-05-07).

1. **OpenAI model snapshot strings.** Resolved — pinned to `gpt-5.4-mini-2026-03-17` for both `ENGINE_JUDGE_MODEL` and `ENGINE_SPEAKER_MODEL` in `.env.example`. The actual current snapshot must be verified in the OpenAI dashboard before merging the implementation PR; if it differs, use that date.

2. **`/rejoin` and `engine_checkpoint`.** Resolved — `/rejoin` always restarts. The checkpoint column is kept for server-side crash recovery and forensic debugging only. Mid-conversation resume is post-MVP. See §6.11.

3. **`speaker.error` event kind.** Resolved — added. See §6.4 event kinds list and the §9 self-healing table.

4. **`questions_skipped` semantics.** Resolved — always emit `0`. The new structured agent uses hard advance and never skips. The field is preserved for wire compatibility with the existing `SessionResult` schema; it carries no meaningful data in the new design.

5. **Time budget source.** Resolved — `SessionConfig.stage.duration_minutes * 60`. The stage's `duration_minutes` is the contractual budget. `QuestionConfig.estimated_minutes` is per-question allocation guidance for the bank generator and is **not** summed. See §6.10.

6. **Redaction rules for new event kinds.** Resolved — candidate utterance text is **not redacted** in either mode; it is the audit-grade artifact required for replay determinism. PII concerns are handled separately by `KnockoutFailure._scrub_pii`. State mutations and Speaker final utterances are full-text in both modes. See §6.4.

7. **`set_attributes` rate limiting.** Resolved — low risk for v1. Ship the diffing wrapper as designed; monitor for issues empirically.

## 14. Schema corrections from review (2026-05-07)

Captured here for clarity; the affected sections (§6.1, §8.1, §8.2, §9) have been updated.

- **`CoverageState`:** `strong` removed; `failed` added as the terminal failure state. Answer-quality grading lives in the post-session Report Builder, not the agent.
- **`CoverageTransition`:** `*→strong` transitions removed; `*→failed` transitions added (`none→failed`, `partial→failed`, `sufficient→failed`, `failed→failed` terminal/idempotent).
- **`Observation.confidence`:** removed. The locked design treats confidence as wasted tokens; can be added back later if production data shows it's needed.
- **`NextAction` extended** with `clarify` and `repeat`. Without these, candidate behaviors like "what do you mean by validators?" or "can you say that again?" misclassify as off-topic.
- **`InstructionKind` extended** with `clarify` and `repeat`. The `repeat` flow bypasses the Speaker LLM — the State Engine resolves the cached prior agent utterance and the orchestrator delivers it directly via `session.say()`. Audit kind: `speaker.cached`.

---

## 15. Implementation order

Mirrors the build prompt's suggested order; no surprises.

1. **Pydantic models module** (`models/*`) — standalone, no dependencies.
2. **State Engine** (`state/*`) — pure Python, full unit-test coverage.
3. **Bank text resolver** (`bank_resolver.py`) — pure function.
4. **Audit envelope writer** (`audit_events.py` + `event_kinds.py` extension).
5. **Judge module** (`judge/*`) — input builder + Responses API call + fallback synthesizer + tests with mocked OpenAI.
6. **Speaker module** (`speaker/*`) — same testing pattern.
7. **Frontend attributes** (`frontend_attributes.py`) — small, low-risk.
8. **STT factory seam** (`stt_factory.py`) — trivial.
9. **Per-turn orchestrator** (`orchestrator.py`) — wires everything together.
10. **Session lifecycle wiring** — start, end, crash recovery / checkpoint integration.
11. **Slim down `agent.py`** — replace `GenericInterviewAgent` internals with delegation to orchestrator.
12. **`SessionResult` extension** + Alembic migration `0029`.
13. **Prompts** — Judge and Speaker system prompts, drafted + iterated.
14. **Dev tool** — `scripts/run_engine_dry.py` with scenario YAML schema.
15. **End-to-end test harness** — load a real SessionConfig, run a scripted session, verify SessionResult.

---

## 16. Things explicitly NOT changing

For belt-and-suspenders clarity:

- `app/ai/realtime.py` — no changes. Audio pipeline factories untouched.
- Audio constraints (`noiseSuppression: false`, `echoCancellation: true`, `autoGainControl: true`) and `audio_processing_hints` — unchanged.
- LK Cloud lock-in — unchanged.
- `engine_dispatch_*` (already retired in migration 0025) — no resurrection.
- The `Session` model's existing columns (`transcript`, `questions_asked`, `probes_fired`, `knockout_failures`, `audio_tuning_summary`, `raw_result_json`, etc.) — unchanged.
- `record_session_result` signature — unchanged.
- `get_openai_client()` (instructor batch client) — unchanged. Engine uses a separate `AsyncOpenAI` instance.
- Existing event log envelope schema — unchanged structure; only new kinds + payloads.
- LiveKit Agent dispatch routing — `@server.rtc_session(agent_name=settings.engine_agent_name)` unchanged.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Judge** | The rubric-aware LLM that decides actions and emits structured output. Never speaks. |
| **Speaker** | The persona-aware LLM that rephrases bank text into spoken English. Never sees rubric. |
| **State Engine** | Deterministic Python core. Owns ledger, queue, claims, lifecycle. The firewall between Judge and Speaker. |
| **SignalLedger** | Append-only event log of evidence Observations + per-signal coverage snapshots. |
| **QuestionQueue** | Ordered list of questions with per-question status, probes asked, anchors hit, time spent. |
| **CandidateClaimsPool** | Capped pool (50, drop-oldest) of biographical/experience claims volunteered by the candidate. |
| **Audit envelope** | Per-session JSON written to file or S3 by `EventCollector`. Immutable per-turn forensic log. |
| **Anchor** | One bullet point in `QuestionConfig.positive_evidence`. Indexed by integer (anchor_id). |
| **Coverage** | Per-signal aggregate of anchors hit: `none`, `partial`, `sufficient`, `failed`. The terminal `failed` state is set on no-experience or knockout disclosure; answer-quality grading is the Report Builder's job, not the agent's. |
| **Coverage transition** | The before→after coverage state recorded on each Observation. Forward and failure transitions only — backward is never legal. |
| **Probe** | A follow-up question from `QuestionConfig.follow_ups` (list[str], indexed by position). |
| **Hard advance** | Once the queue advances past a question, it never returns. Late-mention content goes to transcript only. |
| **InstructionKind** | Enum on Speaker input that tells the Speaker what scaffold to apply (deliver_first_question, deliver_probe, polite_close, etc.). |
| **Synthetic JudgeOutput** | A JudgeOutput constructed by code (not by the LLM) — used for session-start delivery and for fallback after Judge failure. |
