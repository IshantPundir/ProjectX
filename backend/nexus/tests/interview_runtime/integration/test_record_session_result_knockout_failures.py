"""Integration test: record_session_result persists knockout_failures."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text as sql_text

from app.modules.interview_runtime import (
    KnockoutFailure,
    SessionResult,
    record_session_result,
)
from app.modules.session.models import Session as SessionRow
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)


async def _seed_active_session(db) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (session_id, tenant_id) for a session in state='active'.

    Uses the shared `make_assignment_with_stage` graph builder from conftest
    (org_unit -> job -> instance -> stage -> candidate -> assignment) and
    attaches a session in state='active' on top.
    """
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
        state="active",
        state_changed_at=datetime.now(UTC),
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()
    return sess.id, tenant.id


def _result_with_knockouts(session_id: uuid.UUID) -> SessionResult:
    return SessionResult(
        session_id=str(session_id),
        job_title="CS Specialist",
        stage_id=str(uuid.uuid4()),
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=420.0,
        questions_asked=3,
        questions_skipped=0,
        total_probes_fired=1,
        question_results=[],
        full_transcript=[],
        completed_at=datetime.now(UTC).isoformat(),
        knockout_failures=[
            KnockoutFailure(
                question_id="q3",
                reason="Cannot work UK shift hours.",
                signal_values=["uk_shift"],
                occurred_at_ms=120_000,
            )
        ],
    )


@pytest.mark.asyncio
async def test_writes_knockout_failures_column(db) -> None:
    session_id, tenant_id = await _seed_active_session(db)
    result = _result_with_knockouts(session_id)

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.flush()

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert len(row.knockout_failures) == 1
    assert row.knockout_failures[0]["question_id"] == "q3"
    assert row.knockout_failures[0]["signal_values"] == ["uk_shift"]
    assert row.knockout_failures[0]["reason"] == "Cannot work UK shift hours."
    assert row.knockout_failures[0]["occurred_at_ms"] == 120_000
    assert row.state == "completed"


@pytest.mark.asyncio
async def test_idempotent_retry_preserves_knockout_failures(db) -> None:
    session_id, tenant_id = await _seed_active_session(db)
    result = _result_with_knockouts(session_id)

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.flush()

    # Second call — session is now `completed`, must be a silent no-op.
    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.flush()

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert len(row.knockout_failures) == 1


@pytest.mark.asyncio
async def test_empty_knockout_failures_writes_empty_list(db) -> None:
    session_id, tenant_id = await _seed_active_session(db)
    result = SessionResult(
        session_id=str(session_id),
        job_title="CS Specialist",
        stage_id=str(uuid.uuid4()),
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=420.0,
        questions_asked=3,
        questions_skipped=0,
        total_probes_fired=1,
        question_results=[],
        full_transcript=[],
        completed_at=datetime.now(UTC).isoformat(),
        # knockout_failures defaults to []
    )

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=result,
        correlation_id="test-corr-1",
    )
    await db.flush()

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.knockout_failures == []
