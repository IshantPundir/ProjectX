"""Phase C build-step gate — streaming cancellation latency spike.

This is a STANDALONE SCRIPT (not a pytest test). Runs once before the
SpeechAgent class merges. Result is recorded in
docs/superpowers/specs/2026-05-05-ai-screening-phase-c-close-out.md.

What this measures:
    The latency of `AsyncStream.close()` returning, NOT actual TCP
    teardown. Per spec §3.4, the close-handler's 2s timeout bounds the
    consumer Task's wait — not the underlying TCP connection. The SDK's
    close() returning promptly is sufficient evidence that the Task
    won't deadlock the close handler. (Tracing into
    `openai/_streaming.py` → `httpx/_models.py` confirms close() sets
    flags + signals the connection pool, with no awaiting of TCP
    FIN-ACK or server ack — application-layer call timing only.)

Why this is sufficient:
    The pre-render Task lifecycle (spec §3) cancels in-flight streams
    on candidate disconnect. Spec §3.4 explicitly accepts that "if the
    OpenAI HTTP client is being slow to release, we move on — Python's
    GC will tear down the dangling connection eventually." The 2s
    timeout was always meant to bound the close-handler's wait on the
    consumer Task, not actual TCP teardown. So a fast SDK close() ⇒
    Task is unblocked ⇒ close-handler doesn't hit its 2s ceiling on
    the normal path.

PASS criterion:
    max `AsyncStream.close()` return latency < 500ms across 10 measured
    runs (one warm-up run is discarded). With N=10 the tail figure is a
    max, not a true p99 — labelled "max" accordingly.

FAIL action:
    ARCH-D collapses to ARCH-D-buffered-non-streaming (Option α —
    eager-buffer-all). The Protocol surface preserves; the
    StreamingRenderHandle internals change. See spec §5.2 step 3.

Run:
    cd backend/nexus
    docker compose run --rm \\
        -e OPENAI_API_KEY=$OPENAI_API_KEY \\
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
MAX_THRESHOLD_MS = 500


async def run_one() -> float:
    """Returns AsyncStream.close() return latency in milliseconds."""
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
            # "Count to 100 slowly" guarantees a long output stream so
            # the connection is still actively producing tokens when
            # we cancel after TOKENS_BEFORE_CANCEL — this exercises the
            # mid-flight cancellation path, not a near-EOF close.
            {"role": "user", "content": "Count to 100 slowly, one number per line."}
        ],
    )
    tokens_seen = 0
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            tokens_seen += 1
            if tokens_seen >= TOKENS_BEFORE_CANCEL:
                break

    # Now cancel and measure how long AsyncStream.close() takes to
    # return. This is the application-layer call latency — NOT actual
    # TCP teardown. See module docstring for why that's sufficient.
    cancel_start = time.perf_counter()
    await stream.close()
    cancel_end = time.perf_counter()

    await client.close()
    return (cancel_end - cancel_start) * 1000.0


async def main() -> None:
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY not set in environment")

    # Warm-up run: the first run includes httpx connection-pool
    # initialization, DNS resolution, and TLS handshake — all of which
    # are one-time costs we don't want polluting the latency stats.
    print("Warm-up run (discarded from stats)...")
    warmup_ms = await run_one()
    print(f"  Warm-up: {warmup_ms:.1f}ms\n")

    print(f"Running {NUM_RUNS} streaming cancellation runs against gpt-5-mini...")
    latencies: list[float] = []
    for i in range(NUM_RUNS):
        ms = await run_one()
        latencies.append(ms)
        print(f"  Run {i+1}: {ms:.1f}ms")

    p50 = statistics.median(latencies)
    max_latency = max(latencies)
    print(
        f"\nResults: p50={p50:.1f}ms  max={max_latency:.1f}ms  "
        f"min={min(latencies):.1f}ms"
    )
    print(f"Threshold: max < {MAX_THRESHOLD_MS}ms")
    if max_latency < MAX_THRESHOLD_MS:
        print(
            "\nPASS — AsyncStream.close() returns promptly across all runs. "
            "The consumer Task won't deadlock the close handler's 2s budget "
            "(spec §3.4). ARCH-D Option β (streaming) ships as designed."
        )
        print(f"   Document p50={p50:.1f}ms, max={max_latency:.1f}ms in close-out ADR.")
    else:
        print(f"\nFAIL — max={max_latency:.1f}ms exceeds threshold.")
        print("   ARCH-D collapses to ARCH-D-buffered-non-streaming (Option α).")
        print("   STOP and surface to user before proceeding.")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
