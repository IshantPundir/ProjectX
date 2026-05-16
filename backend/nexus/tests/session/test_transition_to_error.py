"""Tests for transition_to_error — atomic state→error transition."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.session.models import Session as SessionRow
from app.modules.session.service import transition_to_error
from tests.conftest import seed_minimal_session


@pytest.mark.asyncio
async def test_active_to_error_returns_true_and_writes_audit(db):
    """state='active' → 'error' transition succeeds; audit row written."""
    session, tenant_id = await seed_minimal_session(db, state="active")

    won = await transition_to_error(
        db,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_internal_error",
        correlation_id="corr-1",
        reason="engine_entrypoint",
    )
    await db.flush()

    assert won is True

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_internal_error"

    audit = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource == "session",
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_internal_error"
    assert audit.payload["reason"] == "engine_entrypoint"
    assert audit.payload["correlation_id"] == "corr-1"


@pytest.mark.asyncio
async def test_consented_to_error_succeeds(db):
    session, tenant_id = await seed_minimal_session(db, state="consented")

    won = await transition_to_error(
        db,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_session_config_invalid",
        correlation_id="corr-2",
        reason="engine_entrypoint",
    )
    await db.flush()

    assert won is True
    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_session_config_invalid"


@pytest.mark.asyncio
async def test_completed_state_is_not_clobbered(db):
    """A completed session must NOT be transitioned to error (no-clobber)."""
    session, tenant_id = await seed_minimal_session(db, state="completed")

    won = await transition_to_error(
        db,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_internal_error",
        correlation_id="corr-3",
        reason="reaper",
    )
    await db.flush()

    assert won is False

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "completed"
    assert refreshed.error_code is None

    # No audit row written.
    rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_error_state_is_idempotent_noop(db):
    """Calling transition_to_error on an already-errored row is a clean no-op."""
    session, tenant_id = await seed_minimal_session(db, state="error")

    won = await transition_to_error(
        db,
        session_id=session.id,
        tenant_id=tenant_id,
        error_code="engine_unresponsive",
        correlation_id="corr-4",
        reason="reaper",
    )
    await db.flush()

    assert won is False

    audit_rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalars().all()
    assert audit_rows == []
