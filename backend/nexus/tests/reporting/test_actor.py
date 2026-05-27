"""Tests for the score_session_report Dramatiq actor (Task 17).

DB-safety contract:
- ``actors.get_bypass_session`` is patched to yield the conftest ``db`` fixture
  session, which is already bound to a test-transaction that rolls back after
  each test. No dev-DB access ever occurs.
- ``actors.build_report`` is patched to return a minimal ReportRead — no LLM
  calls, no network IO.
- ``persist_report`` runs for real against the test DB (via the patched session).

Two invariants verified:
1. Score path — actor calls build_report once and creates a ready SessionReport.
2. Idempotency — when a ready row already exists, build_report is NOT called again.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.modules.reporting.models import SessionReport
from app.modules.reporting.schemas import ReportRead, SummaryOut
from app.modules.session.models import Session as SessionRow

# ---------------------------------------------------------------------------
# Minimal ReportRead returned by the mocked build_report
# ---------------------------------------------------------------------------

_FAKE_REPORT = ReportRead(
    verdict="advance",
    verdict_reason="strong candidate",
    overall_score=85,
    overall_coverage=0.9,
    overall_confidence="high",
    dimension_scores={},
    knockout_results=[],
    signal_scorecards=[],
    question_scorecards=[],
    summary=SummaryOut(headline="Strong candidate — recommended for advancement."),
    status="ready",
    engine_version="v2",
)


def _make_bypass_session_patcher(db):
    """Return an async context manager that yields the injected ``db`` session.

    This shim allows us to patch ``actors.get_bypass_session`` so that
    _score_session_report_async uses the test-transaction session instead of
    opening a real connection to the dev DB.

    The actor calls ``await db.execute(text("SET LOCAL ..."))`` on the yielded
    session — AsyncSession.execute() accepts text() so this works without any
    special override.
    """

    @asynccontextmanager
    async def _shim():
        yield db

    return _shim


# ---------------------------------------------------------------------------
# Seed helper — builds the minimum FK chain for a completed v2 session
# ---------------------------------------------------------------------------


async def _seed_completed_v2_session(db):
    """Create tenant → user → assignment+stage → session (completed, v2 marker).

    Returns (session_id, tenant_id, assignment_id).

    The session row carries:
    - state = 'completed'
    - raw_result_json with coverage_summary (v2 marker) and no audit_envelope_ref
    - transcript = []

    A minimal confirmed StageQuestionBank + one StageQuestion are seeded so the
    actor's bank-loading path succeeds and the full actor path (including
    build_report) is exercised.  A confirmed JobPostingSignalSnapshot is also
    seeded so signal_metadata loading succeeds.
    """
    from datetime import UTC, datetime

    from app.modules.candidates.models import CandidateJobAssignment
    from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
    from app.modules.question_bank.models import StageQuestion, StageQuestionBank
    from tests.conftest import (
        create_test_client,
        create_test_user,
        make_assignment_with_stage,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    # Locate the job created by make_assignment_with_stage
    asgn = (
        await db.execute(
            select(CandidateJobAssignment).where(
                CandidateJobAssignment.id == assignment.id
            )
        )
    ).scalar_one()

    job = (
        await db.execute(
            select(JobPosting).where(
                JobPosting.id == asgn.job_posting_id
            )
        )
    ).scalar_one()

    # Insert a minimal confirmed signal snapshot so the actor can load signal_metadata
    now = datetime.now(UTC)
    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "Python experience",
                "type": "experience",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "screen",
                "evaluation_method": "verbal_response",
                "evaluation_hint": None,
            }
        ],
        seniority_level="mid",
        role_summary="Backend engineer",
        prompt_version="v1",
        confirmed_at=now,
        confirmed_by=user.id,
    )
    db.add(snapshot)
    await db.flush()

    # Insert a minimal question bank + one question so bank-loading succeeds
    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    question = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Tell me about your Python experience.",
        signal_values=["Python experience"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric={
            "below_bar": "No Python experience",
            "meets_bar": "Some Python experience",
            "excellent": "Deep Python expertise",
        },
        evaluation_hint="",
        question_kind="technical_scenario",
        difficulty="medium",
    )
    db.add(question)
    await db.flush()

    # Create a completed v2 session row
    raw_result_json = {
        "session_id": str(uuid.uuid4()),
        "coverage_summary": {"Python experience": "sufficient"},  # v2 marker
        "audit_envelope_ref": None,
        "questions_asked": 1,
        "total_probes_fired": 0,
    }
    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state="completed",
        raw_result_json=raw_result_json,
        transcript=[],
        questions_asked=1,
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()

    return sess.id, tenant.id, assignment.id


# ---------------------------------------------------------------------------
# Test 1: Happy-path — builds and persists report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_scores_and_persists_report(db):
    """Actor calls build_report once and persists a ready SessionReport row."""
    from app.modules.reporting.actors import _score_session_report_async

    session_id, tenant_id, assignment_id = await _seed_completed_v2_session(db)

    bypass_patcher = _make_bypass_session_patcher(db)

    with (
        patch(
            "app.modules.reporting.actors.get_bypass_session",
            bypass_patcher,
        ),
        patch(
            "app.modules.reporting.actors.build_report",
            new_callable=AsyncMock,
            return_value=_FAKE_REPORT,
        ) as mock_build,
    ):
        await _score_session_report_async(session_id, tenant_id, "c1")

    # build_report must have been called exactly once
    mock_build.assert_called_once()

    # A SessionReport row must now exist with status=ready
    row = (
        await db.execute(
            select(SessionReport).where(
                SessionReport.session_id == session_id,
                SessionReport.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None, "SessionReport row should have been created"
    assert row.status == "ready"
    assert row.verdict == "advance"
    assert row.version == 1


# ---------------------------------------------------------------------------
# Test 2: Idempotency — skips when a ready row already exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_idempotent_skip_when_ready(db):
    """When a ready SessionReport row already exists, build_report is NOT called."""
    from datetime import UTC, datetime

    from app.modules.reporting.actors import _score_session_report_async

    session_id, tenant_id, assignment_id = await _seed_completed_v2_session(db)

    # Pre-seed a ready SessionReport row to simulate a previous successful run
    existing_report = SessionReport(
        session_id=session_id,
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        version=1,
        status="ready",
        verdict="reject",
        engine_version="v2",
        generated_at=datetime.now(UTC),
    )
    db.add(existing_report)
    await db.flush()

    bypass_patcher = _make_bypass_session_patcher(db)

    with (
        patch(
            "app.modules.reporting.actors.get_bypass_session",
            bypass_patcher,
        ),
        patch(
            "app.modules.reporting.actors.build_report",
            new_callable=AsyncMock,
            return_value=_FAKE_REPORT,
        ) as mock_build,
    ):
        await _score_session_report_async(session_id, tenant_id, "c2", force=False)

    # build_report must NOT have been called (idempotent skip)
    mock_build.assert_not_called()

    # The existing row must be unchanged
    row = (
        await db.execute(
            select(SessionReport).where(
                SessionReport.session_id == session_id,
                SessionReport.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    assert row.status == "ready"
    assert row.verdict == "reject"   # unchanged from the pre-seeded value
    assert row.version == 1


# ---------------------------------------------------------------------------
# _resolve_envelope unit tests (Task 1)
# ---------------------------------------------------------------------------


import json
from pathlib import Path
from app.modules.reporting.actors import _resolve_envelope


def test_resolve_envelope_prefers_config_dir(tmp_path, monkeypatch):
    sid = "c7173674-7795-4268-b4ab-829ad45b801b"
    (tmp_path / f"{sid}.json").write_text(json.dumps({"events": [{"kind": "x"}]}))
    monkeypatch.setattr("app.modules.reporting.actors.settings.engine_event_log_dir", str(tmp_path))
    env = _resolve_envelope(session_id=sid, stored_ref="/tmp/engine-events/does-not-exist.json")
    assert env["events"] == [{"kind": "x"}]


def test_resolve_envelope_falls_back_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.modules.reporting.actors.settings.engine_event_log_dir", str(tmp_path))
    env = _resolve_envelope(session_id="nope", stored_ref=None)
    assert env == {"events": []}
