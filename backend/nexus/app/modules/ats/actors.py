"""Dramatiq actor: poll_ats_connection.

One actor invocation = one tenant's sync run. The scheduler tick
(app/cli/ats_tick.py — Task 24) enqueues one message per
(connection_id, tenant_id) when ats_connections.next_poll_at <= now().

Lifecycle (mirrors app/modules/jd/actors.py pattern):
  Phase A: load + decrypt state, open sync_log row, audit started
  Phase B: ensure_authenticated() — may mutate tokens; persist on success.
           On ATSCredentialsInvalidError: disable connection + finalize +
           audit + raise.
  Phase C: ATSImporter().sync_tenant(adapter) — five phases, partial-tolerant.
           On ATSRateLimitedError: advance next_poll_at + close partial + return.
           On ATSPermanentError: finalize_failure + raise (DLQ).
           ATSTransientError propagates → Dramatiq retries.
  Phase D: persist final state, advance next_poll_at, close success log,
           audit completed.

Each phase opens its OWN bypass-RLS session so partial failures keep the
sync_log row in a consistent state.
"""
from __future__ import annotations

import uuid

import dramatiq
import structlog
from opentelemetry import trace
from sqlalchemy import text

from app.database import get_bypass_session
from app.modules.ats.connection import (
    load_connection_state,
    persist_connection_state,
)
from app.modules.ats.errors import (
    ATSConnectionNotFoundError,
    ATSCredentialsInvalidError,
    ATSPermanentError,
    ATSRateLimitedError,
)
from app.modules.ats.importer import ATSImporter
from app.modules.ats.registry import get_ats_adapter
from app.modules.ats.service import (
    advance_next_poll_at,
    create_sync_log_row,
    disable_connection,
    finalize_sync_log_failure,
    finalize_sync_log_partial,
    finalize_sync_log_success,
)
from app.modules.audit import log_event

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


@dramatiq.actor(
    max_retries=3,
    min_backoff=30_000,
    max_backoff=600_000,
    queue_name="ats_poll",
)
async def poll_ats_connection(connection_id: str, tenant_id: str) -> None:
    """Dramatiq entry point. Thin wrapper — real work in `_run_poll` so it can
    be tested without standing up the Dramatiq broker.

    Dramatiq serializes every actor argument as JSON-compatible primitives,
    so the signature takes ``str`` (not ``UUID``) on the wire.
    """
    await _run_poll(connection_id, tenant_id)


async def _run_poll(connection_id: str, tenant_id: str) -> None:
    safe_tenant = str(uuid.UUID(tenant_id))
    correlation_id = f"ats-{uuid.uuid4()}"

    structlog.contextvars.bind_contextvars(
        connection_id=connection_id,
        tenant_id=safe_tenant,
        correlation_id=correlation_id,
        queue="ats_poll",
    )

    try:
        with tracer.start_as_current_span(
            "ats.poll",
            attributes={
                "connection_id": connection_id,
                "tenant_id": safe_tenant,
            },
        ):
            await _do_poll(
                uuid.UUID(connection_id),
                uuid.UUID(tenant_id),
                correlation_id,
                safe_tenant,
            )
    except ATSConnectionNotFoundError:
        # The connection row was deleted between scheduler tick and actor
        # execution (or mid-sync). DB cascade has already cleaned up
        # ats_sync_logs / ats_client_mappings / ats_user_mappings /
        # ats_job_recruiter_assignments. There is nothing meaningful to
        # finalize and no point retrying — the connection is gone.
        # Returning cleanly here is the contract: Dramatiq will not retry
        # because no exception escapes.
        logger.info(
            "ats.poll.connection_gone",
            connection_id=connection_id,
            tenant_id=safe_tenant,
            correlation_id=correlation_id,
        )
        return
    finally:
        structlog.contextvars.clear_contextvars()


async def _do_poll(
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: str,
    safe_tenant: str,
) -> None:
    # ---- Phase A: load state + open sync_log ----
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
        state = await load_connection_state(db, connection_id)
        sync_log_id = await create_sync_log_row(
            db,
            connection_id=connection_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            actor_email="ats-scheduler",
            action="ats.sync.started",
            resource="ats_connection",
            resource_id=connection_id,
            payload={"vendor": state.vendor, "correlation_id": correlation_id},
        )
        await db.commit()

    adapter = get_ats_adapter(state)

    # ---- Phase B: ensure_authenticated (may refresh tokens) ----
    try:
        with tracer.start_as_current_span("ats.poll.auth"):
            await adapter.ensure_authenticated()
    except ATSCredentialsInvalidError as exc:
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'")
            )
            await disable_connection(db, connection_id, reason=str(exc))
            await finalize_sync_log_failure(
                db, sync_log_id, phase="auth", error_summary=str(exc),
            )
            await log_event(
                db,
                tenant_id=tenant_id,
                actor_id=None,
                actor_email="ats-scheduler",
                action="ats.connection.disabled",
                resource="ats_connection",
                resource_id=connection_id,
                payload={"reason": str(exc)[:200]},
            )
            await db.commit()
        raise

    # Persist refreshed tokens immediately so we don't lose them mid-sync.
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
        await persist_connection_state(db, state)
        await db.commit()

    # ---- Phase C: run sync ----
    try:
        sync_result = await ATSImporter().sync_tenant(adapter)
    except ATSRateLimitedError as exc:
        # sync_tenant attaches the partial SyncResult to the exception
        # so the sync log reflects what DID succeed before the rate
        # limit fired. Falls back to an empty result if attribute is
        # missing (defensive — shouldn't happen with current code).
        partial = getattr(exc, "partial_result", None) or ATSImporter._empty_partial_result()
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'")
            )
            await advance_next_poll_at(
                db,
                connection_id,
                interval_seconds=exc.retry_after_seconds,
                jitter_seconds=0,
            )
            await finalize_sync_log_partial(
                db,
                sync_log_id,
                partial,
                error_summary=str(exc),
            )
            await db.commit()
        logger.info(
            "ats.poll.rate_limited",
            retry_after_seconds=exc.retry_after_seconds,
            entity_counts=partial.entity_counts(),
        )
        return  # NO retry — next tick handles it
    except ATSPermanentError as exc:
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'")
            )
            await finalize_sync_log_failure(
                db, sync_log_id, phase="sync", error_summary=str(exc),
            )
            await db.commit()
        raise  # lands in DLQ for visibility
    # ATSTransientError (and any other Exception) propagates → Dramatiq retries
    # with exp backoff per the actor decorator's retry policy.

    # ---- Phase D: persist state + advance + close log ----
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"))
        await persist_connection_state(db, state)
        await advance_next_poll_at(db, connection_id)
        await finalize_sync_log_success(db, sync_log_id, sync_result)
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            actor_email="ats-scheduler",
            action="ats.sync.completed",
            resource="ats_connection",
            resource_id=connection_id,
            payload={
                "vendor": state.vendor,
                "entity_counts": sync_result.entity_counts(),
                "correlation_id": correlation_id,
            },
        )
        await db.commit()

    logger.info(
        "ats.poll.completed",
        entity_counts=sync_result.entity_counts(),
    )
