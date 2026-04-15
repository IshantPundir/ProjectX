"""Server-Sent Events stream for question bank generation status.

Polls the DB every 500ms, emits events only when state changes (dedup).
Closes when all banks in the pipeline are terminal OR on 10 minutes of idle
OR when the client disconnects.

Disconnect detection is critical: without it, an orphaned browser tab
would hold the stream open until the 10-minute idle timeout, pinning ~2 DB
connections per poll iteration. 15-20 orphaned streams exhausts the pool.
Matches the pattern used in app/modules/jd/sse.py.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from uuid import UUID

import structlog
from fastapi import Request
from sqlalchemy import select

from app.database import get_tenant_session
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    StageQuestion,
    StageQuestionBank,
)

logger = structlog.get_logger()


POLL_INTERVAL_SEC = 0.5
IDLE_TIMEOUT_SEC = 600  # 10 minutes


async def stream_question_bank_status(
    *,
    request: Request,
    tenant_id: UUID,
    job_id: UUID,
):
    """Async generator yielding SSE-formatted event strings.

    Format: `event: <name>\\ndata: <json>\\n\\n`

    The ``request`` parameter is used to detect client disconnects: if the
    browser closes the tab or the connection drops, the generator bails
    out on the next iteration and the DB session is returned to the pool.
    """
    # Canonicalise tenant_id so get_tenant_session's own validation can't
    # trip on a malformed claim (same defense used by get_tenant_db).
    safe_tenant_id = str(uuid.UUID(str(tenant_id)))

    last_snapshots: dict[UUID, dict] = {}  # bank_id → last emitted state
    idle_since = asyncio.get_running_loop().time()

    while True:
        if await request.is_disconnected():
            return

        # Collect events inside the session context, yield them after the
        # session is released. Two reasons: (1) holding the DB connection
        # across a `yield` would pin a pool slot while the client processes
        # events, and (2) `get_tenant_session` wraps SET LOCAL ROLE nexus_app
        # + SET LOCAL app.current_tenant in a single explicit transaction —
        # opening the session raw via async_session_factory() would skip
        # the role switch and RLS would be silently bypassed on streaming.
        events_to_emit: list[str] = []
        should_terminate = False
        any_change = False
        all_terminal = True
        num_stages = 0

        async with get_tenant_session(safe_tenant_id) as db:
            instance_result = await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.job_posting_id == job_id
                )
            )
            instance = instance_result.scalar_one_or_none()
            if instance is None:
                events_to_emit.append(
                    _format("error", {"error": "No pipeline for this job"})
                )
                should_terminate = True
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
                    total_minutes = float(sum(q.estimated_minutes for q in questions))

                    if bank.status in ("draft", "generating"):
                        all_terminal = False

                    current_state = {
                        "status": bank.status,
                        "question_count": question_count,
                        "total_minutes": total_minutes,
                        "error": bank.generation_error,
                    }

                    if last_snapshots.get(bank.id) != current_state:
                        any_change = True
                        last_snapshots[bank.id] = current_state
                        event_payload = {
                            "stage_id": str(stage.id),
                            "status": bank.status,
                            "question_count": question_count,
                            "total_minutes": total_minutes,
                        }
                        if bank.generation_error:
                            event_payload["error"] = bank.generation_error
                        events_to_emit.append(
                            _format("bank.status_changed", event_payload)
                        )

        # Session released — yield events without holding a pool slot.
        for ev in events_to_emit:
            yield ev

        if should_terminate:
            return

        if any_change:
            idle_since = asyncio.get_running_loop().time()
        elif asyncio.get_running_loop().time() - idle_since > IDLE_TIMEOUT_SEC:
            # Close the stream after 10 minutes of no changes
            return

        if all_terminal and len(last_snapshots) == num_stages:
            # All banks reached a terminal state — emit completion and close
            succeeded = sum(
                1
                for s in last_snapshots.values()
                if s["status"] == "confirmed" or s["status"] == "reviewing"
            )
            failed = sum(1 for s in last_snapshots.values() if s["status"] == "failed")
            yield _format(
                "pipeline.generation_complete",
                {"succeeded": succeeded, "failed": failed, "total": num_stages},
            )
            return

        await asyncio.sleep(POLL_INTERVAL_SEC)


def _format(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
