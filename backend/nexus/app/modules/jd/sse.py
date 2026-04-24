"""Server-Sent Events for JD status + enrichment transitions.

Fan-in of two sources into one emit stream:
  1. Fast path: pubsub.subscribe("job:{id}") — typical latency <100ms.
     Driven by JD handlers' BackgroundTasks publishes (J2-J6) + JD
     actors' inline post-commit publishes (J7, J8).
  2. Backstop: DB poll every 5s — correctness insurance for pub/sub
     misses (connection reconnect windows, Redis outages). Same state
     detection as before: (status, enrichment_status) diff per connection.

Both paths push pubsub.Envelope objects into a shared asyncio.Queue.
A None sentinel pushed by the backstop signals "job gone" — the main
loop exits cleanly without waiting for a queue entry that will never come.

The generator yields formatted SSE frames. No server-side dedup —
the client's TanStack Query invalidation is idempotent.

Termination:
  - request.is_disconnected() — checked on every loop iteration
  - Terminal status AND enrichment not streaming — matches original behavior
  - None sentinel in queue — backstop detected job gone under RLS scope

Cancellation: both child tasks are cancelled via try/finally when the
generator is closed (client disconnect or terminal state return).

DB session discipline: the backstop opens a short-lived session per poll
cycle. The session is closed before the envelope is pushed to the queue,
so no pool slot is held while the client drains the queue.

TaskGroup vs manual tasks: Python's async generator protocol does not
allow `yield` from within `async with TaskGroup()` — the generator frame
suspends at yield, but TaskGroup prohibits any suspension inside its block.
We therefore manage the two background tasks manually with create_task +
explicit cancellation in a try/finally block. This achieves the same
fan-in + cleanup guarantee without the suspend constraint.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime, timezone
from uuid import UUID

import orjson
import structlog
from fastapi import Request

from app import pubsub
from app.database import get_tenant_session
from app.modules.jd.service import get_job_status

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS: float = 5.0  # pub/sub is the fast path; backstop is correctness only
TERMINAL_STATES: frozenset[str] = frozenset(
    {"signals_extracted", "signals_extraction_failed", "signals_confirmed"}
)


async def job_status_event_generator(
    tenant_id: str,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events until terminal state, client disconnect, or cancellation.

    Fan-in of pub/sub fast path and DB poll backstop. Both paths push
    pubsub.Envelope objects into a shared asyncio.Queue; this generator
    drains the queue and yields SSE frames.

    Yield shape: {"event": "status", "data": "<json>"} — matches
    FastAPI's EventSourceResponse contract. No deviation; the router
    wraps this generator directly.

    De-dup on (status, enrichment_status): the client sees each distinct
    combination exactly once per connection. last_status starts as None
    so the first envelope always emits regardless of its state value.
    """
    # Canonicalise tenant_id through uuid.UUID so get_tenant_session's
    # own validation can't trip on a malformed claim. Matches get_tenant_db's
    # defense; Dramatiq actors do the same.
    safe_tenant_id = str(uuid.UUID(str(tenant_id)))

    # Queue carries Envelope objects or None. None is a sentinel from the
    # backstop signalling that the job has disappeared under RLS scope.
    emit_queue: asyncio.Queue[pubsub.Envelope | None] = asyncio.Queue(maxsize=100)
    last_status: str | None = None
    last_enrichment_status: str | None = None

    async def fast_path() -> None:
        """Subscribe to pub/sub and forward JD_STATUS_CHANGED envelopes to the queue."""
        try:
            async for envelope in pubsub.subscribe(pubsub.job_channel(job_id)):
                if envelope.event != pubsub.Events.JD_STATUS_CHANGED:
                    # Filter out bank.* events on the same job:{id} channel.
                    continue
                try:
                    emit_queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    logger.warning(
                        "jd.sse.queue_full.fast_path",
                        job_id=str(job_id),
                        event=envelope.event,
                    )
        except asyncio.CancelledError:
            raise

    async def backstop() -> None:
        """Poll the DB every POLL_INTERVAL_SECONDS and push envelopes to the queue.

        Pushes a None sentinel to the queue when the job disappears under RLS
        scope, so the drain loop exits cleanly instead of hanging.
        """
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                async with get_tenant_session(safe_tenant_id) as db:
                    event = await get_job_status(db, job_id)
                if event is None:
                    # Job gone under RLS scope — signal the drain loop to exit.
                    with suppress(asyncio.QueueFull):
                        emit_queue.put_nowait(None)
                    return
                cycle_correlation = f"jd-backstop-{uuid.uuid4()}"
                envelope = pubsub.Envelope(
                    event=pubsub.Events.JD_STATUS_CHANGED,
                    payload=orjson.loads(event.model_dump_json()),
                    correlation_id=cycle_correlation,
                    emitted_at=datetime.now(timezone.utc).isoformat(),
                )
                try:
                    emit_queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    logger.warning(
                        "jd.sse.queue_full.backstop",
                        job_id=str(job_id),
                    )
        except asyncio.CancelledError:
            raise

    fast_task = asyncio.create_task(fast_path(), name=f"jd-sse-fast-{job_id}")
    backstop_task = asyncio.create_task(backstop(), name=f"jd-sse-backstop-{job_id}")

    try:
        while True:
            if await request.is_disconnected():
                return

            try:
                env = await asyncio.wait_for(emit_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Re-check disconnect on next iteration, then try again.
                continue

            # None sentinel from backstop: job is gone, exit cleanly.
            if env is None:
                return

            payload = env.payload
            status = payload.get("status")
            enrichment_status = payload.get("enrichment_status")

            # De-dupe on (status, enrichment_status) — client sees each
            # distinct combination exactly once per connection.
            if status == last_status and enrichment_status == last_enrichment_status:
                # Even on a duplicate, honor terminal-state termination.
                # This fires if we somehow re-receive a terminal envelope after
                # already emitting it (e.g. backstop catching up post-disconnect).
                if status in TERMINAL_STATES and enrichment_status != "streaming":
                    return
                continue

            last_status = status
            last_enrichment_status = enrichment_status

            # Preserve the existing SSE frame contract: dict with 'event' + 'data'
            # keys, data is a JSON string matching JobStatusEvent.model_dump_json().
            yield {
                "event": "status",
                "data": orjson.dumps(payload).decode("utf-8"),
            }

            # Only close the stream when the job is in a terminal state AND
            # enrichment is not actively streaming (we need to stay open to
            # deliver the enrichment completion event).
            if status in TERMINAL_STATES and enrichment_status != "streaming":
                return
    finally:
        fast_task.cancel()
        backstop_task.cancel()
        # Await both so their finally blocks run and Redis subs are released.
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(fast_task, backstop_task, return_exceptions=True)
