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


@pytest.mark.asyncio
async def test_get_pre_check_context_advances_created_to_pre_check(db):
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    assert sess.state == "created"

    resp = await service.get_pre_check_context(db, session_id=sess.id)

    assert resp.state == SessionState.PRE_CHECK
    assert resp.session_id == sess.id
    assert resp.job_title  # company / title populated (may be empty string if helper returns "")
    await db.refresh(sess)
    assert sess.state == "pre_check"


@pytest.mark.asyncio
async def test_get_pre_check_context_is_monotonic_from_consented(db):
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    sess.consent_recorded_at = datetime.now(UTC)
    await db.flush()

    resp = await service.get_pre_check_context(db, session_id=sess.id)

    assert resp.state == SessionState.CONSENTED  # no regression
    await db.refresh(sess)
    assert sess.state == "consented"


@pytest.mark.asyncio
async def test_record_consent_stamps_and_transitions(db):
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    # Must be at pre_check before consent is allowed
    sess.state = "pre_check"
    await db.flush()

    await service.record_consent(
        db, session_id=sess.id, user_agent="Mozilla/5.0", ip_address="1.2.3.4",
    )
    await db.refresh(sess)

    assert sess.state == "consented"
    assert sess.consent_recorded_at is not None


@pytest.mark.asyncio
async def test_record_consent_is_idempotent_once_already_consented(db):
    tenant, user, stage, _c, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    sess.state = "consented"
    original_ts = datetime.now(UTC)
    sess.consent_recorded_at = original_ts
    await db.flush()

    await service.record_consent(
        db, session_id=sess.id, user_agent="NewUA", ip_address="1.2.3.4",
    )
    await db.refresh(sess)
    # Timestamp not overwritten
    assert sess.consent_recorded_at == original_ts


@pytest.mark.asyncio
async def test_request_otp_issues_code_and_wipes_prior_attempts(db):
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    sess.otp_attempts = 2  # stale attempts from a prior (expired) code
    await db.flush()

    code = await service.request_otp(db, session_id=sess.id)

    await db.refresh(sess)
    assert len(code) == 6 and code.isdigit()
    assert sess.otp_hash is not None
    assert sess.otp_issued_at is not None
    assert sess.otp_attempts == 0


@pytest.mark.asyncio
async def test_request_otp_enforces_rate_limit(db):
    from datetime import timedelta
    from app.modules.session.errors import OtpRateLimitedError
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    sess.otp_issued_at = datetime.now(UTC) - timedelta(seconds=10)
    sess.otp_hash = "dummy"
    await db.flush()

    with pytest.raises(OtpRateLimitedError) as exc:
        await service.request_otp(db, session_id=sess.id)
    assert exc.value.retry_after_seconds > 40  # ~50s remaining


@pytest.mark.asyncio
async def test_verify_otp_success_wipes_hash_and_stamps_verified(db):
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    await db.flush()
    code = await service.request_otp(db, session_id=sess.id)

    await service.verify_otp(db, session_id=sess.id, code=code)

    await db.refresh(sess)
    assert sess.otp_hash is None
    assert sess.otp_verified_at is not None


@pytest.mark.asyncio
async def test_verify_otp_wrong_code_increments_attempts(db):
    from app.modules.session.errors import InvalidOtpError
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    await db.flush()
    await service.request_otp(db, session_id=sess.id)

    with pytest.raises(InvalidOtpError) as exc:
        await service.verify_otp(db, session_id=sess.id, code="000000")
    assert exc.value.attempts_remaining == 2


@pytest.mark.asyncio
async def test_verify_otp_third_miss_wipes_and_raises_max_attempts(db):
    from app.modules.session.errors import (
        InvalidOtpError, OtpMaxAttemptsReachedError,
    )
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    await db.flush()
    await service.request_otp(db, session_id=sess.id)

    # 2 misses
    for _ in range(2):
        with pytest.raises(InvalidOtpError):
            await service.verify_otp(db, session_id=sess.id, code="000000")
    # 3rd miss: MAX_ATTEMPTS_REACHED + hash wiped
    with pytest.raises(OtpMaxAttemptsReachedError):
        await service.verify_otp(db, session_id=sess.id, code="000000")

    await db.refresh(sess)
    assert sess.otp_hash is None
    assert sess.otp_verified_at is None


@pytest.mark.asyncio
async def test_verify_otp_after_expiry_raises_otp_expired(db):
    from datetime import timedelta
    from app.modules.session.errors import OtpExpiredError
    tenant, user, stage, _c, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    await db.flush()
    await service.request_otp(db, session_id=sess.id)
    # Backdate issuance by 11 minutes
    sess.otp_issued_at = datetime.now(UTC) - timedelta(minutes=11)
    await db.flush()

    with pytest.raises(OtpExpiredError):
        await service.verify_otp(db, session_id=sess.id, code="000000")
    await db.refresh(sess)
    assert sess.otp_hash is None


@pytest.mark.asyncio
async def test_start_session_requires_consented_state(db):
    from app.modules.session.errors import IllegalStartStateError
    tenant, user, stage, candidate, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    # state is 'created' — not 'consented'
    _, token_row = await service.mint_token(db, session=sess, candidate_id=candidate.id)

    with pytest.raises(IllegalStartStateError):
        await service.start_session(
            db, session_id=sess.id, jti=token_row.jti,
            ip_address="1.2.3.4", user_agent="UA",
        )


@pytest.mark.asyncio
async def test_start_session_rejects_when_otp_required_but_not_verified(db):
    from app.modules.session.errors import OtpRequiredError
    tenant, user, stage, candidate, assignment = await _seed_assignment(db, otp_default=True)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=True, user=ctx,
    )
    sess.state = "consented"
    await db.flush()
    _, token_row = await service.mint_token(db, session=sess, candidate_id=candidate.id)

    with pytest.raises(OtpRequiredError):
        await service.start_session(
            db, session_id=sess.id, jti=token_row.jti,
            ip_address="1.2.3.4", user_agent="UA",
        )


@pytest.mark.asyncio
async def test_get_session_returns_detail_shape(db):
    tenant, user, stage, _c, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    sess = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    resp = await service.get_session(db, session_id=sess.id)
    assert resp.id == sess.id
    assert resp.stage_name == stage.name
    assert resp.state == SessionState.CREATED


@pytest.mark.asyncio
async def test_get_session_missing_raises_session_not_found(db):
    from app.modules.session.errors import SessionNotFoundError
    with pytest.raises(SessionNotFoundError):
        await service.get_session(db, session_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_list_sessions_filters_by_assignment_and_state(db):
    tenant, user, stage, _c, assignment = await _seed_assignment(db)
    ctx = _make_ctx(user)
    s1 = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    s2 = await service.create_session(
        db, assignment=assignment, stage=stage, otp_required=False, user=ctx,
    )
    s2.state = "cancelled"
    await db.flush()

    page = await service.list_sessions(
        db, tenant_id=tenant.id, filters={"assignment_id": assignment.id},
    )
    assert page.total == 2

    page_active = await service.list_sessions(
        db, tenant_id=tenant.id,
        filters={"assignment_id": assignment.id, "state": "created"},
    )
    assert page_active.total == 1
    assert page_active.items[0].id == s1.id
