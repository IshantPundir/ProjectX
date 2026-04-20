"""Session service layer — scheduler-facing plumbing (create, mint token, supersede)."""
import uuid
from datetime import datetime, UTC

import pytest
from sqlalchemy import select

from app.models import (
    Candidate, CandidateJobAssignment, CandidateSessionToken,
    JobPipelineInstance, JobPipelineStage, JobPosting, Session,
)
from app.modules.auth.context import UserContext
from app.modules.session import service
from app.modules.session.schemas import SessionState
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


def _make_ctx(user, is_super=False):
    return UserContext(user=user, is_super_admin=is_super, assignments=[])


async def _seed_assignment(db, otp_default=False):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)
    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=org_unit.id, title="T",
        description_raw="R" * 60, created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()
    instance = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()
    stage = JobPipelineStage(
        tenant_id=tenant.id, instance_id=instance.id, position=0,
        name="AI Interview", stage_type="ai_interview", duration_minutes=30,
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
        current_stage_id=stage.id, assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()
    return tenant, user, stage, candidate, assignment


@pytest.mark.asyncio
async def test_create_session_persists_row_with_state_created(db):
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)

    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )

    assert sess.state == "created"
    assert sess.assignment_id == assignment.id
    assert sess.stage_id == stage.id
    assert sess.created_by == user.id
    assert sess.otp_required is False


@pytest.mark.asyncio
async def test_create_session_honors_otp_required_override(db):
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=False)
    ctx = _make_ctx(user)

    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    assert sess.otp_required is True


@pytest.mark.asyncio
async def test_mint_token_inserts_token_row_and_returns_jwt(db):
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )

    token_str, token_row = await service.mint_token(
        db, session=sess, candidate_id=candidate.id,
    )
    assert isinstance(token_str, str)
    assert token_row.session_id == sess.id
    assert token_row.tenant_id == sess.tenant_id
    assert token_row.used_at is None
    assert token_row.superseded_at is None


@pytest.mark.asyncio
async def test_supersede_token_marks_prior_and_links_successor(db):
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    _old_str, old = await service.mint_token(db, session=sess, candidate_id=candidate.id)
    _new_str, new = await service.mint_token(db, session=sess, candidate_id=candidate.id)

    await service.supersede_token(db, prior=old, successor=new)

    await db.refresh(old)
    assert old.superseded_at is not None
    assert old.superseded_by == new.jti
