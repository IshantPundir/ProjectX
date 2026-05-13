"""Service-layer functions for ATS connection lifecycle + sync log writers.

Connection-management endpoints (router.py) and the poll_ats_connection actor
(actors.py) both call into here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
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
    if log is None:
        logger.warning("ats.sync_log.finalize_skipped", log_id=str(log_id), status="success")
        return
    log.status = "success"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    await db.flush()


async def finalize_sync_log_partial(
    db: AsyncSession, log_id: UUID, sync_result: SyncResult, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    if log is None:
        logger.warning(
            "ats.sync_log.finalize_skipped",
            log_id=str(log_id), status="partial", error_summary=error_summary[:200],
        )
        return
    log.status = "partial"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    log.error_summary = error_summary[:1000]  # truncate
    await db.flush()


async def finalize_sync_log_failure(
    db: AsyncSession, log_id: UUID, *, phase: str, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    if log is None:
        logger.warning(
            "ats.sync_log.finalize_skipped",
            log_id=str(log_id), status="failed", phase=phase,
            error_summary=error_summary[:200],
        )
        return
    log.status = "failed"
    log.completed_at = datetime.now(tz=UTC)
    log.error_phase = phase
    log.error_summary = error_summary[:1000]
    await db.flush()


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
        # next_poll_at + poll_interval_seconds are vestigial — the scheduler
        # was removed. Manual sync is the only path. Column is NOT NULL, so
        # we satisfy it with the column defaults.
        next_poll_at=datetime.now(tz=UTC),
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
    *,
    phase_filter: list[str] | None = None,
) -> None:
    """Enqueue a poll_ats_connection actor immediately, bypassing next_poll_at.

    ``phase_filter`` — optional explicit list of phase names. Forwarded
    verbatim to the actor; the importer maps it to a set. ``None`` means
    "run all five phases" (the cron default).

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
        payload={"vendor": row.vendor, "phase_filter": phase_filter},
    )
    poll_ats_connection.send(str(connection_id), str(tenant_id), phase_filter)


async def update_job_status_filter(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    status_ids: list[int],
    names: list[str],
) -> None:
    """Persist the job-status filter on a connection; drop jobs cursor if widened.

    Widen-detection: any id in ``status_ids`` not present in the prior
    ``job_status_filter.ids`` triggers a reset of
    ``last_synced_cursors.jobs``. Narrowing (only removing ids) keeps the
    cursor — re-pulling previously-included rows would be wasted work.

    Always writes an ``ats.connection.job_status_filter_updated`` audit row.
    """
    if not status_ids:
        raise ValueError("status_ids must be non-empty")
    if len(status_ids) != len(names):
        raise ValueError("status_ids and names length mismatch")

    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return

    prior = row.job_status_filter or {}
    prior_ids = set(prior.get("ids", []))
    new_ids = set(status_ids)
    widened = bool(new_ids - prior_ids)

    row.job_status_filter = {"ids": list(status_ids), "names": list(names)}
    if widened:
        cursors = dict(row.last_synced_cursors or {})
        cursors.pop("jobs", None)
        row.last_synced_cursors = cursors

    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.job_status_filter_updated",
        resource="ats_connection", resource_id=connection_id,
        payload={
            "prior_ids": sorted(prior_ids),
            "new_ids":   sorted(new_ids),
            "widened":   widened,
        },
    )
    await db.flush()
