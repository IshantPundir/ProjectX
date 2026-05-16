"""Regression tests for the engine entrypoint failure handler.

Verifies that an uncaught exception inside _run_entrypoint produces:
  1. A DB transition to state='error' with the right error_code.
  2. An audit row recording the failure.
  3. A best-effort session_outcome='error' attribute publish.

The original incident (2026-05-16, session c795c0b4-08eb-…) was a
pydantic ValidationError raised by build_session_config that crashed
the engine silently. This test fixes that bug class as
non-regressable.

Implementation note
-------------------
`_handle_entrypoint_failure` opens its own `get_bypass_session()` context
manager — a completely independent DB connection that would not see
uncommitted rows from the test's rollback-isolated `db` fixture.  We
monkeypatch `app.modules.interview_engine.agent.get_bypass_session` to
yield the test's `db` session instead (the same pattern used in
`tests/test_question_banks_events.py`).  This keeps the seed data,
the handler's UPDATE, and the audit INSERT all inside the per-test
rollback transaction — nothing persists after the test.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.interview_engine.agent import (
    _best_effort_publish_outcome_attribute,
    _handle_entrypoint_failure,
)
from app.modules.session.models import Session as SessionRow
from tests.conftest import seed_minimal_session


class _StubModel(BaseModel):
    name: str = Field(min_length=5)


def _make_validation_error() -> Exception:
    try:
        _StubModel(name="x")
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("expected ValidationError")


def _fake_job_context() -> MagicMock:
    """A minimal stand-in for livekit.agents.JobContext."""
    ctx = MagicMock()
    ctx.room = MagicMock()
    ctx.room.isconnected = MagicMock(return_value=True)
    ctx.room.local_participant.set_attributes = AsyncMock()
    ctx.connect = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_validation_error_during_build_session_config(db, monkeypatch):
    """The actual 2026-05-16 bug shape — durable failure path engaged."""
    session, tenant_id = await seed_minimal_session(db, state="active")
    await db.flush()

    # Patch get_bypass_session to yield the test db so the handler's UPDATE
    # and audit INSERT stay inside the per-test rollback transaction.
    @asynccontextmanager
    async def _fake_bypass():
        yield db

    monkeypatch.setattr(
        "app.modules.interview_engine.agent.get_bypass_session",
        _fake_bypass,
    )

    ctx = _fake_job_context()

    await _handle_entrypoint_failure(
        exc=_make_validation_error(),
        ctx=ctx,
        session_id=session.id,
        tenant_uuid=tenant_id,
        correlation_id="corr-vald",
    )
    await db.flush()

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_session_config_invalid"

    audit = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_session_config_invalid"
    assert audit.payload["reason"] == "engine_entrypoint"
    assert audit.payload["correlation_id"] == "corr-vald"

    ctx.room.local_participant.set_attributes.assert_awaited_once_with(
        {"session_outcome": "error"}
    )


@pytest.mark.asyncio
async def test_outcome_publish_failure_is_swallowed():
    """If set_attributes raises, the handler logs and moves on — does NOT propagate."""
    ctx = _fake_job_context()
    ctx.room.local_participant.set_attributes.side_effect = RuntimeError("LK boom")

    # Must not raise.
    await _best_effort_publish_outcome_attribute(ctx)


@pytest.mark.asyncio
async def test_pre_connect_failure_still_writes_db_row(db, monkeypatch):
    """Handler runs DB transition even when ctx.connect() raises later."""
    session, tenant_id = await seed_minimal_session(db, state="consented")
    await db.flush()

    @asynccontextmanager
    async def _fake_bypass():
        yield db

    monkeypatch.setattr(
        "app.modules.interview_engine.agent.get_bypass_session",
        _fake_bypass,
    )

    ctx = _fake_job_context()
    ctx.room.isconnected.return_value = False
    ctx.connect.side_effect = RuntimeError("no router to room")

    await _handle_entrypoint_failure(
        exc=RuntimeError("connect failed upstream"),
        ctx=ctx,
        session_id=session.id,
        tenant_uuid=tenant_id,
        correlation_id="corr-conn",
    )
    await db.flush()

    refreshed = (await db.execute(
        select(SessionRow).where(SessionRow.id == session.id)
    )).scalar_one()
    assert refreshed.state == "error"
    assert refreshed.error_code == "engine_internal_error"

    # The audit row must be written regardless of which downstream step
    # fails — DB transition + audit are one atomic unit inside
    # transition_to_error.
    audit = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == session.id,
            AuditLog.action == "session.errored",
        )
    )).scalar_one()
    assert audit.payload["error_code"] == "engine_internal_error"
    assert audit.payload["reason"] == "engine_entrypoint"
    assert audit.payload["correlation_id"] == "corr-conn"

    # set_attributes was never reached because connect() raised first.
    ctx.room.local_participant.set_attributes.assert_not_awaited()
