"""Dramatiq actor: poll_ats_connection.

One actor invocation = one tenant's sync run, scoped to the new
job-driven single-mode orchestrator. The actor is enqueued ONLY by the
manual-sync endpoint (POST /api/ats/connections/{id}/sync) — there is no
scheduler in MVP.

Lifecycle:
  Phase A: load + decrypt state, open sync_log row, audit started.
  Phase B: ensure_authenticated() — may mutate tokens; persist on success.
           On ATSCredentialsInvalidError: disable connection + finalize +
           audit + raise.
  Phase C: acquire pg_try_advisory_xact_lock(connection_id) inside a fresh
           transaction. If the lock is unavailable, finalize the sync_log
           as 'failed' with phase='lock' and return (another sync is
           in-flight). Then run ATSSyncOrchestrator(adapter).run() in its
           own session chain. The lock is held for the duration of the
           outer transaction only (advisory_xact_lock); the orchestrator
           opens its own per-job transactions inside.

           Actually, advisory_xact_lock is bound to the transaction that
           acquires it. Since the orchestrator opens many transactions,
           we use ``pg_try_advisory_lock`` (session-scoped) at the actor
           entry and ``pg_advisory_unlock`` in a try/finally so the lock
           survives the orchestrator's internal transactions.

  Phase D: persist final state, close success log, audit completed.

Each phase opens its OWN bypass-RLS session so partial failures keep the
sync_log row in a consistent state.
"""
from __future__ import annotations

import uuid

import dramatiq
import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
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
from app.modules.ats.orchestrator import ATSSyncOrchestrator, ATSSyncResult
from app.modules.ats.registry import get_ats_adapter
from app.modules.ats.service import (
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
    # Auto-retry is wrong for this actor: the sync makes incremental progress
    # via per-job transactions, and the cursor is advanced atomically at the
    # END. A Dramatiq retry doesn't gain anything; it spawns a duplicate run
    # against the same advisory-lock slot and creates orphan `running` rows.
    # The recruiter manually re-triggers from the UI if a run is interrupted.
    max_retries=0,
    # Default time_limit is 10 minutes. A full first-sync at 0.5 qps with
    # hundreds of jobs and per-job submissions can easily run 1-2 hours.
    # Override to 8 hours — long enough for the largest tenants we plan to
    # support, short enough that a truly stuck actor still gets killed.
    time_limit=8 * 60 * 60 * 1000,
    queue_name="ats_poll",
)
async def poll_ats_connection(
    connection_id: str,
    tenant_id: str,
    actor_id: str,
) -> None:
    """Dramatiq entry point.

    actor_id is the recruiter who clicked Resync; flows end-to-end into
    audit rows for every event the orchestrator emits.
    """
    await _run_poll(connection_id, tenant_id, actor_id)


async def _run_poll(
    connection_id: str,
    tenant_id: str,
    actor_id: str,
) -> None:
    safe_tenant = str(uuid.UUID(tenant_id))
    safe_actor = str(uuid.UUID(actor_id))
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
        ) as span:
            try:
                await _do_poll(
                    uuid.UUID(connection_id),
                    uuid.UUID(tenant_id),
                    uuid.UUID(safe_actor),
                    correlation_id,
                    safe_tenant,
                )
            except ATSConnectionNotFoundError:
                span.set_status(Status(StatusCode.OK, "connection_gone"))
                logger.info(
                    "ats.poll.connection_gone",
                    connection_id=connection_id,
                    tenant_id=safe_tenant,
                    correlation_id=correlation_id,
                )
                return
    finally:
        structlog.contextvars.clear_contextvars()


def _advisory_lock_key(connection_id: uuid.UUID) -> int:
    """Build a 64-bit signed int hash from the connection UUID.

    Postgres advisory locks key on `bigint`. We hash the UUID's int form
    to a stable bigint via modulo. This deterministically maps each
    connection to one lock slot per cluster.
    """
    # int128 → bigint signed range [-2^63, 2^63 - 1].
    bigint_max = 1 << 63
    return (connection_id.int % (bigint_max * 2)) - bigint_max


async def _try_acquire_session_advisory_lock(
    db, key: int,
) -> bool:
    """Try to acquire a session-scoped advisory lock. Returns True on success."""
    row = await db.execute(
        text("SELECT pg_try_advisory_lock(:key)").bindparams(key=key),
    )
    return bool(row.scalar_one())


async def _release_session_advisory_lock(db, key: int) -> None:
    """Release the session-scoped advisory lock. Idempotent."""
    await db.execute(
        text("SELECT pg_advisory_unlock(:key)").bindparams(key=key),
    )


async def _do_poll(
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
    safe_tenant: str,
) -> None:
    # ---- Phase A: load state + open sync_log ----
    async with get_bypass_session() as db:
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
        )
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
            actor_id=actor_id,
            actor_email="recruiter",
            action="ats.sync.started",
            resource="ats_connection",
            resource_id=connection_id,
            payload={
                "vendor": state.vendor,
                "correlation_id": correlation_id,
            },
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
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
            )
            await disable_connection(db, connection_id, reason=str(exc))
            await finalize_sync_log_failure(
                db, sync_log_id, phase="auth", error_summary=str(exc),
            )
            await log_event(
                db,
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_email="recruiter",
                action="ats.connection.disabled",
                resource="ats_connection",
                resource_id=connection_id,
                payload={"reason": str(exc)[:200]},
            )
            await db.commit()
        raise
    except Exception as exc:
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
            )
            await finalize_sync_log_failure(
                db, sync_log_id, phase="auth",
                error_summary=f"{type(exc).__name__}: {exc}",
            )
            await db.commit()
        raise

    # Persist refreshed tokens immediately so we don't lose them mid-sync.
    async with get_bypass_session() as db:
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
        )
        await persist_connection_state(db, state)
        await db.commit()

    # ---- Phase C: acquire advisory lock + run orchestrator ----
    lock_key = _advisory_lock_key(connection_id)
    lock_holder = get_bypass_session()
    lock_db = await lock_holder.__aenter__()
    try:
        # Bind a tenant scope so the lock-holder session is consistent with
        # the rest. (advisory locks are tenant-agnostic but other queries
        # on this session would see RLS otherwise.)
        await lock_db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
        )
        acquired = await _try_acquire_session_advisory_lock(
            lock_db, lock_key,
        )
        if not acquired:
            async with get_bypass_session() as db:
                await db.execute(
                    text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
                )
                await finalize_sync_log_failure(
                    db, sync_log_id, phase="lock",
                    error_summary="sync_already_running",
                )
                await db.commit()
            logger.info(
                "ats.poll.lock_unavailable",
                connection_id=str(connection_id),
            )
            return

        # Orphan-row cleanup. We're holding the advisory lock now, which
        # means no other actor is processing this connection. Any other
        # ats_sync_logs row in `status='running'` is therefore a stranded
        # record from a previous worker that died mid-sync (SIGTERM during
        # `docker compose restart`, OOM kill, crash) before it could
        # finalize. Mark them failed so the UI stops showing fake
        # in-flight syncs and so the in-flight pre-check in
        # `service.trigger_manual_sync` doesn't reject the next legitimate
        # trigger. We do NOT touch our own row (`sync_log_id`).
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
            )
            result = await db.execute(
                text(
                    "UPDATE ats_sync_logs "
                    "   SET status='failed', "
                    "       completed_at=now(), "
                    "       error_phase='abandoned', "
                    "       error_summary='worker died mid-sync; "
                    "superseded by redelivered run' "
                    " WHERE connection_id = :cid "
                    "   AND status = 'running' "
                    "   AND id <> :self_id"
                ).bindparams(cid=connection_id, self_id=sync_log_id),
            )
            await db.commit()
            cleaned = result.rowcount or 0
        if cleaned > 0:
            logger.info(
                "ats.poll.orphan_rows_cleaned",
                connection_id=str(connection_id),
                rows_cleaned=cleaned,
            )

        # Run the orchestrator.
        orch = ATSSyncOrchestrator(
            adapter,
            connection_id=connection_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_email="recruiter",
            action_source="manual",
            sync_log_id=sync_log_id,
        )
        try:
            sync_result: ATSSyncResult = await orch.run()
        except ATSRateLimitedError as exc:
            async with get_bypass_session() as db:
                await db.execute(
                    text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
                )
                await finalize_sync_log_partial(
                    db,
                    sync_log_id,
                    ATSSyncResult(),     # empty counts; orchestrator died early
                    error_summary=str(exc),
                )
                await log_event(
                    db,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_email="recruiter",
                    action="ats.sync.partial",
                    resource="ats_connection",
                    resource_id=connection_id,
                    payload={
                        "reason": "rate_limited",
                        "retry_after_seconds": exc.retry_after_seconds,
                    },
                )
                await db.commit()
            logger.info(
                "ats.poll.rate_limited",
                retry_after_seconds=exc.retry_after_seconds,
            )
            return  # NO Dramatiq retry — recruiter re-triggers manually
        except ATSPermanentError as exc:
            async with get_bypass_session() as db:
                await db.execute(
                    text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
                )
                await finalize_sync_log_failure(
                    db, sync_log_id, phase="sync",
                    error_summary=str(exc),
                )
                await log_event(
                    db,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_email="recruiter",
                    action="ats.sync.failed",
                    resource="ats_connection",
                    resource_id=connection_id,
                    payload={"reason": str(exc)[:300]},
                )
                await db.commit()
            raise  # lands in DLQ for visibility

        # ---- Phase D: close log on success ----
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant}'"),
            )
            await persist_connection_state(db, state)
            await finalize_sync_log_success(db, sync_log_id, sync_result)
            await db.commit()
        logger.info(
            "ats.poll.completed",
            entity_counts=sync_result.entity_counts(),
        )
    finally:
        try:
            await _release_session_advisory_lock(lock_db, lock_key)
        finally:
            await lock_holder.__aexit__(None, None, None)
        await adapter.aclose()
