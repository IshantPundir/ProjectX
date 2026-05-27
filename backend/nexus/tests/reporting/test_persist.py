"""DB-level integration tests for persist_report (Task 9 restore).

These tests call persist_report against the real test DB session to verify:
  (a) A new row is created with verdict / overall_score / dimension_scores.
  (b) A second call with force=False is an idempotent no-op (version stays 1).
  (c) force=True overwrites the row and bumps version to 2.

The test DB is the ``projectx_test`` Postgres database, reached via the
per-test-transaction ``db`` fixture from conftest.py.  No LLM calls are made.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reporting.models import SessionReport
from app.modules.reporting.schemas import (
    DecisionOut,
    MethodologyOut,
    ReportRead,
    ScoreOut,
    WhyColumn,
)
from app.modules.reporting.service import persist_report
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
    seed_minimal_session,
)


# ---------------------------------------------------------------------------
# Minimal valid ReportRead (all required fields populated)
# ---------------------------------------------------------------------------


def _make_report(**overrides) -> ReportRead:
    """Build a minimal but fully valid ReportRead for persist tests."""
    base = dict(
        verdict="advance",
        verdict_reason="Excellent demonstration.",
        overall_score=85,
        overall_coverage=0.9,
        overall_confidence="high",
        decision=DecisionOut(
            headline="Strong — recommend advance.",
            why_positive=WhyColumn(title="Clear reasoning", body="Answered well."),
            why_negative=WhyColumn(title="", body=""),
        ),
        scores={
            "overall": ScoreOut(
                score=85,
                tier_label="Strong",
                tone="ok",
                confidence="high",
                coverage=0.9,
            ),
            "technical": ScoreOut(
                score=85,
                tier_label="Strong",
                tone="ok",
                confidence="high",
                coverage=0.9,
            ),
            "behavioral": ScoreOut(
                score=80,
                tier_label="Strong",
                tone="ok",
                confidence="medium",
                coverage=0.8,
            ),
            "communication": ScoreOut(
                score=70,
                tier_label="Meets Bar",
                tone="ok",
                confidence="medium",
                coverage=1.0,
            ),
        },
        quick_summary="Candidate performed well overall.",
        strengths=[],
        concerns=[],
        questions=[],
        methodology=MethodologyOut(note="", charity_flags=[]),
        signal_assessments=[],
        status="ready",
        engine_version="v2",
        version=1,
    )
    base.update(overrides)
    return ReportRead(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_report_creates_row(db: AsyncSession):
    """persist_report creates a new row when none exists.

    Asserts:
    - A SessionReport row is created for the session.
    - verdict / overall_score / dimension_scores are stored correctly.
    - version is 1, status is 'ready'.
    """
    session_row, tenant_id = await seed_minimal_session(db)
    report = _make_report()

    created = await persist_report(
        db,
        session_id=session_row.id,
        tenant_id=tenant_id,
        assignment_id=session_row.assignment_id,
        report=report,
        force=False,
    )

    assert created is not None
    assert created.version == 1
    assert created.status == "ready"
    assert created.verdict == "advance"
    assert created.overall_score == 85

    # dimension_scores should store the ScoreOut-shaped dict keyed by dimension
    assert isinstance(created.dimension_scores, dict)
    assert "overall" in created.dimension_scores
    assert created.dimension_scores["overall"]["score"] == 85
    assert created.dimension_scores["technical"]["confidence"] == "high"

    # Verify the row is actually in the DB
    fetched = (
        await db.execute(
            select(SessionReport).where(
                SessionReport.session_id == session_row.id,
                SessionReport.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_persist_report_force_false_is_noop(db: AsyncSession):
    """A second persist_report call with force=False is idempotent.

    Asserts:
    - The same row is returned.
    - version stays 1 (not incremented).
    - The returned row ID equals the first call's ID.
    """
    session_row, tenant_id = await seed_minimal_session(db)
    report = _make_report()

    first = await persist_report(
        db,
        session_id=session_row.id,
        tenant_id=tenant_id,
        assignment_id=session_row.assignment_id,
        report=report,
        force=False,
    )

    # Call again with a different verdict — should be ignored
    report2 = _make_report(verdict="reject", overall_score=40)
    second = await persist_report(
        db,
        session_id=session_row.id,
        tenant_id=tenant_id,
        assignment_id=session_row.assignment_id,
        report=report2,
        force=False,
    )

    assert second.id == first.id
    assert second.version == 1           # still version 1 — not bumped
    assert second.verdict == "advance"   # original value — not overwritten
    assert second.overall_score == 85    # original value — not overwritten


@pytest.mark.asyncio
async def test_persist_report_force_true_overwrites_and_bumps_version(db: AsyncSession):
    """force=True overwrites field values and bumps version to 2.

    Asserts:
    - verdict / overall_score are updated to the new report's values.
    - version is incremented from 1 → 2.
    """
    session_row, tenant_id = await seed_minimal_session(db)
    report = _make_report()

    first = await persist_report(
        db,
        session_id=session_row.id,
        tenant_id=tenant_id,
        assignment_id=session_row.assignment_id,
        report=report,
        force=False,
    )
    assert first.version == 1

    # Overwrite with a reject report
    report2 = _make_report(
        verdict="reject",
        verdict_reason="Failed knockout.",
        overall_score=30,
        overall_confidence="high",
    )
    second = await persist_report(
        db,
        session_id=session_row.id,
        tenant_id=tenant_id,
        assignment_id=session_row.assignment_id,
        report=report2,
        force=True,
    )

    assert second.id == first.id          # same row
    assert second.version == 2            # bumped from 1 → 2
    assert second.verdict == "reject"     # overwritten
    assert second.verdict_reason == "Failed knockout."
    assert second.overall_score == 30     # overwritten
