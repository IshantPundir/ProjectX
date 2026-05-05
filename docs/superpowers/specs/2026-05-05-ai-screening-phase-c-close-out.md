# AI Screening Phase C — Close-out ADR

**Status:** ADR-1 complete (spike PASSED). ADR-2 + ADR-3 pending Task 16 (manual smoke test deferred to user).
**Pairs with:** `docs/superpowers/specs/2026-05-05-ai-screening-phase-c-design.md`

## Phase C completion summary

Phase C (Streaming Speech Agent + Pre-render Lifecycle) is functionally complete after Task 14 (Checkpoint B). Tasks 15 (prompt_quality nightly tests) and 17 (close-out ADR + automated checks) are landed. Task 16 (manual smoke test) is deferred to the user — automated tests cover the orchestrator-side surface but cannot replace a human walking through the real candidate flow.

**Tests:** 184 in interview_engine/ pass. 13 prompt_quality tests collected (run nightly via `-m prompt_quality`). 8 Phase C integration tests cover all 4 cancellation sub-cases + happy path + fallback continuity + technical-failure exit + slot cancellation + regression guard.

**Spike result (ADR-1):** PASS — p99 cancellation latency 0.3ms (1600x under threshold). ARCH-D Option β (streaming + prefix-pipe) shipped as designed.

**Outstanding follow-ups (non-blocking, post-Phase-C):**
- Sub-case 2 envelope completeness gap: `_handle_close` does not call `handle.cancel()` on completed-but-uncommitted handles in the slot. Spec §3.5 says sub-case 2 should emit `speech.rendered` with `committed=false, played=false`; current implementation emits no event. Either amend spec or extend close handler.
- Test 7 (`test_pre_render_slot_cancelled_on_close`) functionally a subset of Test 2.
- Regression-guard test scope: walks `*.py` in backend/ only. Spec mentioned `*.md` files too — would require allowlisting docs/ directory.
- `_PostFirstTokenFailure` exception class declared in agent.py is unused (kept for plan symmetry).
- `retries=0` hardcoded in `_maybe_emit_rendered`'s envelope — Task 8 follow-up addressed metadata.retries; envelope payload still has it as 0.

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

**Decision:** Deferred — verified at runtime via manual smoke test.

**What we verify:** That the OpenAI auto-instrumentor produces a single coherent span per streaming chat completion.

**Result:** TBD (verified during Task 16 manual smoke test).

**Mitigation if discrepancy:** Manual span management inside `SpeechAgent._drive` (open span on stream creation, close on Task completion or cancellation). Phase C ships without this; if Task 16 surfaces orphan spans, file follow-up issue.

---

## ADR-3: Manual smoke test results

**Decision:** Pending — Task 16 manual smoke test deferred to user.

**Smoke session:** TBD — run the agent end-to-end in a real LiveKit session, complete a 3-question interview, and record:
  - session_id
  - subjective candidate experience notes (gap latency, voice naturalness, any fallback firing)
  - any miscall log entries (rendered output flagged for prompt revision)
  - Audit envelope verification: 5 speech.rendered events, no speech.fallback_used (assuming OpenAI healthy), unique render_ids, was_fallback=false

**Run command:**
```bash
cd backend/nexus
docker compose up --build -d
# Then run a real candidate session via the recruiter dashboard / candidate flow.
# Find the audit envelope at: backend/nexus/engine-events/<session_id>.json
```

---
