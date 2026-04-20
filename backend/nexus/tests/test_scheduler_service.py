"""Scheduler service — send_invite, resend_invite, revoke_invite."""
import uuid
from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    Candidate, CandidateJobAssignment, CandidateSessionToken,
    JobPipelineInstance, JobPipelineStage, JobPosting, Session,
)
from app.modules.auth.context import UserContext
from app.modules.scheduler import service
from app.modules.scheduler.schemas import InviteCreateRequest
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


def _make_ctx(user):
    return UserContext(user=user, is_super_admin=False, assignments=[])


async def _seed(db, stage_type="ai_interview", otp_default=False, assignment_status="active"):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)
    org_unit.company_profile = {"name": "Acme Corp"}
    await db.flush()
    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=org_unit.id, title="Engineer",
        description_raw="R" * 60, created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()
    inst = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(inst)
    await db.flush()
    stage = JobPipelineStage(
        tenant_id=tenant.id, instance_id=inst.id, position=0,
        name="AI Interview", stage_type=stage_type, duration_minutes=30,
        difficulty="medium", signal_filter={}, pass_criteria={},
        advance_behavior="manual", otp_required_default=otp_default,
    )
    db.add(stage)
    await db.flush()
    candidate = Candidate(
        tenant_id=tenant.id, name="Alice", email="alice@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()
    assignment = CandidateJobAssignment(
        tenant_id=tenant.id, candidate_id=candidate.id, job_posting_id=job.id,
        current_stage_id=stage.id, assigned_by=user.id, status=assignment_status,
    )
    db.add(assignment)
    await db.flush()
    return tenant, user, stage, candidate, assignment


@pytest.mark.asyncio
async def test_send_invite_creates_session_and_token_and_dispatches_email(db):
    tenant, user, _stage, candidate, assignment = await _seed(db)
    ctx = _make_ctx(user)
    req = InviteCreateRequest(assignment_id=assignment.id)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()) as mock_email:
        resp = await service.send_invite(db, req, ctx)

    # Session + token persisted
    sess = (await db.execute(
        select(Session).where(Session.id == resp.session_id)
    )).scalar_one()
    assert sess.assignment_id == assignment.id
    assert sess.otp_required is False  # stage default

    token = (await db.execute(
        select(CandidateSessionToken).where(CandidateSessionToken.session_id == sess.id)
    )).scalar_one()
    assert token.used_at is None

    # Email dispatched
    mock_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_invite_honors_otp_override(db):
    tenant, user, _stage, _cand, assignment = await _seed(db, otp_default=False)
    ctx = _make_ctx(user)
    req = InviteCreateRequest(assignment_id=assignment.id, otp_required=True)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        resp = await service.send_invite(db, req, ctx)

    sess = (await db.execute(
        select(Session).where(Session.id == resp.session_id)
    )).scalar_one()
    assert sess.otp_required is True


@pytest.mark.asyncio
async def test_send_invite_rejects_non_ai_interview_stage(db):
    from app.modules.scheduler.errors import InvalidStageTypeForInviteError
    tenant, user, _stage, _cand, assignment = await _seed(db, stage_type="manual_review")
    ctx = _make_ctx(user)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        with pytest.raises(InvalidStageTypeForInviteError):
            await service.send_invite(
                db, InviteCreateRequest(assignment_id=assignment.id), ctx,
            )


@pytest.mark.asyncio
async def test_send_invite_rejects_non_active_assignment(db):
    from app.modules.scheduler.errors import AssignmentNotActiveError
    tenant, user, _stage, _cand, assignment = await _seed(db, assignment_status="archived")
    ctx = _make_ctx(user)

    with patch("app.modules.scheduler.service.send_email", new=AsyncMock()):
        with pytest.raises(AssignmentNotActiveError):
            await service.send_invite(
                db, InviteCreateRequest(assignment_id=assignment.id), ctx,
            )
