# AI Screening Agent — Phase C Design Spec
## Streaming Speech Agent + Pre-render Lifecycle (ARCH-D)

**Status:** Draft v1 (consolidates 5-section brainstorming output, 2026-05-05)
**Owner:** Engineering / Solo Dev
**Supersedes:** `docs/ai-screening-agent/ai-screening-agent-implementation.md` §7 Phase C section
**Pairs with:**
- `docs/ai-screening-agent/ai-screening-agent-design.md` §5.6 (Speech Agent role)
- `docs/ai-screening-agent/ai-screening-agent-design.md` §11.5 (model output safety, re-amended 2026-05-05 — three-layer model)
- `docs/ai-screening-agent/ai-screening-agent-implementation.md` §8 rules 3+5 (re-amended 2026-05-05)

**Architectural designation:** ARCH-D — streaming OpenAI → buffered prefix → token-streamed TTS, with pre-render Task slot for parallel-execution overlap and prompt-only safety enforcement (no regex layer).

---

## 0. Mission and scope

### What ships in Phase C

Replace the three Phase-B hardcoded utterance constants (`INTRO`, `ASK_QUESTION_STANDARD`, `WRAP_NORMAL` in `_phase_b_utterances.py`) with LLM-rendered utterances produced by a new `SpeechAgent` class. Every agent utterance flows:

```
Orchestrator → SpeechAgent.render() → SpeechRenderHandle → session.say(handle.stream) → TTS
```

The realtime LLM stays no-op'd via the `llm_node` override (Pattern 2 hard guardrail unchanged from Phase B).

### Phase C deliverables

- **`SpeechAgent` class** (full infrastructure, not stub-and-fill): streaming OpenAI client, internal Task lifecycle, retry policy, fallback construction, audit envelope emission.
- **`SpeechRenderHandle` Protocol** with two implementations: `StreamingRenderHandle` (live LLM path) and `StaticFallbackHandle` (fallback path).
- **3 typed delivery wrappers** (`render_intro`, `render_ask_question_standard`, `render_wrap_normal`) — one per Phase C template.
- **3 hand-reviewed static fallback strings** keyed by template name in `speech/fallbacks.py`.
- **Pre-render Task slot** on `StructuredInterviewAgent` with three trigger sites (intro / Q0 / Qn+1) and four cancellation sub-cases.
- **`get_openai_raw_client()` factory** in `app/ai/client.py` (plain `AsyncOpenAI`, not instructor-wrapped) for streaming chat completions.
- **`speech_agent_model` + `speech_agent_effort`** AIConfig keys with `INTERVIEW_SPEECH_AGENT_MODEL` env var.
- **Doc amendments**: `design.md` §11.5 v3 (three-layer model, regex layer dropped entirely), `implementation.md` §7 Phase C amendment header v3, `implementation.md` §8 rules 3+5 v3.

### Phase C non-goals (explicit)

- The other 11 Speech Agent templates from design doc §7 (`ask_question_deepening`, `ask_followup`, `ask_followup_dynamic`, `meta_response`, `polite_deflection`, `confirmation_turn`, `pause_request_decline`, `gentle_prompt`, `resume_from_state`, `wrap_knockout_exit`, `wrap_candidate_initiated_exit`). Each is filled by its consuming phase (D-I).
- Sufficiency Checker, Intent Classifier, Disclaim Classifier (Phases D-H).
- Speculative pre-render (Phase E — `cancel-and-replace` based on Sufficiency outcome). The handle Protocol's two-phase `ready_to_commit() → commit()` contract is forward-ready for Phase E; Phase C always commits immediately.
- Regex-based safety layer (eliminated per Q1-reopened in brainstorming round).
- Length-cap retries (per Q4 A2 — length is logged as a metric on `speech.rendered`, not retried).
- Mid-stream retry after first token (per Section 4 retry policy — non-recoverable).
- Eval harness corpus (parallel workstream, separate spec).

### Safety enforcement

See `docs/ai-screening-agent/ai-screening-agent-design.md` §11.5 (re-amended 2026-05-05) for the three-layer model: prompt as gate / versioned templates / manual adversarial eval + miscall log, with forward-reference to Layer 4 (eval harness, parallel workstream). **Phase C adds no separate safety mechanism beyond what §11.5 defines.** The spec does not duplicate the three layers — duplication risks drift.

---

## 1. Architecture overview and file structure

### Data flow

```
Orchestrator decides "render template T with inputs X"
        ↓
deliveries.render_<T>(speech_agent, **inputs)        (typed wrapper, validates required inputs)
        ↓
SpeechAgent.render(template_name=T, version="v1", inputs=X)
        ├─ load_template via Phase A's template_loader.get(role, name, version)
        ├─ substitute {placeholders} (string-only, fail loud on missing)
        ├─ open OpenAI streaming chat completion
        │   • stream=True
        │   • stream_options={"include_usage": True}     (token counts in final chunk)
        │   • max_retries=0                              (SpeechAgent owns retry policy)
        ├─ spawn internal consumer Task (the _drive coroutine)
        ├─ return StreamingRenderHandle SYNCHRONOUSLY (stream is hot, futures unresolved)
        ↓
StructuredInterviewAgent._consume_pending_or_render(...)
        ├─ if pending slot: await it
        │   └─ on SpeechRenderError: fall back via deliveries.fallback_for(...)
        ├─ else: render synchronously (cold path)
        ↓
StructuredInterviewAgent._say(handle)
        ├─ await handle.ready_to_commit()       (raises SpeechRenderError → handled by helper above)
        ├─ await session.say(handle.commit(), allow_interruptions=True)
        │       ↓
        │   LiveKit pulls tokens from joined iterable → Cartesia TTS plugin → audio frames
        │   Candidate hears the agent (~150-280ms TTFT in pre-render hot path)
        ├─ await handle.completed_text + handle.metadata
        └─ emit SPEECH_RENDERED with full metadata + length_words + render_id
```

### File structure deltas

```
backend/nexus/app/modules/interview_engine/
├── _phase_b_utterances.py           [DELETED]
├── agent.py                          [MODIFY — construct SpeechAgent + raw OpenAI client; close handler bounded cancellation]
├── structured_agent.py               [MODIFY — _say() takes Handle, pre-render slot, deliveries, no _phase_b_utterances import]
├── event_kinds.py                    [MODIFY — DELETE SPEECH_SAFETY_VIOLATION + remove from ALL_EVENT_KINDS;
│                                                ADD SPEECH_STREAM_INTERRUPTED]
├── prompts/speech_agent/
│   ├── intro.v1.txt                  [KEEP — Phase A landed]
│   ├── ask_question_standard.v1.txt  [KEEP — Phase A landed]
│   └── wrap_normal.v1.txt            [KEEP — Phase A landed]
├── speech/
│   ├── __init__.py                   [MODIFY — drop SafetyResult/SafetyViolation/check_safety re-exports;
│   │                                            export SpeechAgent, SpeechRenderHandle (Protocol),
│   │                                            SpeechRenderError, RenderMetadata, StreamingRenderHandle,
│   │                                            StaticFallbackHandle]
│   ├── templates.py                  [KEEP — Phase A]
│   ├── safety.py                     [DELETED]
│   ├── agent.py                      [NEW — SpeechAgent class + StreamingRenderHandle + Protocol + types]
│   ├── deliveries.py                 [NEW — render_intro / render_ask_question_standard / render_wrap_normal
│   │                                          + fallback_for(speech_agent, template_name, **inputs)]
│   └── fallbacks.py                  [NEW — StaticFallbackHandle + _FALLBACK_BUILDERS + build_fallback_text]
└── orchestrator/                     [unchanged]

backend/nexus/app/ai/
└── client.py                         [MODIFY — add get_openai_raw_client() factory]

backend/nexus/app/ai/config.py        [MODIFY — speech_agent_model + speech_agent_effort properties]
backend/nexus/app/config.py           [MODIFY — corresponding Settings fields]
backend/nexus/.env.example            [MODIFY — INTERVIEW_SPEECH_AGENT_MODEL, INTERVIEW_SPEECH_AGENT_EFFORT]

backend/nexus/tests/interview_engine/
├── speech/test_safety.py             [DELETED]
├── speech/test_speech_agent.py       [NEW]
├── speech/test_handles.py            [NEW]
├── speech/test_fallbacks.py          [NEW]
├── speech/spike_streaming_cancellation.py  [NEW — standalone build-step gate, not pytest]
├── speech/prompt_quality/
│   ├── test_intro_quality.py         [NEW — @pytest.mark.prompt_quality]
│   ├── test_ask_question_standard_quality.py  [NEW]
│   └── test_wrap_normal_quality.py   [NEW]
└── test_structured_agent_integration.py    [MODIFY — drop SPEECH_SAFETY_VIOLATION assertions; add Phase C suite]
```

---

## 2. SpeechAgent class API contract

### 2.1 The class

```python
# speech/agent.py
class SpeechAgent:
    def __init__(
        self,
        *,
        client: openai.AsyncOpenAI,         # raw, not instructor-wrapped
        model: str,                          # ai_config.speech_agent_model
        effort: str | None,                  # ai_config.speech_agent_effort (None for chat-tier)
        collector: EventCollector,           # for SPEECH_RENDERED + SPEECH_FALLBACK_USED + SPEECH_STREAM_INTERRUPTED
    ) -> None: ...

    async def render(
        self,
        *,
        template_name: str,
        template_version: str,
        inputs: dict[str, Any],
    ) -> SpeechRenderHandle:                 # Protocol return type
        """
        Returns synchronously after opening the OpenAI stream.
        The returned handle is HOT — the internal Task is already
        producing tokens by the time the caller gets the handle back.

        Raises SpeechRenderError(reason="template_not_found"|"placeholder_missing")
        synchronously for input-validation failures (programmer errors;
        not retried; not caught by the consumption helper).

        Does NOT raise on OpenAI errors — those resolve via
        handle.ready_to_commit() raising at consumption time.
        """

    def fallback_handle(
        self,
        *,
        template_name: str,
        template_version: str,
        text: str,
        failure_reason: str,                 # openai_timeout | openai_5xx | openai_connection_dropped_pre_first_token
        retries_attempted: int,              # 1 in Phase C — single retry policy
        render_id: str,                      # carried through from the failed render attempt
    ) -> SpeechRenderHandle:
        """Constructs a StaticFallbackHandle. Emits SPEECH_FALLBACK_USED
        envelope event at construction time (Pin 1)."""
```

The constructor is dependency-injected. Constructed once in `agent.py`'s entrypoint and passed into `StructuredInterviewAgent.__init__`. No re-construction per render — the OpenAI client + collector are stable for the session lifetime.

`render()` is fast (~5ms): load template, substitute placeholders, open OpenAI stream, spawn internal Task, return handle. By the time the caller gets the handle back, the OpenAI request is already in flight.

### 2.2 Types

```python
# speech/agent.py
@dataclass(frozen=True)
class RenderMetadata:
    render_id: str                            # uuid4, generated at render() time, joins envelope events
    template_name: str
    template_version: str
    model: str
    latency_first_token_ms: int | None        # None for fallback handles (Pin 2)
    latency_last_token_ms: int | None         # None for fallback handles
    tokens_in: int | None                     # None for fallback handles
    tokens_out: int | None                    # None for fallback handles
    length_words: int                         # always populated (computable from final text)
    playout_duration_ms: int | None           # None until consumer finishes iterating commit()
    was_fallback: bool
    retries: int                              # 0 on happy path; 1 if pre-first-token retry; 1 on fallback


class SpeechRenderError(Exception):
    """Raised by SpeechAgent.render() synchronously for programmer errors,
    or by handle.ready_to_commit() for post-retry-exhaustion infrastructure errors.
    Caught only at StructuredInterviewAgent._consume_pending_or_render."""
    reason: Literal[
        "template_not_found",
        "placeholder_missing",
        "openai_timeout",
        "openai_5xx",
        "openai_connection_dropped_pre_first_token",
    ]
    render_id: str | None                     # set for runtime errors; None for synchronous programmer errors


@runtime_checkable
class SpeechRenderHandle(Protocol):
    """Single-use handle. Three terminal states: completed (committed and drained),
    cancelled, errored. Idempotent cancel(); commit() can only fire once."""

    async def ready_to_commit(self) -> None:
        """Blocks until the prefix buffer is ready (sentence boundary OR max-prefix-cap)
        OR until OpenAI failure is final (post-retry).
        Raises SpeechRenderError on failure path; raises asyncio.CancelledError
        if cancelled while awaiting; returns None on success."""

    def commit(self) -> AsyncIterable[str]:
        """Single-use. Returns a joined async iterator that yields:
            (1) the buffered prefix (instant), then
            (2) the live OpenAI stream (token-paced).
        Raises RuntimeError if called after cancel() or if called twice."""

    async def cancel(self) -> None:
        """Idempotent. Closes OpenAI stream, hard-cancels internal Task.
        Pre-commit: cheap. Post-commit: best-effort (TTS may have synthesized audio)."""

    @property
    def is_committed(self) -> bool: ...

    @property
    def is_cancelled(self) -> bool: ...

    @property
    def metadata(self) -> asyncio.Future[RenderMetadata]: ...

    @property
    def completed_text(self) -> asyncio.Future[str]: ...
```

The Protocol is `@runtime_checkable` for test isinstance assertions. Two implementations: `StreamingRenderHandle` in `speech/agent.py` (live LLM path) and `StaticFallbackHandle` in `speech/fallbacks.py` (fallback path). Each implementation independently satisfies the Protocol; consumers depend on the Protocol type, not the concrete class.

### 2.3 The two-phase contract

The `ready_to_commit() → commit()` separation is the load-bearing primitive. It admits:

- **Phase C usage:** `await handle.ready_to_commit()` then immediately `handle.commit()` — Phase C never cancels post-ready-pre-commit.
- **Phase E forward-compat:** `await handle.ready_to_commit()`, then await Sufficiency Checker decision, then either `handle.commit()` (Sufficiency says `move_on`) or `handle.cancel()` (Sufficiency says `ask_followup`, render a follow-up handle instead). Cancellation in this window is cheap (no audio leaked).

Phase C tests verify both the happy-path commit and the cancel-without-commit path even though Phase C orchestrator code only exercises the former. The cancel-without-commit invariants are load-bearing for Phase E.

### 2.4 Buffer flavor — Option β (buffer-prefix-then-pipe-rest)

The internal Task buffers tokens until the **first sentence boundary** OR a **max-prefix cap of 100 tokens** is hit, whichever first. At that point `ready_to_commit()` resolves. `commit()` returns a joined async iterator: prefix tokens yielded immediately, then live OpenAI stream tokens at LLM pace.

**Sentence-boundary regex:** `[.!?]\s+[A-Z]` matched against the accumulating buffer. Terminator + whitespace + capital letter — defends against false positives on:

- Decimals: "11.5" doesn't close the prefix (no capital after decimal).
- Acronyms: "U.S." doesn't close at "U." (no capital after).
- Numbers: "1.5 dollars" doesn't close at "1.5" (no terminator-space-capital pattern).

The regex matches the **boundary**; the prefix is the buffer up to and including the terminator and trailing whitespace.

**Max-prefix cap:** 100 tokens. If no sentence boundary lands within the first 100 tokens, commit anyway with what's accumulated. Prevents unbounded buffering on long sentences. Will not normally fire for Phase C templates (`intro` is ~50 words, always with sentence boundary in first ~15 tokens).

**Empty-prefix protection:** if the first token IS a terminator (extremely unlikely), treated as `_PostFirstTokenFailure(reason="empty_stream")`; truncate path fires.

### 2.5 Internal Task lifecycle (`_drive` coroutine state machine)

```
                        ┌──────────────┐
                        │   opening    │   HTTP request opening, no tokens yet
                        └──────┬───────┘
                               │
                  first token / OpenAI failure
                               │
              ┌────────────────┼─────────────────┐
              ▼                                  ▼
     ┌─────────────────┐                ┌──────────────────┐
     │buffering_prefix │                │  errored_pre_    │
     │ (accumulating)  │                │   first_token    │
     └────────┬────────┘                │ (raise on r2c)   │
              │                         └──────────────────┘
   sentence boundary
   OR max-prefix cap
              │
              ▼
       ┌──────────┐
       │  ready   │  ready_to_commit() resolves here
       └─────┬────┘
             │
        commit() called
             │
             ▼
      ┌────────────┐         cancel() called
      │ committed  │──────────────────────────────►┌────────────┐
      │ (piping)   │                               │ cancelled  │
      └─────┬──────┘                               └────────────┘
            │
   stream closes &
   consumer drains
            │
            ▼
      ┌────────────┐
      │ completed  │  metadata + completed_text futures resolve
      └────────────┘
```

`asyncio.Event` primitives mediate transitions. The Task is a single `async def _drive(self)` coroutine on `StreamingRenderHandle`; callers interact only via the handle's public surface.

### 2.6 Error classes within the Task

| Failure | When | Behavior |
|---|---|---|
| `openai_timeout` (no first token within 8s) | Pre-first-token | One retry with same prompt. If retry also times out, transition to `errored_pre_first_token`. `ready_to_commit()` raises `SpeechRenderError(reason="openai_timeout")`. |
| `openai_5xx` | Pre-first-token | One retry, then errored. |
| `openai_connection_dropped_pre_first_token` | Connection drops before first token | One retry, then errored. |
| `openai_429` | Pre-first-token | **NOT retried** (rate-limit retry would compound the rate-limit). Immediate errored. |
| OpenAI failure post-first-token | Mid-stream after `ready_to_commit()` resolved | **Non-recoverable.** Buffer is what we have. If consumer has committed and TTS is playing: TTS finishes partial audio; emit `SPEECH_STREAM_INTERRUPTED` with `tokens_received`. If consumer has not committed yet (rare race): commit completes successfully with partial text. |
| `template_not_found` / `placeholder_missing` | Inside `render()`, before Task spawned | Synchronous raise as `SpeechRenderError`. Programmer error; not retried; fallback path NOT used (templates are broken → fallback machinery is too). |

**Retry construction:** same prompt, same model, same parameters. No "stricter instruction" — there's no content-violation path. Retry hedges against transient infrastructure errors only.

**SDK-level retries disabled:** SpeechAgent passes `max_retries=0` to `client.chat.completions.create`. The SpeechAgent owns the retry policy in its Task body; SDK retries would compound and exceed the per-attempt timeout.

### 2.7 Audit envelope emission timing

`SPEECH_RENDERED` fires at the **later** of:
- (a) the OpenAI stream has closed (final usage chunk received), and
- (b) the consumer has finished iterating `commit()`'s iterable (TTS playout complete or interrupted).

For cancelled handles: emit at `cancel()` time with payload reflecting the cancellation state.
For fallback handles: emit at `commit()` consumer-finished time with `was_fallback=true`.

---

## 3. Pre-render Task lifecycle owner

### 3.1 The slot

A single field on `StructuredInterviewAgent`:

```python
class StructuredInterviewAgent(Agent):
    def __init__(self, ..., speech_agent: SpeechAgent) -> None:
        ...
        self._speech_agent = speech_agent
        self._pending_next_render: asyncio.Task[SpeechRenderHandle] | None = None
```

Single field, not a dict. Phase C has at most one pre-render in flight at a time. Phase D adds a sibling slot (`_pending_sufficiency_check`); Phase E adds speculative behavior to the same slot. YAGNI: ship the field, refactor when needed.

### 3.2 Three trigger sites

**Trigger 1 — Intro pre-render in `on_enter()`.** Kicked off before `CONNECTING→CONSENT` transitions begin. Sits in the room-join + TTS-warmup window (Phase B smoke gate showed ~13s before intro audio). Free latency.

```python
# structured_agent.py::on_enter()
async def on_enter(self) -> None:
    self._session_start_monotonic = time.monotonic()
    log.info("structured_agent.on_enter", session_id=self._config.session_id, ...)

    self._pending_next_render = asyncio.create_task(
        deliveries.render_intro(
            self._speech_agent,
            candidate_first_name=_first_name(self._config.candidate.name),
            role_title=self._config.job_title,
            target_duration_minutes=self._config.stage.duration_minutes,
        )
    )
    self._main_loop_task = asyncio.create_task(self._run_main_loop())
    self._main_loop_task.add_done_callback(self._on_main_loop_done)
```

**Trigger 2 — Q0 pre-render at `INTRO→MAIN_LOOP`.** Kicked off before awaiting intro TTS playout, so Q0 LLM render overlaps intro speech (~3-5s playout, ~600ms LLM render).

```python
# structured_agent.py::_run_main_loop()
async def _run_main_loop(self) -> None:
    await self._transition_with_persist(InterviewPhase.CONSENT, reason="...")
    await self._transition_with_persist(InterviewPhase.INTRO, reason="...")

    intro_handle = await self._consume_pending_or_render(
        deliveries.render_intro,
        candidate_first_name=_first_name(self._config.candidate.name),
        role_title=self._config.job_title,
        target_duration_minutes=self._config.stage.duration_minutes,
    )

    # Spawn Q0 pre-render BEFORE awaiting intro playout.
    first_q = pick_next_question(self._state, self._config)
    if first_q is not None:
        self._pending_next_render = asyncio.create_task(
            deliveries.render_ask_question_standard(
                self._speech_agent, question_text=first_q.text,
            )
        )

    await self._say(intro_handle)
    await self._transition_with_persist(InterviewPhase.MAIN_LOOP, reason="...")

    while True:
        next_q = pick_next_question(self._state, self._config)
        if next_q is None:
            break
        await self._ask_one_question(next_q)

    # Wrap pre-render is the last assignment to _pending_next_render
    # made by _ask_one_question's tail logic.
    wrap_handle = await self._consume_pending_or_render(deliveries.render_wrap_normal)
    await self._transition_with_persist(InterviewPhase.NORMAL_WRAP, reason="...")
    await self._say(wrap_handle)
    self._end_outcome = "completed"
    await self._transition_with_persist(InterviewPhase.CLOSED, reason="normal_close")
    self._state.set_exit_mode(ExitMode.COMPLETED, ended_at=_now_utc())
    self._collector.append(kind=ORCHESTRATOR_EXIT, payload={...}, ...)
```

**Trigger 3 — Qn+1 pre-render after prior transcript.** Kicked off in `_ask_one_question` immediately after `transcript_future` resolves, parallel to ledger-write + envelope emission. **Spawn-before-persist order is load-bearing** — the LLM round-trip overlaps the persistence I/O window.

```python
# structured_agent.py::_ask_one_question()
async def _ask_one_question(self, q: QuestionConfig) -> None:
    qs = next((s for s in self._state.questions if s.question_id == q.id), None)
    qs.asked_at = _now_utc()
    qs.asked_mode = "standard"
    await self._persistence.write_state(self._state)
    self._collector.append(kind=ORCHESTRATOR_QUESTION_ASKED, ...)

    handle = await self._consume_pending_or_render(
        deliveries.render_ask_question_standard, question_text=q.text,
    )

    transcript_future = self._arm_user_turn()
    await self._say(handle)
    transcript = await transcript_future
    self._candidate_transcripts[q.id] = transcript
    qs.completed_at = _now_utc()
    qs.elapsed_seconds = (qs.completed_at - qs.asked_at).total_seconds()

    # Spawn next pre-render BEFORE persistence/eventing — load-bearing parallelism.
    # Phase C: pick_next_question(state, config) returns the right answer because
    # qs.completed_at has been set above. Phase D's coverage-aware selection makes
    # this less trivial; see carryforward 3.
    next_q = pick_next_question(self._state, self._config)
    if next_q is not None:
        self._pending_next_render = asyncio.create_task(
            deliveries.render_ask_question_standard(
                self._speech_agent, question_text=next_q.text,
            )
        )
    elif self._all_questions_done():
        self._pending_next_render = asyncio.create_task(
            deliveries.render_wrap_normal(self._speech_agent)
        )

    await self._persistence.write_ledger(self._ledger)
    self._collector.append(kind=ORCHESTRATOR_QUESTION_COMPLETED, ...)
```

### 3.3 The consumption helper

```python
async def _consume_pending_or_render(
    self,
    render_fn: Callable[..., Awaitable[SpeechRenderHandle]],
    **inputs,
) -> SpeechRenderHandle:
    """Use the pending slot if hot; otherwise render synchronously.

    Common path: slot is non-None and has been running in parallel.
    Cold path (failure / edge case, NOT common): slot is None — cold start
    before first pre-render fires, or slot was cancelled by template
    invalidation. Render synchronously; longer latency for that single utterance.
    """
    if self._pending_next_render is not None:
        try:
            return await self._pending_next_render
        except SpeechRenderError as exc:
            log.warning("speech.pre_render.failed", reason=exc.reason, render_id=exc.render_id)
            return await deliveries.fallback_for(
                self._speech_agent,
                template_name=render_fn.template_name,
                failure_reason=exc.reason,
                render_id=exc.render_id,
                **inputs,
            )
        finally:
            self._pending_next_render = None

    # Cold path
    try:
        return await render_fn(self._speech_agent, **inputs)
    except SpeechRenderError as exc:
        log.warning("speech.render.failed", reason=exc.reason, render_id=exc.render_id)
        return await deliveries.fallback_for(
            self._speech_agent,
            template_name=render_fn.template_name,
            failure_reason=exc.reason,
            render_id=exc.render_id,
            **inputs,
        )
```

`render_fn.template_name` is set by a marker decorator on each delivery wrapper (`@delivery(template_name="ask_question_standard")`) so the helper can pick the right fallback factory.

`deliveries.fallback_for` is a thin pass-through that calls `SpeechAgent.fallback_handle(template_name=..., template_version="v1", text=build_fallback_text(template_name=..., **inputs), failure_reason=exc.reason, retries_attempted=1, render_id=exc.render_id)`. The **same `render_id`** that the failed live render generated is reused for the fallback handle's events — this is what makes `speech.fallback_used` and `speech.rendered` (with `was_fallback=true`) correlate as a single logical fallback episode (§4.5).

### 3.4 Close handler cancellation propagation

Extends `agent.py`'s existing `_close_session` with one responsibility: cancel the pre-render slot if in flight, with a 2-second bounded wait.

```python
# agent.py::_close_session() — Phase C extension
async def _close_session(self, agent: StructuredInterviewAgent, ...) -> None:
    pending = agent._pending_next_render
    if pending is not None and not pending.done():
        pending.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(pending), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, SpeechRenderError):
            pass

    # Existing Phase B close work — persist SessionResult, set outcome,
    # finalize event log — unchanged.
    await agent._persist_session_result(outcome)
    ...
```

The 2-second timeout caps worst-case wait. The streaming-cancellation spike (§5.2) validates that actual cancellation propagation is comfortably under this cap (target p99 < 500ms).

### 3.5 The four cancellation sub-cases

| # | When | Cleanup work | Envelope events |
|---|---|---|---|
| 1 | Disconnect during render Task, no commit yet | Cancel Task → `_drive` catches `CancelledError` → closes OpenAI stream → resolves metadata Future with partial data | `SPEECH_RENDERED` with `committed=false, played=false, was_fallback=false`, `tokens_received=N` |
| 2 | Disconnect after Task complete, before commit | `pending` is done; close handler reads handle from slot, calls `handle.cancel()` (idempotent) | `SPEECH_RENDERED` with `committed=false, played=false` |
| 3 | Disconnect mid-PLAYOUT (after commit) | LiveKit cancels SpeechHandle → propagates to joined iterator → stops yielding → Task drains-and-discards remaining tokens; OpenAI conn closes | `SPEECH_RENDERED` with `committed=true, played=true, played_to_completion=false, was_fallback=false, retries=0`; `SPEECH_STREAM_INTERRUPTED` with `tokens_received` |
| 4 | Mid-render template invalidation (Phase H knockout / Phase I pause-end / Phase E speculative-prune) | **Out of Phase C scope.** Reserved-but-unused. Handle's `cancel()` exists and is exercised by tests, but no Phase C orchestrator code calls it during normal flow. | n/a in Phase C |

### 3.6 Edge cases pinned

- **Race: candidate disconnects while `_consume_pending_or_render` awaits `handle.ready_to_commit()`.** The await raises `CancelledError` (propagated from Task cancellation). Orchestrator's main loop unwinds, close handler takes over. No new code path.
- **Defensive cancel on `CLOSED` transition.** Close handler in §3.4 cancels the slot defensively regardless of how `CLOSED` was reached.
- **Q0 pre-render races against intro TTS.** Intro TTS is ~3-5s; Q0 render is ~600ms. Q0 always finishes first; the slot is hot at consumption time. No race.

---

## 4. Error handling and fallback path

### 4.1 Static fallback strings

Three strings, hand-reviewed for outcome-neutrality. Live in `speech/fallbacks.py`. **No runtime regex check.** Hand-review discipline encoded in test assertions only (see §5.1).

```python
# speech/fallbacks.py
def _intro_fallback(*, target_duration_minutes: int, **_) -> str:
    return (
        f"Hi, I'll be running a short technical screen with you today. "
        f"We'll be about {target_duration_minutes} minutes. "
        f"Take your time. Let's get started."
    )

def _ask_question_standard_fallback(*, question_text: str, **_) -> str:
    return question_text  # QuestionConfig.text is recruiter-validated content

_WRAP_NORMAL_FALLBACK: str = (
    "That's everything from my side. The recruiting team will be "
    "in touch with next steps."
)

_FALLBACK_BUILDERS: dict[str, Callable[..., str]] = {
    "intro": _intro_fallback,
    "ask_question_standard": _ask_question_standard_fallback,
    "wrap_normal": lambda **_: _WRAP_NORMAL_FALLBACK,
}

def build_fallback_text(*, template_name: str, **inputs) -> str:
    """Returns the fallback string for a given template.
    Raises KeyError on unknown template_name (programmer error)."""
    return _FALLBACK_BUILDERS[template_name](**inputs)
```

`intro` parameterizes `target_duration_minutes` — never hardcodes 15. A 30-minute senior-engineer session falling back to "about 30 minutes" preserves trust at the worst possible moment (post-infrastructure-failure).

### 4.2 StaticFallbackHandle

```python
# speech/fallbacks.py
class StaticFallbackHandle:
    """Satisfies the SpeechRenderHandle Protocol structurally.
    All futures pre-resolved at construction; commit() yields one chunk."""

    def __init__(
        self,
        *,
        text: str,
        template_name: str,
        template_version: str,
        failure_reason: str,
        retries_attempted: int,
        render_id: str,
        collector: EventCollector,
    ) -> None:
        self._text = text
        self._committed = False
        self._cancelled = False
        # Pre-resolve futures
        loop = asyncio.get_event_loop()
        self._metadata_fut: asyncio.Future[RenderMetadata] = loop.create_future()
        self._completed_text_fut: asyncio.Future[str] = loop.create_future()
        self._metadata_fut.set_result(RenderMetadata(
            render_id=render_id,
            template_name=template_name,
            template_version=template_version,
            model="<fallback>",  # intent model name, not "<fallback>" — set by factory
            latency_first_token_ms=None,
            latency_last_token_ms=None,
            tokens_in=None,
            tokens_out=None,
            length_words=len(text.split()),
            playout_duration_ms=None,
            was_fallback=True,
            retries=retries_attempted,
        ))
        self._completed_text_fut.set_result(text)

        # Emit speech.fallback_used at construction time (Pin 1)
        collector.append(
            kind=SPEECH_FALLBACK_USED,
            payload={
                "render_id": render_id,
                "template_name": template_name,
                "template_version": template_version,
                "reason": failure_reason,
                "retries_attempted": retries_attempted,
            },
            wall_ms=_wall_ms(),
        )

    async def ready_to_commit(self) -> None:
        return  # immediate

    def commit(self) -> AsyncIterable[str]:
        if self._cancelled:
            raise RuntimeError("Cannot commit a cancelled handle")
        if self._committed:
            raise RuntimeError("commit() may only be called once")
        self._committed = True

        async def _yield_once() -> AsyncIterator[str]:
            yield self._text
        return _yield_once()

    async def cancel(self) -> None:
        self._cancelled = True  # idempotent; nothing else to clean up

    @property
    def is_committed(self) -> bool:
        return self._committed

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def metadata(self) -> asyncio.Future[RenderMetadata]:
        return self._metadata_fut

    @property
    def completed_text(self) -> asyncio.Future[str]:
        return self._completed_text_fut
```

### 4.3 Catch sites — `SpeechRenderError` raises and catches

`SpeechRenderError` has **exactly two raise sites** and **exactly one catch site**.

**Raise site 1: synchronously inside `SpeechAgent.render()`** for `template_not_found` or `placeholder_missing`. Programmer errors. Propagate up to the orchestrator's main loop and crash the session loudly — fallback machinery assumes coherent templates and inputs; if templates are broken, fallback is too. Tests catch this before deploy. Resulting orchestrator crash → close handler → `SessionResult.exit_mode = TECHNICAL_FAILURE`.

**Raise site 2: out of `handle.ready_to_commit()` after retry exhaustion.** `openai_timeout`, `openai_5xx`, `openai_connection_dropped_pre_first_token`. Internal Task transitions to `errored_pre_first_token`; `ready_to_commit()` raises `SpeechRenderError(reason=...)`.

**Catch site: `StructuredInterviewAgent._consume_pending_or_render`** (§3.3). Catches the runtime-error subset (raise site 2), invokes `deliveries.fallback_for(...)`, returns the fallback handle. Does NOT catch synchronous programmer errors (raise site 1) — those propagate.

### 4.4 Retry policy

Retry happens **inside** `SpeechAgent._drive` (the Task body), not at the consumer level. **Pre-first-token only** — after first token is yielded, retrying would mean producing different tokens that conflict with what TTS may already be playing.

```python
async def _drive(self) -> None:
    for attempt in range(2):  # 1 original + 1 retry
        try:
            await self._open_stream_and_buffer_prefix(attempt=attempt)
            return  # success → live-pipe phase continues
        except _PreFirstTokenFailure as exc:
            if attempt == 0 and exc.reason != "openai_429":
                log.warning("speech.render.retry", reason=exc.reason, attempt=1)
                continue
            self._fail(reason=exc.reason, retries_attempted=2)
            return
        except _PostFirstTokenFailure as exc:
            self._truncate(reason=exc.reason)
            return
```

- `openai_429` is **not retried** — rate-limit retry would compound the rate-limit. Immediate fallback.
- Other pre-first-token failures: 1 retry, fixed. Worst-case wall-clock: 2 × 8s timeout = 16s before fallback fires.
- No exponential backoff. Single immediate retry hedges against transient errors without compounding the per-turn budget.

### 4.5 Audit envelope event sequence

**Happy path (live render):** one event per utterance — `SPEECH_RENDERED` with `was_fallback=false`.

**Fallback path (live render fails, fallback used):** **two events** per utterance.

```jsonc
// Event 1 — emitted at StaticFallbackHandle construction (in SpeechAgent.fallback_handle())
{
  "kind": "speech.fallback_used",
  "payload": {
    "render_id": "abc-123-...",
    "template_name": "ask_question_standard",
    "template_version": "v1",
    "reason": "openai_connection_dropped_pre_first_token",
    "retries_attempted": 1
  }
}

// Event 2 — emitted at consumer-finished gate (in _say after session.say returns)
{
  "kind": "speech.rendered",
  "payload": {
    "render_id": "abc-123-...",
    "template_name": "ask_question_standard",
    "template_version": "v1",
    "model": "gpt-5-mini",
    "latency_first_token_ms": null,
    "latency_last_token_ms": null,
    "tokens_in": null,
    "tokens_out": null,
    "length_words": 19,
    "playout_duration_ms": 3450,
    "committed": true,
    "played": true,
    "played_to_completion": true,
    "was_fallback": true,
    "retries": 1
  }
}
```

`render_id` is shared across both events (Pin 3), enabling clean joins under concurrency. Latency/token fields are `null` for fallback path (Pin 2) — analytics differentiate via `was_fallback` flag without floor-spike artifacts.

**Mid-stream interruption path (sub-case 3):** different code path.

```jsonc
{
  "kind": "speech.rendered",
  "payload": {
    "render_id": "...",
    "was_fallback": false,           // live render that got truncated
    "committed": true,
    "played": true,
    "played_to_completion": false,
    "retries": 0,                    // mid-stream errors aren't retried
    ...
  }
}
{
  "kind": "speech.stream_interrupted",
  "payload": {
    "render_id": "...",
    "tokens_received": 12,
    "reason": "openai_connection_dropped_post_first_token"
  }
}
```

`speech.fallback_used` does **not** fire on mid-stream interruption — it's a live render that truncated, not a fallback.

---

## 5. Testing strategy and integration points

### 5.1 Test taxonomy

| Category | Location | Runs | Mocks |
|---|---|---|---|
| **Unit (pure logic)** | `tests/interview_engine/speech/` | Per-PR | Mocked `AsyncOpenAI`, no LiveKit, no DB |
| **Integration (real wiring)** | `tests/interview_engine/test_structured_agent_integration.py` | Per-PR | Mocked LiveKit transport; real `SpeechAgent`, real `StructuredInterviewAgent`, mocked OpenAI |
| **Prompt quality (real LLM)** | `tests/interview_engine/speech/prompt_quality/` | `@pytest.mark.prompt_quality`, **nightly only** | Real OpenAI; per-template properties |
| **Fallback content (test-time hand-review)** | `tests/interview_engine/speech/test_fallbacks.py` | Per-PR | None — pure string assertions with inline `FORBIDDEN_PHRASES` |
| **Build-step gate (one-time spike)** | `tests/interview_engine/speech/spike_streaming_cancellation.py` | **Once, before SpeechAgent class merge.** Result documented in close-out ADR. | Real `AsyncOpenAI` against sandbox key |

### 5.2 Streaming-cancellation spike — build-step gate

**What it verifies:** that cancelling the SpeechAgent's internal Task during a live OpenAI stream actually closes the underlying httpx connection (vs. abandoning a Future while the connection leaks).

**Spike protocol:** open a streaming chat completion, consume 5 tokens, cancel the Task, observe httpx connection state. **Run 10 times.** Assert **p99 cancellation-to-connection-close latency < 500ms**. The runtime close-handler timeout (2 seconds, §3.4) is the safety cap; the spike validates cancellation is comfortably under the cap so the timeout isn't hit on the normal path.

**Build-sequence gate:**

```
[Phase C build sequence]
1. Add get_openai_raw_client() factory in app/ai/client.py + tests
2. Add speech_agent_model + speech_agent_effort in AIConfig + Settings + .env.example + tests
3. RUN streaming-cancellation spike against real sandbox OpenAI key.
   Document result in docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md ADR section.
   ──────── HARD GATE ────────
   PASS  → ARCH-D ships as designed (Option β, prefix-pipe streaming).
   FAIL  → ARCH-D collapses to ARCH-D-buffered-non-streaming
           (Option α, eager-buffer-all). Protocol surface preserved;
           StreamingRenderHandle internal semantics change to
           drain-fully-before-ready_to_commit.
4. (PASS path) Implement StreamingRenderHandle, SpeechAgent class
5. Implement StaticFallbackHandle in fallbacks.py
6. Implement deliveries.py with 3 render_X wrappers + fallback_for
7. Wire into structured_agent.py (replace _phase_b_utterances calls)
8. Modify agent.py — construct SpeechAgent in entrypoint
9. Run integration test suite end-to-end
10. Manual smoke test in real LiveKit session (one full interview)
11. Phase C close-out ADR + miscall log entries from manual smoke
```

### 5.3 Unit tests — `tests/interview_engine/speech/test_speech_agent.py`

| Test | Asserts |
|---|---|
| `test_render_happy_path` | Mocked client streams 3 chunks; `ready_to_commit()` resolves; `commit()` yields concatenated tokens; metadata correct |
| `test_render_first_sentence_prefix` | Mocked stream `"Hi there. Let's begin."`; prefix is `"Hi there. "`; rest pipes live |
| `test_render_max_prefix_cap_100_tokens` | Mocked stream of 150 tokens with no sentence boundary; commit fires at 100 |
| `test_render_prefix_avoids_false_sentence_boundaries` *(parameterized)* | Three sub-cases: `"In section 11.5 we describe..."` (decimal), `"The U.S. office hours are..."` (acronym), `"That costs 1.5 dollars. Let's continue."` (terminator-but-not-followed-by-capital). Prefix in each case includes the full first real sentence. |
| `test_retries_once_on_openai_timeout` | Mocked client times out attempt 1, succeeds attempt 2; `metadata.retries == 1` |
| `test_429_not_retried` | Mocked client returns 429 attempt 1; no retry; immediate fallback path |
| `test_falls_back_after_two_failures` | Mocked client times out twice; `ready_to_commit()` raises; consumption helper invokes fallback; both envelope events fire with shared `render_id` |
| `test_does_not_retry_post_first_token_failure` | Mocked client emits 5 tokens then drops; no retry; `speech.stream_interrupted` emits with `tokens_received=5`; `speech.fallback_used` does NOT emit |
| `test_template_not_found_raises_synchronously` | `render(template_name="nonexistent", ...)` raises `SpeechRenderError(reason="template_not_found")` before Task spawn |
| `test_placeholder_missing_raises_synchronously` | Template has `{candidate_first_name}`; inputs omits it; raises `SpeechRenderError(reason="placeholder_missing")` |
| `test_max_retries_zero_passed_to_openai_client` | `client.chat.completions.create` kwargs include `max_retries=0` |
| `test_cancel_during_buffering_raises_cancelled_error_on_ready_to_commit` | Mocked: emit 1-2 tokens, call `handle.cancel()` while `ready_to_commit()` awaiting; raises `asyncio.CancelledError` (NOT `SpeechRenderError`); OpenAI stream closes; subsequent `commit()` raises `RuntimeError` |
| `test_cancel_in_ready_state_raises_runtime_error_on_subsequent_commit` *(load-bearing for Phase E)* | Stream up through `ready_to_commit()` resolution; `cancel()` without `commit()`; awaits cleanly (no exception); subsequent `commit()` raises `RuntimeError`; `is_cancelled` becomes True; driver Task hard-cancelled (httpx connection released — verified per spike) |
| `test_render_id_propagates_to_envelope_events` | Generated `render_id` appears in `speech.rendered`, `speech.fallback_used`, `speech.stream_interrupted` for the same logical render |
| `test_speech_rendered_emits_after_both_stream_close_and_playout` | Mocked: stream closes at t=500ms; TTS playout finishes at t=2000ms. `SPEECH_RENDERED.wall_ms >= 2000`; `latency_last_token_ms ~= 500`; `playout_duration_ms ~= 1500`; both fields non-null |
| `test_empty_stream_yields_minimal_completed_text` | OpenAI returns finish_reason on first chunk with no content; treated as `_PostFirstTokenFailure(reason="empty_stream")`; truncate path |

### 5.4 Unit tests — `tests/interview_engine/speech/test_handles.py`

| Test | Asserts |
|---|---|
| `test_streaming_render_handle_satisfies_protocol` | `isinstance(StreamingRenderHandle(...), SpeechRenderHandle)` via `runtime_checkable` |
| `test_static_fallback_handle_satisfies_protocol` | Same for `StaticFallbackHandle` |
| `test_static_fallback_handle_pre_resolved_futures` | `metadata` and `completed_text` futures pre-resolved on construction; `commit()` yields exactly one chunk == text |
| `test_static_fallback_handle_cancel_is_noop` | `cancel()` returns immediately; subsequent `commit()` raises `RuntimeError` |
| `test_static_fallback_handle_emits_fallback_used_on_construction` | Constructing emits `speech.fallback_used` with `reason`, `template_name`, `template_version`, `retries_attempted`, `render_id` |

### 5.5 Unit tests — `tests/interview_engine/speech/test_fallbacks.py`

```python
# inline in this test file ONLY — NEVER as production constants
FORBIDDEN_PHRASES = (
    "passed", "failed", "rejected", "advanced",
    "unfortunately", "best of luck",
    "thanks for your interest",
)
```

| Test | Asserts |
|---|---|
| `test_intro_fallback_uses_duration` | `_intro_fallback(target_duration_minutes=30)` contains `"30"`, not `"15"` |
| `test_intro_fallback_length_le_50_words_across_durations` | Word count ≤ 50 for `target_duration_minutes ∈ {5, 15, 30, 60}` |
| `test_wrap_normal_fallback_length_le_30_words` | Word count ≤ 30 |
| `test_ask_question_standard_fallback_is_verbatim` | `_ask_question_standard_fallback(question_text="X") == "X"` |
| `test_fallback_strings_outcome_neutral` | Each builder's output passes the inline `FORBIDDEN_PHRASES` check (case-insensitive substring) |
| `test_fallback_strings_no_salary_or_scheduling` | Inline tests for currency markers, scheduling commitments, hiring-manager mentions |
| `test_build_fallback_text_unknown_template_raises` | `build_fallback_text(template_name="nonexistent")` raises `KeyError` |

The list of forbidden phrases lives **inline in this test file only**. Recreating it as a production constants module reintroduces exactly the `safety.py` we deleted. Code review enforces this discipline.

### 5.6 Integration tests — `tests/interview_engine/test_structured_agent_integration.py`

| Test | Asserts |
|---|---|
| `test_full_happy_path_with_pre_render_slot` | 3-question session; envelope contains exactly **5** `speech.rendered` events (intro + 3 questions + wrap_normal); pre-render slot consumed each turn; no fallbacks; `render_id` values are unique per event |
| `test_disconnect_during_render_task_subcase_1` | Disconnect mid-buffering; envelope: `speech.rendered` with `committed=false, played=false`; `SessionResult.exit_mode = candidate_disconnected` |
| `test_disconnect_after_render_before_commit_subcase_2` | Slot Task completes; close handler fires before consume; envelope: `speech.rendered` with `committed=false, played=false` |
| `test_disconnect_mid_playout_subcase_3` | TTS playing; participant disconnects; envelope: `speech.rendered` with `committed=true, played=true, played_to_completion=false, was_fallback=false, retries=0` + `speech.stream_interrupted` with `tokens_received` |
| `test_speech_render_error_triggers_fallback_path_and_session_continues` | 3-question session; mocked OpenAI fails twice on Q1; Q1 fallback fires; Q2 + Q3 render normally. Envelope contains 6 `speech.rendered` (intro + Q0 + Q1-fallback + Q2 + Q3 + wrap), exactly 1 `speech.fallback_used`. `SessionResult.question_results` has 3 entries, all populated. `SessionResult.exit_mode = COMPLETED` (NOT TECHNICAL_FAILURE — fallback continues, doesn't fail the session) |
| `test_template_not_found_results_in_technical_failure_exit` | `render(template_name="nonexistent")` raises synchronously; orchestrator main loop crashes; close handler fires; `SessionResult.exit_mode = TECHNICAL_FAILURE` persisted |
| `test_pre_render_slot_cancelled_on_close` | Pre-render Task in flight when close fires; close handler cancels within 2s timeout; httpx connection released (verified per spike result) |
| `test_no_speech_safety_violation_constant_imported` | Both: (i) `from app.modules.interview_engine.event_kinds import SPEECH_SAFETY_VIOLATION` raises `ImportError`; (ii) repo-wide grep for `SPEECH_SAFETY_VIOLATION` and `speech.safety_violation` returns zero matches across all `.py` and `.md` files. Catches both deliberate re-imports and accidental name reuse with new purpose. |

### 5.7 Prompt quality tests — `@pytest.mark.prompt_quality`, nightly only

| Test | Asserts |
|---|---|
| `test_intro_real_llm_no_outcome_words` | Real LLM call across 5 (candidate_name, role_title, target_duration_minutes) tuples; assert no outcome words in any output |
| `test_intro_real_llm_length_target` | Same; assert ≤ 50 words |
| `test_intro_real_llm_does_not_mention_question_count` | Same; assert no digit-counting language |
| `test_ask_question_standard_real_llm_preserves_meaning` | Real LLM call; assert key noun phrases from input `question_text` appear in output |
| `test_wrap_normal_real_llm_no_outcome_implications` | Real LLM call across 3 invocations; assert no `best of luck` / `thanks for your interest` / outcome words |

Until the eval harness ships (parallel workstream, separate spec), `test_fallbacks.py` + the prompt-quality marker tests are the only programmatic gates on rendered content. PR review is the human gate. The harness later closes the regression-detection loop without changing these tests' role.

### 5.8 Integration-points checklist

#### `backend/nexus/app/ai/client.py` — add `get_openai_raw_client()`

| Aspect | Detail |
|---|---|
| Diff scope | One new factory function (~15 lines) returning `openai.AsyncOpenAI`. Existing `get_openai_client()` unchanged. |
| Downstream consumers | Only `speech/agent.py` imports the new factory. Evaluators (Phase D-H) continue using `get_openai_client()` (instructor-wrapped). |
| Tests gating | `tests/test_ai_client.py` adds: (i) raw client returns `AsyncOpenAI` instance; (ii) raw and instructor-wrapped clients share underlying httpx config (timeout, base URL). |

#### `backend/nexus/app/ai/config.py` + `app/config.py` + `.env.example`

| Aspect | Detail |
|---|---|
| Diff scope | Two new properties on `AIConfig` (`speech_agent_model`, `speech_agent_effort`); two new fields on `Settings`; two env vars in `.env.example` (`INTERVIEW_SPEECH_AGENT_MODEL` default `gpt-5-mini`; `INTERVIEW_SPEECH_AGENT_EFFORT` default empty). |
| Downstream consumers | Only `agent.py`'s SpeechAgent construction. |
| Tests gating | `tests/test_ai_config.py`: (i) properties read from settings, (ii) chat-tier model with empty effort doesn't raise, (iii) reasoning-tier model with effort flows through. |

#### `backend/nexus/app/modules/interview_engine/agent.py`

| Aspect | Detail |
|---|---|
| Diff scope | ~20 lines added. Imports `SpeechAgent`, `get_openai_raw_client`. Entrypoint constructs `SpeechAgent` and passes it as a kwarg to `StructuredInterviewAgent(...)`. Close handler extended with 2-second cancellation grace per §3.4. |
| Downstream consumers | None outside `interview_engine`. |
| Tests gating | Existing entrypoint smoke test extended to verify `SpeechAgent` construction + injection. New test: close handler cancels `_pending_next_render` if non-None and not-done, with bounded wait. |

#### `backend/nexus/app/modules/interview_engine/structured_agent.py`

| Aspect | Detail |
|---|---|
| Diff scope | Largest single-file change in Phase C (~150 lines net delta). Constructor accepts `speech_agent: SpeechAgent`. Adds `_pending_next_render: asyncio.Task | None` field. New `_consume_pending_or_render` helper. New `_say(handle: SpeechRenderHandle)` signature replacing string-arg version. Three trigger sites for pre-render kickoff. Removes `_phase_b_utterances` import. |
| Downstream consumers | `agent.py` constructs and uses it — kwarg signature change caught at construction. Tests directly imported. |
| Tests gating | Integration test suite (§5.6); module-boundary test re-runs to confirm no deep imports. |

#### `backend/nexus/app/modules/interview_engine/event_kinds.py`

| Aspect | Detail |
|---|---|
| Diff scope | DELETE `SPEECH_SAFETY_VIOLATION` constant. ADD `SPEECH_STREAM_INTERRUPTED`. Update `ALL_EVENT_KINDS` membership. `SPEECH_RENDERED` and `SPEECH_FALLBACK_USED` constants stay; payload schemas documented in §4.5. |
| Downstream consumers | Only `structured_agent.py` (in scope) and tests. Frontend has zero references (verified by grep). |
| Tests gating | `tests/interview_engine/test_event_kinds.py` registry test; manual repo-wide grep for `SPEECH_SAFETY_VIOLATION` and `speech.safety_violation` — must return zero matches. |

#### `backend/nexus/app/modules/interview_engine/speech/__init__.py`

| Aspect | Detail |
|---|---|
| Diff scope | DROP re-exports of `SafetyResult`, `SafetyViolation`, `check_safety`. ADD re-exports of `SpeechAgent`, `SpeechRenderHandle` (Protocol), `SpeechRenderError`, `RenderMetadata`, `StreamingRenderHandle`, `StaticFallbackHandle`. |
| Downstream consumers | `structured_agent.py`, tests. Deleted re-exports cause clean ImportError at any rogue consumer. |
| Tests gating | Module-boundary test confirms `__all__` matches new public surface. |

---

## 6. Phase C close-out carryforwards

Concerns surfaced during Phase C brainstorming that live downstream. Each Phase D-H spec inherits the relevant items.

1. **Pre-render Task lifecycle owner.** `_pending_next_render: asyncio.Task[SpeechRenderHandle] | None` with three trigger sites + four cancellation sub-cases. Phase D adds a sibling slot for `_pending_sufficiency_check`. Phase E adds speculative-branch behavior to `_pending_next_render` (cancel-and-replace on `ask_followup`). Phase H adds template-invalidation cancel (sub-case 4). Phase I adds pause-decline-to-end cancel.

2. **Speculative-branch readiness as the buffered+commit primitive's reason.** Phase E should use `handle.cancel()` post-`ready_to_commit()` pre-`commit()` for `ask_followup` branches. Phase C tests verify the Protocol invariants this depends on.

3. **Phase C's pre-render slot fills based on `pick_next_question(state, config)` called after `qs.completed_at` is set.** Phase D's coverage-aware question selection makes the next-question identity uncertain at the spawn-pre-render site — the just-completed question's signal coverage is decided by Sufficiency Checker, which runs in parallel. Phase D spec needs a section on "what does the pre-render slot pre-render under coverage-uncertainty?" Options: speculative pre-render (Phase E primitive) or sequential render (latency tax).

4. **`get_openai_raw_client()` is a Phase C addition.** Phase D evaluators do NOT call it; they continue with `get_openai_client()` (instructor-wrapped). The raw factory is reserved for plain-text streaming consumers.

5. **Pre-render LIFECYCLE pattern is shared infrastructure** — spawn-as-Task, hold in slot, await-or-cancel, idempotent cancel, bounded close-handler timeout. Phase D's sufficiency check, Phase F's intent classification, Phase H's disclaim check all reuse this lifecycle.

   The Protocol SURFACE is template-specific: `SpeechRenderHandle` exposes streaming-text shape (`AsyncIterable[str]`, string `completed_text`). Evaluator handles expose structured-output shape (Pydantic result). Each phase defines its own handle Protocol; the lifecycle wrapping (Task management, cancel semantics, slot field on `StructuredInterviewAgent`) is the reusable abstraction.

   Phase D defines `_pending_sufficiency_check` field with the same Task lifecycle as `_pending_next_render`, but the Task's result type is `SufficiencyOutput` (Pydantic), not `SpeechRenderHandle`. The slot's contract is "await it, get a result; cancel it, get clean cancellation" — that's the reusable contract.

6. **`render_id` correlation field.** Phase D-H envelope events join on `render_id` for "this evaluator output corresponds to this rendered utterance" analytics. Phase D spec must enforce this.

7. **OTel span lifetime under streaming.** Phase C verifies the OpenAI auto-instrumentor spans correctly under streaming. If a discrepancy is found, manual span management lives in `SpeechAgent._drive`; Phase D-H inherit the pattern. Result documented in close-out ADR.

8. **The eval harness is a parallel workstream, not Phase C scope.** Phase C ships with `@pytest.mark.prompt_quality` smoke coverage only. The eval harness corpus (~80-120 cases per template across C/D/F/G/H) is owned by a separate spec, runs nightly, and is the long-term regression net.

---

## 7. Doc amendments queue

Will be committed in the **same commit** as this spec file:

1. **`docs/ai-screening-agent/ai-screening-agent-design.md` §11.5** — replace four-layer audit-only-regex model (current v2) with three-layer model: prompt as gate / versioned templates / manual adversarial eval + miscall log. Forward-reference Layer 4 (eval harness, parallel workstream). Drop "audit envelope monitoring" layer entirely. Drop "Patterns monitored by audit" subsection. Rewrite "Why not regex-as-blocker" → "Why no regex layer at all" with the five-point reasoning condensed.

2. **`docs/ai-screening-agent/ai-screening-agent-implementation.md` §7 Phase C amendment header** — `"audit-only safety"` → `"prompt-only safety enforcement (no regex layer)"`.

3. **`docs/ai-screening-agent/ai-screening-agent-implementation.md` §8 rule 3** — confirm no `+ safety regex` clause; keep length-cap-lenient text.

4. **`docs/ai-screening-agent/ai-screening-agent-implementation.md` §8 rule 5** — drop `Monitored by safety.py regex post-hoc` clause; reframe to "Enforced by template prompt's MUST-NOT rules; verified by manual session review and eval harness regression tests. See design doc §11.5."

5. **This spec file.**

All five edits land atomically. No v2-then-v3 churn.

---

## 8. Open questions / explicit non-goals

### 8.1 Open until close-out

| Item | Resolution gate |
|---|---|
| Streaming-cancellation spike result (ARCH-D vs ARCH-D-buffered-non-streaming) | Documented in `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md` ADR before merge |
| OTel auto-instrumentor behavior under streaming | Verified by build-step check; documented in close-out ADR; manual span management implemented if discrepancy found |
| Median + p99 cancellation latency observed in spike | Documented in close-out ADR alongside spike PASS/FAIL |
| Real-LLM `was_fallback` rate over week-1 sample | Tracked as a metric; if >5% sustained, prompt revision triggers `intro.v2.txt` etc. |

### 8.2 Deferred to specific later phases

| Item | Owning phase |
|---|---|
| 11 remaining Speech Agent templates | Each template's consuming phase (D-I) |
| Speculative pre-render (cancel-and-replace based on Sufficiency outcome) | Phase E |
| Coverage-aware next-question selection at pre-render site | Phase D |
| Mid-render template invalidation triggers (knockout, pause-decline-to-end) | Phase H, Phase I |
| Sufficiency Checker, Intent Classifier, Disclaim Classifier | Phases D, F, H respectively |
| Eval harness corpus (~80-120 cases per template) | Parallel workstream, separate spec |
| Report Builder spike against Phase C-shaped audit envelope | Phase D gate |
| Reconnect protocol, silence policy, pause request | Phase I |

### 8.3 Explicit non-goals (will not happen in any phase)

- Regex-based safety layer at runtime (prompt + versioning + audit + eval harness is the model; see design doc §11.5).
- Length-cap retries (Q4 A2 lock — length is a metric, not a gate).
- Mid-stream LLM retry after first token (non-recoverable; see §4.4).
- New AIConfig keys for Phase C beyond `speech_agent_*` (evaluator keys landed in Phase A; do not duplicate).
- `SPEECH_AUDIT_FLAGGED` event kind (audit-only regex was rejected; constant never created).
- Production constants module duplicating the `FORBIDDEN_PHRASES` test-inline list (would reintroduce `safety.py`).

---

*End of Phase C design spec. Single source of truth for the SpeechAgent class API, pre-render lifecycle, error handling, testing, and integration points. Pairs with re-amended design doc §11.5 (three-layer safety model). Supersedes implementation doc §7 Phase C section.*
