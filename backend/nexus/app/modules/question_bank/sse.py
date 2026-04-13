"""Server-Sent Events stream for question bank generation status.

Polls the DB every 500ms, emits events only when state changes (dedup).
Closes when all banks in the pipeline are terminal OR on 10 minutes of idle.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

import structlog
from sqlalchemy import select

from app.database import async_session_factory
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
    tenant_id: UUID,
    job_id: UUID,
):
    """Async generator yielding SSE-formatted event strings.

    Format: `event: <name>\\ndata: <json>\\n\\n`
    """
    last_snapshots: dict[UUID, dict] = {}  # bank_id → last emitted state
    idle_since = asyncio.get_event_loop().time()

    while True:
        async with async_session_factory() as db:
            from sqlalchemy.sql import text
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )

            # Load pipeline + stages + banks
            instance_result = await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.job_posting_id == job_id
                )
            )
            instance = instance_result.scalar_one_or_none()
            if instance is None:
                yield _format("error", {"error": "No pipeline for this job"})
                return

            stages_result = await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position)
            )
            stages = list(stages_result.scalars().all())

            any_change = False
            all_terminal = True

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
                    yield _format("bank.status_changed", event_payload)

        if any_change:
            idle_since = asyncio.get_event_loop().time()
        elif asyncio.get_event_loop().time() - idle_since > IDLE_TIMEOUT_SEC:
            # Close the stream after 10 minutes of no changes
            return

        if all_terminal and len(last_snapshots) == len(stages):
            # All banks reached a terminal state — emit completion and close
            succeeded = sum(
                1 for s in last_snapshots.values() if s["status"] == "confirmed" or s["status"] == "reviewing"
            )
            failed = sum(1 for s in last_snapshots.values() if s["status"] == "failed")
            yield _format(
                "pipeline.generation_complete",
                {"succeeded": succeeded, "failed": failed, "total": len(stages)},
            )
            return

        await asyncio.sleep(POLL_INTERVAL_SEC)


def _format(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
