"""Service-layer functions for ATS connection lifecycle + sync log writers.

Connection-management endpoints (router.py) and the poll_ats_connection actor
(actors.py) both call into here.
"""
from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.crypto import encrypt_credentials_blob, encrypt_secret
from app.modules.ats.importer import SyncResult
from app.modules.ats.models import ATSConnection, ATSSyncLog
from app.modules.ats.registry import get_ats_adapter
from app.modules.audit import log_event


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
        # Use the stored interval. ``make_interval`` takes the seconds count
        # directly without text concat — sidesteps the asyncpg ``||``-typing
        # issue (operands must be text) and the SQLAlchemy ``text()`` parser
        # that treats ``::`` after a bound parameter as a stray colon.
        await db.execute(text(
            "UPDATE ats_connections "
            "SET next_poll_at = now() "
            "  + make_interval(secs => poll_interval_seconds + :j), "
            "poll_lock_acquired_at = NULL "
            "WHERE id = :i"
        ), {"i": connection_id, "j": j})
    else:
        await db.execute(text(
            "UPDATE ats_connections "
            "SET next_poll_at = now() + make_interval(secs => :s), "
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


async def create_connection(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    vendor: str,
    credentials: dict[str, Any],
    created_by: UUID,
) -> UUID:
    """Test credentials via adapter, then persist an ats_connections row.

    Flow:
      1. Build a temporary in-memory state (no DB write).
      2. Construct adapter; await ensure_authenticated().
         - on success: state.access_token / refresh_token / expiries are set.
         - on ATSCredentialsInvalidError / ATSAuthorizationError: propagate
           without persisting.
      3. Encrypt credentials + tokens; insert ats_connections row.
      4. Audit log: ats.connection.created (vendor only; never credentials).
    Returns the new connection id.
    """
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=tenant_id, vendor=vendor,
        credentials=credentials,
    )
    adapter = get_ats_adapter(state)
    await adapter.ensure_authenticated()
    # state was mutated by ensure_authenticated — access_token + expiries set

    row = ATSConnection(
        id=state.id,
        tenant_id=tenant_id,
        vendor=vendor,
        credentials_ciphertext=encrypt_credentials_blob(credentials),
        access_token_ciphertext=(
            encrypt_secret(state.access_token) if state.access_token else None
        ),
        refresh_token_ciphertext=(
            encrypt_secret(state.refresh_token) if state.refresh_token else None
        ),
        access_token_expires_at=state.access_token_expires_at,
        refresh_token_expires_at=state.refresh_token_expires_at,
        next_poll_at=datetime.now(tz=UTC),    # poll immediately
        poll_interval_seconds=900,
        active=True,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()

    await log_event(
        db, tenant_id=tenant_id, actor_id=created_by,
        actor_email="recruiter",
        action="ats.connection.created",
        resource="ats_connection", resource_id=row.id,
        payload={"vendor": vendor},   # NEVER credentials
    )
    return row.id


async def delete_connection(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Hard-delete an ats_connections row. CASCADE drops dependent
    sync_logs / mappings; explicit audit row is written BEFORE delete."""
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.deleted",
        resource="ats_connection", resource_id=connection_id,
        payload={"vendor": row.vendor},
    )
    await db.delete(row)
    await db.flush()


async def trigger_manual_sync(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Enqueue a poll_ats_connection actor immediately, bypassing next_poll_at.

    Caller is responsible for rate-limiting at the router layer (per root
    CLAUDE.md: 30/min per-IP, 12/hour per-tenant).
    """
    # Local import to avoid a service<->actors module cycle.
    from app.modules.ats.actors import poll_ats_connection

    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.sync.manually_triggered",
        resource="ats_connection", resource_id=connection_id,
        payload={"vendor": row.vendor},
    )
    poll_ats_connection.send(str(connection_id), str(tenant_id))


async def map_ats_user_to_internal(
    db: AsyncSession,
    *,
    connection_id: UUID,
    external_user_id: str,
    internal_user_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Set ats_user_mappings.internal_user_id for a specific external user.

    Audit: ats.user_mapping.created.
    """
    from app.modules.ats.models import ATSUserMapping

    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != tenant_id:
        return
    mapping = await db.scalar(
        select(ATSUserMapping).where(
            ATSUserMapping.tenant_id == tenant_id,
            ATSUserMapping.ats_vendor == conn.vendor,
            ATSUserMapping.external_user_id == external_user_id,
        )
    )
    if mapping is None:
        return
    mapping.internal_user_id = internal_user_id
    mapping.mapped_at = datetime.now(tz=UTC)
    mapping.mapped_by = actor_id
    await db.flush()
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.user_mapping.created",
        resource="ats_user_mapping", resource_id=mapping.id,
        payload={"external_user_id": external_user_id,
                 "internal_user_id": str(internal_user_id)},
    )
