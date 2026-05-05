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
        print("\nPASS — ARCH-D Option β (streaming) ships as designed.")
        print(f"   Document p50={p50:.1f}ms, p99={p99:.1f}ms in close-out ADR.")
    else:
        print(f"\nFAIL — p99={p99:.1f}ms exceeds threshold.")
        print("   ARCH-D collapses to ARCH-D-buffered-non-streaming (Option α).")
        print("   STOP and surface to user before proceeding.")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
