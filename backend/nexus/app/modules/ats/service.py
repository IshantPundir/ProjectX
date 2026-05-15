"""Service-layer functions for ATS connection lifecycle + sync log writers.

Connection-management endpoints (router.py) and the poll_ats_connection actor
(actors.py) both call into here.

Sync model — cursor-based incremental, manual-trigger only. Per the
`2026-05-14-job-scoped-ats-sync-design.md` cutover, the legacy 5-phase
importer is replaced by a single-trigger job-driven orchestrator; the
``phases`` parameter is removed and ``trigger_manual_sync`` accepts no
filter argument. Force-full-rescan is the separate ``reset_cursor``
endpoint.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.crypto import encrypt_credentials_blob, encrypt_secret
from app.modules.ats.errors import ATSPermanentError
from app.modules.ats.models import ATSConnection, ATSSyncLog
from app.modules.ats.orchestrator import ATSSyncResult
from app.modules.ats.registry import get_ats_adapter
from app.modules.audit import log_event

logger = structlog.get_logger()


# ─────────────────── Errors surfaced as 4xx ────────────────────────


class JobStatusFilterEmptyError(ATSPermanentError):
    """No job statuses selected — cannot run a sync. Router 422s."""


class SyncAlreadyRunningError(ATSPermanentError):
    """A sync is already in-flight for this connection. Router 409s."""


# ──────────────────── Sync log lifecycle ───────────────────────────


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
    db: AsyncSession, log_id: UUID, sync_result: ATSSyncResult,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    if log is None:
        logger.warning(
            "ats.sync_log.finalize_skipped",
            log_id=str(log_id), status="success",
        )
        return
    log.status = "success"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    await db.flush()


async def finalize_sync_log_partial(
    db: AsyncSession,
    log_id: UUID,
    sync_result: ATSSyncResult,
    error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    if log is None:
        logger.warning(
            "ats.sync_log.finalize_skipped",
            log_id=str(log_id),
            status="partial",
            error_summary=error_summary[:200],
        )
        return
    log.status = "partial"
    log.completed_at = datetime.now(tz=UTC)
    log.entity_counts = sync_result.entity_counts()
    log.error_summary = error_summary[:1000]
    await db.flush()


async def finalize_sync_log_failure(
    db: AsyncSession, log_id: UUID, *, phase: str, error_summary: str,
) -> None:
    log = await db.get(ATSSyncLog, log_id)
    if log is None:
        logger.warning(
            "ats.sync_log.finalize_skipped",
            log_id=str(log_id),
            status="failed",
            phase=phase,
            error_summary=error_summary[:200],
        )
        return
    log.status = "failed"
    log.completed_at = datetime.now(tz=UTC)
    log.error_phase = phase
    log.error_summary = error_summary[:1000]
    await db.flush()


# ─────────────────── Connection lifecycle ──────────────────────────


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
      3. Encrypt credentials + tokens; insert ats_connections row with
         ``status_sync_mode='advisory'`` (default).
      4. Audit log: ats.connection.created (vendor only; never credentials).
    Returns the new connection id.
    """
    state = ATSConnectionState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        vendor=vendor,
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
            encrypt_secret(state.refresh_token)
            if state.refresh_token else None
        ),
        access_token_expires_at=state.access_token_expires_at,
        refresh_token_expires_at=state.refresh_token_expires_at,
        last_synced_at=None,
        tenant_timezone=None,
        status_sync_mode="advisory",
        active=True,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=created_by,
        actor_email="recruiter",
        action="ats.connection.created",
        resource="ats_connection",
        resource_id=row.id,
        payload={"vendor": vendor},   # NEVER credentials
    )
    return row.id


async def delete_connection(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Hard-delete an ats_connections row.

    CASCADE drops dependent sync_logs / job_assignments / stage_mappings /
    advisory_actions. Explicit audit row is written BEFORE delete.
    """
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.deleted",
        resource="ats_connection",
        resource_id=connection_id,
        payload={"vendor": row.vendor},
    )
    await db.delete(row)
    await db.flush()


# ─────────────────── Sync trigger ──────────────────────────────────


async def trigger_manual_sync(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Enqueue a poll_ats_connection actor for an immediate sync.

    Pre-conditions enforced here (all 4xx-mapped at the router):
      - connection exists and belongs to tenant
      - connection is active
      - connection has a non-empty job_status_filter (else 422)
      - no sync currently `running` for this connection (else 409)

    Per-tenant 5/hour ceiling is the router-layer rate limit; this helper
    just validates pre-conditions and enqueues the actor.
    """
    # Local import to avoid a service<->actors module cycle.
    from app.modules.ats.actors import poll_ats_connection

    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    if not row.active:
        raise ATSPermanentError("connection is not active")

    filter_ids: list[Any] = (
        (row.job_status_filter or {}).get("ids", []) or []
    )
    if not filter_ids:
        raise JobStatusFilterEmptyError(
            "No job statuses selected — configure the filter first",
        )

    # Check for an in-flight sync on this connection. The orchestrator's
    # advisory lock is the authoritative concurrency guard; this pre-check
    # surfaces a friendly 409 before we even enqueue. The advisory lock
    # backstops it for the race window between this query and Dramatiq
    # picking up the message.
    from sqlalchemy import select

    in_flight = await db.execute(
        select(ATSSyncLog.id)
        .where(ATSSyncLog.connection_id == connection_id)
        .where(ATSSyncLog.status == "running")
        .limit(1),
    )
    if in_flight.scalar_one_or_none() is not None:
        raise SyncAlreadyRunningError("A sync is already running")

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email="recruiter",
        action="ats.sync.manually_triggered",
        resource="ats_connection",
        resource_id=connection_id,
        payload={"vendor": row.vendor},
    )
    poll_ats_connection.send(
        str(connection_id), str(tenant_id), str(actor_id),
    )


# ─────────────────── Filter config ─────────────────────────────────


async def update_job_status_filter(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    status_ids: list[int],
    names: list[str],
) -> None:
    """Persist the job-status filter on a connection.

    Empty filter is rejected (ValueError → 422 at router). On a widen
    (a status id was added that wasn't in the prior set), the cursor is
    cleared so the next sync re-walks the full filter.
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
        # Force the next sync to pull the full filter; the records that were
        # outside the prior filter haven't been seen before.
        row.last_synced_at = None

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.job_status_filter_updated",
        resource="ats_connection",
        resource_id=connection_id,
        payload={
            "prior_ids": sorted(prior_ids),
            "new_ids":   sorted(new_ids),
            "widened":   widened,
        },
    )
    await db.flush()


# ──────────────── Sync-mode + force-full-rescan ─────────────────────


async def set_status_sync_mode(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    mode: str,
) -> None:
    """Update connection.status_sync_mode. Mode must be one of the three
    DB-enforced values: 'advisory', 'mirror', 'one_way'.
    """
    if mode not in ("advisory", "mirror", "one_way"):
        raise ValueError(f"invalid status_sync_mode: {mode!r}")
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    prior_mode = row.status_sync_mode
    row.status_sync_mode = mode
    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.status_sync_mode_changed",
        resource="ats_connection",
        resource_id=connection_id,
        payload={"prior": prior_mode, "new": mode},
    )
    await db.flush()


async def reset_cursor(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    reason: str,
) -> None:
    """Clear last_synced_at so the next manual sync does a full filter walk.

    Existing rows are diffed in place; nothing is deleted.
    """
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    row.last_synced_at = None
    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.cursor_reset",
        resource="ats_connection",
        resource_id=connection_id,
        payload={"reason": (reason or "")[:500]},
    )
    await db.flush()


# ──────────────── Quarantined-job retry ─────────────────────────────


async def retry_job_import(
    db: AsyncSession,
    *,
    job_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
) -> None:
    """Clear import_quarantined_at + reset retry count on a quarantined job.

    The next manual sync re-attempts the import. Authorisation (jobs.edit
    on the job's org_unit) is enforced at the router.
    """
    from app.modules.jd.models import JobPosting

    job = await db.get(JobPosting, job_id)
    if job is None or job.tenant_id != tenant_id:
        return
    if job.import_quarantined_at is None and job.import_retry_count == 0:
        return  # idempotent no-op
    job.import_retry_count = 0
    job.import_quarantined_at = None
    job.import_last_error = None
    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email="recruiter",
        action="ats.job.import_quarantine_cleared",
        resource="job_posting",
        resource_id=job_id,
        payload={"external_id": job.external_id},
    )
    await db.flush()
