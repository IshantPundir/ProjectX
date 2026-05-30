"""Stuck-session reaper.

Single-flight via pg_try_advisory_lock — concurrent ticks across replicas
return immediately on lock contention. Targets state='active' rows whose
LAST SIGN OF LIFE — COALESCE(last_engine_heartbeat_at, state_changed_at) —
is older than reaper_stuck_threshold_seconds AND have no agent_completed_at.

Liveness, not duration: the running engine pulses last_engine_heartbeat_at
periodically (agent.py), so a legitimately long interview that is still
pulsing is NEVER reaped, while a dead engine (pulse goes stale) or one that
never connected (no pulse → falls back to state_changed_at) is reaped once
that timestamp ages past the threshold.

The in-process entrypoint handler (Phase 2) covers the happy-error path
where the engine catches its own exception. This reaper covers the cases
the in-process handler can't: SIGKILL/OOM/process crash before the
try/except runs, LK Cloud dispatch never arriving, etc.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy import text as sql_text

from app.config import settings
from app.database import get_bypass_session
from app.modules.session.models import Session as SessionRow
from app.modules.session.service import transition_to_error

log = structlog.get_logger("session.reaper")

_REAPER_LOCK_KEY = "stuck_session_reaper"


async def run_stuck_session_reaper() -> None:
    """One tick of the stuck-session sweeper."""
    async with get_bypass_session() as db:
        # Single-flight across replicas via a TRANSACTION-scoped advisory lock.
        # `pg_try_advisory_xact_lock` is held only for the current transaction and
        # is auto-released when get_bypass_session()'s `session.begin()` block
        # commits (or rolls back) at exit. This avoids the session-level
        # `pg_advisory_lock` + manual unlock pattern, which required a manual
        # `db.commit()` mid-run that closed the begin()-managed transaction and
        # then raised `InvalidRequestError` on the finally-block unlock.
        acquired = (
            await db.execute(
                sql_text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                {"k": _REAPER_LOCK_KEY},
            )
        ).scalar_one()
        if not acquired:
            log.debug("reaper.lock.contended")
            return

        cutoff = datetime.now(UTC) - timedelta(
            seconds=settings.reaper_stuck_threshold_seconds
        )
        # Last sign of life: a fresh engine heartbeat keeps a long-but-live
        # interview safe; NULL (never connected) falls back to state_changed_at.
        last_sign_of_life = func.coalesce(
            SessionRow.last_engine_heartbeat_at, SessionRow.state_changed_at
        )
        stuck = (
            await db.execute(
                select(SessionRow.id, SessionRow.tenant_id).where(
                    SessionRow.state == "active",
                    last_sign_of_life < cutoff,
                    SessionRow.agent_completed_at.is_(None),
                )
            )
        ).all()

        transitioned = 0
        for row in stuck:
            won = await transition_to_error(
                db,
                session_id=row.id,
                tenant_id=row.tenant_id,
                error_code="engine_unresponsive",
                correlation_id=f"reaper-{row.id}",
                reason="reaper",
            )
            if won:
                transitioned += 1

        # get_bypass_session()'s `session.begin()` commits on clean exit (and
        # rolls back on exception); the xact-scoped advisory lock releases with
        # it. No manual commit or unlock is needed.
        #
        # Log at INFO when work happened, DEBUG on idle ticks. At a 5-min
        # interval, idle-tick INFO would be ~288 lines/day per replica of pure
        # noise — the no-op case isn't worth filtering downstream.
        log_fn = log.info if stuck else log.debug
        log_fn(
            "reaper.tick",
            stuck_found=len(stuck),
            transitioned=transitioned,
            threshold_seconds=settings.reaper_stuck_threshold_seconds,
        )
