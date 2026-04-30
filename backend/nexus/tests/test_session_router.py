"""Session router — candidate-facing + recruiter-read HTTP contracts.

These tests exercise the full ASGI stack through httpx + AuthMiddleware,
so the candidate JWT flow (sig + exp + DB JTI lookup) is real. The
`get_tenant_db` dependency is overridden to reuse the per-test session
that owns the seeded rows.

Task-chain note: main.py registers `candidate_session_router` /
`session_router` in Task 3C.1.19. Until that commit lands these tests
404 because the routers are not attached to the FastAPI app. They are
kept in the skip-list for the regression suite (`--ignore=...`) and flip
green as soon as 3C.1.19 wires the `include_router` calls.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_tenant_db
from app.main import app
from app.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateSessionToken,
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    Session,
)
from app.modules.auth.service import create_candidate_token
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


async def _seed_ready_session(db, otp_required=False, state="pre_check"):
    """Return (tenant, candidate, session, token_row, token_str)."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)
    org_unit.company_profile = {"name": "Acme"}
    await db.flush()
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="Engineer",
        description_raw="R" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add(job)
    await db.flush()
    inst = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(inst)
    await db.flush()
    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=inst.id,
        position=0,
        name="AI Interview",
        stage_type="ai_interview",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    db.add(stage)
    await db.flush()
    cand = Candidate(
        tenant_id=tenant.id,
        name="Alice",
        email="alice@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(cand)
    await db.flush()
    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=cand.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()
    sess = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
        otp_required=otp_required,
        state=state,
    )
    db.add(sess)
    await db.flush()
    jti = uuid.uuid4()
    token_str, exp = create_candidate_token(
        jti=jti,
        candidate_id=cand.id,
        session_id=sess.id,
        tenant_id=tenant.id,
    )
    tok = CandidateSessionToken(
        jti=jti,
        tenant_id=tenant.id,
        session_id=sess.id,
        expires_at=exp,
    )
    db.add(tok)
    await db.flush()
    return tenant, cand, sess, tok, token_str


@pytest.fixture
async def http_client(db):
    """Wire the ASGI client + patch both tenant and middleware-bypass DB
    handles to the test session.

    `get_tenant_db` is overridden via FastAPI's dependency_overrides so
    route handlers see the seeded rows. `get_bypass_session` is patched on
    `app.middleware.auth` so the candidate-JWT JTI lookup in
    AuthMiddleware resolves against the same rolled-back test transaction
    instead of opening a fresh connection that can't see the seed inserts.
    Pattern copied from tests/test_middleware_candidate_single_use.py.
    """
    async def _override_db():
        yield db

    @asynccontextmanager
    async def _fake_bypass():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    with patch("app.middleware.auth.get_bypass_session", _fake_bypass):
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                yield ac
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_pre_check_returns_context(db, http_client):
    _t, _c, sess, _tok, token = await _seed_ready_session(db, state="created")
    r = await http_client.get(f"/api/candidate-session/{token}/pre-check")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == str(sess.id)
    assert body["state"] == "pre_check"
    assert body["otp_required"] is False


@pytest.mark.asyncio
async def test_post_consent_transitions_state(db, http_client):
    _t, _c, sess, _tok, token = await _seed_ready_session(db, state="pre_check")
    r = await http_client.post(
        f"/api/candidate-session/{token}/consent",
        json={"consented": True, "user_agent": "UA/1.0"},
    )
    assert r.status_code == 204
    await db.refresh(sess)
    assert sess.state == "consented"


@pytest.mark.asyncio
async def test_post_request_otp_returns_204(db, http_client):
    _t, _c, sess, _tok, token = await _seed_ready_session(
        db, otp_required=True, state="consented"
    )
    with patch("app.modules.session.router.send_email", new=AsyncMock()):
        r = await http_client.post(f"/api/candidate-session/{token}/request-otp")
    assert r.status_code == 204
    await db.refresh(sess)
    assert sess.otp_hash is not None


@pytest.mark.asyncio
async def test_post_verify_otp_invalid_returns_422_with_attempts(db, http_client):
    from app.modules.session import service as session_service

    _t, _c, sess, _tok, token = await _seed_ready_session(
        db, otp_required=True, state="consented"
    )
    # Issue an OTP so verify has something to compare against.
    await session_service.request_otp(db, session_id=sess.id)
    r = await http_client.post(
        f"/api/candidate-session/{token}/verify-otp",
        json={"code": "000000"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "INVALID_OTP"
    assert body["attempts_remaining"] == 2


@pytest.mark.asyncio
async def test_post_start_rejects_when_otp_required_but_not_verified(db, http_client):
    _t, _c, _sess, _tok, token = await _seed_ready_session(
        db, otp_required=True, state="consented"
    )
    r = await http_client.post(f"/api/candidate-session/{token}/start")
    assert r.status_code == 422
    assert r.json()["code"] == "OTP_REQUIRED"


@pytest.mark.asyncio
async def test_post_consent_from_already_consented_is_idempotent(db, http_client):
    _t, _c, sess, _tok, token = await _seed_ready_session(db, state="consented")
    sess.consent_recorded_at = datetime.now(UTC)
    await db.flush()
    r = await http_client.post(
        f"/api/candidate-session/{token}/consent",
        json={"consented": True, "user_agent": "UA"},
    )
    assert r.status_code == 204
