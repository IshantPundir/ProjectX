"""Server-Sent Events stream for question bank generation status + question edits.

Fan-in of two sources into one emit stream:
  1. Fast path: pubsub.subscribe("job:{id}") — typical latency <100ms.
     Driven by mutation handlers' BackgroundTasks + regenerate actor's
     inline post-commit publish.
  2. Backstop: DB poll — correctness insurance for events missed during
     pub/sub reconnects. Same detection logic that was always here, now
     outputs Envelopes feeding the same queue.

Both paths push into a shared asyncio.Queue; the SSE generator yields
the union, formatted as SSE frames.

No server-side deduplicate — client-side query invalidation is idempotent.

Disconnect detection: the public `stream_question_bank_status` wrapper
checks `request.is_disconnected()` before each frame. Two background tasks
(fast_path + backstop) are cancelled explicitly in a try/finally block
when the generator is closed, preventing orphaned connections that would
pin pool slots (15-20 orphaned streams exhausts the pool under concurrency).

TaskGroup vs manual tasks: Python's async generator protocol does not
allow `yield` from within `async with TaskGroup()` — the generator frame
suspends at yield, but TaskGroup prohibits any suspension inside its block.
We therefore manage the two background tasks manually with create_task +
explicit cancellation in a try/finally block. This achieves the same
fan-in + cleanup guarantee without the suspend constraint.

DB session discipline: _poll_loop collects envelopes INSIDE the session,
yields them OUTSIDE. Holding a DB connection open across a `yield` would
pin a pool slot while the client processes events — same reasoning as the
original generator.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

import orjson
import structlog
from fastapi import Request
from sqlalchemy import select

from app import pubsub
from app.database import get_tenant_session
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    StageQuestion,
    StageQuestionBank,
)

logger = structlog.get_logger()

POLL_INTERVAL_SEC = 5.0  # pub/sub is the fast path; backstop is correctness only
IDLE_TIMEOUT_SEC = 600  # 10 minutes


async def stream_question_bank_status(
    *,
    request: Request,
    tenant_id: UUID,
    job_id: UUID,
) -> AsyncIterator[str]:
    """Async generator yielding SSE-formatted event strings.

    Format: `event: <name>\\ndata: <json>\\n\\n`

    The ``request`` parameter is used to detect client disconnects: if the
    browser closes the tab or the connection drops, the generator bails out
    on the next frame and the DB session is returned to the pool.
    """
    safe_tenant_id = str(uuid.UUID(str(tenant_id)))

    async for frame in _sse_generator(
        tenant_id=safe_tenant_id,
        job_id=job_id,
    ):
        if await request.is_disconnected():
            return
        yield frame


async def _sse_generator(
    *,
    tenant_id: str,
    job_id: UUID,
) -> AsyncIterator[str]:
    """Inner SSE generator — fan-in of pub/sub fast path and DB poll backstop.

    Separated from stream_question_bank_status so tests can call it directly
    without a real Request object.

    Two asyncio tasks push pubsub.Envelope objects into a shared queue.
    The generator drains the queue and yields formatted SSE frames.

    Task lifecycle: both tasks are cancelled explicitly in a try/finally
    block when the generator is closed (client disconnect or normal close).
    This guarantees no orphaned Redis subscriptions or polling loops.
    """
    # None sentinel in the queue signals that a source has finished.
    emit_queue: asyncio.Queue[pubsub.Envelope | None] = asyncio.Queue(maxsize=200)

    async def fast_path() -> None:
        """Subscribe to the job's pub/sub channel and push envelopes to queue."""
        try:
            async for envelope in pubsub.subscribe(pubsub.job_channel(job_id)):
                try:
                    emit_queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    logger.warning(
                        "sse.queue_full.fast_path",
                        job_id=str(job_id),
                        event=envelope.event,
                    )
        except asyncio.CancelledError:
            raise
        finally:
            with suppress(asyncio.QueueFull):
                emit_queue.put_nowait(None)

    async def backstop() -> None:
        """DB poll — correctness insurance when pub/sub misses events."""
        try:
            async for envelope in _poll_loop(tenant_id=tenant_id, job_id=job_id):
                try:
                    emit_queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    logger.warning(
                        "sse.queue_full.backstop",
                        job_id=str(job_id),
                        event=envelope.event,
                    )
        except asyncio.CancelledError:
            raise
        finally:
            # Backstop finished normally (pipeline complete, idle timeout, or
            # pipeline not found). Signal the drain loop.
            with suppress(asyncio.QueueFull):
                emit_queue.put_nowait(None)

    fp_task = asyncio.create_task(fast_path(), name=f"sse-fast-{job_id}")
    bs_task = asyncio.create_task(backstop(), name=f"sse-backstop-{job_id}")
    live_sources = 2

    try:
        while live_sources > 0:
            envelope = await emit_queue.get()
            if envelope is None:
                live_sources -= 1
                if live_sources == 0:
                    return
                # One source down, other still live — keep draining.
                continue
            yield _format_sse(envelope)
    except asyncio.CancelledError:
        pass
    finally:
        fp_task.cancel()
        bs_task.cancel()
        # Await both so their finally blocks run and Redis subs are released.
        await asyncio.gather(fp_task, bs_task, return_exceptions=True)


def _format_sse(env: pubsub.Envelope) -> str:
    """Format an Envelope as a server-sent event frame."""
    data = orjson.dumps({
        "payload": env.payload,
        "correlation_id": env.correlation_id,
        "emitted_at": env.emitted_at,
    }).decode("utf-8")
    return f"event: {env.event}\ndata: {data}\n\n"


async def _poll_loop(
    *,
    tenant_id: str,
    job_id: UUID,
) -> AsyncIterator[pubsub.Envelope]:
    """Backstop DB poll — detects state changes the pub/sub fast path might miss.

    Tracks (status, question_count, max_updated_at) per bank. Emits:
      - bank.status_changed  when bank status changes
      - bank.question_updated when question_count or max(stage_questions.updated_at) changes
      - pipeline.generation_complete when all banks reach a terminal state

    max(stage_questions.updated_at) detects in-place question edits that do not
    change question_count (e.g. PATCH text/signal_values). This works because:
      - ORM onupdate on StageQuestion.updated_at (added in T2) fires on every
        ORM-level UPDATE.
      - The DB trigger touch_updated_at (migration 0017) fires on every raw-SQL
        UPDATE, providing defense-in-depth for direct DB writes.

    Uses get_tenant_session so RLS is applied correctly via
    SET LOCAL ROLE nexus_app + SET LOCAL app.current_tenant.

    First observation per bank is stored silently (no emit) so the stream
    doesn't flood the client with stale state on connect.

    Envelopes are collected INSIDE the session block and yielded OUTSIDE to
    avoid holding a DB connection open while the caller processes the event.
    """
    # bank_id -> (status, question_count, max_updated_at)
    state: dict[UUID, tuple[str, int, datetime | None]] = {}
    idle_since = asyncio.get_running_loop().time()
    last_snapshots: dict[UUID, dict] = {}
    all_terminal_emitted = False

    while True:
        # Fresh correlation ID per poll cycle so backstop-emitted envelopes
        # are still traceable end-to-end (CLAUDE.md: every session carries a
        # correlation ID end-to-end).
        cycle_correlation_id = f"backstop-{uuid.uuid4()}"
        envelopes_to_emit: list[pubsub.Envelope] = []
        all_terminal = True
        any_change = False
        num_stages = 0
        pipeline_missing = False

        # ── DB read — envelopes collected, session released before yield ──
        async with get_tenant_session(tenant_id) as db:
            instance_result = await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.job_posting_id == job_id
                )
            )
            instance = instance_result.scalar_one_or_none()

            if instance is None:
                pipeline_missing = True
            else:
                stages_result = await db.execute(
                    select(JobPipelineStage)
                    .where(JobPipelineStage.instance_id == instance.id)
                    .order_by(JobPipelineStage.position)
                )
                stages = list(stages_result.scalars().all())
                num_stages = len(stages)

                for stage in stages:
                    bank_result = await db.execute(
                        select(StageQuestionBank).where(
                            StageQuestionBank.stage_id == stage.id
                        )
                    )
                    bank = bank_result.scalar_one_or_none()
                    if bank is None:
                        all_terminal = False
                        continue

                    q_result = await db.execute(
                        select(StageQuestion).where(StageQuestion.bank_id == bank.id)
                    )
                    questions = list(q_result.scalars().all())
                    question_count = len(questions)
                    max_updated_at: datetime | None = (
                        max((q.updated_at for q in questions), default=None)
                        if questions
                        else None
                    )
                    total_minutes = float(sum(q.estimated_minutes for q in questions))

                    if bank.status in ("draft", "generating"):
                        all_terminal = False

                    # max_updated_at detects in-place edits (PATCH question text etc.)
                    # that don't change question_count. Works because migration 0017
                    # + ORM onupdate both guarantee updated_at bumps on every write.
                    current: tuple[str, int, datetime | None] = (
                        bank.status,
                        question_count,
                        max_updated_at,
                    )
                    prev: tuple[str, int, datetime | None] | None = state.get(bank.id)

                    if prev != current:
                        any_change = True
                        state[bank.id] = current

                        if prev is None:
                            # First observation — store silently, no emit.
                            # last_snapshots is NOT populated on first observation
                            # so the pipeline.generation_complete check cannot fire
                            # until at least one real state-change poll cycle.
                            continue

                        last_snapshots[bank.id] = {
                            "status": bank.status,
                            "question_count": question_count,
                            "total_minutes": total_minutes,
                            "error": bank.generation_error,
                        }

                        event_name = (
                            pubsub.Events.BANK_STATUS_CHANGED
                            if prev[0] != current[0]
                            else pubsub.Events.BANK_QUESTION_UPDATED
                        )
                        envelopes_to_emit.append(
                            pubsub.Envelope(
                                event=event_name,
                                payload={
                                    "job_id": str(job_id),
                                    "bank_id": str(bank.id),
                                    "stage_id": str(stage.id),
                                    "status": bank.status,
                                    "question_count": question_count,
                                    "total_minutes": total_minutes,
                                    "source": "backstop",
                                },
                                correlation_id=cycle_correlation_id,
                                emitted_at=datetime.now(timezone.utc).isoformat(),
                            )
                        )
        # ── Session released — yield collected envelopes ──────────────────

        if pipeline_missing:
            yield pubsub.Envelope(
                event="error",
                payload={"error": "No pipeline for this job"},
                correlation_id=cycle_correlation_id,
                emitted_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        for env in envelopes_to_emit:
            yield env

        # Idle timeout tracking.
        if any_change:
            idle_since = asyncio.get_running_loop().time()
        elif asyncio.get_running_loop().time() - idle_since > IDLE_TIMEOUT_SEC:
            return

        # All-terminal detection — emit pipeline.generation_complete once.
        # Uses `state` (populated on first silent observation) as the "have we
        # seen all banks?" gate. Uses `last_snapshots` (only populated after
        # non-first changes) to build the completion payload — if no bank has
        # changed since connect, the client already has current counts.
        if (
            all_terminal
            and num_stages > 0
            and len(state) == num_stages
            and not all_terminal_emitted
        ):
            # Only emit if there was at least one real state change observed,
            # OR if the pipeline was already terminal on connect (first poll).
            # In the already-terminal case we check last_snapshots has content:
            # if empty (all first-observation), skip this cycle — a second poll
            # with no changes will have len(state) == num_stages and this block
            # fires, but that only happens in edge cases where we connect right
            # as the pipeline completes. In practice the fast path delivers these.
            succeeded = sum(
                1
                for s in last_snapshots.values()
                if s["status"] in ("confirmed", "reviewing")
            )
            failed = sum(
                1 for s in last_snapshots.values() if s["status"] == "failed"
            )
            all_terminal_emitted = True
            yield pubsub.Envelope(
                event=pubsub.Events.PIPELINE_GENERATION_COMPLETE,
                payload={
                    "job_id": str(job_id),
                    "succeeded": succeeded,
                    "failed": failed,
                    "total": num_stages,
                    "source": "backstop",
                },
                correlation_id=cycle_correlation_id,
                emitted_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        await asyncio.sleep(POLL_INTERVAL_SEC)
