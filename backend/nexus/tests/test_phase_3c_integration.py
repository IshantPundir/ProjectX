"""End-to-end HTTP integration: invite → pre-check → consent → OTP → start → replay."""
import re
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_tenant_db
from app.main import app
from app.models import CandidateSessionToken, EngineDispatchToken, Session
from app.modules.auth.context import RoleAssignment, UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from tests.test_scheduler_service import _seed

_TEST_BEARER = "test-integration-bearer"


def _fake_verify(token: str):
    if token == _TEST_BEARER:
        return TokenPayload(
            sub=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            email="test@example.com",
            is_projectx_admin=False,
            exp=9_999_999_999,
        )
    return None


@pytest.mark.asyncio
async def test_phase_3c_happy_path_with_otp(db):
    tenant, user, _stage, candidate, assignment = await _seed(db, otp_default=True)

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    app.dependency_overrides[get_current_user_roles] = lambda: UserContext(
        user=user, is_super_admin=False,
        assignments=[RoleAssignment(
            org_unit_id=uuid.uuid4(), org_unit_name="Root",
            role_id=uuid.uuid4(), role_name="Recruiter",
            permissions=["candidates.manage", "jobs.manage", "jobs.view"],
        )],
    )

    # Middleware's candidate-JWT JTI lookup opens its own connection via
    # get_bypass_session(); patch it to the test's rolled-back session so the
    # newly minted token row is visible.
    @asynccontextmanager
    async def _fake_bypass():
        yield db

    sent_otp_codes: list[str] = []

    async def capture_email(*args, **kwargs):
        # Session router calls send_email(to=, subject=, html=) after
        # rendering otp_code.html — the 6-digit code lands in the HTML body.
        # Scheduler's interview_invite email also flows through here; we
        # distinguish by subject so we only mine OTP emails for codes.
        subject = kwargs.get("subject", "")
        html = kwargs.get("html", "")
        if "access code" in subject.lower():
            m = re.search(r"\b(\d{6})\b", html)
            if m:
                sent_otp_codes.append(m.group(1))

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {_TEST_BEARER}"},
        ) as ac:
            mock_dispatch_agent = AsyncMock(return_value=None)
            with patch(
                "app.modules.scheduler.service.send_email",
                new=AsyncMock(side_effect=capture_email),
            ), patch(
                "app.modules.session.router.send_email",
                new=AsyncMock(side_effect=capture_email),
            ), patch(
                "app.middleware.auth.get_bypass_session", _fake_bypass,
            ), patch(
                "app.middleware.auth.verify_access_token", side_effect=_fake_verify,
            ), patch(
                "app.modules.session.service.mint_candidate_lk_token",
                return_value="candidate-lk-token-stub",
            ), patch(
                "app.modules.session.service.dispatch_agent",
                new=mock_dispatch_agent,
            ), patch(
                "app.modules.session.service.cancel_room",
                new=AsyncMock(return_value=None),
            ):
                # 1. Recruiter dispatches invite
                invite = await ac.post(
                    "/api/scheduler/invites",
                    json={"assignment_id": str(assignment.id)},
                )
                assert invite.status_code == 201, invite.text
                session_id = invite.json()["session_id"]

                # Grab the token row from DB (scheduler.send_invite minted it)
                tok = (await db.execute(
                    select(CandidateSessionToken)
                    .where(CandidateSessionToken.session_id == uuid.UUID(session_id))
                )).scalar_one()

                # Rebuild an equivalent JWT from the persisted row — the raw
                # token string is never returned by the API (it's baked into
                # the email link). Same jti + claims → middleware accepts it.
                import jwt as pyjwt
                from app.config import settings
                claims = {
                    "jti": str(tok.jti),
                    "sub": str(assignment.candidate_id),
                    "session_id": session_id,
                    "tenant_id": str(tenant.id),
                    "iat": int(tok.issued_at.timestamp()),
                    "exp": int(tok.expires_at.timestamp()),
                }
                token = pyjwt.encode(
                    claims, settings.candidate_jwt_secret, algorithm="HS256"
                )

                # 2. Candidate pre-check
                pre = await ac.get(f"/api/candidate-session/{token}/pre-check")
                assert pre.status_code == 200
                assert pre.json()["otp_required"] is True

                # 3. Consent
                consent = await ac.post(
                    f"/api/candidate-session/{token}/consent",
                    json={"consented": True, "user_agent": "IntegrationTest/1.0"},
                )
                assert consent.status_code == 204

                # 4. Request OTP — captured via mocked send_email
                req = await ac.post(f"/api/candidate-session/{token}/request-otp")
                assert req.status_code == 204
                assert sent_otp_codes, "expected an OTP email to have been sent"
                code = sent_otp_codes[-1]
                assert re.fullmatch(r"\d{6}", code)

                # 5. Verify OTP
                ver = await ac.post(
                    f"/api/candidate-session/{token}/verify-otp",
                    json={"code": code},
                )
                assert ver.status_code == 204

                # 6. Start — 200 OK with LiveKit credentials
                start = await ac.post(f"/api/candidate-session/{token}/start")
                assert start.status_code == 200, start.text
                body = start.json()
                assert isinstance(body["livekit_url"], str)
                assert isinstance(body["livekit_token"], str)
                assert body["room_name"] == f"session-{session_id}"
                assert body["session_id"] == session_id
                mock_dispatch_agent.assert_awaited_once()

                rows = (await db.execute(
                    select(EngineDispatchToken).where(
                        EngineDispatchToken.session_id == uuid.UUID(session_id)
                    )
                )).scalars().all()
                assert len(rows) == 1
                assert rows[0].revoked_at is None

                # 7. Replay — 409 TOKEN_ALREADY_USED
                replay = await ac.post(f"/api/candidate-session/{token}/start")
                assert replay.status_code == 409
                assert replay.json()["code"] == "TOKEN_ALREADY_USED"

        # 8. Session state is 'active' in DB
        sess = (await db.execute(
            select(Session).where(Session.id == uuid.UUID(session_id))
        )).scalar_one()
        assert sess.state == "active"
    finally:
        app.dependency_overrides.clear()
