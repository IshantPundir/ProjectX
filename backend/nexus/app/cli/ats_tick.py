"""ATS scheduler tick — stateless CLI run by external cron.

Lifecycle (200ms typical):
  1. Init structlog + OTel (mirrors ``app/worker.py``).
  2. Open one bypass-RLS session.
  3. SELECT due connections FOR UPDATE SKIP LOCKED.
  4. For each: stamp poll_lock_acquired_at + last_poll_started_at, enqueue
     poll_ats_connection.
  5. Commit + exit.

The cron firing rate is NOT the per-tenant cadence — ``next_poll_at`` is.
Cron fires every 5 min; each connection's ``poll_interval_seconds`` (default
900) governs per-tenant cadence. ``FOR UPDATE SKIP LOCKED`` makes the tick
safe under concurrent runners — each row is locked for the duration of the
enclosing transaction, so a second tick picks the next-due row instead of
double-enqueuing.

The 20-minute stale-lock check is a safety net: if a previous tick crashed
mid-flight without releasing the lock, the next tick after 20 minutes
re-attempts. ``advance_next_poll_at`` in the poll actor also clears
``poll_lock_acquired_at = NULL`` after a normal completion.

Invocation::

    python -m app.cli.ats_tick

Deploy targets:
  - Railway: separate ``ats-scheduler`` service, cron ``*/5 * * * *``.
  - AWS ECS: EventBridge Scheduler → ECS RunTask, same image.
  - Local dev: docker-compose service ``nexus-scheduler`` in a sleep loop
    (added in Task 25).
"""
from __future__ import annotations

import asyncio
import atexit

import structlog
from opentelemetry import trace
from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory

_TICK_QUERY = """
SELECT id::text, tenant_id::text FROM ats_connections
WHERE active = true
  AND next_poll_at <= now()
  AND (poll_lock_acquired_at IS NULL
       OR poll_lock_acquired_at < now() - interval '20 minutes')
ORDER BY next_poll_at ASC
LIMIT 500
FOR UPDATE SKIP LOCKED
"""


def _init_structlog() -> None:
    """Mirrors ``app/worker.py`` init."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            (
                structlog.dev.ConsoleRenderer()
                if settings.debug
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            10 if settings.debug else 20
        ),
    )


def _init_otel() -> None:
    """Bootstrap the OTel TracerProvider for this short-lived process.

    Lazy import of ``app.ai.otel`` so a misconfigured tracer doesn't crash
    structlog setup — structlog must succeed first so the OTel failure is
    visible in logs.
    """
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)


# Broker setup — MUST be imported before any ``.send()`` call so Dramatiq
# binds to the Redis broker configured in ``app/brokers.py`` instead of the
# default localhost:6379 stub.
from app import brokers  # noqa: F401, E402
from app.modules.ats.actors import poll_ats_connection  # noqa: E402

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


async def run_tick() -> None:
    """One scheduler tick: SELECT due connections, enqueue actor per row.

    Single transaction:
      - ``SET LOCAL app.bypass_rls = 'true'`` so the SELECT/UPDATE bypass
        the tenant_isolation policy (tick runs cross-tenant).
      - ``FOR UPDATE SKIP LOCKED`` locks each candidate row until commit.
      - Stamp ``poll_lock_acquired_at`` + ``last_poll_started_at`` on each
        row before enqueueing.
      - Enqueue the Dramatiq actor.
      - Commit releases locks atomically.
    """
    with tracer.start_as_current_span("ats.tick") as span:
        async with async_session_factory() as session, session.begin():
            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            rows = await session.execute(text(_TICK_QUERY))
            due = list(rows)
            for row in due:
                await session.execute(
                    text(
                        "UPDATE ats_connections "
                        "SET poll_lock_acquired_at = now(), "
                        "    last_poll_started_at = now() "
                        "WHERE id = :i"
                    ),
                    {"i": row.id},
                )
                poll_ats_connection.send(row.id, row.tenant_id)
            await session.commit()
        span.set_attribute("ats.tick.enqueued_count", len(due))
        logger.info("ats.tick.completed", enqueued_count=len(due))


def main() -> None:
    _init_structlog()
    _init_otel()
    asyncio.run(run_tick())


if __name__ == "__main__":
    main()
