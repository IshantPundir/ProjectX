"""record_engine_heartbeat: the engine's liveness pulse for the reaper.

Sets last_engine_heartbeat_at on a running (state='active') session; a no-op on a
session that has already terminated (so a stale beat can't resurrect liveness).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text as sql_text

from app.modules.interview_runtime import record_engine_heartbeat
from app.modules.session.models import Session as SessionRow
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)


async def _seed_session(db, *, state: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))
    assignment, stage = await make_assignment_with_stage(db, tenant, user)
    sess = SessionRow(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state=state,
        state_changed_at=datetime.now(UTC),
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()
    return sess.id, tenant.id


@pytest.mark.asyncio
async def test_heartbeat_sets_timestamp_on_active_session(db) -> None:
    session_id, tenant_id = await _seed_session(db, state="active")

    await record_engine_heartbeat(db, session_id=session_id, tenant_id=tenant_id)

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.last_engine_heartbeat_at is not None


@pytest.mark.asyncio
async def test_heartbeat_is_noop_on_terminated_session(db) -> None:
    session_id, tenant_id = await _seed_session(db, state="completed")

    await record_engine_heartbeat(db, session_id=session_id, tenant_id=tenant_id)

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.last_engine_heartbeat_at is None  # gated on state='active'


@pytest.mark.asyncio
async def test_heartbeat_is_tenant_scoped(db) -> None:
    session_id, _tenant_id = await _seed_session(db, state="active")
    wrong_tenant = uuid.uuid4()

    await record_engine_heartbeat(db, session_id=session_id, tenant_id=wrong_tenant)

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.last_engine_heartbeat_at is None  # cross-tenant write must not land
