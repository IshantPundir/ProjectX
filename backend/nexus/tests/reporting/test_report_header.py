"""Tests for ReportHeader schema + skills_from_assessments + attach_report_header join.

Pure-function tests (no DB): Steps 1-3 (schema defaults, skills derivation).
Router composition test (DB required): Step 4 (join populates header on the endpoint).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reporting.assets import skills_from_assessments
from app.modules.reporting.schemas import ReportHeader, SignalAssessmentOut


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def _sa(signal: str, level: str, weight: int) -> SignalAssessmentOut:
    return SignalAssessmentOut(
        signal=signal,
        type="competency",
        weight=weight,
        knockout=False,
        priority="required",
        provenance="asked_directly",
        level=level,
        score=None,
    )


# ---------------------------------------------------------------------------
# Schema tests (no DB)
# ---------------------------------------------------------------------------


def test_report_header_schema_defaults():
    h = ReportHeader(
        candidate_name="Punar", job_title="EMM Engineer", stage_label="AI Screening"
    )
    assert h.skills == [] and h.candidate_email is None


def test_report_header_all_fields():
    h = ReportHeader(
        candidate_name="Arjun",
        candidate_email="arjun@example.com",
        candidate_title="Senior Accountant",
        candidate_location="Mumbai, India",
        company_name="Acme Corp",
        job_title="SRE",
        job_location="Bangalore",
        work_arrangement="Remote",
        stage_label="Round 1",
        session_started_at="2026-06-19T10:00:00+00:00",
        duration_seconds=1800,
        skills=["Python", "Kubernetes"],
        reference_photo_url="https://example.com/photo.jpg",
    )
    assert h.candidate_name == "Arjun"
    assert h.duration_seconds == 1800
    assert len(h.skills) == 2
    assert h.company_name == "Acme Corp"
    assert h.candidate_title == "Senior Accountant"
    assert h.job_location == "Bangalore"
    assert h.work_arrangement == "Remote"


def test_report_header_new_fields_default_none():
    h = ReportHeader(candidate_name="X", job_title="Y", stage_label="Z")
    assert h.company_name is None
    assert h.candidate_title is None
    assert h.candidate_location is None
    assert h.job_location is None
    assert h.work_arrangement is None


# ---------------------------------------------------------------------------
# skills_from_assessments tests (no DB)
# ---------------------------------------------------------------------------


def test_skills_are_demonstrated_signals_by_weight():
    aa = [
        _sa("Intune", "strong", 3),
        _sa("Comms", "thin", 2),
        _sa("CondAccess", "solid", 1),
        _sa("Identity", "absent", 2),
    ]
    assert skills_from_assessments(aa) == ["Intune", "CondAccess"]


def test_skills_cap():
    aa = [_sa(f"S{i}", "strong", 10 - i) for i in range(10)]
    assert len(skills_from_assessments(aa, cap=4)) == 4


def test_skills_sorted_by_weight_descending():
    aa = [
        _sa("Low", "solid", 1),
        _sa("High", "strong", 5),
        _sa("Mid", "solid", 3),
    ]
    result = skills_from_assessments(aa)
    assert result == ["High", "Mid", "Low"]


def test_skills_empty_assessments():
    assert skills_from_assessments([]) == []


def test_skills_excludes_thin_and_absent():
    aa = [
        _sa("Thin", "thin", 10),
        _sa("Absent", "absent", 9),
        _sa("NotReached", "not_reached", 8),
    ]
    assert skills_from_assessments(aa) == []


# ---------------------------------------------------------------------------
# Router composition test — header appears on the endpoint response (DB join)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_by_session_includes_header(db: AsyncSession):
    """get_report_by_session returns header.candidate_name and header.job_title
    populated by attach_report_header (DB join over sessions → candidates / jobs / stages)."""
    from app.database import get_tenant_db
    from app.main import app
    from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
    from app.modules.auth.schemas import TokenPayload
    from app.modules.reporting.models import SessionReport
    from tests.conftest import create_test_client, create_test_user, seed_minimal_session

    session_row, tenant_id = await seed_minimal_session(db)

    report = SessionReport(
        tenant_id=tenant_id,
        session_id=session_row.id,
        assignment_id=session_row.assignment_id,
        version=1,
        status="ready",
        engine_version="v3",
        verdict="advance",
        verdict_reason="Clear bar.",
        overall_score=85,
        overall_coverage=0.9,
        overall_confidence="high",
        dimension_scores={
            "overall": {"score": 85, "tier_label": "Strong", "tone": "ok",
                        "confidence": "high", "coverage": 0.9},
            "technical": {"score": 85, "tier_label": "Strong", "tone": "ok",
                          "confidence": "high", "coverage": 0.9},
        },
        signal_scorecards=[],
        question_scorecards=[],
        summary={
            "decision": {
                "headline": "Recommend advance.",
                "why_positive": {"title": "Good", "body": "Answered well."},
                "why_negative": {"title": "", "body": ""},
            },
            "quick_summary": "Solid screen.",
            "strengths": [],
            "concerns": [],
            "methodology": {"note": "", "charity_flags": []},
        },
        generated_at=datetime.now(UTC),
    )
    db.add(report)
    await db.flush()

    # Load the user created by seed_minimal_session
    user_row = (
        await db.execute(
            sqlalchemy.select(
                __import__(
                    "app.modules.auth.models", fromlist=["User"]
                ).User
            ).where(
                __import__(
                    "app.modules.auth.models", fromlist=["User"]
                ).User.tenant_id == tenant_id
            ).limit(1)
        )
    ).scalar_one()

    fake_payload = TokenPayload(
        sub=str(user_row.auth_user_id),
        tenant_id=str(tenant_id),
        email=user_row.email,
        is_projectx_admin=False,
        exp=9999999999,
    )
    ctx = UserContext(
        user=user_row,
        is_super_admin=False,
        assignments=[
            RoleAssignment(
                org_unit_id=uuid.uuid4(),
                org_unit_name="Root",
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["reports.view"],
            )
        ],
    )

    def _fake_verify(token: str) -> Any:
        if token == "test-header-bearer":
            return fake_payload
        return None

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    # Ensure router is registered
    existing = {
        getattr(r, "path_format", "") or getattr(r, "path", "")
        for r in app.routes
    }
    if not any(p.startswith("/api/reports") for p in existing):
        from app.modules.reporting.router import router as reporting_router
        app.include_router(reporting_router)

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(
                f"/api/reports/session/{session_row.id}",
                headers={"Authorization": "Bearer test-header-bearer"},
            )
    finally:
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    assert r.status_code == 200, r.text
    body = r.json()
    header = body.get("header")
    assert header is not None, "header block missing from response"
    assert header["candidate_name"] == "Charlie"        # seeded by make_assignment_with_stage
    assert header["job_title"] == "Senior Engineer"     # seeded job title
    assert isinstance(header["skills"], list)
