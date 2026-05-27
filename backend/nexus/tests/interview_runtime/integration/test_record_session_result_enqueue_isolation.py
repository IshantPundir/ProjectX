"""record_session_result must DURABLY complete the session even if the
report-scoring enqueue (Dramatiq -> Redis) fails.

Regression test for the 2026-05-27 incident: the enqueue was coupled into the
critical state-transition transaction, BEFORE the commit, so a broker failure
(the engine process targeting the wrong/default Redis) rolled back the
`state='completed'` write — the session was left `active` and the reaper later
mislabeled it `engine_unresponsive` ("Failed") despite a complete interview.

The enqueue is a best-effort side-effect; its failure must never roll back or
raise out of the completion path.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text as sql_text

import app.modules.reporting as reporting
from app.modules.interview_runtime import SessionResult, record_session_result
from app.modules.session.models import Session as SessionRow
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)


async def _seed_active_session(db) -> tuple[uuid.UUID, uuid.UUID]:
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


def _result(session_id: uuid.UUID) -> SessionResult:
    return SessionResult(
        session_id=str(session_id),
        job_title="Jr. FDE",
        stage_id=str(uuid.uuid4()),
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=600.0,
        questions_asked=5,
        questions_skipped=0,
        total_probes_fired=2,
        full_transcript=[],
        completed_at=datetime.now(UTC).isoformat(),
        knockout_failures=[],
        # Non-None coverage_summary => the report-scoring enqueue path fires.
        coverage_summary={"communication": "sufficient"},
        audit_envelope_ref=None,
    )


@pytest.mark.asyncio
async def test_completion_survives_report_enqueue_failure(db, monkeypatch) -> None:
    """A failing report-scoring enqueue must NOT raise or roll back the
    durable `state='completed'` transition."""
    session_id, tenant_id = await _seed_active_session(db)

    def _boom(*_a, **_k):  # simulates Redis unreachable from the engine process
        raise RuntimeError("redis broker unreachable")

    monkeypatch.setattr(reporting.score_session_report, "send", _boom)

    # Must return normally — the enqueue is best-effort, not part of the contract.
    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=_result(session_id),
        correlation_id="corr-isolation",
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.state == "completed"
    assert row.agent_completed_at is not None
    assert row.result_status == "ok"


@pytest.mark.asyncio
async def test_successful_enqueue_still_completes(db, monkeypatch) -> None:
    """Happy path: enqueue is invoked exactly once after a successful transition."""
    session_id, tenant_id = await _seed_active_session(db)
    calls: list[tuple] = []
    monkeypatch.setattr(
        reporting.score_session_report,
        "send",
        lambda *a, **k: calls.append((a, k)),
    )

    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=_result(session_id),
        correlation_id="corr-ok",
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.state == "completed"
    assert len(calls) == 1
    assert calls[0][0][0] == str(session_id)  # first positional arg = session_id
