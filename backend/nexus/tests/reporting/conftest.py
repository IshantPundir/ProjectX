"""Reporting-test fixtures.

Builds on the root conftest factory helpers (create_test_client / _user /
make_assignment_with_stage / seed_minimal_session) so the share-actor test
exercises the real DB + FK chain the actor reads.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.reporting.models import ReportShare, SessionReport
from tests.conftest import seed_minimal_session


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db: AsyncSession):
    """Alias for the root ``db`` fixture (share-actor tests use this name)."""
    yield db


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_share(db: AsyncSession) -> ReportShare:
    """Build session -> ready SessionReport -> pending ReportShare.

    Reuses ``seed_minimal_session`` (client + user + org_unit + job + stage +
    candidate + assignment + session). The candidate name is set to a real
    value so the actor's email subject / filename are realistic.
    """
    session, tenant_id = await seed_minimal_session(db)

    # Give the candidate a real name (seed_minimal_session names it "Charlie").
    assignment = (
        await db.execute(
            select(CandidateJobAssignment).where(
                CandidateJobAssignment.id == session.assignment_id,
            )
        )
    ).scalar_one()
    candidate = (
        await db.execute(
            select(Candidate).where(Candidate.id == assignment.candidate_id)
        )
    ).scalar_one()
    candidate.name = "Ishant Pundir"
    await db.flush()

    report = SessionReport(
        tenant_id=tenant_id,
        session_id=session.id,
        assignment_id=session.assignment_id,
        version=1,
        status="ready",
        engine_version="v3",
        verdict="advance",
        verdict_reason="Clears the bar.",
        overall_score=88,
        overall_coverage=0.9,
        overall_confidence="high",
        dimension_scores={
            "overall": {
                "score": 88, "tier_label": "Strong", "tone": "ok",
                "confidence": "high", "coverage": 0.9,
            },
            "technical": {
                "score": 88, "tier_label": "Strong", "tone": "ok",
                "confidence": "high", "coverage": 0.9,
            },
        },
        signal_scorecards=[],
        question_scorecards=[],
        summary={
            "quick_summary": "Solid screen.",
            "decision": {
                "headline": "Recommend advance.",
                "why_positive": {"title": "Clear reasoning", "body": "Answered well."},
                "why_negative": {"title": "", "body": ""},
            },
            "strengths": [],
            "concerns": [],
            "methodology": {"note": "", "charity_flags": []},
        },
        generated_at=datetime.now(UTC),
    )
    db.add(report)
    await db.flush()

    share = ReportShare(
        tenant_id=tenant_id,
        session_id=session.id,
        report_id=report.id,
        recipient_email="client@acme.com",
        status="pending",
    )
    db.add(share)
    await db.flush()

    return share
