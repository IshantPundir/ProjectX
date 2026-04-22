"""Integration tests for pipeline stage participants feature.

Tests exercise the full HTTP stack via AsyncClient + ASGITransport. Auth is
faked using the same pattern as test_question_banks_router.py:

  1. Patch ``app.middleware.auth.verify_access_token`` to accept a sentinel bearer.
  2. Override ``get_current_user_roles`` to return a synthesized UserContext.
  3. Override ``get_tenant_db`` to yield the test session.

Covers:
  - participants=None leaves existing participants untouched (PATCH sentinel)
  - reviewer role rejected on human_interview stage (422)
  - user outside org unit ancestry rejected (422)
  - cascade on stage delete removes participant rows
  - intake stage rejects participants (422)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    OrganizationalUnit,
    PipelineStageParticipant,
    Role,
    User,
    UserRoleAssignment,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.database import get_tenant_db
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_TEST_BEARER = "test-pipeline-participants-token"

_VALID_PROFILE = {
    "about": "We build enterprise HR software at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end.",
}

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


async def _setup_tenant_user_unit(db: AsyncSession):
    """Create tenant + super-admin + company org unit. Mirror of test_question_banks_router."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


async def _make_job_with_signals(
    db: AsyncSession,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=1,
        signals=[],
        seniority_level="senior",
        role_summary="A senior engineer.",
        prompt_version="v1",
        confirmed_by=user_id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_instance(
    db: AsyncSession,
    *,
    job: JobPosting,
) -> JobPipelineInstance:
    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()
    return instance


async def _make_stage(
    db: AsyncSession,
    *,
    job: JobPosting,
    instance: JobPipelineInstance,
    stage_type: str = "human_interview",
    position: int = 0,
    name: str = "Human Interview",
) -> JobPipelineStage:
    stage = JobPipelineStage(
        tenant_id=job.tenant_id,
        instance_id=instance.id,
        position=position,
        name=name,
        stage_type=stage_type,
        duration_minutes=60,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()
    return stage


async def _lookup_role_id(db: AsyncSession, role_name: str) -> UUID:
    """Look up a system role by name (tenant_id=NULL).

    The test DB is built by SQLAlchemy create_all — it does NOT run the Supabase
    initial migration that seeds system roles. Insert on first miss so every
    test is self-contained (these rows survive only for the duration of the
    per-test rollback window).
    """
    result = await db.execute(
        select(Role).where(Role.name == role_name, Role.tenant_id.is_(None))
    )
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(name=role_name, tenant_id=None, is_system=True)
        db.add(role)
        await db.flush()
    return role.id


async def _assign_role(
    db: AsyncSession,
    *,
    user: User,
    org_unit: OrganizationalUnit,
    role_name: str,
    assigned_by: UUID | None = None,
) -> UserRoleAssignment:
    """Create a UserRoleAssignment row for the given user + org unit + role name."""
    role_id = await _lookup_role_id(db, role_name)
    assignment = UserRoleAssignment(
        user_id=user.id,
        org_unit_id=org_unit.id,
        role_id=role_id,
        tenant_id=user.tenant_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)
    await db.flush()
    return assignment


def _setup_test_context(
    db: AsyncSession,
    user: User,
    tenant_id: UUID,
    is_super_admin: bool = True,
):
    """Install fake auth + DB overrides for one HTTP request.

    Returns (headers, restore_fn). Mirror of test_question_banks_router pattern.
    """
    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )

    ctx = UserContext(
        user=user,
        is_super_admin=is_super_admin,
        workspace_mode="enterprise",
        assignments=[],
    )

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return fake_payload
        return None

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


def _stage_patch_body(
    stage: JobPipelineStage,
    participants: list[dict] | None = None,
    include_participants_key: bool = True,
) -> dict:
    """Build a minimal UpdateJobPipelineRequest body for a single stage."""
    stage_dict: dict = {
        "id": str(stage.id),
        "position": stage.position,
        "name": stage.name,
        "stage_type": stage.stage_type,
        "duration_minutes": stage.duration_minutes,
        "difficulty": stage.difficulty,
        "signal_filter": stage.signal_filter,
        "pass_criteria": stage.pass_criteria,
        "advance_behavior": stage.advance_behavior,
    }
    if include_participants_key:
        stage_dict["participants"] = participants if participants is not None else []
    # When include_participants_key=False, no "participants" key → None sentinel
    return {"stages": [stage_dict]}


# ---------------------------------------------------------------------------
# Test 1 — participants=None leaves existing participants untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_with_participants_none_leaves_existing_untouched(db: AsyncSession):
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    # Create an Interviewer user assigned to the company unit
    interviewer = await create_test_user(db, tenant.id)
    await _assign_role(db, user=interviewer, org_unit=company, role_name="Interviewer")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)
    instance = await _make_pipeline_instance(db, job=job)
    stage = await _make_stage(db, job=job, instance=instance, stage_type="human_interview")

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # First PATCH: add one participant
            body1 = _stage_patch_body(
                stage,
                participants=[{"user_id": str(interviewer.id), "role": "interviewer"}],
            )
            resp1 = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body1,
                headers=headers,
            )
            assert resp1.status_code == 200, resp1.text
            data1 = resp1.json()
            stage_resp1 = data1["stages"][0]
            assert len(stage_resp1["participants"]) == 1
            assert stage_resp1["participants"][0]["user_id"] == str(interviewer.id)

            # Second PATCH: no "participants" key → sentinel None → should not touch staffing
            body2 = _stage_patch_body(stage, include_participants_key=False)
            resp2 = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body2,
                headers=headers,
            )
            assert resp2.status_code == 200, resp2.text
            data2 = resp2.json()
            stage_resp2 = data2["stages"][0]
            assert len(stage_resp2["participants"]) == 1, (
                "Existing participant should survive a PATCH with participants=None"
            )
            assert stage_resp2["participants"][0]["user_id"] == str(interviewer.id)
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2 — reviewer role rejected on human_interview stage (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_role_rejected_on_human_interview_stage(db: AsyncSession):
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    # Hiring Manager user — valid person, wrong role for human_interview
    hm = await create_test_user(db, tenant.id)
    await _assign_role(db, user=hm, org_unit=company, role_name="Hiring Manager")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)
    instance = await _make_pipeline_instance(db, job=job)
    stage = await _make_stage(db, job=job, instance=instance, stage_type="human_interview")

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            body = _stage_patch_body(
                stage,
                participants=[{"user_id": str(hm.id), "role": "reviewer"}],
            )
            resp = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body,
                headers=headers,
            )
            assert resp.status_code == 422, resp.text
            detail_str = str(resp.json())
            assert "interviewer" in detail_str.lower(), (
                f"Expected error mentioning 'interviewer', got: {detail_str}"
            )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3 — user outside org unit ancestry rejected (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_outside_org_unit_ancestry_rejected(db: AsyncSession):
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    # Create a SIBLING org unit (not in ancestry of job's unit)
    sibling_unit = await create_test_org_unit(db, tenant.id, unit_type="division")

    # Interviewer assigned only to the sibling (not to company or any ancestor)
    outsider = await create_test_user(db, tenant.id)
    await _assign_role(db, user=outsider, org_unit=sibling_unit, role_name="Interviewer")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)
    instance = await _make_pipeline_instance(db, job=job)
    stage = await _make_stage(db, job=job, instance=instance, stage_type="human_interview")

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            body = _stage_patch_body(
                stage,
                participants=[{"user_id": str(outsider.id), "role": "interviewer"}],
            )
            resp = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body,
                headers=headers,
            )
            assert resp.status_code == 422, resp.text
            detail_str = str(resp.json())
            assert "not eligible" in detail_str.lower() or "eligible" in detail_str.lower(), (
                f"Expected error mentioning eligibility, got: {detail_str}"
            )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 4 — cascade on stage delete removes participant rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_on_stage_delete(db: AsyncSession):
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    interviewer_a = await create_test_user(db, tenant.id)
    await _assign_role(db, user=interviewer_a, org_unit=company, role_name="Interviewer")

    interviewer_b = await create_test_user(db, tenant.id)
    await _assign_role(db, user=interviewer_b, org_unit=company, role_name="Interviewer")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)
    instance = await _make_pipeline_instance(db, job=job)
    stage_a = await _make_stage(
        db, job=job, instance=instance, stage_type="human_interview", position=0, name="Round 1"
    )
    stage_b = await _make_stage(
        db, job=job, instance=instance, stage_type="human_interview", position=1, name="Round 2"
    )

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Staff both stages
            body_both = {
                "stages": [
                    {
                        "id": str(stage_a.id),
                        "position": 0,
                        "name": stage_a.name,
                        "stage_type": "human_interview",
                        "duration_minutes": 60,
                        "difficulty": "medium",
                        "signal_filter": {"include_types": ["competency", "behavioral"]},
                        "pass_criteria": {"type": "all_knockouts_pass"},
                        "advance_behavior": "manual_review",
                        "participants": [{"user_id": str(interviewer_a.id), "role": "interviewer"}],
                    },
                    {
                        "id": str(stage_b.id),
                        "position": 1,
                        "name": stage_b.name,
                        "stage_type": "human_interview",
                        "duration_minutes": 60,
                        "difficulty": "medium",
                        "signal_filter": {"include_types": ["competency", "behavioral"]},
                        "pass_criteria": {"type": "all_knockouts_pass"},
                        "advance_behavior": "manual_review",
                        "participants": [{"user_id": str(interviewer_b.id), "role": "interviewer"}],
                    },
                ]
            }
            resp_setup = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body_both,
                headers=headers,
            )
            assert resp_setup.status_code == 200, resp_setup.text
            # Both stages staffed
            stages_data = resp_setup.json()["stages"]
            assert len(stages_data) == 2
            assert len(stages_data[0]["participants"]) == 1
            assert len(stages_data[1]["participants"]) == 1

            # Now PATCH keeping only stage_a (dropping stage_b via diff-and-sync)
            body_keep_one = {
                "stages": [
                    {
                        "id": str(stage_a.id),
                        "position": 0,
                        "name": stage_a.name,
                        "stage_type": "human_interview",
                        "duration_minutes": 60,
                        "difficulty": "medium",
                        "signal_filter": {"include_types": ["competency", "behavioral"]},
                        "pass_criteria": {"type": "all_knockouts_pass"},
                        "advance_behavior": "manual_review",
                        # participants=None → don't touch staffing on stage_a
                    }
                ]
            }
            resp_drop = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body_keep_one,
                headers=headers,
            )
            assert resp_drop.status_code == 200, resp_drop.text

        # Query PipelineStageParticipant rows for the deleted stage directly via db
        part_result = await db.execute(
            select(PipelineStageParticipant).where(
                PipelineStageParticipant.stage_id == stage_b.id
            )
        )
        rows = list(part_result.scalars().all())
        assert rows == [], (
            f"Expected FK-cascade to delete participant rows for stage_b, found {len(rows)} row(s)"
        )
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 5 — intake stage rejects participants (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intake_stage_rejects_participants(db: AsyncSession):
    tenant, admin_user, company = await _setup_tenant_user_unit(db)

    someone = await create_test_user(db, tenant.id)
    await _assign_role(db, user=someone, org_unit=company, role_name="Interviewer")

    job, _ = await _make_job_with_signals(db, tenant.id, company.id, admin_user.id)
    instance = await _make_pipeline_instance(db, job=job)
    stage = await _make_stage(db, job=job, instance=instance, stage_type="intake", position=0)

    headers, restore = _setup_test_context(db, admin_user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            body = {
                "stages": [
                    {
                        "id": str(stage.id),
                        "position": 0,
                        "name": stage.name,
                        "stage_type": "intake",
                        "duration_minutes": 60,
                        "difficulty": "medium",
                        "signal_filter": {"include_types": ["competency"]},
                        "pass_criteria": {"type": "all_knockouts_pass"},
                        "advance_behavior": "manual_review",
                        "participants": [{"user_id": str(someone.id), "role": "interviewer"}],
                    }
                ]
            }
            resp = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=body,
                headers=headers,
            )
            assert resp.status_code == 422, resp.text
            detail_str = str(resp.json())
            assert "cannot carry participants" in detail_str.lower() or "participants" in detail_str.lower(), (
                f"Expected error mentioning participants restriction, got: {detail_str}"
            )
    finally:
        restore()
