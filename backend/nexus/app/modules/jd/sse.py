"""Server-Sent Events generator for job posting status updates.

Contract:
  - Polls the job_postings row every POLL_INTERVAL_SECONDS.
  - Emits a 'status' event ONLY when job.status changes from the last
    observed value (de-duplication).
  - Terminates and closes the HTTP connection when the job reaches a
    terminal state (signals_extracted or signals_extraction_failed).
  - Terminates immediately if the client disconnects mid-stream.
  - Does NOT enforce RBAC — the router's require_job_access() dependency
    has already validated access before this generator is invoked.
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jd.service import get_job_status

POLL_INTERVAL_SECONDS: float = 1.5
TERMINAL_STATES: frozenset[str] = frozenset(
    {"signals_extracted", "signals_extraction_failed"}
)


async def job_status_event_generator(
    db: AsyncSession,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events until terminal state or client disconnect."""
    last_status: str | None = None
    while True:
        if await request.is_disconnected():
            return

        event = await get_job_status(db, job_id)
        if event is None:
            return  # job disappeared (shouldn't happen under RLS scope)

        if event.status != last_status:
            yield {
                "event": "status",
                "data": event.model_dump_json(),
            }
            last_status = event.status

        if event.status in TERMINAL_STATES:
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
