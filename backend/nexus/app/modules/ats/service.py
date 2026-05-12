"""Service-layer functions for ATS connection lifecycle + sync log writers.

Connection-management endpoints (router.py) and the poll_ats_connection actor
(actors.py) both call into here.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ats.importer import SyncResult
from app.modules.ats.models import ATSConnection, ATSSyncLog


logger = structlog.get_logger()


async def create_sync_log_row(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    correlation_id: str,
) -> UUID:
    """Insert an ats_sync_logs row with status='running' and return its id."""
    row = ATSSyncLog(
        connection_id=connection_id,
        tenant_id=tenant_id,
        started_at=datetime.now(tz=UTC),
        status="running",
        correlation_id=correlation_id,
        entity_counts={},
    )
    db.add(row)
    await db.flush()
    return row.id


async def finalize_sync_log_success(
    db: AsyncSession, log_id: UUID, sync_result: SyncResult,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    log.status = "success"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    await db.flush()


async def finalize_sync_log_partial(
    db: AsyncSession, log_id: UUID, sync_result: SyncResult, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    log.status = "partial"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    log.error_summary = error_summary[:1000]  # truncate
    await db.flush()


async def finalize_sync_log_failure(
    db: AsyncSession, log_id: UUID, *, phase: str, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    log.status = "failed"
    log.completed_at = datetime.now(tz=UTC)
    log.error_phase = phase
    log.error_summary = error_summary[:1000]
    await db.flush()


async def advance_next_poll_at(
    db: AsyncSession,
    connection_id: UUID,
    interval_seconds: int | None = None,
    jitter_seconds: int = 60,
) -> None:
    """next_poll_at = now() + interval + jitter(0, jitter_seconds).

    interval_seconds: if None, uses the connection's stored poll_interval_seconds.
    """
    j = random.randint(0, jitter_seconds)
    if interval_seconds is None:
        # Use the stored interval
        await db.execute(text(
            "UPDATE ats_connections "
            "SET next_poll_at = now() + (poll_interval_seconds || ' seconds')::interval "
            "+ (:j || ' seconds')::interval, "
            "poll_lock_acquired_at = NULL "
            "WHERE id = :i"
        ), {"i": connection_id, "j": j})
    else:
        await db.execute(text(
            "UPDATE ats_connections "
            "SET next_poll_at = now() + (:s || ' seconds')::interval, "
            "poll_lock_acquired_at = NULL "
            "WHERE id = :i"
        ), {"i": connection_id, "s": interval_seconds + j})


async def disable_connection(
    db: AsyncSession, connection_id: UUID, reason: str,
) -> None:
    """Mark a connection inactive. Recruiter must reconnect via UI."""
    row = await db.get(ATSConnection, connection_id)
    if row is None:
        return
    row.active = False
    row.disabled_reason = reason[:500]
    row.disabled_at = datetime.now(tz=UTC)
    await db.flush()
