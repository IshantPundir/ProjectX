"""Tests for the rejoin path — fresh LiveKit token mint without re-dispatch."""
import pytest
from unittest.mock import patch
from app.modules.session.service import rejoin_session
from app.modules.session.errors import SessionNotRejoinableError
from tests.test_session_router import _seed_ready_session, http_client  # noqa: F401


@pytest.mark.asyncio
async def test_rejoin_active_session_returns_fresh_lk_token(db):
    _t, _c, sess, _tok, _ts = await _seed_ready_session(db, state="active")
    sess.livekit_room_name = "lk-room-stub"
    await db.flush()

    # mint_candidate_lk_token is imported into service.py from session.livekit,
    # so we patch the name bound at the service module's call site.
    with patch(
        "app.modules.session.service.mint_candidate_lk_token",
        return_value="new-lk-token",
    ) as mint:
        response = await rejoin_session(db, session_id=sess.id)

    assert response.livekit_token == "new-lk-token"
    assert response.room_name == "lk-room-stub"
    mint.assert_called_once()


@pytest.mark.asyncio
async def test_rejoin_rejects_completed_session(db):
    _t, _c, sess, _tok, _ts = await _seed_ready_session(db, state="completed")
    with pytest.raises(SessionNotRejoinableError):
        await rejoin_session(db, session_id=sess.id)


@pytest.mark.asyncio
async def test_rejoin_endpoint_returns_200_for_active(db, http_client):
    _t, _c, sess, _tok, token_str = await _seed_ready_session(db, state="active")
    sess.livekit_room_name = "lk-room-stub"
    await db.flush()

    with patch(
        "app.modules.session.service.mint_candidate_lk_token",
        return_value="new-lk-token",
    ):
        r = await http_client.post(
            f"/api/candidate-session/{token_str}/rejoin",
        )
    assert r.status_code == 200
    body = r.json()
    assert body["livekit_token"] == "new-lk-token"


@pytest.mark.asyncio
async def test_rejoin_endpoint_409_for_completed(db, http_client):
    _t, _c, sess, _tok, token_str = await _seed_ready_session(db, state="completed")
    r = await http_client.post(
        f"/api/candidate-session/{token_str}/rejoin",
    )
    assert r.status_code == 409
    assert r.json()["code"] == "SESSION_NOT_REJOINABLE"
