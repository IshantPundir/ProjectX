"""Integration test: record_session_evidence persists SessionEvidence.

Validates:
- Atomic active→completed transition
- Correct result_status derivation (ok when questions_asked >= 1)
- Round-trip equality: SessionEvidence.model_validate(row.session_evidence_json) == original
- Idempotent retry: second call is a silent no-op, row stays completed
- result_status == "partial" when questions_asked == 0
- ValueError raised when the session row does not exist
- Evidence is ATTACHED (state preserved) when the session was externally terminated
  first (e.g. proctoring → 'terminated'), and that attach is idempotent
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text as sql_text

from app.modules.interview_runtime.evidence import (
    CompletionReason,
    EvidenceNote,
    EvidenceStance,
    EvidenceTexture,
    QuestionOutcome,
    QuestionRecord,
    QuestionTier,
    SessionEvidence,
    SessionMeta,
    SignalEvidence,
    SignalPriority,
    SignalType,
    Speaker,
    ThreadClosure,
    TimeSpan,
    TranscriptTurn,
    Word,
)
from app.modules.interview_runtime import record_session_evidence
from app.modules.session.models import Session as SessionRow
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)


async def _seed_active_session(db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (session_id, tenant_id, job_id, stage_id) for a session in state='active'.

    Uses the shared `make_assignment_with_stage` graph builder from conftest.
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
    return sess.id, tenant.id, assignment.job_posting_id, stage.id


def _build_minimal_evidence(
    session_id: uuid.UUID,
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    stage_id: uuid.UUID,
) -> SessionEvidence:
    """Construct a minimal but fully-round-trippable SessionEvidence."""
    now = datetime.now(UTC)
    span = TimeSpan(start_ms=0, end_ms=5000)

    note = EvidenceNote(
        seq=1,
        turn_ref="turn-001",
        signal="python_proficiency",
        stance=EvidenceStance.supports,
        texture=EvidenceTexture.concrete,
        quote="I built a data pipeline in Python with asyncio and SQLAlchemy.",
        span=span,
        from_question_id="q1",
        via_probe=False,
        retracts_seq=None,
    )

    signal_ev = SignalEvidence(
        signal="python_proficiency",
        signal_type=SignalType.competency,
        weight=2,
        priority=SignalPriority.required,
        knockout=False,
        provenance=__import__(
            "app.modules.interview_runtime.evidence", fromlist=["Provenance"]
        ).Provenance.asked_directly,
    )

    question_rec = QuestionRecord(
        question_id="q1",
        primary_signal="python_proficiency",
        tier=QuestionTier.core,
        outcome=QuestionOutcome.asked,
        closure=ThreadClosure.satisfied,
        asked_at_turn="turn-001",
        probes_used=[],
        probes_available=2,
        time_spent_s=45.0,
    )

    transcript_turn = TranscriptTurn(
        turn_ref="turn-001",
        speaker=Speaker.candidate,
        text="I built a data pipeline in Python with asyncio and SQLAlchemy.",
        span=span,
        pre_turn_gap_ms=800,
        words=[
            Word(text="I", start_ms=0, end_ms=100),
            Word(text="built", start_ms=110, end_ms=300),
        ],
        question_id="q1",
    )

    return SessionEvidence(
        meta=SessionMeta(
            session_id=str(session_id),
            job_id=str(job_id),
            candidate_id=str(candidate_id),
            stage_id=str(stage_id),
            started_at=now,
            ended_at=now,
            duration_s=300.0,
            time_budget_s=1800.0,
            completion=CompletionReason.completed,
            questions_asked=1,
            questions_core_total=3,
            questions_overflow_asked=0,
        ),
        signals=[signal_ev],
        notes=[note],
        questions=[question_rec],
        transcript=[transcript_turn],
        knockout=None,
    )


@pytest.mark.asyncio
async def test_record_session_evidence_transitions_to_completed(db) -> None:
    """Persists SessionEvidence, transitions session to completed, round-trips cleanly."""
    session_id, tenant_id, job_id, stage_id = await _seed_active_session(db)
    candidate_id = uuid.uuid4()  # not a FK on SessionEvidence.meta — any UUID works

    evidence = _build_minimal_evidence(session_id, job_id, candidate_id, stage_id)

    await record_session_evidence(
        db,
        tenant_id=tenant_id,
        evidence=evidence,
        correlation_id="test-corr",
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()

    assert row.state == "completed"
    assert row.result_status == "ok"  # questions_asked >= 1
    assert row.agent_completed_at is not None

    # Round-trip: the JSON stored in session_evidence_json must deserialise back
    # to an object equal to the original evidence.
    assert row.session_evidence_json is not None
    round_tripped = SessionEvidence.model_validate(row.session_evidence_json)
    assert round_tripped == evidence


@pytest.mark.asyncio
async def test_record_session_evidence_idempotent(db) -> None:
    """Calling record_session_evidence twice on the same session is a silent no-op."""
    session_id, tenant_id, job_id, stage_id = await _seed_active_session(db)
    candidate_id = uuid.uuid4()
    evidence = _build_minimal_evidence(session_id, job_id, candidate_id, stage_id)

    # First call — transitions active → completed.
    await record_session_evidence(
        db,
        tenant_id=tenant_id,
        evidence=evidence,
        correlation_id="test-corr-1",
    )

    # Second call — session is now `completed`; must be a silent no-op.
    await record_session_evidence(
        db,
        tenant_id=tenant_id,
        evidence=evidence,
        correlation_id="test-corr-2",
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.state == "completed"
    # session_evidence_json from the first write is still intact
    assert row.session_evidence_json is not None


@pytest.mark.asyncio
async def test_record_session_evidence_partial_status_when_no_questions(db) -> None:
    """result_status is 'partial' when evidence.meta.questions_asked == 0."""
    session_id, tenant_id, job_id, stage_id = await _seed_active_session(db)
    candidate_id = uuid.uuid4()

    # Build evidence with questions_asked=0 by overriding the meta field.
    base_evidence = _build_minimal_evidence(session_id, job_id, candidate_id, stage_id)
    zero_meta = base_evidence.meta.model_copy(update={"questions_asked": 0})
    evidence = base_evidence.model_copy(update={"meta": zero_meta})

    await record_session_evidence(
        db,
        tenant_id=tenant_id,
        evidence=evidence,
        correlation_id="test-corr-partial",
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.state == "completed"
    assert row.result_status == "partial"


@pytest.mark.asyncio
async def test_record_session_evidence_missing_session_raises(db) -> None:
    """ValueError is raised when evidence references a session_id that does not exist."""
    # Use a real tenant for the call but a random session_id that was never seeded.
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))

    ghost_session_id = uuid.uuid4()
    job_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    stage_id = uuid.uuid4()

    evidence = _build_minimal_evidence(ghost_session_id, job_id, candidate_id, stage_id)

    with pytest.raises(ValueError, match=str(ghost_session_id)):
        await record_session_evidence(
            db,
            tenant_id=tenant.id,
            evidence=evidence,
            correlation_id="test-corr-missing",
        )


@pytest.mark.asyncio
async def test_record_session_evidence_attaches_to_proctoring_terminated(db) -> None:
    """Externally terminated (e.g. proctoring → 'terminated'): the engine ATTACHES
    its evidence WITHOUT clobbering the terminal state — the report still gets the
    notes/transcript even though proctoring ended the screen first."""
    session_id, tenant_id, job_id, stage_id = await _seed_active_session(db)
    candidate_id = uuid.uuid4()
    evidence = _build_minimal_evidence(session_id, job_id, candidate_id, stage_id)

    # Proctoring terminated the session first (state='terminated' + an outcome).
    await db.execute(
        sql_text(
            "UPDATE sessions SET state='terminated', proctoring_outcome='multiple_faces' "
            f"WHERE id = '{session_id}'"
        )
    )
    await db.flush()

    await record_session_evidence(
        db, tenant_id=tenant_id, evidence=evidence, correlation_id="test-corr-proctor"
    )

    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    # Evidence attached…
    assert row.session_evidence_json is not None
    assert SessionEvidence.model_validate(row.session_evidence_json) == evidence
    assert row.agent_completed_at is not None
    # …but the proctoring terminal state + outcome are PRESERVED (not flipped to completed).
    assert row.state == "terminated"
    assert row.proctoring_outcome == "multiple_faces"

    # Idempotent: a re-call is a silent no-op and does not change anything.
    await record_session_evidence(
        db, tenant_id=tenant_id, evidence=evidence, correlation_id="test-corr-proctor-2"
    )
    row2 = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row2.state == "terminated"


async def test_record_session_evidence_attach_is_idempotent_when_already_recorded(db) -> None:
    """If evidence was already recorded (any state), a re-call is a silent no-op."""
    session_id, tenant_id, job_id, stage_id = await _seed_active_session(db)
    candidate_id = uuid.uuid4()
    evidence = _build_minimal_evidence(session_id, job_id, candidate_id, stage_id)

    # First call (active → completed) records the evidence.
    await record_session_evidence(
        db, tenant_id=tenant_id, evidence=evidence, correlation_id="c1"
    )
    # Now externally flip to 'terminated' AFTER evidence exists; re-call no-ops.
    await db.execute(
        sql_text(f"UPDATE sessions SET state='terminated' WHERE id = '{session_id}'")
    )
    await db.flush()
    await record_session_evidence(
        db, tenant_id=tenant_id, evidence=evidence, correlation_id="c2"
    )
    row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one()
    assert row.session_evidence_json is not None
    assert row.state == "terminated"  # the later external flip is preserved
