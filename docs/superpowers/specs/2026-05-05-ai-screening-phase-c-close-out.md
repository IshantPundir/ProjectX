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

**What was measured:** `AsyncStream.close()` return latency — i.e. how long the *application-layer* SDK call takes to return after the consumer breaks out of the stream loop. Tracing into `openai/_streaming.py:233–239` → `httpx/_models.py:1065–1076` confirms that `close()` sets internal flags and signals the connection pool, but does NOT await TCP FIN-ACK or any server acknowledgment.

**What was NOT measured:** Actual TCP connection teardown (FIN-ACK round trip, kernel socket reclamation). The SDK is fire-and-forget at the close call site; the kernel and httpx connection pool clean up the socket asynchronously.

**Why this is sufficient (per spec §3.4):** The close-handler's 2-second timeout was always meant to bound the consumer Task's wait, not the underlying TCP teardown. Spec §3.4 explicitly states: *"If the OpenAI HTTP client is being slow to release, we move on — Python's GC will tear down the dangling connection eventually."* So a fast SDK `close()` ⇒ the consumer Task is unblocked ⇒ the 2 s close-handler budget is not consumed by the SDK call. Whether the kernel reclaims the socket 5 ms or 5 s later is acceptable per design.

**Result:** PASS (run 2026-05-05, 1 warm-up + 10 measured runs against `gpt-5-mini`, `time.perf_counter()` clock)
- p50 `AsyncStream.close()` return latency: 0.3 ms
- max `AsyncStream.close()` return latency: 0.3 ms
- Min: 0.2 ms
- (N=10 — labelled "max", not "p99", because one tail sample doesn't constitute a p99 statistic.)

The SDK call returns ~1,600x under the 500 ms threshold and well below the 2 s close-handler budget (spec §3.4). The consumer Task will not deadlock the close handler on the normal cancellation path.

**Decision:**
- [x] PASS (max `AsyncStream.close()` return < 500 ms): ARCH-D Option β ships as designed (streaming + prefix-pipe).
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
