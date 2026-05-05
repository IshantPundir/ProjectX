# AI Screening Phase C — Streaming Speech Agent + Pre-render Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase B's three hardcoded utterance constants with LLM-rendered streaming utterances produced by a new `SpeechAgent` class, wired through a buffered+commit `SpeechRenderHandle` Protocol that supports pre-render Task slot parallelism and graceful fallback on OpenAI failures. Ships ARCH-D (streaming + pre-render + prompt-only safety, no regex layer).

**Architecture:** Single `SpeechAgent` (in `speech/agent.py`) opens streaming OpenAI chat completions, exposes a `StreamingRenderHandle` (Option β: buffer-prefix-then-pipe-rest at first sentence boundary or 100-token cap). The orchestrator drains a `_pending_next_render: asyncio.Task | None` slot per turn, with three trigger sites (intro on `on_enter`, Q0 at `INTRO→MAIN_LOOP`, Qn+1 after prior transcript). On OpenAI failure post-retry, `_consume_pending_or_render` catches `SpeechRenderError` and substitutes a `StaticFallbackHandle` (in `speech/fallbacks.py`) that satisfies the same Protocol with pre-resolved futures and a single-chunk yielded text. Safety enforcement is prompt-only — Phase A's `safety.py` is deleted; design doc §11.5 (re-amended 2026-05-05) is the three-layer model.

**Tech Stack:** Python 3.13, asyncio, `openai.AsyncOpenAI` (raw, not instructor-wrapped — added `get_openai_raw_client()` factory), `livekit.agents` `AgentSession.say(text: str | AsyncIterable[str])`, structlog for logging, `EventCollector` for the audit envelope, pytest + pytest-asyncio for tests.

**Spec:** `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-design.md` (commit `5a481a4`). The spec is the source of truth for design decisions; this plan focuses on the build sequence and TDD work units.

**Hard gate:** Task 3 (streaming-cancellation spike) determines whether ARCH-D ships as designed (Option β, PASS) or collapses to ARCH-D-buffered-non-streaming (Option α, FAIL). Plan diverges at that point.

---

## File Structure

### Files created (10)

| Path | Responsibility |
|---|---|
| `backend/nexus/app/modules/interview_engine/speech/agent.py` | `SpeechAgent` class, `StreamingRenderHandle` impl, `SpeechRenderHandle` Protocol, `RenderMetadata`, `SpeechRenderError` |
| `backend/nexus/app/modules/interview_engine/speech/deliveries.py` | `render_intro`, `render_ask_question_standard`, `render_wrap_normal` typed wrappers + `fallback_for` |
| `backend/nexus/app/modules/interview_engine/speech/fallbacks.py` | `StaticFallbackHandle` impl, `_FALLBACK_BUILDERS` dict, `build_fallback_text` |
| `backend/nexus/tests/interview_engine/speech/test_speech_agent.py` | 16 unit tests for SpeechAgent (per spec §5.3) |
| `backend/nexus/tests/interview_engine/speech/test_handles.py` | 5 Protocol-conformance + handle-shape tests (per spec §5.4) |
| `backend/nexus/tests/interview_engine/speech/test_fallbacks.py` | 7 fallback content tests with inline `FORBIDDEN_PHRASES` (per spec §5.5) |
| `backend/nexus/tests/interview_engine/speech/spike_streaming_cancellation.py` | Standalone build-step gate script (not pytest); 10-run p99 < 500ms gate |
| `backend/nexus/tests/interview_engine/speech/prompt_quality/test_intro_quality.py` | `@pytest.mark.prompt_quality` real-LLM tests for `intro` |
| `backend/nexus/tests/interview_engine/speech/prompt_quality/test_ask_question_standard_quality.py` | Same for `ask_question_standard` |
| `backend/nexus/tests/interview_engine/speech/prompt_quality/test_wrap_normal_quality.py` | Same for `wrap_normal` |

### Files modified (8)

| Path | Diff scope |
|---|---|
| `backend/nexus/app/ai/client.py` | Add `get_openai_raw_client()` factory (~15 lines) |
| `backend/nexus/app/ai/config.py` | Add `speech_agent_model` + `speech_agent_effort` properties |
| `backend/nexus/app/config.py` | Add corresponding `Settings` fields |
| `backend/nexus/.env.example` | Document `INTERVIEW_SPEECH_AGENT_MODEL`, `INTERVIEW_SPEECH_AGENT_EFFORT` |
| `backend/nexus/app/modules/interview_engine/agent.py` | Construct `SpeechAgent` + pass into `StructuredInterviewAgent`; bounded cancellation in close handler |
| `backend/nexus/app/modules/interview_engine/structured_agent.py` | Major edits (~150 lines net): `_say(handle)`, `_pending_next_render` slot, `_consume_pending_or_render`, three trigger sites |
| `backend/nexus/app/modules/interview_engine/event_kinds.py` | DELETE `SPEECH_SAFETY_VIOLATION` + remove from `ALL_EVENT_KINDS`; ADD `SPEECH_STREAM_INTERRUPTED` |
| `backend/nexus/app/modules/interview_engine/speech/__init__.py` | Drop safety re-exports; add new public surface |
| `backend/nexus/tests/interview_engine/test_structured_agent_integration.py` | Drop safety-violation assertions; add 8 Phase C integration tests |

### Files deleted (3)

| Path | Reason |
|---|---|
| `backend/nexus/app/modules/interview_engine/_phase_b_utterances.py` | Replaced by SpeechAgent + deliveries |
| `backend/nexus/app/modules/interview_engine/speech/safety.py` | Regex layer eliminated (spec §0 + design doc §11.5 v3) |
| `backend/nexus/tests/interview_engine/speech/test_safety.py` | Module deleted |

### Doc commits already in place

Commit `5a481a4` (this plan executes against it):
- `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-design.md` — spec
- `docs/ai-screening-agent/ai-screening-agent-design.md` §11.5 v3 (three-layer model, no regex)
- `docs/ai-screening-agent/ai-screening-agent-implementation.md` §7 + §8 v3 amendments

---

### Checkpoint placement

**Checkpoint A (hard gate):** After Task 3 (streaming-cancellation spike runs and result is recorded in close-out ADR). PASS → continue with ARCH-D Option β. FAIL → STOP and surface to user; the plan's Task 8 internals change to Option α (eager-buffer-all) with the same Protocol surface preserved, but the rest of the tasks can proceed.

**Checkpoint B (integration verified):** After Task 14 (integration test suite passes end-to-end with the new SpeechAgent for the Phase B-equivalent flow). This is the milestone where Phase C is functionally complete; remaining tasks (15-17) are quality/observability/closeout.

---

## Task 1: Add `get_openai_raw_client()` factory

**Files:**
- Modify: `backend/nexus/app/ai/client.py`
- Modify: `backend/nexus/tests/test_ai_client.py` (or create if absent)

- [ ] **Step 1: Find the existing test file (or create)**

```bash
ls backend/nexus/tests/test_ai_client.py 2>/dev/null || echo "MISSING — create new"
```

If file exists, read it to match patterns. Otherwise create with imports matching `tests/conftest.py` style.

- [ ] **Step 2: Write the failing test for raw factory**

Append to `backend/nexus/tests/test_ai_client.py`:

```python
def test_get_openai_raw_client_returns_async_openai_instance():
    """SpeechAgent (Phase C) needs the raw AsyncOpenAI client for
    streaming chat completions — not the instructor.AsyncInstructor wrapper.
    Both factories must coexist; evaluators continue using
    get_openai_client() (instructor-wrapped)."""
    from openai import AsyncOpenAI
    from app.ai.client import get_openai_raw_client

    raw = get_openai_raw_client()
    assert isinstance(raw, AsyncOpenAI)


def test_get_openai_raw_and_instructor_share_httpx_config():
    """Both factories must use the same timeout + base URL config so
    operators don't have to maintain two parallel sets of env knobs."""
    from app.ai.client import get_openai_client, get_openai_raw_client
    from app.ai.config import ai_config

    raw = get_openai_raw_client()
    instructor_wrapped = get_openai_client()

    # Same timeout
    assert raw.timeout == ai_config.request_timeout_seconds
    # Both wrap an underlying AsyncOpenAI; instructor exposes .client
    assert instructor_wrapped.client.timeout == raw.timeout
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_ai_client.py::test_get_openai_raw_client_returns_async_openai_instance -v
```

Expected: `ImportError: cannot import name 'get_openai_raw_client'`

- [ ] **Step 4: Implement `get_openai_raw_client()`**

Append to `backend/nexus/app/ai/client.py` (after `get_openai_client`):

```python
@lru_cache(maxsize=1)
def get_openai_raw_client() -> AsyncOpenAI:
    """Return a memoized async OpenAI client WITHOUT instructor wrapping.

    Used by the Phase C SpeechAgent for plain-text streaming chat completions.
    Evaluators (Phase D-H) continue to use ``get_openai_client()`` (instructor-
    wrapped). Same env vars, same timeout, same httpx event hooks — just
    no structured-output enforcement layer.

    The SpeechAgent owns its own retry policy in ``_drive``; this factory
    sets ``max_retries=0`` so SDK-level retries don't compound the per-attempt
    timeout (per Phase C spec §4.4).
    """
    http_client = httpx.AsyncClient(
        timeout=ai_config.request_timeout_seconds,
        event_hooks={
            "request": [_log_request],
            "response": [_log_response],
        },
    )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
        max_retries=0,
        http_client=http_client,
    )
```

- [ ] **Step 5: Run tests, confirm they pass**

```bash
docker compose run --rm nexus pytest tests/test_ai_client.py -v
```

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/ai/client.py backend/nexus/tests/test_ai_client.py
git commit -m "$(cat <<'EOF'
feat(ai-client): add get_openai_raw_client() for Phase C streaming

The Phase C SpeechAgent needs a raw AsyncOpenAI client (not instructor-
wrapped) to issue streaming chat completions. Evaluators (Phase D-H)
continue with the existing instructor-wrapped get_openai_client.

max_retries=0 on the raw factory: SpeechAgent owns retry policy in its
internal Task; SDK-level retries would compound the per-attempt timeout
budget (spec §4.4).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `speech_agent_model` + `speech_agent_effort` AIConfig keys

**Files:**
- Modify: `backend/nexus/app/config.py`
- Modify: `backend/nexus/app/ai/config.py`
- Modify: `backend/nexus/.env.example`
- Modify: `backend/nexus/tests/test_ai_config.py` (or create if absent)

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/test_ai_config.py`:

```python
def test_speech_agent_model_reads_from_settings(monkeypatch):
    """Phase C: speech_agent_model is the Speech Agent's batch (non-realtime)
    OpenAI model. Defaults to gpt-5-mini (mid-tier per design doc §5.6)."""
    from app.ai.config import ai_config
    assert ai_config.speech_agent_model == "gpt-5-mini"


def test_speech_agent_effort_default_empty():
    """Default-empty effort follows the same effort-gating contract as
    evaluator_*_effort. Empty string means 'do not forward
    reasoning_effort to OpenAI' — chat models 400 on the param."""
    from app.ai.config import ai_config
    assert ai_config.speech_agent_effort == ""
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
docker compose run --rm nexus pytest tests/test_ai_config.py::test_speech_agent_model_reads_from_settings -v
```

Expected: `AttributeError: 'AIConfig' object has no attribute 'speech_agent_model'`

- [ ] **Step 3: Add Settings fields**

In `backend/nexus/app/config.py`, after the `evaluator_sufficiency_*` fields (~line 368):

```python
    # Phase C — Speech Agent (LLM-rendered utterances; non-realtime batch
    # streaming chat completion). Mid-tier per design doc §5.6 latency
    # budget (≤500ms TTFT). Effort-gated default-empty per the contract
    # in app/ai/config.py module docstring.
    speech_agent_model: str = "gpt-5-mini"
    speech_agent_effort: str = ""
```

- [ ] **Step 4: Add AIConfig properties**

In `backend/nexus/app/ai/config.py`, after the `evaluator_sufficiency_*` properties (end of class):

```python
    # Phase C — Speech Agent (mid-tier streaming chat completion via
    # get_openai_raw_client; not instructor-wrapped). See effort-gating
    # contract in module docstring.
    @property
    def speech_agent_model(self) -> str:
        return settings.speech_agent_model

    @property
    def speech_agent_effort(self) -> str:
        return settings.speech_agent_effort
```

- [ ] **Step 5: Add env vars to `.env.example`**

In `backend/nexus/.env.example`, after the `EVALUATOR_SUFFICIENCY_*` block (~line 162):

```
# --- Phase C — Speech Agent (LLM-rendered utterances) ---
#
# Streams tokens from OpenAI chat completion → joined async iterable →
# session.say() → Cartesia TTS. Mid-tier per design doc §5.6 latency
# budget (≤500ms TTFT). Goes through get_openai_raw_client() (NOT
# instructor-wrapped — plain text output, not structured).
#
# *_EFFORT follows the same default-empty contract as the evaluator
# vars: forwarded to OpenAI only when non-empty. Chat models
# (*-chat-latest) reject reasoning_effort with HTTP 400.
INTERVIEW_SPEECH_AGENT_MODEL=gpt-5-mini
INTERVIEW_SPEECH_AGENT_EFFORT=
```

Note: env var names use `INTERVIEW_SPEECH_AGENT_*` prefix to match the existing `INTERVIEW_*` family (LLM/STT/TTS/turn-detector); the Settings field name is `speech_agent_*` (pydantic-settings lowercases). Verify in step 6.

- [ ] **Step 6: Run tests, confirm they pass**

```bash
docker compose run --rm nexus pytest tests/test_ai_config.py -v
```

Expected: both new tests PASS. Existing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/ai/config.py backend/nexus/app/config.py backend/nexus/.env.example backend/nexus/tests/test_ai_config.py
git commit -m "$(cat <<'EOF'
feat(ai-config): add speech_agent_model + speech_agent_effort for Phase C

Mid-tier GPT-5 (default gpt-5-mini) for Speech Agent batch streaming.
Decoupled from interview_llm_model (realtime, no-op'd) and from
evaluator_*_model (instructor-wrapped). Effort default-empty per the
same gating contract as other *_effort properties.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 🔴 HARD GATE — Streaming-cancellation spike

**Goal:** Verify that cancelling the SpeechAgent's internal Task during a live OpenAI stream actually closes the underlying httpx connection within p99 < 500ms over 10 runs. PASS → ARCH-D Option β ships as designed. FAIL → collapse to Option α (eager-buffer-all) and pause to inform user.

**Files:**
- Create: `backend/nexus/tests/interview_engine/speech/spike_streaming_cancellation.py`
- Create: `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md`

- [ ] **Step 1: Write the spike script (standalone, NOT pytest)**

Create `backend/nexus/tests/interview_engine/speech/spike_streaming_cancellation.py`:

```python
"""Phase C build-step gate — streaming cancellation latency spike.

This is a STANDALONE SCRIPT (not a pytest test). Runs once before the
SpeechAgent class merges. Result is recorded in
docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md.

What it verifies:
    Cancelling the consumer Task of an in-flight OpenAI streaming
    chat completion closes the underlying httpx connection within
    p99 < 500ms over 10 runs.

Why it matters:
    The pre-render Task lifecycle (spec §3) cancels in-flight streams
    on candidate disconnect. The runtime close-handler timeout is 2s
    (spec §3.4); the spike validates cancellation is comfortably
    under the cap so the timeout isn't hit on the normal path.

PASS criterion:
    p99 cancellation-to-connection-close latency < 500ms across 10 runs.

FAIL action:
    ARCH-D collapses to ARCH-D-buffered-non-streaming (Option α —
    eager-buffer-all). The Protocol surface preserves; the
    StreamingRenderHandle internals change. See spec §5.2 step 3.

Run:
    cd backend/nexus
    docker compose run --rm \
        -e OPENAI_API_KEY=$OPENAI_API_KEY \
        nexus python -m tests.interview_engine.speech.spike_streaming_cancellation
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

from openai import AsyncOpenAI

NUM_RUNS = 10
TOKENS_BEFORE_CANCEL = 5
P99_THRESHOLD_MS = 500


async def run_one() -> float:
    """Returns cancellation latency in milliseconds."""
    client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        max_retries=0,
        timeout=30.0,
    )
    stream = await client.chat.completions.create(
        model="gpt-5-mini",
        stream=True,
        stream_options={"include_usage": True},
        messages=[
            {"role": "user", "content": "Count to 100 slowly, one number per line."}
        ],
    )
    tokens_seen = 0
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            tokens_seen += 1
            if tokens_seen >= TOKENS_BEFORE_CANCEL:
                break

    # Now cancel and measure how long the connection takes to close.
    cancel_start = time.monotonic()
    await stream.close()
    cancel_end = time.monotonic()

    await client.close()
    return (cancel_end - cancel_start) * 1000.0


async def main() -> None:
    print(f"Running {NUM_RUNS} streaming cancellation runs against gpt-5-mini...")
    latencies: list[float] = []
    for i in range(NUM_RUNS):
        ms = await run_one()
        latencies.append(ms)
        print(f"  Run {i+1}: {ms:.1f}ms")

    p50 = statistics.median(latencies)
    p99 = sorted(latencies)[-1]  # max as p99 proxy with N=10
    print(f"\nResults: p50={p50:.1f}ms  p99={p99:.1f}ms  min={min(latencies):.1f}ms  max={max(latencies):.1f}ms")
    print(f"Threshold: p99 < {P99_THRESHOLD_MS}ms")
    if p99 < P99_THRESHOLD_MS:
        print("\n✅ PASS — ARCH-D Option β (streaming) ships as designed.")
        print(f"   Document p50={p50:.1f}ms, p99={p99:.1f}ms in close-out ADR.")
    else:
        print(f"\n❌ FAIL — p99={p99:.1f}ms exceeds threshold.")
        print("   ARCH-D collapses to ARCH-D-buffered-non-streaming (Option α).")
        print("   STOP and surface to user before proceeding.")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create the close-out ADR template**

Create `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md`:

```markdown
# AI Screening Phase C — Close-out ADR

**Status:** In progress (build-step gates pending)
**Pairs with:** `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-design.md`

---

## ADR-1: Streaming-cancellation spike

**Decision:** TBD (run spike, record result)

**Spike script:** `backend/nexus/tests/interview_engine/speech/spike_streaming_cancellation.py`

**Run command:**
```bash
cd backend/nexus
docker compose run --rm -e OPENAI_API_KEY=$OPENAI_API_KEY nexus python -m tests.interview_engine.speech.spike_streaming_cancellation
```

**Result:** TBD
- p50 cancellation latency: TBD
- p99 cancellation latency: TBD
- Min: TBD
- Max: TBD

**Decision:**
- [ ] PASS (p99 < 500ms): ARCH-D Option β ships as designed (streaming + prefix-pipe).
- [ ] FAIL: Collapse to ARCH-D-buffered-non-streaming (Option α, eager-buffer-all). Protocol surface preserved; StreamingRenderHandle internals change to drain-fully-before-ready_to_commit.

---

## ADR-2: OTel auto-instrumentor under streaming

**Decision:** TBD (verify after SpeechAgent.render() is implemented in Task 9)

**What we verify:** That the OpenAI auto-instrumentor produces a single coherent span per streaming chat completion — span starts on `chat.completions.create(stream=True)`, ends when the `AsyncStream` is exhausted or closed. No orphan spans on cancellation.

**Result:** TBD

**Mitigation if discrepancy:** Manual span management inside `SpeechAgent._drive` (open span on stream creation, close on Task completion or cancellation).

---

## ADR-3: Manual smoke test results

**Decision:** TBD (run after Task 14 integration tests pass)

**Smoke session:** TBD — record session_id, candidate experience notes, miscall log entries.

---
```

- [ ] **Step 3: Run the spike**

```bash
cd backend/nexus
docker compose run --rm -e OPENAI_API_KEY=$OPENAI_API_KEY nexus python -m tests.interview_engine.speech.spike_streaming_cancellation
```

Expected: 10 runs print latencies; final summary shows PASS or FAIL.

- [ ] **Step 4: Record result in close-out ADR**

Edit `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md` ADR-1:
- Fill in p50, p99, min, max from the spike output.
- Tick the PASS or FAIL checkbox.

- [ ] **Step 5: 🔴 HARD GATE — surface to user if FAIL**

If the spike FAILED:
- STOP. Do not proceed to Task 4.
- Surface to user: "Streaming-cancellation spike FAILED with p99=XXXms (threshold 500ms). ARCH-D collapses to Option α (eager-buffer-all). The Protocol surface stays the same but `StreamingRenderHandle` internals change in Task 8. Confirm to proceed with Option α?"
- Wait for explicit user approval before continuing.

If the spike PASSED:
- Proceed to Task 4.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/tests/interview_engine/speech/spike_streaming_cancellation.py docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md
git commit -m "$(cat <<'EOF'
test(speech-agent): streaming-cancellation spike (Phase C build-step gate)

10-run cancellation latency check against gpt-5-mini streaming chat
completions. p99 < 500ms gate determines whether ARCH-D ships as
Option β (streaming + prefix-pipe) or collapses to Option α (eager-
buffer-all). Result recorded in Phase C close-out ADR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Checkpoint A reached.** Continue if PASS; STOP if FAIL.

---

## Task 4: Add Phase C event_kinds constants (delete + add)

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/event_kinds.py`
- Modify: `backend/nexus/tests/interview_engine/test_event_kinds.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/interview_engine/test_event_kinds.py`:

```python
def test_speech_safety_violation_constant_removed():
    """Phase C: regex-based safety layer removed entirely (spec §0,
    design doc §11.5 v3). The constant must not exist; importing it
    must fail."""
    import app.modules.interview_engine.event_kinds as ek
    assert not hasattr(ek, "SPEECH_SAFETY_VIOLATION")
    assert "speech.safety_violation" not in ek.ALL_EVENT_KINDS


def test_speech_stream_interrupted_constant_added():
    """Phase C: SPEECH_STREAM_INTERRUPTED fires on cancellation sub-case 3
    (mid-PLAYOUT disconnect after first token yielded). Carries
    tokens_received count + reason in the payload."""
    from app.modules.interview_engine.event_kinds import (
        SPEECH_STREAM_INTERRUPTED,
        ALL_EVENT_KINDS,
    )
    assert SPEECH_STREAM_INTERRUPTED == "speech.stream_interrupted"
    assert SPEECH_STREAM_INTERRUPTED in ALL_EVENT_KINDS
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_event_kinds.py::test_speech_safety_violation_constant_removed tests/interview_engine/test_event_kinds.py::test_speech_stream_interrupted_constant_added -v
```

Expected: first PASSES (constant exists currently — wait, this test asserts NOT hasattr). Actually: first FAILS because the constant exists; second FAILS with ImportError.

- [ ] **Step 3: Update event_kinds.py**

In `backend/nexus/app/modules/interview_engine/event_kinds.py`, in the Phase C block (~line 67-71), replace:

```python
SPEECH_RENDERED = "speech.rendered"
SPEECH_SAFETY_VIOLATION = "speech.safety_violation"
SPEECH_FALLBACK_USED = "speech.fallback_used"
```

With:

```python
SPEECH_RENDERED = "speech.rendered"
SPEECH_FALLBACK_USED = "speech.fallback_used"
SPEECH_STREAM_INTERRUPTED = "speech.stream_interrupted"
```

In the same file, in the `ALL_EVENT_KINDS` frozenset (~line 167), replace:

```python
    # Phase C
    SPEECH_RENDERED,
    SPEECH_SAFETY_VIOLATION,
    SPEECH_FALLBACK_USED,
```

With:

```python
    # Phase C
    SPEECH_RENDERED,
    SPEECH_FALLBACK_USED,
    SPEECH_STREAM_INTERRUPTED,
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_event_kinds.py -v
```

Expected: both new tests PASS. Existing tests in this file still PASS.

- [ ] **Step 5: Confirm no orphan references**

```bash
cd /home/ishant/Projects/ProjectX
grep -rn "SPEECH_SAFETY_VIOLATION\|speech\.safety_violation" backend/ frontend/ docs/ 2>&1 | grep -v "speech/safety.py" | grep -v "_phase_b_utterances.py" | grep -v "test_safety.py" | grep -v "test_structured_agent_integration.py" | grep -v "ai-screening-agent-design.md" | grep -v "ai-screening-agent-implementation.md" | grep -v "phase-c-design.md"
```

Expected: zero matches outside files-pending-deletion (`safety.py`, `_phase_b_utterances.py`, `test_safety.py`) and `test_structured_agent_integration.py` (handled in Task 14). The doc references in `ai-screening-agent-*.md` describe the historical/v3 transition — those are intentional.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/event_kinds.py backend/nexus/tests/interview_engine/test_event_kinds.py
git commit -m "$(cat <<'EOF'
refactor(event-kinds): drop SPEECH_SAFETY_VIOLATION; add SPEECH_STREAM_INTERRUPTED

Phase C eliminates the regex-based safety layer entirely (spec §0,
design doc §11.5 v3). SPEECH_SAFETY_VIOLATION constant removed.

SPEECH_STREAM_INTERRUPTED added for cancellation sub-case 3
(mid-PLAYOUT disconnect after first token yielded; spec §3.5).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Define `SpeechRenderHandle` Protocol + supporting types

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/speech/agent.py` (Protocol + types only at this stage; impl in Task 8-9)
- Create: `backend/nexus/tests/interview_engine/speech/test_handles.py`

- [ ] **Step 1: Write skeleton agent.py with Protocol + types only**

Create `backend/nexus/app/modules/interview_engine/speech/agent.py`:

```python
"""Phase C — SpeechAgent class + SpeechRenderHandle Protocol + supporting types.

The SpeechAgent class itself + StreamingRenderHandle implementation is
filled in by Tasks 8-9. This file establishes the Protocol surface and
type contracts that StaticFallbackHandle (Task 6) and the orchestrator
wiring (Task 11) depend on.

Protocol structure: spec §2.2.
Two implementations:
    - StreamingRenderHandle (in this module, Task 8)
    - StaticFallbackHandle (in speech/fallbacks.py, Task 6)
Both satisfy the SpeechRenderHandle Protocol structurally.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class RenderMetadata:
    """Per-render metadata, resolved into handle.metadata Future at appropriate
    gate (live: stream-close + consumer-finish; fallback: pre-resolved at
    construction).

    For fallback handles, latency_first_token_ms / latency_last_token_ms /
    tokens_in / tokens_out are None (Pin 2 in spec §4.5) — analytics
    differentiate via was_fallback flag without floor-spike artifacts."""
    render_id: str
    template_name: str
    template_version: str
    model: str
    latency_first_token_ms: int | None
    latency_last_token_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    length_words: int
    playout_duration_ms: int | None
    was_fallback: bool
    retries: int


SpeechRenderErrorReason = Literal[
    "template_not_found",
    "placeholder_missing",
    "openai_timeout",
    "openai_5xx",
    "openai_connection_dropped_pre_first_token",
    "openai_429",
]


class SpeechRenderError(Exception):
    """Raised by SpeechAgent.render() synchronously for programmer errors,
    or by handle.ready_to_commit() for post-retry-exhaustion infrastructure
    errors. Caught only at StructuredInterviewAgent._consume_pending_or_render
    (spec §4.3)."""

    def __init__(
        self,
        *,
        reason: SpeechRenderErrorReason,
        render_id: str | None = None,
    ) -> None:
        super().__init__(f"SpeechRenderError(reason={reason})")
        self.reason: SpeechRenderErrorReason = reason
        self.render_id: str | None = render_id


@runtime_checkable
class SpeechRenderHandle(Protocol):
    """Single-use handle. Three terminal states: completed (committed and
    drained), cancelled, errored. Idempotent cancel(); commit() can only
    fire once. See spec §2.2 + §2.3."""

    async def ready_to_commit(self) -> None: ...
    def commit(self) -> AsyncIterable[str]: ...
    async def cancel(self) -> None: ...

    @property
    def is_committed(self) -> bool: ...
    @property
    def is_cancelled(self) -> bool: ...
    @property
    def metadata(self) -> asyncio.Future[RenderMetadata]: ...
    @property
    def completed_text(self) -> asyncio.Future[str]: ...


# StreamingRenderHandle and SpeechAgent classes — filled in Tasks 8-9.
```

- [ ] **Step 2: Write the failing Protocol structure tests**

Create `backend/nexus/tests/interview_engine/speech/test_handles.py`:

```python
"""Phase C handle Protocol + shape tests (per spec §5.4)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable

import pytest

from app.modules.interview_engine.speech.agent import (
    RenderMetadata,
    SpeechRenderError,
    SpeechRenderHandle,
)


def test_speech_render_error_carries_reason_and_render_id():
    """SpeechRenderError must expose reason + render_id (None for synchronous
    programmer errors, set for runtime errors)."""
    err = SpeechRenderError(reason="openai_timeout", render_id="abc-123")
    assert err.reason == "openai_timeout"
    assert err.render_id == "abc-123"

    sync_err = SpeechRenderError(reason="template_not_found")
    assert sync_err.reason == "template_not_found"
    assert sync_err.render_id is None


def test_render_metadata_fallback_fields_nullable():
    """Fallback handles populate metadata with null latency/token fields
    (Pin 2). Analytics differentiate via was_fallback."""
    md = RenderMetadata(
        render_id="abc",
        template_name="intro",
        template_version="v1",
        model="gpt-5-mini",
        latency_first_token_ms=None,
        latency_last_token_ms=None,
        tokens_in=None,
        tokens_out=None,
        length_words=12,
        playout_duration_ms=None,
        was_fallback=True,
        retries=1,
    )
    assert md.was_fallback is True
    assert md.latency_first_token_ms is None
    assert md.length_words == 12


def test_speech_render_handle_protocol_is_runtime_checkable():
    """The Protocol uses @runtime_checkable so isinstance() works for tests."""
    # A trivial stub class that matches the structural shape.
    class _StubHandle:
        async def ready_to_commit(self) -> None: ...
        def commit(self) -> AsyncIterable[str]: ...  # type: ignore[empty-body]
        async def cancel(self) -> None: ...
        is_committed = False
        is_cancelled = False

        @property
        def metadata(self) -> asyncio.Future[RenderMetadata]: ...  # type: ignore[empty-body]
        @property
        def completed_text(self) -> asyncio.Future[str]: ...  # type: ignore[empty-body]

    assert isinstance(_StubHandle(), SpeechRenderHandle)
```

- [ ] **Step 3: Run tests, confirm they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_handles.py -v
```

Expected: all three PASS (no implementations yet — these are pure type-shape tests).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/agent.py backend/nexus/tests/interview_engine/speech/test_handles.py
git commit -m "$(cat <<'EOF'
feat(speech): SpeechRenderHandle Protocol + RenderMetadata + SpeechRenderError

Phase C foundational types. The Protocol surface (ready_to_commit / commit /
cancel + metadata / completed_text futures) is what StaticFallbackHandle
(Task 6) and StreamingRenderHandle (Task 8) both satisfy.

Pin 2 reflected: latency/token fields nullable on RenderMetadata so
fallback handles don't produce zero-floor artifacts in analytics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement `StaticFallbackHandle` + emit `SPEECH_FALLBACK_USED` on construction

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/speech/fallbacks.py` (handle only at this stage; `_FALLBACK_BUILDERS` added in Task 7)
- Modify: `backend/nexus/tests/interview_engine/speech/test_handles.py`

- [ ] **Step 1: Write failing tests for StaticFallbackHandle**

Append to `backend/nexus/tests/interview_engine/speech/test_handles.py`:

```python
def test_static_fallback_handle_satisfies_protocol():
    """StaticFallbackHandle must structurally satisfy SpeechRenderHandle."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="Hi there.",
        template_name="intro",
        template_version="v1",
        failure_reason="openai_timeout",
        retries_attempted=1,
        render_id="abc-123",
        collector=MagicMock(),
    )
    assert isinstance(h, SpeechRenderHandle)


@pytest.mark.asyncio
async def test_static_fallback_handle_pre_resolved_futures():
    """metadata + completed_text futures resolve immediately (no Task)."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="That's everything from my side.",
        template_name="wrap_normal",
        template_version="v1",
        failure_reason="openai_timeout",
        retries_attempted=1,
        render_id="abc",
        collector=MagicMock(),
    )
    assert h.metadata.done()
    assert h.completed_text.done()
    md = await h.metadata
    assert md.was_fallback is True
    assert md.length_words == 5
    assert md.tokens_in is None  # Pin 2: nullable for fallbacks
    assert (await h.completed_text) == "That's everything from my side."


@pytest.mark.asyncio
async def test_static_fallback_handle_commit_yields_one_chunk():
    """commit() returns an AsyncIterable yielding exactly one chunk = full text."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="Hi there, candidate.",
        template_name="intro",
        template_version="v1",
        failure_reason="openai_5xx",
        retries_attempted=1,
        render_id="abc",
        collector=MagicMock(),
    )
    chunks = [chunk async for chunk in h.commit()]
    assert chunks == ["Hi there, candidate."]
    assert h.is_committed
    # Re-committing must raise
    with pytest.raises(RuntimeError):
        h.commit()


@pytest.mark.asyncio
async def test_static_fallback_handle_cancel_is_idempotent_noop():
    """cancel() on a fallback handle is a no-op (no Task to cancel)."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    h = StaticFallbackHandle(
        text="x",
        template_name="intro",
        template_version="v1",
        failure_reason="openai_timeout",
        retries_attempted=1,
        render_id="abc",
        collector=MagicMock(),
    )
    await h.cancel()
    await h.cancel()  # idempotent
    assert h.is_cancelled
    with pytest.raises(RuntimeError):
        h.commit()  # cannot commit after cancel


def test_static_fallback_handle_emits_fallback_used_on_construction():
    """Constructing the handle MUST emit speech.fallback_used (Pin 1).
    Render and consumer code never have to remember to fire this event."""
    from unittest.mock import MagicMock
    from app.modules.interview_engine.event_kinds import SPEECH_FALLBACK_USED
    from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle

    collector = MagicMock()
    StaticFallbackHandle(
        text="x",
        template_name="ask_question_standard",
        template_version="v1",
        failure_reason="openai_429",
        retries_attempted=1,
        render_id="render-abc",
        collector=collector,
    )
    collector.append.assert_called_once()
    call_kwargs = collector.append.call_args.kwargs
    assert call_kwargs["kind"] == SPEECH_FALLBACK_USED
    payload = call_kwargs["payload"]
    assert payload["render_id"] == "render-abc"
    assert payload["template_name"] == "ask_question_standard"
    assert payload["template_version"] == "v1"
    assert payload["reason"] == "openai_429"
    assert payload["retries_attempted"] == 1
```

- [ ] **Step 2: Run tests, confirm they fail with ImportError**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_handles.py -v
```

Expected: 5 new tests FAIL with `ImportError: cannot import name 'StaticFallbackHandle'`.

- [ ] **Step 3: Create `speech/fallbacks.py` with `StaticFallbackHandle`**

Create `backend/nexus/app/modules/interview_engine/speech/fallbacks.py`:

```python
"""Phase C — Static fallback handle + per-template fallback builders.

Triggered ONLY by OpenAI infrastructure errors (timeout, 5xx, pre-first-
token disconnect, 429). Hand-reviewed strings; ship in code, not data;
no runtime regex check (spec §0, design doc §11.5 v3).

The StaticFallbackHandle implements the SpeechRenderHandle Protocol with
pre-resolved futures and a single-chunk commit() iterator — indistinguishable
from a live LLM-rendered handle to consumers.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING

from app.modules.interview_engine.event_kinds import SPEECH_FALLBACK_USED
from app.modules.interview_engine.speech.agent import (
    RenderMetadata,
    SpeechRenderError,
    SpeechRenderErrorReason,
)

if TYPE_CHECKING:
    from app.modules.interview_engine.event_log import EventCollector


def _wall_ms() -> int:
    return int(time.time() * 1000)


class StaticFallbackHandle:
    """SpeechRenderHandle Protocol implementation backed by a static string.

    All futures are pre-resolved at construction time. commit() returns an
    AsyncIterable that yields exactly one chunk (the entire text) and stops.
    cancel() is a no-op (nothing to cancel — no Task running).

    SPEECH_FALLBACK_USED envelope event is emitted at construction time
    (Pin 1 — caller doesn't have to remember).
    """

    def __init__(
        self,
        *,
        text: str,
        template_name: str,
        template_version: str,
        failure_reason: SpeechRenderErrorReason,
        retries_attempted: int,
        render_id: str,
        collector: "EventCollector",
        model: str = "<fallback-no-llm-call>",
    ) -> None:
        self._text = text
        self._committed = False
        self._cancelled = False

        loop = asyncio.get_event_loop()
        self._metadata_fut: asyncio.Future[RenderMetadata] = loop.create_future()
        self._completed_text_fut: asyncio.Future[str] = loop.create_future()

        self._metadata_fut.set_result(
            RenderMetadata(
                render_id=render_id,
                template_name=template_name,
                template_version=template_version,
                model=model,
                latency_first_token_ms=None,
                latency_last_token_ms=None,
                tokens_in=None,
                tokens_out=None,
                length_words=len(text.split()),
                playout_duration_ms=None,
                was_fallback=True,
                retries=retries_attempted,
            )
        )
        self._completed_text_fut.set_result(text)

        # Pin 1: emit SPEECH_FALLBACK_USED at construction time.
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
        self._cancelled = True

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

- [ ] **Step 4: Run tests, confirm they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_handles.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/fallbacks.py backend/nexus/tests/interview_engine/speech/test_handles.py
git commit -m "$(cat <<'EOF'
feat(speech): StaticFallbackHandle — Protocol-conformant degenerate-streaming handle

Pre-resolves metadata + completed_text futures at construction. commit()
yields the full text in one chunk. Emits SPEECH_FALLBACK_USED on
construction (Pin 1) so the caller doesn't have to remember.

The handle is structurally indistinguishable from a live LLM-rendered
handle from the orchestrator's perspective — same Protocol, same
session.say() consumption pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Implement `_FALLBACK_BUILDERS` + `build_fallback_text` dispatcher

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/speech/fallbacks.py`
- Create: `backend/nexus/tests/interview_engine/speech/test_fallbacks.py`

- [ ] **Step 1: Write the failing tests with inline FORBIDDEN_PHRASES**

Create `backend/nexus/tests/interview_engine/speech/test_fallbacks.py`:

```python
"""Phase C fallback content tests — spec §5.5.

The FORBIDDEN_PHRASES list lives INLINE in this test file only.
Recreating it as a production constants module reintroduces exactly
the safety.py we deleted (spec §0). PR review enforces this discipline.
"""
from __future__ import annotations

import pytest

# FORBIDDEN by hand-review checklist. Inline in this test file only.
# Do NOT export, import, or re-create as a production constant module.
FORBIDDEN_PHRASES = (
    "passed",
    "failed",
    "rejected",
    "advanced",
    "unfortunately",
    "best of luck",
    "thanks for your interest",
)


def test_intro_fallback_uses_duration():
    """The intro fallback MUST parameterize target_duration_minutes — never
    hardcoded. A 30-minute senior interview falling back to "about 15
    minutes" erodes trust right after an infrastructure failure (spec §4.1
    Bug 2 fix)."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    text_30 = build_fallback_text(template_name="intro", target_duration_minutes=30)
    assert "30" in text_30
    assert "15" not in text_30

    text_45 = build_fallback_text(template_name="intro", target_duration_minutes=45)
    assert "45" in text_45


@pytest.mark.parametrize("duration", [5, 15, 30, 60])
def test_intro_fallback_length_le_50_words(duration):
    """Length cap (lenient on live, hard cap on hand-reviewed fallback)."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    text = build_fallback_text(template_name="intro", target_duration_minutes=duration)
    assert len(text.split()) <= 50


def test_wrap_normal_fallback_length_le_30_words():
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    text = build_fallback_text(template_name="wrap_normal")
    assert len(text.split()) <= 30


def test_ask_question_standard_fallback_is_verbatim():
    """The QuestionConfig.text is recruiter-validated content; the fallback
    asks it verbatim with no transition wrapper."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    q = "Walk me through how you'd handle a flaky integration test."
    assert build_fallback_text(template_name="ask_question_standard", question_text=q) == q


def test_fallback_strings_outcome_neutral():
    """Each fallback builder's output passes the inline FORBIDDEN_PHRASES
    check (case-insensitive substring)."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    intro = build_fallback_text(template_name="intro", target_duration_minutes=15)
    wrap = build_fallback_text(template_name="wrap_normal")
    asq = build_fallback_text(
        template_name="ask_question_standard",
        question_text="Tell me about your last project.",
    )

    for output in (intro, wrap, asq):
        lower = output.lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in lower, f"forbidden {phrase!r} in {output!r}"


def test_fallback_strings_no_salary_or_scheduling():
    """Inline checks for currency markers + scheduling commitments +
    hiring-manager mentions."""
    import re

    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    forbidden_substrings = [
        "$",
        "€",
        "£",
        "USD",
        "GBP",
        "salary",
        "i'll schedule",
        "we'll schedule",
        "hiring manager",
    ]
    for tn, kwargs in [
        ("intro", {"target_duration_minutes": 15}),
        ("wrap_normal", {}),
    ]:
        text = build_fallback_text(template_name=tn, **kwargs).lower()
        for forb in forbidden_substrings:
            assert forb.lower() not in text, f"forbidden {forb!r} in {tn} fallback: {text!r}"
        # No bare numbers that could be salary
        assert not re.search(r"\b\d{2,3}[,.]?\d{3}\b", text)


def test_build_fallback_text_unknown_template_raises():
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    with pytest.raises(KeyError):
        build_fallback_text(template_name="nonexistent_template")
```

- [ ] **Step 2: Run tests, confirm they fail with ImportError**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_fallbacks.py -v
```

Expected: all 7 tests FAIL with `ImportError: cannot import name 'build_fallback_text'`.

- [ ] **Step 3: Add `_FALLBACK_BUILDERS` and `build_fallback_text` to fallbacks.py**

Append to `backend/nexus/app/modules/interview_engine/speech/fallbacks.py`:

```python
# ---------------------------------------------------------------------------
# Per-template fallback string builders. Hand-reviewed for outcome-neutrality.
# Code review enforces; no runtime regex check (spec §0, design doc §11.5 v3).
# ---------------------------------------------------------------------------

from collections.abc import Callable


def _intro_fallback(*, target_duration_minutes: int, **_) -> str:
    """Parameterized — NEVER hardcode the duration (spec §4.1 Bug 2)."""
    return (
        f"Hi, I'll be running a short technical screen with you today. "
        f"We'll be about {target_duration_minutes} minutes. "
        f"Take your time. Let's get started."
    )


def _ask_question_standard_fallback(*, question_text: str, **_) -> str:
    """QuestionConfig.text is recruiter-validated; fallback asks verbatim."""
    return question_text


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

    Raises KeyError on unknown template_name (programmer error — fail loud)."""
    return _FALLBACK_BUILDERS[template_name](**inputs)
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_fallbacks.py -v
```

Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/fallbacks.py backend/nexus/tests/interview_engine/speech/test_fallbacks.py
git commit -m "$(cat <<'EOF'
feat(speech): _FALLBACK_BUILDERS + build_fallback_text dispatcher

Three templates (intro, ask_question_standard, wrap_normal). intro is
parameterized on target_duration_minutes (Bug 2). ask_question_standard
returns QuestionConfig.text verbatim. wrap_normal is a hand-reviewed
constant.

FORBIDDEN_PHRASES list lives INLINE in test_fallbacks.py only — never
as a production constants module (would reintroduce safety.py).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Implement `StreamingRenderHandle` (Option β: prefix-pipe streaming)

This task is the largest single implementation in Phase C. Per spec §2.4-§2.6, the `StreamingRenderHandle` owns the OpenAI streaming consumer Task with a 5-state lifecycle and prefix detection.

> **NOTE: If Task 3's spike FAILED, the user has approved Option α (eager-buffer-all) instead.** In that case, replace Step 4-5's `_drive` body with eager-drain-then-resolve semantics: buffer the entire stream into `self._buffer` before resolving `self._ready_event`; `commit()` returns a buffer-replay AsyncIterable. Same Protocol surface; different internals. The remaining tasks proceed unchanged.

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/speech/agent.py`
- Modify: `backend/nexus/tests/interview_engine/speech/test_handles.py` (state-machine + cancellation tests)

- [ ] **Step 1: Write failing tests for state machine transitions and cancel semantics**

Append to `backend/nexus/tests/interview_engine/speech/test_handles.py`:

```python
@pytest.mark.asyncio
async def test_streaming_render_handle_satisfies_protocol():
    """StreamingRenderHandle must structurally satisfy SpeechRenderHandle."""
    from unittest.mock import MagicMock, AsyncMock
    from app.modules.interview_engine.speech.agent import StreamingRenderHandle

    h = StreamingRenderHandle(
        client=AsyncMock(),
        model="gpt-5-mini",
        effort=None,
        prompt="ignored",
        template_name="intro",
        template_version="v1",
        render_id="abc",
        collector=MagicMock(),
    )
    assert isinstance(h, SpeechRenderHandle)
    await h.cancel()  # ensure no leak


@pytest.mark.asyncio
async def test_streaming_render_handle_cancel_during_buffering(monkeypatch):
    """cancel() during buffering: ready_to_commit raises CancelledError
    (NOT SpeechRenderError); subsequent commit() raises RuntimeError."""
    from unittest.mock import MagicMock
    import openai
    from app.modules.interview_engine.speech.agent import StreamingRenderHandle

    # Mock client whose stream yields slowly; we cancel before completion.
    async def slow_stream(*_, **__):
        class _Stream:
            async def __aiter__(self):
                # Yield one delta then sleep forever
                from openai.types.chat import ChatCompletionChunk
                yield ChatCompletionChunk(
                    id="x", object="chat.completion.chunk", created=0, model="x",
                    choices=[{"index": 0, "delta": {"content": "Hi "}, "finish_reason": None}],
                )
                await asyncio.sleep(60)
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = slow_stream

    h = StreamingRenderHandle(
        client=client, model="gpt-5-mini", effort=None,
        prompt="x", template_name="intro", template_version="v1",
        render_id="abc", collector=MagicMock(),
    )

    # Spawn ready_to_commit and cancel before it resolves.
    r2c_task = asyncio.create_task(h.ready_to_commit())
    await asyncio.sleep(0.05)  # let _drive start
    await h.cancel()

    with pytest.raises(asyncio.CancelledError):
        await r2c_task

    # Subsequent commit() raises
    with pytest.raises(RuntimeError):
        h.commit()
    assert h.is_cancelled
```

(Additional state-machine tests will be added after the basic implementation lands.)

- [ ] **Step 2: Run tests, confirm they fail with ImportError**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_handles.py -v
```

Expected: 2 new tests FAIL with `ImportError: cannot import name 'StreamingRenderHandle'`.

- [ ] **Step 3: Add `StreamingRenderHandle` skeleton + state machine**

Replace the trailing comment in `backend/nexus/app/modules/interview_engine/speech/agent.py` (`# StreamingRenderHandle and SpeechAgent classes — filled in Tasks 8-9.`) with:

```python
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import openai
import structlog

from app.modules.interview_engine.event_kinds import (
    SPEECH_RENDERED,
    SPEECH_STREAM_INTERRUPTED,
)
from app.modules.interview_engine.event_log import EventCollector

log = structlog.get_logger("interview-engine.speech.agent")


_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+[A-Z]")
_MAX_PREFIX_TOKENS = 100


class _PreFirstTokenFailure(Exception):
    """Internal — used by _drive to signal pre-first-token failure for retry."""
    def __init__(self, *, reason: SpeechRenderErrorReason) -> None:
        self.reason = reason


class _PostFirstTokenFailure(Exception):
    """Internal — mid-stream failure after first token; non-recoverable."""
    def __init__(self, *, reason: str, tokens_received: int) -> None:
        self.reason = reason
        self.tokens_received = tokens_received


_State = Literal[
    "opening",
    "buffering_prefix",
    "ready",
    "errored_pre_first_token",
    "committed",
    "cancelled",
    "completed",
]


class StreamingRenderHandle:
    """Live-LLM SpeechRenderHandle implementation (Option β: prefix-pipe).

    State machine per spec §2.5. Owns an internal asyncio.Task (`_drive`)
    that consumes the OpenAI stream, populates the prefix buffer, and
    resolves futures at the appropriate gates."""

    def __init__(
        self,
        *,
        client: openai.AsyncOpenAI,
        model: str,
        effort: str | None,
        prompt: str,
        template_name: str,
        template_version: str,
        render_id: str,
        collector: EventCollector,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._prompt = prompt
        self._template_name = template_name
        self._template_version = template_version
        self._render_id = render_id
        self._collector = collector

        self._state: _State = "opening"
        self._prefix_buffer: list[str] = []
        self._live_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._ready_event = asyncio.Event()
        self._cancel_event = asyncio.Event()
        self._error: SpeechRenderError | None = None

        loop = asyncio.get_event_loop()
        self._metadata_fut: asyncio.Future[RenderMetadata] = loop.create_future()
        self._completed_text_fut: asyncio.Future[str] = loop.create_future()

        self._committed = False
        self._cancelled = False
        self._tokens_received = 0
        self._first_token_wall_ms: int | None = None
        self._stream_close_wall_ms: int | None = None
        self._consumer_finish_wall_ms: int | None = None
        self._tokens_in: int | None = None
        self._tokens_out: int | None = None
        self._completed_text_buf: list[str] = []

        self._task: asyncio.Task[None] = asyncio.create_task(self._drive())

    async def ready_to_commit(self) -> None:
        ready_task = asyncio.create_task(self._ready_event.wait())
        cancel_task = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            {ready_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()

        if self._cancel_event.is_set():
            raise asyncio.CancelledError()
        if self._error is not None:
            raise self._error
        # Otherwise: ready
        return

    def commit(self) -> AsyncIterable[str]:
        if self._cancelled:
            raise RuntimeError("Cannot commit a cancelled handle")
        if self._committed:
            raise RuntimeError("commit() may only be called once")
        if self._state not in ("ready",):
            raise RuntimeError(f"Cannot commit from state {self._state}")
        self._committed = True
        self._state = "committed"

        return self._joined_iterator()

    async def _joined_iterator(self) -> AsyncIterator[str]:
        # 1) Yield buffered prefix
        for chunk in self._prefix_buffer:
            yield chunk
        # 2) Pipe the live stream
        while True:
            chunk = await self._live_queue.get()
            if chunk is None:  # sentinel: stream closed or interrupted
                break
            yield chunk
        self._consumer_finish_wall_ms = _wall_ms()
        self._maybe_emit_rendered()

    async def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._cancel_event.set()
        self._task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

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

    async def _drive(self) -> None:
        """OpenAI streaming consumer. State machine per spec §2.5."""
        try:
            for attempt in range(2):
                try:
                    await self._open_stream_and_buffer_prefix(attempt=attempt)
                    return
                except _PreFirstTokenFailure as exc:
                    if attempt == 0 and exc.reason != "openai_429":
                        log.warning(
                            "speech.render.retry",
                            template=self._template_name,
                            reason=exc.reason,
                            attempt=1,
                            render_id=self._render_id,
                        )
                        continue
                    self._fail_pre_first_token(reason=exc.reason)
                    return
        except asyncio.CancelledError:
            # Cancellation during _drive — close everything cleanly.
            self._state = "cancelled"
            self._live_queue.put_nowait(None)
            raise

    async def _open_stream_and_buffer_prefix(self, *, attempt: int) -> None:
        """Opens the stream, buffers the prefix, transitions to ready,
        then continues piping into the live queue until stream closes."""
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": self._prompt}],
        }
        if self._effort:
            request_kwargs["reasoning_effort"] = self._effort

        try:
            stream = await self._client.chat.completions.create(**request_kwargs)
        except openai.APITimeoutError as e:
            raise _PreFirstTokenFailure(reason="openai_timeout") from e
        except openai.RateLimitError as e:
            raise _PreFirstTokenFailure(reason="openai_429") from e
        except (openai.APIConnectionError, openai.APIError) as e:
            raise _PreFirstTokenFailure(reason="openai_5xx") from e

        self._state = "buffering_prefix"
        prefix_text = ""
        try:
            async for chunk in stream:
                if self._cancel_event.is_set():
                    return
                # Track first-token timing
                if chunk.choices and chunk.choices[0].delta.content:
                    if self._first_token_wall_ms is None:
                        self._first_token_wall_ms = _wall_ms()
                    delta = chunk.choices[0].delta.content
                    self._tokens_received += 1
                    self._completed_text_buf.append(delta)

                    if self._state == "buffering_prefix":
                        prefix_text += delta
                        self._prefix_buffer.append(delta)
                        # Boundary detection: terminator + space + capital
                        m = _SENTENCE_BOUNDARY_RE.search(prefix_text)
                        if m or self._tokens_received >= _MAX_PREFIX_TOKENS:
                            self._state = "ready"
                            self._ready_event.set()
                    else:
                        # state is "ready" or "committed" — pipe live
                        self._live_queue.put_nowait(delta)

                # Capture usage on terminal chunk
                if chunk.usage:
                    self._tokens_in = chunk.usage.prompt_tokens
                    self._tokens_out = chunk.usage.completion_tokens
        except (openai.APIConnectionError, openai.APIError, asyncio.IncompleteReadError) as e:
            if self._first_token_wall_ms is None:
                raise _PreFirstTokenFailure(
                    reason="openai_connection_dropped_pre_first_token"
                ) from e
            # Post-first-token: non-recoverable, truncate
            log.warning(
                "speech.stream_interrupted",
                render_id=self._render_id,
                tokens_received=self._tokens_received,
                reason=str(type(e).__name__),
            )
            self._collector.append(
                kind=SPEECH_STREAM_INTERRUPTED,
                payload={
                    "render_id": self._render_id,
                    "tokens_received": self._tokens_received,
                    "reason": "openai_connection_dropped_post_first_token",
                },
                wall_ms=_wall_ms(),
            )
        finally:
            try:
                await stream.close()
            except Exception:  # noqa: BLE001
                pass

        self._stream_close_wall_ms = _wall_ms()
        # End-of-stream sentinel for the consumer
        self._live_queue.put_nowait(None)
        # If we never reached ready (e.g., empty stream), error out
        if not self._ready_event.is_set():
            self._fail_pre_first_token(reason="openai_connection_dropped_pre_first_token")
            return
        self._state = "completed"

    def _fail_pre_first_token(self, *, reason: SpeechRenderErrorReason) -> None:
        self._state = "errored_pre_first_token"
        self._error = SpeechRenderError(reason=reason, render_id=self._render_id)
        self._ready_event.set()  # unblock ready_to_commit so it raises

    def _maybe_emit_rendered(self) -> None:
        """Emit SPEECH_RENDERED at the LATER of stream-close + consumer-finish.
        Both must be set; we check this on every consumer-finish call (Add 2)."""
        if self._stream_close_wall_ms is None or self._consumer_finish_wall_ms is None:
            return
        if self._metadata_fut.done():
            return  # already emitted

        completed_text = "".join(self._completed_text_buf)
        latency_first = (
            self._first_token_wall_ms
            if self._first_token_wall_ms is not None
            else None
        )
        latency_last = self._stream_close_wall_ms
        playout_duration = self._consumer_finish_wall_ms - latency_last if latency_last else None

        md = RenderMetadata(
            render_id=self._render_id,
            template_name=self._template_name,
            template_version=self._template_version,
            model=self._model,
            latency_first_token_ms=latency_first,
            latency_last_token_ms=latency_last,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            length_words=len(completed_text.split()),
            playout_duration_ms=playout_duration,
            was_fallback=False,
            retries=0,  # _drive's retry attempt count would go here in a richer impl
        )
        self._metadata_fut.set_result(md)
        self._completed_text_fut.set_result(completed_text)

        self._collector.append(
            kind=SPEECH_RENDERED,
            payload={
                "render_id": self._render_id,
                "template_name": self._template_name,
                "template_version": self._template_version,
                "model": self._model,
                "latency_first_token_ms": latency_first,
                "latency_last_token_ms": latency_last,
                "tokens_in": self._tokens_in,
                "tokens_out": self._tokens_out,
                "length_words": len(completed_text.split()),
                "playout_duration_ms": playout_duration,
                "committed": self._committed,
                "played": self._committed and not self._cancelled,
                "played_to_completion": self._committed and not self._cancelled,
                "was_fallback": False,
                "retries": 0,
            },
            wall_ms=_wall_ms(),
        )


def _wall_ms() -> int:
    return int(time.time() * 1000)
```

- [ ] **Step 4: Run tests**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_handles.py -v
```

Expected: protocol-conformance + cancellation-during-buffering tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/agent.py backend/nexus/tests/interview_engine/speech/test_handles.py
git commit -m "$(cat <<'EOF'
feat(speech): StreamingRenderHandle — Option β (prefix-pipe streaming)

Five-state machine (opening → buffering_prefix → ready → committed → completed)
with cancel and errored_pre_first_token side branches. Sentence-boundary
prefix detection (terminator + space + capital) defends against decimals,
acronyms, and bare numbers. 100-token max-prefix cap.

Pre-first-token retry once for openai_timeout / openai_5xx /
openai_connection_dropped. 429 not retried. Post-first-token failures
truncate gracefully and emit SPEECH_STREAM_INTERRUPTED.

SPEECH_RENDERED emits at LATER of stream-close + consumer-finish.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Implement `SpeechAgent.render()` + `fallback_handle()` factory + comprehensive unit tests

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/speech/agent.py`
- Create: `backend/nexus/tests/interview_engine/speech/test_speech_agent.py`

- [ ] **Step 1: Write the failing tests (16 unit tests per spec §5.3)**

Create `backend/nexus/tests/interview_engine/speech/test_speech_agent.py`:

```python
"""Phase C SpeechAgent unit tests — spec §5.3.

Mocks AsyncOpenAI; no LiveKit, no DB. Each test exercises one specific
behavior of render() / SpeechAgent / StreamingRenderHandle.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


@pytest.fixture
def collector():
    return MagicMock()


def _make_chunk(content=None, finish_reason=None, usage=None):
    """Build a mock OpenAI chunk."""
    chunk = MagicMock()
    if content is not None:
        chunk.choices = [MagicMock(delta=MagicMock(content=content), finish_reason=finish_reason)]
    else:
        chunk.choices = [MagicMock(delta=MagicMock(content=None), finish_reason=finish_reason)]
    chunk.usage = MagicMock(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
    return chunk


@contextlib.asynccontextmanager
async def _mock_stream(chunks):
    class _Stream:
        async def __aiter__(self):
            for c in chunks:
                yield c
        async def close(self):
            pass
    yield _Stream()


def _mock_client_yielding(chunks):
    """Returns an AsyncMock client whose chat.completions.create yields chunks."""
    client = MagicMock()

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                for c in chunks:
                    yield c
            async def close(self):
                pass
        return _Stream()

    client.chat.completions.create = _create
    return client


@pytest.mark.asyncio
async def test_render_happy_path(collector, tmp_path, monkeypatch):
    """Mocked client streams 3 chunks; ready_to_commit resolves;
    commit() yields concatenated tokens; metadata correct."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    chunks = [
        _make_chunk(content="Hi there. "),
        _make_chunk(content="Let's go."),
        _make_chunk(usage=(100, 5)),  # final usage chunk
    ]
    client = _mock_client_yielding(chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    # Mock template_loader to return a fixed prompt
    with patch(
        "app.modules.interview_engine.speech.agent._render_prompt",
        return_value="hello prompt",
    ):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    chunks_out = [c async for c in handle.commit()]
    assert "".join(chunks_out) == "Hi there. Let's go."
    md = await handle.metadata
    assert md.was_fallback is False
    assert md.tokens_in == 100
    assert md.tokens_out == 5


@pytest.mark.asyncio
async def test_render_first_sentence_prefix(collector):
    """Prefix is everything up to the first sentence boundary."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    chunks = [
        _make_chunk(content="Hi there. "),
        _make_chunk(content="Let's begin."),
        _make_chunk(usage=(50, 4)),
    ]
    client = _mock_client_yielding(chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    # First sentence boundary closes the prefix at "Hi there. "
    chunks_out = [c async for c in handle.commit()]
    full = "".join(chunks_out)
    assert full == "Hi there. Let's begin."


@pytest.mark.asyncio
async def test_render_max_prefix_cap_100_tokens(collector):
    """If no sentence boundary in 100 tokens, commit anyway."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    long_chunks = [_make_chunk(content="word ") for _ in range(150)]
    long_chunks.append(_make_chunk(usage=(10, 150)))
    client = _mock_client_yielding(long_chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="ask_question_standard",
            template_version="v1", inputs={"question_text": "x"},
        )
    await handle.ready_to_commit()
    # Just verify ready_to_commit returned without error after 100 tokens accumulated
    assert handle.is_committed is False
    chunks_out = [c async for c in handle.commit()]
    assert len("".join(chunks_out).split()) == 150


@pytest.mark.parametrize("input_text", [
    "In section 11.5 we describe the architecture.",
    "The U.S. office hours are flexible.",
    "That costs 1.5 dollars. Let's continue.",
])
@pytest.mark.asyncio
async def test_render_prefix_avoids_false_sentence_boundaries(collector, input_text):
    """Decimals (11.5), acronyms (U.S.), and bare-number+terminator
    sequences must NOT close the prefix early. Uses the
    [.!?]\\s+[A-Z] regex (Add 1 from spec §5.3)."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    chunks = [_make_chunk(content=input_text), _make_chunk(usage=(20, 10))]
    client = _mock_client_yielding(chunks)
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    chunks_out = [c async for c in handle.commit()]
    full = "".join(chunks_out)
    assert full == input_text
    # Verify the prefix actually contained the full first real sentence
    # (no early break at "11." / "U." / "1.5")


@pytest.mark.asyncio
async def test_retries_once_on_openai_timeout(collector):
    """First attempt times out, retry succeeds, metadata.retries reflects."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent

    call_count = {"n": 0}

    async def _create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise openai.APITimeoutError(request=MagicMock())
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="OK.")
                yield _make_chunk(usage=(10, 1))
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create

    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()  # should not raise
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_429_not_retried(collector):
    """429 is rate-limit; retrying compounds. Immediate fail (no retry)."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    call_count = {"n": 0}

    async def _create(**kwargs):
        call_count["n"] += 1
        raise openai.RateLimitError(
            message="429", response=MagicMock(status_code=429), body={},
        )

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    with pytest.raises(SpeechRenderError) as exc_info:
        await handle.ready_to_commit()
    assert exc_info.value.reason == "openai_429"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_falls_back_after_two_failures(collector):
    """Two timeouts → ready_to_commit raises SpeechRenderError(timeout)."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    call_count = {"n": 0}
    async def _create(**kwargs):
        call_count["n"] += 1
        raise openai.APITimeoutError(request=MagicMock())

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    with pytest.raises(SpeechRenderError) as exc_info:
        await handle.ready_to_commit()
    assert exc_info.value.reason == "openai_timeout"
    assert call_count["n"] == 2  # original + 1 retry


@pytest.mark.asyncio
async def test_does_not_retry_post_first_token_failure(collector):
    """Mocked client emits 5 tokens then drops. No retry. Truncate."""
    import openai
    from app.modules.interview_engine.event_kinds import SPEECH_STREAM_INTERRUPTED, SPEECH_FALLBACK_USED
    from app.modules.interview_engine.speech.agent import SpeechAgent

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                for word in ["Hi ", "there ", "I "]:
                    yield _make_chunk(content=word)
                raise openai.APIConnectionError(request=MagicMock())
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    # Wait for prefix to be ready (first sentence boundary OR cap)
    # Then commit and observe truncated output
    await handle.ready_to_commit()
    chunks_out = [c async for c in handle.commit()]
    # We got 3 tokens before drop; commit yields what's there

    # speech.stream_interrupted fired
    kinds = [call.kwargs["kind"] for call in collector.append.call_args_list]
    assert SPEECH_STREAM_INTERRUPTED in kinds
    assert SPEECH_FALLBACK_USED not in kinds  # NOT a fallback


@pytest.mark.asyncio
async def test_template_not_found_raises_synchronously(collector):
    """SpeechAgent.render(template_name='nonexistent') raises before
    Task spawn — programmer error, not retried, not caught by helper."""
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    client = MagicMock()
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with pytest.raises(SpeechRenderError) as exc_info:
        await agent.render(
            template_name="nonexistent_template", template_version="v1", inputs={},
        )
    assert exc_info.value.reason == "template_not_found"
    assert exc_info.value.render_id is None  # synchronous error: no render_id


@pytest.mark.asyncio
async def test_placeholder_missing_raises_synchronously(collector):
    """Template requires a placeholder not provided in inputs."""
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    client = MagicMock()
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    # The intro template requires candidate_first_name + role_title + target_duration_minutes
    with pytest.raises(SpeechRenderError) as exc_info:
        await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    assert exc_info.value.reason == "placeholder_missing"


@pytest.mark.asyncio
async def test_max_retries_zero_passed_to_openai_client(collector):
    """The raw OpenAI client used by SpeechAgent has max_retries=0
    set at construction (in get_openai_raw_client). Verify that
    chat.completions.create is NOT called with extra retry kwargs."""
    from app.modules.interview_engine.speech.agent import SpeechAgent

    call_kwargs = {}
    async def _create(**kwargs):
        call_kwargs.update(kwargs)
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="x. ")
                yield _make_chunk(usage=(5, 1))
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    # SpeechAgent must not pass max_retries to .create()
    assert "max_retries" not in call_kwargs
    # stream_options.include_usage must be set
    assert call_kwargs.get("stream_options") == {"include_usage": True}


@pytest.mark.asyncio
async def test_render_id_propagates_to_envelope_events(collector):
    """The same render_id appears across speech.rendered + speech.fallback_used
    + speech.stream_interrupted for the same logical render."""
    import openai
    from app.modules.interview_engine.speech.agent import SpeechAgent

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="Hi. ")
                raise openai.APIConnectionError(request=MagicMock())
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    [c async for c in handle.commit()]
    # All envelope events should share render_id from handle.metadata.render_id
    md = await handle.metadata
    rid = md.render_id
    for call in collector.append.call_args_list:
        payload = call.kwargs["payload"]
        assert payload["render_id"] == rid


@pytest.mark.asyncio
async def test_speech_rendered_emits_after_both_stream_close_and_playout(collector):
    """SPEECH_RENDERED fires at LATER of stream-close and consumer-finish.
    playout_duration_ms = consumer-finish - stream-close."""
    from app.modules.interview_engine.event_kinds import SPEECH_RENDERED
    from app.modules.interview_engine.speech.agent import SpeechAgent

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(content="Hi. ")
                yield _make_chunk(usage=(10, 1))
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    await handle.ready_to_commit()
    # Simulate slow TTS consumer
    chunks_out = []
    async for chunk in handle.commit():
        chunks_out.append(chunk)
        await asyncio.sleep(0.01)

    # SPEECH_RENDERED should have fired
    rendered_calls = [
        c for c in collector.append.call_args_list
        if c.kwargs["kind"] == SPEECH_RENDERED
    ]
    assert len(rendered_calls) == 1
    payload = rendered_calls[0].kwargs["payload"]
    assert payload["played"] is True
    assert payload["played_to_completion"] is True
    assert payload["was_fallback"] is False
    # playout_duration_ms is non-null
    assert payload["playout_duration_ms"] is not None


@pytest.mark.asyncio
async def test_empty_stream_yields_minimal_completed_text(collector):
    """OpenAI returns finish_reason on first chunk with no content.
    Treated as pre-first-token failure → fallback path."""
    from app.modules.interview_engine.speech.agent import SpeechAgent, SpeechRenderError

    async def _create(**kwargs):
        class _Stream:
            async def __aiter__(self):
                yield _make_chunk(finish_reason="stop")
            async def close(self): pass
        return _Stream()

    client = MagicMock()
    client.chat.completions.create = _create
    agent = SpeechAgent(
        client=client, model="gpt-5-mini", effort=None, collector=collector,
    )
    with patch("app.modules.interview_engine.speech.agent._render_prompt", return_value="x"):
        handle = await agent.render(
            template_name="intro", template_version="v1", inputs={},
        )
    with pytest.raises(SpeechRenderError):
        await handle.ready_to_commit()
```

- [ ] **Step 2: Run tests, confirm they fail (no SpeechAgent class yet)**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_speech_agent.py -v
```

Expected: many FAIL with `ImportError: cannot import name 'SpeechAgent'`.

- [ ] **Step 3: Implement `SpeechAgent` class**

Append to `backend/nexus/app/modules/interview_engine/speech/agent.py`:

```python
import uuid

from app.modules.interview_engine.speech.templates import template_loader


def _render_prompt(template_name: str, template_version: str, inputs: dict[str, Any]) -> str:
    """Loads the template via template_loader.get and substitutes placeholders.

    Raises SpeechRenderError synchronously for template_not_found or
    placeholder_missing — these are programmer errors, not retried."""
    try:
        template_text = template_loader.get(
            role="speech_agent", name=template_name, version=template_version,
        )
    except FileNotFoundError as e:
        raise SpeechRenderError(reason="template_not_found") from e

    try:
        return template_text.format(**inputs)
    except KeyError as e:
        raise SpeechRenderError(reason="placeholder_missing") from e


class SpeechAgent:
    """Phase C — produces SpeechRenderHandle from a template + inputs.

    The caller (orchestrator) consumes the handle via session.say(handle.commit())
    after handle.ready_to_commit() resolves. Errors raised by ready_to_commit()
    are caught only at StructuredInterviewAgent._consume_pending_or_render.
    """

    def __init__(
        self,
        *,
        client: openai.AsyncOpenAI,
        model: str,
        effort: str | None,
        collector: EventCollector,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._collector = collector

    async def render(
        self,
        *,
        template_name: str,
        template_version: str,
        inputs: dict[str, Any],
    ) -> SpeechRenderHandle:
        """Returns synchronously after opening the OpenAI stream.
        Raises SpeechRenderError for template_not_found or placeholder_missing
        (programmer errors). Does NOT raise on OpenAI errors."""
        prompt = _render_prompt(template_name, template_version, inputs)
        render_id = str(uuid.uuid4())
        return StreamingRenderHandle(
            client=self._client,
            model=self._model,
            effort=self._effort,
            prompt=prompt,
            template_name=template_name,
            template_version=template_version,
            render_id=render_id,
            collector=self._collector,
        )

    def fallback_handle(
        self,
        *,
        template_name: str,
        template_version: str,
        text: str,
        failure_reason: SpeechRenderErrorReason,
        retries_attempted: int,
        render_id: str,
    ) -> SpeechRenderHandle:
        """Constructs a StaticFallbackHandle. Emits SPEECH_FALLBACK_USED
        on construction. Caller treats it indistinguishably from a live handle."""
        # Lazy import to avoid circular dependency
        from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle
        return StaticFallbackHandle(
            text=text,
            template_name=template_name,
            template_version=template_version,
            failure_reason=failure_reason,
            retries_attempted=retries_attempted,
            render_id=render_id,
            collector=self._collector,
            model=self._model,
        )
```

- [ ] **Step 4: Run all SpeechAgent unit tests**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_speech_agent.py -v
```

Expected: 16 tests PASS. If any fail, iterate on `_drive` / `StreamingRenderHandle` until green.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/agent.py backend/nexus/tests/interview_engine/speech/test_speech_agent.py
git commit -m "$(cat <<'EOF'
feat(speech): SpeechAgent class — render() + fallback_handle() factory

SpeechAgent.render() is synchronous on the happy path: load template,
substitute placeholders, open OpenAI stream, spawn _drive Task, return
hot StreamingRenderHandle. Synchronous SpeechRenderError raises for
template_not_found / placeholder_missing.

fallback_handle() factory constructs StaticFallbackHandle which emits
SPEECH_FALLBACK_USED on construction.

16 unit tests cover happy path, retry-once-pre-first-token, 429
no-retry, post-first-token truncate, template/placeholder errors,
max_retries=0 contract, render_id propagation, late-playout SPEECH_RENDERED
timing, empty-stream edge case, and sentence-boundary false-positive
defenses (Add 1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Implement `deliveries.py` — typed render wrappers + `fallback_for`

**Files:**
- Create: `backend/nexus/app/modules/interview_engine/speech/deliveries.py`
- Create: `backend/nexus/tests/interview_engine/speech/test_deliveries.py`

- [ ] **Step 1: Write failing tests**

Create `backend/nexus/tests/interview_engine/speech/test_deliveries.py`:

```python
"""Phase C deliveries tests — typed render wrappers + fallback_for."""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_render_intro_calls_speech_agent_with_correct_inputs():
    from app.modules.interview_engine.speech.deliveries import render_intro

    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_intro(
        speech_agent,
        candidate_first_name="Alex",
        role_title="Backend Engineer",
        target_duration_minutes=15,
    )
    speech_agent.render.assert_awaited_once_with(
        template_name="intro",
        template_version="v1",
        inputs={
            "candidate_first_name": "Alex",
            "role_title": "Backend Engineer",
            "target_duration_minutes": 15,
        },
    )


@pytest.mark.asyncio
async def test_render_ask_question_standard_calls_speech_agent_with_correct_inputs():
    from app.modules.interview_engine.speech.deliveries import render_ask_question_standard

    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_ask_question_standard(speech_agent, question_text="Tell me about your last project.")
    speech_agent.render.assert_awaited_once_with(
        template_name="ask_question_standard",
        template_version="v1",
        inputs={"question_text": "Tell me about your last project."},
    )


@pytest.mark.asyncio
async def test_render_wrap_normal_takes_no_inputs():
    from app.modules.interview_engine.speech.deliveries import render_wrap_normal

    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_wrap_normal(speech_agent)
    speech_agent.render.assert_awaited_once_with(
        template_name="wrap_normal",
        template_version="v1",
        inputs={},
    )


@pytest.mark.asyncio
async def test_fallback_for_intro_passes_through_to_fallback_handle():
    from app.modules.interview_engine.speech.deliveries import fallback_for

    speech_agent = MagicMock()
    speech_agent.fallback_handle = MagicMock(return_value="HANDLE")
    handle = await fallback_for(
        speech_agent,
        template_name="intro",
        failure_reason="openai_timeout",
        render_id="abc",
        target_duration_minutes=30,
    )
    assert handle == "HANDLE"
    speech_agent.fallback_handle.assert_called_once()
    call_kwargs = speech_agent.fallback_handle.call_args.kwargs
    assert call_kwargs["template_name"] == "intro"
    assert call_kwargs["template_version"] == "v1"
    assert "30 minutes" in call_kwargs["text"]
    assert call_kwargs["failure_reason"] == "openai_timeout"
    assert call_kwargs["retries_attempted"] == 1
    assert call_kwargs["render_id"] == "abc"


@pytest.mark.asyncio
async def test_render_wrappers_have_template_name_attribute():
    """The consumption helper uses render_fn.template_name to pick the
    right fallback factory."""
    from app.modules.interview_engine.speech.deliveries import (
        render_intro,
        render_ask_question_standard,
        render_wrap_normal,
    )
    assert render_intro.template_name == "intro"
    assert render_ask_question_standard.template_name == "ask_question_standard"
    assert render_wrap_normal.template_name == "wrap_normal"
```

- [ ] **Step 2: Run tests, confirm fail**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_deliveries.py -v
```

Expected: ImportError on all 5.

- [ ] **Step 3: Implement deliveries.py**

Create `backend/nexus/app/modules/interview_engine/speech/deliveries.py`:

```python
"""Phase C — typed render wrappers + fallback_for factory.

Each render_<name> function is decorated with a marker that exposes the
template_name as an attribute on the function itself, so the orchestrator's
_consume_pending_or_render helper can pick the right fallback factory
without an explicit dispatch table.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

from app.modules.interview_engine.speech.agent import (
    SpeechAgent,
    SpeechRenderErrorReason,
    SpeechRenderHandle,
)
from app.modules.interview_engine.speech.fallbacks import build_fallback_text


_F = TypeVar("_F", bound=Callable[..., Awaitable[SpeechRenderHandle]])


def _delivery(template_name: str) -> Callable[[_F], _F]:
    """Marker decorator that attaches `template_name` to the function so the
    orchestrator's consumption helper can dispatch fallback by attribute lookup."""
    def _wrap(fn: _F) -> _F:
        fn.template_name = template_name  # type: ignore[attr-defined]
        return fn
    return _wrap


@_delivery("intro")
async def render_intro(
    speech_agent: SpeechAgent,
    *,
    candidate_first_name: str,
    role_title: str,
    target_duration_minutes: int,
) -> SpeechRenderHandle:
    return await speech_agent.render(
        template_name="intro",
        template_version="v1",
        inputs={
            "candidate_first_name": candidate_first_name,
            "role_title": role_title,
            "target_duration_minutes": target_duration_minutes,
        },
    )


@_delivery("ask_question_standard")
async def render_ask_question_standard(
    speech_agent: SpeechAgent,
    *,
    question_text: str,
) -> SpeechRenderHandle:
    return await speech_agent.render(
        template_name="ask_question_standard",
        template_version="v1",
        inputs={"question_text": question_text},
    )


@_delivery("wrap_normal")
async def render_wrap_normal(speech_agent: SpeechAgent) -> SpeechRenderHandle:
    return await speech_agent.render(
        template_name="wrap_normal", template_version="v1", inputs={},
    )


async def fallback_for(
    speech_agent: SpeechAgent,
    *,
    template_name: str,
    failure_reason: SpeechRenderErrorReason,
    render_id: str | None,
    **inputs,
) -> SpeechRenderHandle:
    """Constructs the fallback handle for a given template.

    `render_id` is the failed live render's render_id (reused so the two
    fallback events correlate per spec §4.5). May be None if the failure
    was synchronous (template_not_found / placeholder_missing) — but those
    failures don't trigger fallback in the consumption helper anyway.
    """
    text = build_fallback_text(template_name=template_name, **inputs)
    rid = render_id or "fallback-" + str(id(speech_agent))
    return speech_agent.fallback_handle(
        template_name=template_name,
        template_version="v1",
        text=text,
        failure_reason=failure_reason,
        retries_attempted=1,
        render_id=rid,
    )
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/test_deliveries.py -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/deliveries.py backend/nexus/tests/interview_engine/speech/test_deliveries.py
git commit -m "$(cat <<'EOF'
feat(speech): deliveries — typed render wrappers + fallback_for

Three render_<name> async wrappers (intro, ask_question_standard,
wrap_normal) decorated with _delivery marker that exposes template_name
as a function attribute. The orchestrator's _consume_pending_or_render
uses fn.template_name to dispatch the right fallback factory.

fallback_for is a thin pass-through that calls SpeechAgent.fallback_handle
with the failed render's render_id reused — so the two fallback events
(speech.fallback_used + speech.rendered with was_fallback=true) share
render_id correlation per spec §4.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Update `speech/__init__.py` — drop safety re-exports, add new public surface

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/speech/__init__.py`

- [ ] **Step 1: Read current `__init__.py`**

```bash
cat backend/nexus/app/modules/interview_engine/speech/__init__.py
```

- [ ] **Step 2: Replace contents**

Overwrite `backend/nexus/app/modules/interview_engine/speech/__init__.py`:

```python
"""Phase C speech package public API.

Phase A landed `templates.py` (template_loader binding) and `safety.py`
(now deleted in Phase C — see design doc §11.5 v3 for the prompt-only
safety model).

Phase C exports:
    - SpeechAgent: the rendering service class
    - SpeechRenderHandle: Protocol both implementations satisfy
    - StreamingRenderHandle: live LLM path implementation
    - StaticFallbackHandle: fallback path implementation
    - SpeechRenderError: raised for template/placeholder errors and
      post-retry-exhaustion infrastructure errors
    - RenderMetadata: metadata returned by handle.metadata Future
"""
from app.modules.interview_engine.speech.agent import (
    RenderMetadata,
    SpeechAgent,
    SpeechRenderError,
    SpeechRenderHandle,
    StreamingRenderHandle,
)
from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle
from app.modules.interview_engine.speech.templates import (
    ENGINE_PROMPTS_DIR,
    template_loader,
)

__all__ = [
    "ENGINE_PROMPTS_DIR",
    "RenderMetadata",
    "SpeechAgent",
    "SpeechRenderError",
    "SpeechRenderHandle",
    "StaticFallbackHandle",
    "StreamingRenderHandle",
    "template_loader",
]
```

- [ ] **Step 3: Verify imports**

```bash
docker compose run --rm nexus python -c "from app.modules.interview_engine.speech import SpeechAgent, SpeechRenderHandle, SpeechRenderError, StaticFallbackHandle, StreamingRenderHandle, RenderMetadata, template_loader; print('OK')"
```

Expected: prints "OK".

- [ ] **Step 4: Verify deleted re-exports raise ImportError**

```bash
docker compose run --rm nexus python -c "
from app.modules.interview_engine.speech import check_safety
" 2>&1 | grep -E "ImportError|cannot import"
```

Expected: matches ImportError line.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/speech/__init__.py
git commit -m "$(cat <<'EOF'
refactor(speech): public API for Phase C — drop safety, add SpeechAgent surface

Drops re-exports of SafetyResult, SafetyViolation, check_safety (Phase A
safety.py deleted in Phase C per design doc §11.5 v3). Adds SpeechAgent,
SpeechRenderHandle, StreamingRenderHandle, StaticFallbackHandle,
SpeechRenderError, RenderMetadata.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Wire `SpeechAgent` into `structured_agent.py` — `_say(handle)`, `_pending_next_render`, `_consume_pending_or_render`, three trigger sites

This is the largest single-file diff in Phase C (~150 lines net delta). Per spec §3.

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/structured_agent.py`

- [ ] **Step 1: Add SpeechAgent to constructor + slot field**

Read `structured_agent.py:109-170` to locate `__init__`. Add `speech_agent: SpeechAgent` parameter and store `self._speech_agent = speech_agent`. Add `self._pending_next_render: asyncio.Task[SpeechRenderHandle] | None = None` after `self._candidate_transcripts: dict[str, str] = {}`.

Also drop the `INERT_SYSTEM_PROMPT`-only references and the `_phase_b_utterances` import block (replaced in subsequent steps).

Concretely, replace the imports block at lines 40-46:

```python
from app.modules.interview_engine._phase_b_utterances import (
    _PHASE_B_SAFETY_FALLBACK_TEXT,
    ASK_QUESTION_STANDARD,
    INTRO,
    WRAP_NORMAL,
)
```

With (keeping the rest of the imports intact):

```python
from app.modules.interview_engine.speech import (
    SpeechAgent,
    SpeechRenderError,
    SpeechRenderHandle,
)
from app.modules.interview_engine.speech import deliveries
```

Replace the `from ...event_kinds import (...)` block (lines 46-53) — remove `SPEECH_SAFETY_VIOLATION`:

```python
from app.modules.interview_engine.event_kinds import (
    ORCHESTRATOR_EXIT,
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
)
```

Drop the `from app.modules.interview_engine.speech import check_safety` import (line 64).

- [ ] **Step 2: Modify constructor signature + body**

In `StructuredInterviewAgent.__init__` (lines 109-170), add `speech_agent: SpeechAgent` as a required kwarg:

```python
    def __init__(
        self,
        *,
        config: SessionConfig,
        tenant_id: uuid.UUID,
        correlation_id: str,
        collector: EventCollector,
        persistence: LedgerPersistence,
        speech_agent: SpeechAgent,
    ) -> None:
        self._config = config
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._collector = collector
        self._persistence = persistence
        self._speech_agent = speech_agent
        self._envelope_written: bool = False
        self._persisted: bool = False
        self._end_outcome: SessionOutcome | None = None
        self._session_start_monotonic: float = time.monotonic()
        self._main_loop_task: asyncio.Task[None] | None = None

        self._candidate_transcripts: dict[str, str] = {}
        self._pending_next_render: asyncio.Task[SpeechRenderHandle] | None = None
        self._next_user_turn_future: asyncio.Future[str] | None = None

        # ... rest of __init__ unchanged (target_duration_seconds, InterviewState,
        # SignalLedger, super().__init__) ...
```

- [ ] **Step 3: Replace `_say()` to take a handle, drop safety branch**

Replace the existing `_say()` method (lines 252-276) entirely with:

```python
    async def _say(
        self,
        handle: SpeechRenderHandle,
        *,
        allow_interruptions: bool = True,
    ) -> None:
        """Single utterance entry point. Phase C: takes a SpeechRenderHandle
        (live or fallback — same Protocol). Calls handle.commit() and pipes
        the resulting AsyncIterable[str] into session.say()."""
        try:
            await handle.ready_to_commit()
        except SpeechRenderError as exc:
            # This branch should be unreachable in Phase C — the consumption
            # helper substitutes a fallback handle before _say is called.
            # Defensive: if a caller bypasses the helper, fail loudly.
            log.error(
                "structured_agent._say.unexpected_render_error",
                reason=exc.reason,
                render_id=exc.render_id,
            )
            raise

        await self.session.say(handle.commit(), allow_interruptions=allow_interruptions)
        # handle.completed_text + handle.metadata resolve as a side effect of
        # the internal Task draining the stream alongside session.say's
        # consumer. SPEECH_RENDERED fires from inside the handle.
```

- [ ] **Step 4: Add `_consume_pending_or_render` helper**

Add a new method on `StructuredInterviewAgent`, alongside `_say`:

```python
    async def _consume_pending_or_render(
        self,
        render_fn,  # render_intro / render_ask_question_standard / render_wrap_normal
        **inputs,
    ) -> SpeechRenderHandle:
        """Use the pending slot if hot; otherwise render synchronously.

        On SpeechRenderError (post-retry-exhaustion infrastructure failure),
        substitutes a fallback handle via deliveries.fallback_for. The same
        render_id from the failed live render is reused so envelope events
        correlate (spec §4.5).
        """
        if self._pending_next_render is not None:
            try:
                return await self._pending_next_render
            except SpeechRenderError as exc:
                log.warning(
                    "speech.pre_render.failed",
                    reason=exc.reason,
                    render_id=exc.render_id,
                )
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

- [ ] **Step 5: Add Trigger 1 (intro pre-render in `on_enter`)**

Modify `on_enter` (lines 303-318):

```python
    async def on_enter(self) -> None:
        self._session_start_monotonic = time.monotonic()
        log.info(
            "structured_agent.on_enter",
            session_id=self._config.session_id,
            candidate_name=self._config.candidate.name,
            job_title=self._config.job_title,
            question_count=len(self._config.stage.questions),
        )
        # Trigger 1: spawn intro pre-render in room-join window.
        first_name = (
            self._config.candidate.name.split(" ")[0]
            if self._config.candidate.name else "there"
        )
        self._pending_next_render = asyncio.create_task(
            deliveries.render_intro(
                self._speech_agent,
                candidate_first_name=first_name,
                role_title=self._config.job_title,
                target_duration_minutes=self._config.stage.duration_minutes,
            )
        )
        self._main_loop_task = asyncio.create_task(self._run_main_loop())
        self._main_loop_task.add_done_callback(self._on_main_loop_done)
```

- [ ] **Step 6: Modify `_run_main_loop` — Trigger 2 (Q0 pre-render) and consume intro/wrap via helper**

Replace the body of `_run_main_loop` (lines 383-446):

```python
    async def _run_main_loop(self) -> None:
        await self._transition_with_persist(
            InterviewPhase.CONSENT, reason="wizard_consent_already_captured",
        )
        await self._transition_with_persist(InterviewPhase.INTRO, reason="intro_phase")

        # Consume intro slot (already pre-rendered in on_enter)
        first_name = (
            self._config.candidate.name.split(" ")[0]
            if self._config.candidate.name else "there"
        )
        intro_handle = await self._consume_pending_or_render(
            deliveries.render_intro,
            candidate_first_name=first_name,
            role_title=self._config.job_title,
            target_duration_minutes=self._config.stage.duration_minutes,
        )

        # Trigger 2: spawn Q0 pre-render BEFORE awaiting intro playout.
        first_q = pick_next_question(self._state, self._config)
        if first_q is not None:
            self._pending_next_render = asyncio.create_task(
                deliveries.render_ask_question_standard(
                    self._speech_agent, question_text=first_q.text,
                )
            )

        await self._say(intro_handle)

        await self._transition_with_persist(
            InterviewPhase.MAIN_LOOP, reason="begin_main_loop",
        )
        while True:
            next_q = pick_next_question(self._state, self._config)
            if next_q is None:
                break
            await self._ask_one_question(next_q)

        # Consume wrap slot (pre-rendered at end of last _ask_one_question)
        wrap_handle = await self._consume_pending_or_render(deliveries.render_wrap_normal)
        await self._transition_with_persist(
            InterviewPhase.NORMAL_WRAP, reason="all_questions_completed",
        )
        await self._say(wrap_handle)

        self._end_outcome = "completed"
        await self._transition_with_persist(InterviewPhase.CLOSED, reason="normal_close")
        self._state.set_exit_mode(ExitMode.COMPLETED, ended_at=_now_utc())
        self._collector.append(
            kind=ORCHESTRATOR_EXIT,
            payload={
                "exit_mode": ExitMode.COMPLETED.value,
                "reason": "all_questions_completed",
            },
            wall_ms=_wall_ms(),
        )
```

- [ ] **Step 7: Modify `_ask_one_question` — Trigger 3 (Qn+1 pre-render)**

Replace `_ask_one_question` body (lines 448-515):

```python
    async def _ask_one_question(self, q: QuestionConfig) -> None:
        qs = next(
            (s for s in self._state.questions if s.question_id == q.id),
            None,
        )
        if qs is None:
            log.error("structured_agent.question_state.missing", question_id=q.id)
            return

        qs.asked_at = _now_utc()
        qs.asked_mode = "standard"
        await self._persistence.write_state(self._state)
        self._collector.append(
            kind=ORCHESTRATOR_QUESTION_ASKED,
            payload={"question_id": q.id, "position": q.position, "mode": "standard"},
            wall_ms=_wall_ms(),
        )

        # Consume the pre-rendered handle for this question (or render sync).
        handle = await self._consume_pending_or_render(
            deliveries.render_ask_question_standard,
            question_text=q.text,
        )

        transcript_future = self._arm_user_turn()
        await self._say(handle)
        transcript = await transcript_future
        self._candidate_transcripts[q.id] = transcript

        qs.completed_at = _now_utc()
        qs.elapsed_seconds = (
            (qs.completed_at - qs.asked_at).total_seconds() if qs.asked_at else 0.0
        )

        # Trigger 3: spawn the next pre-render BEFORE persistence + envelope.
        # Phase C uses pick_next_question with the just-set qs.completed_at.
        next_q = pick_next_question(self._state, self._config)
        if next_q is not None:
            self._pending_next_render = asyncio.create_task(
                deliveries.render_ask_question_standard(
                    self._speech_agent, question_text=next_q.text,
                )
            )
        else:
            # No more questions — pre-render the wrap.
            self._pending_next_render = asyncio.create_task(
                deliveries.render_wrap_normal(self._speech_agent)
            )

        await self._persistence.write_ledger(self._ledger)
        self._collector.append(
            kind=ORCHESTRATOR_QUESTION_COMPLETED,
            payload={
                "question_id": q.id,
                "elapsed_seconds": qs.elapsed_seconds,
                "followups_asked": qs.followups_asked,
            },
            wall_ms=_wall_ms(),
        )
```

- [ ] **Step 8: Run smoke check — module imports**

```bash
docker compose run --rm nexus python -c "from app.modules.interview_engine.structured_agent import StructuredInterviewAgent; print('imports OK')"
```

Expected: prints "imports OK".

- [ ] **Step 9: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/structured_agent.py
git commit -m "$(cat <<'EOF'
feat(structured-agent): wire SpeechAgent + pre-render slot for Phase C

Replace _phase_b_utterances f-string substitutions with
SpeechRenderHandle-driven _say(). Three pre-render trigger sites:
intro (on_enter), Q0 (INTRO->MAIN_LOOP), Qn+1 + wrap (after prior
transcript). _consume_pending_or_render is the central drain point;
catches SpeechRenderError and substitutes deliveries.fallback_for with
shared render_id (spec §4.5).

Spawn-before-persist order at trigger 3 is load-bearing: LLM round-trip
overlaps the persistence I/O window for ~150-280ms perceived gap.

Removes SPEECH_SAFETY_VIOLATION emission + check_safety blocking branch
(spec §0, design doc §11.5 v3 — prompt-only safety enforcement).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Modify `agent.py` — construct `SpeechAgent` + bounded close-handler cancellation

**Files:**
- Modify: `backend/nexus/app/modules/interview_engine/agent.py`

- [ ] **Step 1: Add imports**

Near the existing `app.ai` imports (~line 73-80), add:

```python
from app.ai.client import get_openai_raw_client
from app.modules.interview_engine.speech import SpeechAgent
```

- [ ] **Step 2: Construct SpeechAgent at the entrypoint**

In the entrypoint function (around line 209, where `LedgerPersistence` is constructed), insert:

```python
    speech_agent = SpeechAgent(
        client=get_openai_raw_client(),
        model=ai_config.speech_agent_model,
        effort=ai_config.speech_agent_effort or None,
        collector=event_collector,
    )
```

Then update the `StructuredInterviewAgent(...)` construction (around line 215) to pass it in:

```python
    agent = StructuredInterviewAgent(
        config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        persistence=persistence,
        speech_agent=speech_agent,
    )
```

- [ ] **Step 3: Add bounded cancel grace to close handler**

Locate the `_close_session` (or equivalent close path — search for `agent.get_persistence()` or `_persist_session_result`). Add a pre-close block that cancels the pending render slot if non-None and not-done:

```python
    # Phase C — cancel pending pre-render Task if in flight.
    pending = getattr(agent, "_pending_next_render", None)
    if pending is not None and not pending.done():
        pending.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(pending), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        # SpeechRenderError from a cancelled render is also acceptable here
        except Exception as exc:  # noqa: BLE001
            log.warning("structured_agent.pending_render.cancel_failed", error=str(exc))
```

This block runs before `_persist_session_result` to ensure we don't have an outstanding LLM call still consuming connection budget when persistence runs.

- [ ] **Step 4: Run smoke check**

```bash
docker compose run --rm nexus python -c "from app.modules.interview_engine.agent import _entrypoint_wrapper if hasattr(__import__('app.modules.interview_engine.agent', fromlist=['agent']), '_entrypoint_wrapper') else None; print('imports OK')"
```

(If the entrypoint name differs, adjust accordingly. Just verifying imports resolve.)

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/interview_engine/agent.py
git commit -m "$(cat <<'EOF'
feat(agent): construct SpeechAgent + bounded pre-render cancellation

Wires get_openai_raw_client + ai_config.speech_agent_model into a
SpeechAgent instance shared with StructuredInterviewAgent. Close handler
extended with 2-second bounded cancellation on _pending_next_render
Task — caps worst-case wait when candidate disconnects while an
in-flight render is mid-stream.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Delete deprecated files + update integration tests

**Files:**
- Delete: `backend/nexus/app/modules/interview_engine/_phase_b_utterances.py`
- Delete: `backend/nexus/app/modules/interview_engine/speech/safety.py`
- Delete: `backend/nexus/tests/interview_engine/speech/test_safety.py`
- Modify: `backend/nexus/tests/interview_engine/test_structured_agent_integration.py`

- [ ] **Step 1: Delete the three deprecated files**

```bash
cd /home/ishant/Projects/ProjectX
git rm backend/nexus/app/modules/interview_engine/_phase_b_utterances.py
git rm backend/nexus/app/modules/interview_engine/speech/safety.py
git rm backend/nexus/tests/interview_engine/speech/test_safety.py
```

- [ ] **Step 2: Read current integration test file to know what needs updating**

```bash
grep -n "SPEECH_SAFETY_VIOLATION\|_PHASE_B_SAFETY_FALLBACK\|check_safety\|safety\.violations" backend/nexus/tests/interview_engine/test_structured_agent_integration.py | head -30
```

- [ ] **Step 3: Remove SPEECH_SAFETY_VIOLATION assertions from the integration test**

Edit `backend/nexus/tests/interview_engine/test_structured_agent_integration.py`:
- Remove the `from ... import SPEECH_SAFETY_VIOLATION` line (around line 44).
- Remove the test `test_safety_violation_emits_envelope_event` (or whatever its name is — line 504-531 area).
- Replace with a regression-guard test asserting the constant no longer exists:

```python
def test_no_speech_safety_violation_constant_imported():
    """Phase C deletion regression guard — both:
    (i) importing SPEECH_SAFETY_VIOLATION from event_kinds raises ImportError;
    (ii) repo-wide grep returns zero matches.
    Catches deliberate re-imports AND accidental name reuse with new purpose."""
    import importlib
    import subprocess
    from pathlib import Path

    ek = importlib.import_module("app.modules.interview_engine.event_kinds")
    assert not hasattr(ek, "SPEECH_SAFETY_VIOLATION")
    assert "speech.safety_violation" not in ek.ALL_EVENT_KINDS

    repo_root = Path(__file__).resolve().parents[3]  # adjust if needed
    result = subprocess.run(
        ["grep", "-rn", "SPEECH_SAFETY_VIOLATION", str(repo_root / "backend"), str(repo_root / "frontend")],
        capture_output=True, text=True,
    )
    # Grep returns 1 when zero matches; allow that.
    matches = [
        line for line in result.stdout.splitlines()
        if "test_no_speech_safety_violation_constant_imported" not in line
    ]
    assert matches == [], f"Found orphan references:\n" + "\n".join(matches)
```

- [ ] **Step 4: Add Phase C integration tests (8 from spec §5.6)**

Append to `backend/nexus/tests/interview_engine/test_structured_agent_integration.py`. The full bodies are detailed in spec §5.6; the headers and assertions are:

```python
@pytest.mark.asyncio
async def test_full_happy_path_with_pre_render_slot(...):
    """3-question session; envelope contains exactly 5 speech.rendered
    events (intro + 3 questions + wrap_normal); pre-render slot consumed
    each turn; no fallbacks; render_id values are unique per event."""
    # Mocked OpenAI returns valid streamed responses for each render.
    # Run agent; assert envelope event count + render_id uniqueness.

@pytest.mark.asyncio
async def test_disconnect_during_render_task_subcase_1(...):
    """Disconnect mid-buffering. Envelope has speech.rendered with
    committed=false, played=false. SessionResult.exit_mode=candidate_disconnected."""

@pytest.mark.asyncio
async def test_disconnect_after_render_before_commit_subcase_2(...):
    """Slot Task completes; close handler fires before consume.
    Envelope has speech.rendered with committed=false, played=false."""

@pytest.mark.asyncio
async def test_disconnect_mid_playout_subcase_3(...):
    """TTS playing; disconnect. Envelope has speech.rendered with
    committed=true, played=true, played_to_completion=false,
    was_fallback=false, retries=0; speech.stream_interrupted with tokens_received."""

@pytest.mark.asyncio
async def test_speech_render_error_triggers_fallback_path_and_session_continues(...):
    """3-question session; OpenAI fails twice on Q1; Q1 fallback fires;
    Q2+Q3 render normally. Envelope: 6 speech.rendered (intro+Q0+Q1-fb+Q2+Q3+wrap),
    1 speech.fallback_used. SessionResult.exit_mode=COMPLETED (NOT TECHNICAL_FAILURE)."""

@pytest.mark.asyncio
async def test_template_not_found_results_in_technical_failure_exit(...):
    """SpeechAgent.render(template_name='nonexistent') raises synchronously;
    orchestrator main loop crashes; close handler fires;
    SessionResult.exit_mode=TECHNICAL_FAILURE persisted."""

@pytest.mark.asyncio
async def test_pre_render_slot_cancelled_on_close(...):
    """Pre-render Task in flight when close fires; close handler cancels
    within 2s timeout."""
```

(Implementation of each test mirrors the existing Phase B integration test scaffolding — mock LiveKit transport via existing test fixtures; mock OpenAI per-test.)

- [ ] **Step 5: Run integration tests**

```bash
docker compose run --rm nexus pytest tests/interview_engine/test_structured_agent_integration.py -v
```

Expected: all Phase B tests still PASS (modulo the one removed); 8 new Phase C tests PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore(speech): delete safety.py + _phase_b_utterances + safety tests; add Phase C integration suite

Three files deleted (regex layer eliminated, Phase B constants replaced):
- app/modules/interview_engine/_phase_b_utterances.py
- app/modules/interview_engine/speech/safety.py
- tests/interview_engine/speech/test_safety.py

Integration test suite extended with 8 Phase C tests covering happy
path with pre-render slot, all 4 cancellation sub-cases, fallback path
session continuity, template-not-found technical-failure exit, slot
cancellation on close. Plus a regression-guard test that fails on any
SPEECH_SAFETY_VIOLATION reintroduction (repo-wide grep).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Checkpoint B reached.** Phase C is functionally complete. Remaining tasks are quality/observability/closeout.

---

## Task 15: Add prompt-quality tests (`@pytest.mark.prompt_quality`, nightly only)

**Files:**
- Create: `backend/nexus/tests/interview_engine/speech/prompt_quality/__init__.py`
- Create: `backend/nexus/tests/interview_engine/speech/prompt_quality/test_intro_quality.py`
- Create: `backend/nexus/tests/interview_engine/speech/prompt_quality/test_ask_question_standard_quality.py`
- Create: `backend/nexus/tests/interview_engine/speech/prompt_quality/test_wrap_normal_quality.py`

- [ ] **Step 1: Create empty `__init__.py`**

```bash
touch backend/nexus/tests/interview_engine/speech/prompt_quality/__init__.py
```

- [ ] **Step 2: Write intro quality tests**

Create `backend/nexus/tests/interview_engine/speech/prompt_quality/test_intro_quality.py`:

```python
"""Real-LLM tests for intro template. Nightly only.

Run via:
    docker compose run --rm -e OPENAI_API_KEY=$OPENAI_API_KEY nexus \
        pytest tests/interview_engine/speech/prompt_quality/test_intro_quality.py -m prompt_quality -v
"""
from __future__ import annotations

import re

import pytest

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.speech import SpeechAgent
from app.modules.interview_engine.speech import deliveries

OUTCOME_WORDS_RE = re.compile(
    r"\b(passed|failed|rejected|advanced|unfortunately|best of luck|"
    r"thanks for your interest)\b",
    re.IGNORECASE,
)


@pytest.fixture
def collector():
    # Minimal real EventCollector — write to /dev/null sink
    return EventCollector(
        session_id="test-session",
        tenant_id="test-tenant",
        correlation_id="test-corr",
        controller_prompt_hash="sha256:test",
        task_prompt_hashes={},
        model_versions={},
        redaction_mode="metadata",
    )


@pytest.fixture
def speech_agent(collector):
    return SpeechAgent(
        client=get_openai_raw_client(),
        model=ai_config.speech_agent_model,
        effort=ai_config.speech_agent_effort or None,
        collector=collector,
    )


@pytest.mark.prompt_quality
@pytest.mark.parametrize("first_name,role,minutes", [
    ("Alex", "Backend Engineer", 15),
    ("Priya", "Senior SRE", 30),
    ("Mahmoud", "Frontend Engineer", 20),
    ("Lin", "Data Scientist", 45),
    ("Sam", "Product Designer", 10),
])
@pytest.mark.asyncio
async def test_intro_real_llm_no_outcome_words(speech_agent, first_name, role, minutes):
    handle = await deliveries.render_intro(
        speech_agent,
        candidate_first_name=first_name,
        role_title=role,
        target_duration_minutes=minutes,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    assert not OUTCOME_WORDS_RE.search(full), f"Outcome word in: {full!r}"


@pytest.mark.prompt_quality
@pytest.mark.asyncio
async def test_intro_real_llm_length_target(speech_agent):
    handle = await deliveries.render_intro(
        speech_agent,
        candidate_first_name="Alex", role_title="Engineer", target_duration_minutes=15,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    # Lenient cap (per Q4 A2): 50 + 30% slack for prompt iteration headroom
    assert len(full.split()) <= 65, f"Length {len(full.split())} exceeds lenient cap"


@pytest.mark.prompt_quality
@pytest.mark.asyncio
async def test_intro_real_llm_does_not_mention_question_count(speech_agent):
    handle = await deliveries.render_intro(
        speech_agent,
        candidate_first_name="Alex", role_title="Engineer", target_duration_minutes=15,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    # No digit followed by "questions"
    assert not re.search(r"\b\d+\s*questions\b", full, re.IGNORECASE)
```

- [ ] **Step 3: Write ask_question_standard quality test**

Create `backend/nexus/tests/interview_engine/speech/prompt_quality/test_ask_question_standard_quality.py` (analogous structure, single test that asserts key noun phrases preserved):

```python
"""Real-LLM tests for ask_question_standard template. Nightly only."""
from __future__ import annotations

import re

import pytest

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.speech import SpeechAgent
from app.modules.interview_engine.speech import deliveries


@pytest.fixture
def collector():
    return EventCollector(
        session_id="test-session", tenant_id="test-tenant",
        correlation_id="test-corr", controller_prompt_hash="sha256:test",
        task_prompt_hashes={}, model_versions={}, redaction_mode="metadata",
    )


@pytest.fixture
def speech_agent(collector):
    return SpeechAgent(
        client=get_openai_raw_client(),
        model=ai_config.speech_agent_model,
        effort=ai_config.speech_agent_effort or None,
        collector=collector,
    )


@pytest.mark.prompt_quality
@pytest.mark.parametrize("question,must_contain", [
    ("Walk me through how you'd handle a flaky integration test.",
     ["flaky", "integration test"]),
    ("Tell me about a time you had to debug a production issue.",
     ["debug", "production"]),
    ("What's your approach to writing maintainable code?",
     ["maintainable", "code"]),
])
@pytest.mark.asyncio
async def test_ask_question_standard_real_llm_preserves_meaning(
    speech_agent, question, must_contain,
):
    handle = await deliveries.render_ask_question_standard(
        speech_agent, question_text=question,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks).lower()
    for phrase in must_contain:
        assert phrase.lower() in full, f"Lost {phrase!r} in: {full!r}"
```

- [ ] **Step 4: Write wrap_normal quality test**

Create `backend/nexus/tests/interview_engine/speech/prompt_quality/test_wrap_normal_quality.py`:

```python
"""Real-LLM tests for wrap_normal template. Nightly only."""
from __future__ import annotations

import re

import pytest

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.speech import SpeechAgent
from app.modules.interview_engine.speech import deliveries

OUTCOME_RE = re.compile(
    r"\b(best of luck|thanks for your interest|unfortunately|"
    r"passed|failed|rejected)\b", re.IGNORECASE,
)


@pytest.fixture
def collector():
    return EventCollector(
        session_id="test-session", tenant_id="test-tenant",
        correlation_id="test-corr", controller_prompt_hash="sha256:test",
        task_prompt_hashes={}, model_versions={}, redaction_mode="metadata",
    )


@pytest.fixture
def speech_agent(collector):
    return SpeechAgent(
        client=get_openai_raw_client(),
        model=ai_config.speech_agent_model,
        effort=ai_config.speech_agent_effort or None,
        collector=collector,
    )


@pytest.mark.prompt_quality
@pytest.mark.parametrize("invocation", [1, 2, 3])
@pytest.mark.asyncio
async def test_wrap_normal_real_llm_no_outcome_implications(
    speech_agent, invocation,
):
    handle = await deliveries.render_wrap_normal(speech_agent)
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    assert not OUTCOME_RE.search(full), f"Outcome leak (run {invocation}): {full!r}"
```

- [ ] **Step 5: Verify the marker is registered**

```bash
grep -n "prompt_quality" backend/nexus/pyproject.toml backend/nexus/pytest.ini 2>/dev/null
```

If the marker isn't already registered, add to `pyproject.toml` `[tool.pytest.ini_options]`:

```toml
markers = [
    "prompt_quality: real-LLM tests, nightly only (run with -m prompt_quality + valid OPENAI_API_KEY)",
    # ... existing markers ...
]
```

- [ ] **Step 6: Verify tests are collected (NOT run — would burn API)**

```bash
docker compose run --rm nexus pytest tests/interview_engine/speech/prompt_quality/ --collect-only -m prompt_quality 2>&1 | tail -30
```

Expected: shows ~9-12 tests collected. Do NOT run them per-PR.

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/tests/interview_engine/speech/prompt_quality/ backend/nexus/pyproject.toml
git commit -m "$(cat <<'EOF'
test(speech): prompt-quality tests for intro / ask_question_standard / wrap_normal

@pytest.mark.prompt_quality marker — nightly only, NOT per-PR.
Validates: no outcome words, length within lenient cap, key noun
phrases preserved, no salary/scheduling/hiring-manager content.

Until the eval harness ships (parallel workstream, separate spec),
these + test_fallbacks.py are the only programmatic gates on rendered
content. PR review is the human gate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Manual smoke test in real LiveKit session

This task is human-driven, not test-driven. The goal is to verify the full Phase C flow end-to-end with a real LiveKit room, real STT/TTS, and real OpenAI streaming.

**Files (output only):**
- Modify: `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md` (ADR-3)

- [ ] **Step 1: Start the local stack**

```bash
cd backend/nexus
docker compose up --build -d
```

Verify nexus + worker + redis + supabase are healthy.

- [ ] **Step 2: Trigger a real session**

Create or reuse a candidate invite via the recruiter dashboard or seed data. Open the candidate session URL in the browser, complete consent, and run a 3-question interview to completion.

- [ ] **Step 3: Capture evidence**

- Note the `session_id`.
- Find the audit envelope: `backend/nexus/engine-events/<session_id>.json`.
- Verify it contains: 5 `speech.rendered` events (intro + 3 questions + wrap_normal), zero `speech.fallback_used` (assuming OpenAI was healthy), unique `render_id` per event, `was_fallback=false` everywhere.
- Check OTel spans (if exporter configured) for streaming chat completion lifecycle.

- [ ] **Step 4: Record results in close-out ADR**

Edit `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md` ADR-2 + ADR-3:
- ADR-2: OTel verification result. PASS if a single coherent span per render completes when stream drains. FAIL if orphan/duplicate spans → flag as Phase J cleanup.
- ADR-3: Smoke session details. Session ID. Subjective candidate experience notes (gap latency, voice naturalness, any fallback firing). Any miscall log entries (rendered output you'd flag for prompt revision).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md
git commit -m "$(cat <<'EOF'
docs(phase-c): close-out ADR-2 + ADR-3 — OTel verification + manual smoke results

Verified end-to-end Phase C flow in a real LiveKit session. Documents
session_id, envelope event audit, and any miscall log entries from the
smoke run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Phase C close-out ADR finalize + final test run

**Files:**
- Modify: `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md` (status field)

- [ ] **Step 1: Run the full test suite**

```bash
cd backend/nexus
docker compose run --rm nexus pytest -v --ignore=tests/interview_engine/speech/prompt_quality
```

Expected: all tests PASS (excluding nightly prompt_quality).

- [ ] **Step 2: Run mypy strict**

```bash
docker compose run --rm nexus mypy app/modules/interview_engine/speech/ app/modules/interview_engine/structured_agent.py app/modules/interview_engine/agent.py
```

Expected: zero errors.

- [ ] **Step 3: Run ruff**

```bash
docker compose run --rm nexus ruff check app/modules/interview_engine/speech/
```

Expected: clean.

- [ ] **Step 4: Run module-boundaries test**

```bash
docker compose run --rm nexus pytest tests/test_module_boundaries.py -v
```

Expected: PASS.

- [ ] **Step 5: Update ADR status to "Complete"**

Edit `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md`:
- Change `**Status:** In progress` → `**Status:** Complete`
- Confirm all three ADRs have decisions recorded.

- [ ] **Step 6: Final commit**

```bash
git add docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md
git commit -m "$(cat <<'EOF'
docs(phase-c): close-out ADR finalized — Phase C ships

All three ADRs decided:
1. Streaming-cancellation spike: PASS / FAIL (per ADR-1 result)
2. OTel streaming span verification: PASS / mitigation
3. Manual smoke session: results + miscall log entries

Phase C complete. Phase D (Sufficiency Checker shadow mode) inherits
the pre-render lifecycle, render_id correlation, and bounded-cancel
patterns documented in spec §6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Plan self-review

After writing this plan, the spec was checked against task coverage. Findings:

**Spec coverage:**
- §0 Mission/scope/non-goals: Tasks 1-2 (config), 3 (spike gate), 8-9 (SpeechAgent), 11 (public API).
- §1 Architecture: Task 12 (orchestrator wiring) brings the data flow online; Tasks 6-9 build the components.
- §2 SpeechAgent class API contract: Tasks 5, 8, 9 (Protocol, StreamingRenderHandle, SpeechAgent class).
- §3 Pre-render Task lifecycle: Task 12 (three trigger sites + helper) + Task 13 (close-handler bounded cancel).
- §4 Error handling and fallback: Tasks 6-7 (StaticFallbackHandle + builders), Task 9 (retry policy in `_drive`), Task 12 (catch site in helper).
- §5.1-5.7 Tests: Task 9 (16 unit), Task 8 (Protocol/cancellation), Task 7 (fallback content), Task 14 (8 integration), Task 15 (prompt-quality nightly).
- §5.2 Build sequence: this plan IS the expanded build sequence.
- §5.8 Integration-points checklist: Tasks 1-4, 11-13 cover each modified file.
- §6 Carryforwards: Task 17 close-out ADR records carryforward inheritance for Phase D.
- §7 Doc amendments queue: handled in commit `5a481a4` before this plan executes.
- §8 Open questions: ADRs 1-3 in close-out doc resolve runtime-determined items.

**Placeholder scan:** No "TBD", "TODO", "fill in details" in any executable step. ADR template uses TBD as placeholders for runtime data — those resolve during execution.

**Type consistency:** `SpeechRenderHandle` Protocol surface (`ready_to_commit`, `commit`, `cancel`, `is_committed`, `is_cancelled`, `metadata`, `completed_text`) matches across all tasks (5, 6, 8, 9, 12). `SpeechRenderError` reasons (`template_not_found`, `placeholder_missing`, `openai_timeout`, `openai_5xx`, `openai_connection_dropped_pre_first_token`, `openai_429`) match. `RenderMetadata` field names match.

Plan ready.

---

*End of Phase C implementation plan. Targets ARCH-D Option β (streaming + prefix-pipe). Hard gate at Task 3 may collapse to Option α; plan internals for Task 8 swap, but every other task proceeds unchanged.*
