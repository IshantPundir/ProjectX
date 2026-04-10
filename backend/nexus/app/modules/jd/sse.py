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

Session management:
  The generator opens a short-lived DB session per poll iteration to avoid
  holding a connection from the pool for the entire stream duration. With
  the prior design (one session for the whole stream), 20 concurrent SSE
  clients would exhaust the connection pool and block all other requests.
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request

from app.database import async_session_factory
from app.modules.jd.service import get_job_status

POLL_INTERVAL_SECONDS: float = 1.5
TERMINAL_STATES: frozenset[str] = frozenset(
    {"signals_extracted", "signals_extraction_failed", "signals_confirmed"}
)


async def job_status_event_generator(
    tenant_id: str,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events until terminal state or client disconnect."""
    last_status: str | None = None
    while True:
        if await request.is_disconnected():
            return

        # Open a fresh session per iteration — returned to the pool as
        # soon as the query completes. This prevents long-lived SSE
        # streams from holding pool connections indefinitely.
        async with async_session_factory() as db:
            import sqlalchemy

            await db.execute(
                sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )
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
