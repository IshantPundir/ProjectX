"""/start LiveKit provisioning — happy + dispatch failure + token race."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.modules.session.models import (
    CandidateSessionToken,
    Session as SessionRow,
)
from app.modules.session import service as session_service
from app.modules.session.errors import (
    AgentDispatchFailedError,
    TokenAlreadyUsedError,
)
from app.modules.session.schemas import SessionState
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
    make_assignment_with_stage,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def livekit_stubs(monkeypatch):
    """Stub the three module-level LiveKit helpers imported into session.service.

    Patches ``session_service.<helper>`` rather than ``session.livekit.<helper>``
    because the service does ``from app.modules.session.livekit import …``,
    which binds the names as attributes of the service module. Patching
    livekit.<helper> would not affect the call sites.

    Phase 3 retired ``mint_engine_dispatch_jwt`` and the engine_dispatch_tokens
    table; the engine now uses RLS + explicit-tenant filters as the defense
    layer. Tests no longer assert that an engine token row was created.
    """
    stubs = {
        "mint_candidate_lk_token": lambda **kw: "candidate-jwt-stub",
        "create_room": AsyncMock(return_value=None),
        "dispatch_agent": AsyncMock(return_value=None),
        "cancel_room": AsyncMock(return_value=None),
    }
    monkeypatch.setattr(session_service, "mint_candidate_lk_token", stubs["mint_candidate_lk_token"])
    monkeypatch.setattr(session_service, "create_room", stubs["create_room"])
    monkeypatch.setattr(session_service, "dispatch_agent", stubs["dispatch_agent"])
    monkeypatch.setattr(session_service, "cancel_room", stubs["cancel_room"])
    return stubs


async def _seed_consented_session(db) -> tuple[uuid.UUID, uuid.UUID]:
    """Build the FK chain plus a session in 'consented' state with a fresh
    unconsumed CandidateSessionToken. Returns (session_id, jti)."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
        state=SessionState.CONSENTED.value,
        otp_required=False,
        consent_recorded_at=datetime.now(UTC),
    )
    db.add(sess)
    await db.flush()

    jti = uuid.uuid4()
    db.add(CandidateSessionToken(
        jti=jti,
        tenant_id=tenant.id,
        session_id=sess.id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    ))
    await db.flush()

    return sess.id, jti


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_happy_path_returns_creds_and_marks_active(db, livekit_stubs):
    session_id, jti = await _seed_consented_session(db)

    resp = await session_service.start_session(
        db, session_id=session_id, jti=jti,
        ip_address="127.0.0.1", user_agent="ua",
    )

    assert resp.livekit_token == "candidate-jwt-stub"
    assert resp.room_name == f"session-{session_id}"
    assert resp.session_id == session_id
    livekit_stubs["dispatch_agent"].assert_awaited_once()

    sess = (await db.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one()
    assert sess.state == SessionState.ACTIVE.value
    assert sess.livekit_room_name == f"session-{session_id}"
    assert sess.started_at is not None


async def test_happy_path_consumes_candidate_token(db, livekit_stubs):
    session_id, jti = await _seed_consented_session(db)
    await session_service.start_session(
        db, session_id=session_id, jti=jti,
        ip_address="127.0.0.1", user_agent="ua",
    )

    tok = (await db.execute(
        select(CandidateSessionToken).where(CandidateSessionToken.jti == jti)
    )).scalar_one()
    assert tok.used_at is not None
    assert str(tok.used_ip) == "127.0.0.1"
    assert tok.used_user_agent == "ua"


# ---------------------------------------------------------------------------
# Dispatch failure → token preserved
# ---------------------------------------------------------------------------

async def test_dispatch_failure_does_not_consume_token(db, livekit_stubs):
    """LiveKit dispatch raises → AgentDispatchFailedError, token stays unconsumed."""
    livekit_stubs["dispatch_agent"].side_effect = RuntimeError("livekit down")
    session_id, jti = await _seed_consented_session(db)

    with pytest.raises(AgentDispatchFailedError):
        await session_service.start_session(
            db, session_id=session_id, jti=jti,
            ip_address="127.0.0.1", user_agent="ua",
        )

    # Session must still be 'consented' (not transitioned to active).
    sess = (await db.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one()
    assert sess.state == SessionState.CONSENTED.value
    assert sess.started_at is None
    assert sess.livekit_room_name is None

    # Candidate token must still be unconsumed.
    tok = (await db.execute(
        select(CandidateSessionToken).where(CandidateSessionToken.jti == jti)
    )).scalar_one()
    assert tok.used_at is None


# Phase 3 retired test_dispatch_failure_rolls_back_engine_dispatch_token —
# the engine_dispatch_tokens table is gone, so there's nothing to roll back.


# ---------------------------------------------------------------------------
# Atomic consume race → cancel_room called
# ---------------------------------------------------------------------------

async def test_token_consume_race_triggers_cancel_room(db, livekit_stubs):
    """Pre-consume the token before calling start_session — the atomic UPDATE
    will return 0 rows, simulating the 'concurrent /start consumed it first'
    race. Verifies cancel_room is invoked and TokenAlreadyUsedError raises.

    The session row is intentionally left in 'consented' state (not 'active'),
    which is an inconsistent state in production but lets us reach the consume
    block without triggering the state gate. Production never reaches this
    state because consume + transition are atomic within start_session.
    """
    session_id, jti = await _seed_consented_session(db)

    # Pre-consume — set used_at on the token row directly.
    await db.execute(
        update(CandidateSessionToken)
        .where(CandidateSessionToken.jti == jti)
        .values(used_at=datetime.now(UTC))
    )
    await db.flush()

    with pytest.raises(TokenAlreadyUsedError):
        await session_service.start_session(
            db, session_id=session_id, jti=jti,
            ip_address="127.0.0.1", user_agent="ua",
        )

    # Dispatch was attempted (mint+dispatch happen before consume).
    livekit_stubs["dispatch_agent"].assert_awaited_once()
    # cancel_room was called best-effort after the failed consume.
    livekit_stubs["cancel_room"].assert_awaited_once()
    livekit_stubs["cancel_room"].assert_awaited_with(f"session-{session_id}")


# ---------------------------------------------------------------------------
# State / OTP gates (still enforced — Phase 3C.1 invariants preserved)
# ---------------------------------------------------------------------------

async def test_illegal_state_pre_dispatch_does_not_call_livekit(db, livekit_stubs):
    """state != 'consented' AND token unused → IllegalStartStateError, no LK calls."""
    from app.modules.session.errors import IllegalStartStateError

    session_id, jti = await _seed_consented_session(db)
    # Move session into a non-startable state.
    await db.execute(
        update(SessionRow).where(SessionRow.id == session_id).values(
            state=SessionState.CREATED.value,
        )
    )
    await db.flush()

    with pytest.raises(IllegalStartStateError):
        await session_service.start_session(
            db, session_id=session_id, jti=jti,
            ip_address="127.0.0.1", user_agent="ua",
        )

    livekit_stubs["dispatch_agent"].assert_not_awaited()


async def test_otp_required_unmet_does_not_call_livekit(db, livekit_stubs):
    """otp_required=True + otp_verified_at=None → OtpRequiredError, no LK calls."""
    from app.modules.session.errors import OtpRequiredError

    session_id, jti = await _seed_consented_session(db)
    await db.execute(
        update(SessionRow).where(SessionRow.id == session_id).values(
            otp_required=True,
            otp_verified_at=None,
        )
    )
    await db.flush()

    with pytest.raises(OtpRequiredError):
        await session_service.start_session(
            db, session_id=session_id, jti=jti,
            ip_address="127.0.0.1", user_agent="ua",
        )

    livekit_stubs["dispatch_agent"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Session recording (RoomComposite egress) — best-effort wiring
# ---------------------------------------------------------------------------

async def test_start_attaches_auto_egress_and_marks_recording(db, livekit_stubs):
    """Happy path attaches auto-egress at room creation and stamps recording."""
    session_id, jti = await _seed_consented_session(db)

    await session_service.start_session(
        db, session_id=session_id, jti=jti,
        ip_address="127.0.0.1", user_agent="ua",
    )

    # create_room was called with an egress config (auto egress).
    _, kwargs = livekit_stubs["create_room"].await_args
    assert kwargs.get("egress") is not None

    sess = (await db.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one()
    assert sess.recording_status == "recording"
    assert sess.recording_s3_key.startswith("recordings/")
    assert sess.recording_s3_key.endswith(f"{session_id}.mp4")
    assert sess.recording_started_at is not None


async def test_recording_setup_failure_is_non_fatal(db, livekit_stubs, monkeypatch):
    """If egress setup fails, the session still goes active (availability over
    recording completeness); status is marked 'failed' for the report."""
    monkeypatch.setattr(
        session_service, "build_room_egress",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("egress config bad")),
    )
    session_id, jti = await _seed_consented_session(db)

    resp = await session_service.start_session(
        db, session_id=session_id, jti=jti,
        ip_address="127.0.0.1", user_agent="ua",
    )

    assert resp.session_id == session_id  # interview proceeds
    # Room was still created (without egress) so the interview can run.
    livekit_stubs["create_room"].assert_awaited()
    sess = (await db.execute(
        select(SessionRow).where(SessionRow.id == session_id)
    )).scalar_one()
    assert sess.state == SessionState.ACTIVE.value
    assert sess.recording_status == "failed"
    # Token was still consumed — the interview is live regardless.
    token = (await db.execute(
        select(CandidateSessionToken).where(CandidateSessionToken.jti == jti)
    )).scalar_one()
    assert token.used_at is not None
