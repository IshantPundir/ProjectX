"""record_session_result — happy + idempotent + state-guard + audit + cross-tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.models import AuditLog, Session as SessionRow
from app.modules.interview_runtime.errors import SessionNotActiveError
from app.modules.interview_runtime.schemas import (
    QuestionResult,
    SessionResult,
    TranscriptEntry,
)
from app.modules.interview_runtime.service import record_session_result
from tests.test_interview_runtime_config import _seed_full_session_chain

pytestmark = pytest.mark.asyncio


def _make_result(
    session_id: UUID,
    *,
    questions_asked: int = 8,
    total_probes_fired: int = 3,
) -> SessionResult:
    """Construct a minimal SessionResult that passes Pydantic validation."""
    return SessionResult(
        session_id=str(session_id),
        job_title="Senior Backend Engineer",
        stage_id=str(uuid.uuid4()),
        stage_type="ai_screening",
        candidate_name="Alex",
        duration_seconds=600.0,
        questions_asked=questions_asked,
        questions_skipped=1,
        total_probes_fired=total_probes_fired,
        question_results=[],
        full_transcript=[
            TranscriptEntry(role="agent", text="Hi", timestamp_ms=0),
            TranscriptEntry(role="candidate", text="Hello", timestamp_ms=1500),
        ],
        completed_at="2026-04-29T12:00:00Z",
    )


async def _seed_active_session(db, **kwargs) -> tuple[UUID, UUID]:
    """Seed a full chain and force state='active'."""
    session_id, tenant_id = await _seed_full_session_chain(db, **kwargs)
    await db.execute(
        update(SessionRow)
        .where(SessionRow.id == session_id)
        .values(state="active")
    )
    await db.flush()
    return session_id, tenant_id


async def _seed_completed_session(db, **kwargs) -> tuple[UUID, UUID]:
    """Seed a full chain and force state='completed' WITH agent_completed_at."""
    session_id, tenant_id = await _seed_full_session_chain(db, **kwargs)
    await db.execute(
        update(SessionRow)
        .where(SessionRow.id == session_id)
        .values(state="completed", agent_completed_at=datetime.now(UTC))
    )
    await db.flush()
    return session_id, tenant_id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_happy_completes_session(db):
    """The first call against an active session writes all columns + transitions state."""
    session_id, tenant_id = await _seed_active_session(db)
    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=_make_result(session_id),
        jti=uuid.uuid4(),
    )

    sess = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert sess.state == "completed"
    assert sess.questions_asked == 8
    assert sess.probes_fired == 3
    assert sess.agent_completed_at is not None
    assert sess.result_status == "ok"
    assert sess.transcript[0]["text"] == "Hi"
    assert sess.transcript[1]["role"] == "candidate"


async def test_partial_status_when_no_questions_asked(db):
    """questions_asked == 0 derives result_status='partial', not 'ok'."""
    session_id, tenant_id = await _seed_active_session(db)
    await record_session_result(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        result=_make_result(session_id, questions_asked=0),
        jti=uuid.uuid4(),
    )
    sess = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert sess.result_status == "partial"


# ---------------------------------------------------------------------------
# Idempotent retry
# ---------------------------------------------------------------------------

async def test_idempotent_re_post_on_completed_session(db):
    """Re-posting against an already-completed session is a silent no-op (no error, no second audit)."""
    session_id, tenant_id = await _seed_active_session(db)
    await record_session_result(
        db, session_id=session_id, tenant_id=tenant_id,
        result=_make_result(session_id), jti=uuid.uuid4(),
    )
    # Second call with a different jti — should NOT raise.
    await record_session_result(
        db, session_id=session_id, tenant_id=tenant_id,
        result=_make_result(session_id), jti=uuid.uuid4(),
    )

    # Audit log should have exactly one row (no duplicate from the retry).
    audit_rows = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == "engine.session.completed")
        )
    ).scalars().all()
    relevant = [r for r in audit_rows if r.resource_id == session_id]
    assert len(relevant) == 1, (
        f"expected exactly 1 audit row for the session, got {len(relevant)}"
    )


# ---------------------------------------------------------------------------
# State guard
# ---------------------------------------------------------------------------

async def test_state_guard_rejects_non_active_pre_completed(db):
    """A 'completed' session with agent_completed_at=NULL is treated as a real
    state violation, not idempotent — manually clear the timestamp first."""
    session_id, tenant_id = await _seed_completed_session(db)
    await db.execute(
        update(SessionRow).where(SessionRow.id == session_id).values(agent_completed_at=None)
    )
    await db.flush()

    with pytest.raises(SessionNotActiveError):
        await record_session_result(
            db, session_id=session_id, tenant_id=tenant_id,
            result=_make_result(session_id), jti=uuid.uuid4(),
        )


async def test_state_guard_rejects_created(db):
    """'created' state (default after seeding without an _active overlay) is a violation."""
    session_id, tenant_id = await _seed_full_session_chain(db)
    # state defaults to 'created' — don't transition it.
    with pytest.raises(SessionNotActiveError):
        await record_session_result(
            db, session_id=session_id, tenant_id=tenant_id,
            result=_make_result(session_id), jti=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# Missing row
# ---------------------------------------------------------------------------

async def test_unknown_session_returns_value_error(db):
    """A random session_id with a real tenant_id raises ValueError('not found')."""
    _seed_id, tenant_id = await _seed_active_session(db)
    with pytest.raises(ValueError, match="not found"):
        await record_session_result(
            db, session_id=uuid.uuid4(), tenant_id=tenant_id,
            result=_make_result(uuid.uuid4()), jti=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# Cross-tenant
# ---------------------------------------------------------------------------

async def test_cross_tenant_rejected_as_not_found(db):
    """A real session_id with a wrong tenant_id raises ValueError('not found')."""
    session_id, _ = await _seed_active_session(db)
    other_tenant = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await record_session_result(
            db, session_id=session_id, tenant_id=other_tenant,
            result=_make_result(session_id), jti=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# Audit row payload
# ---------------------------------------------------------------------------

async def test_audit_log_written_with_jti_prefix(db):
    """The audit row carries jti_prefix (first 8 chars), questions_asked, result_status."""
    session_id, tenant_id = await _seed_active_session(db)
    jti = uuid.uuid4()
    await record_session_result(
        db, session_id=session_id, tenant_id=tenant_id,
        result=_make_result(session_id, questions_asked=5),
        jti=jti,
    )

    rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "engine.session.completed",
                AuditLog.resource_id == session_id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id is None
    assert row.actor_email is None
    assert row.resource == "session"
    assert row.payload["jti_prefix"] == str(jti)[:8]
    assert row.payload["questions_asked"] == 5
    assert row.payload["result_status"] == "ok"
    # CLAUDE.md PII rule: no full jti, no email anywhere in audit payload.
    assert "jti" not in row.payload or len(row.payload["jti"]) <= 8
