# AI Screening Phase C — Close-out ADR

**Status:** In progress (build-step gates pending)
**Pairs with:** `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-design.md`

---

## ADR-1: Streaming-cancellation spike

**Decision:** PASS — ARCH-D Option β ships as designed (streaming + prefix-pipe).

**Spike script:** `backend/nexus/tests/interview_engine/speech/spike_streaming_cancellation.py`

**Run command:**
```bash
cd backend/nexus
docker compose run --rm -e OPENAI_API_KEY=$OPENAI_API_KEY nexus python -m tests.interview_engine.speech.spike_streaming_cancellation
```

**Result:** PASS (run 2026-05-05, 10 runs against `gpt-5-mini`)
- p50 cancellation latency: 0.3 ms
- p99 cancellation latency: 0.3 ms
- Min: 0.2 ms
- Max: 0.3 ms

p99 is ~1,600x under the 500 ms threshold. The `AsyncStream.close()` path returns essentially synchronously after the consumer break — httpx tears the connection down well inside the 2 s close-handler timeout (spec §3.4) on the normal path.

**Decision:**
- [x] PASS (p99 < 500ms): ARCH-D Option β ships as designed (streaming + prefix-pipe).
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
