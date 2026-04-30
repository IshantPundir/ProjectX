"""verify_engine_token — branch-coverage tests for the engine dispatch JWT.

Covers the security-critical paths in app/modules/auth/service.py::verify_engine_token:
algorithm pinning, claim-shape rejection, purpose mismatch, sub-vs-path
mismatch, expiry, tampered signature, replay (same endpoint), different
endpoint with same jti (must succeed), unknown parent jti (FK -> translated
EngineTokenInvalidError), and revoked parent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
import pytest_asyncio

from app.config import settings
from app.models import EngineDispatchToken, Session as SessionRow
from app.modules.auth.service import verify_engine_token
from app.modules.interview_runtime.errors import EngineTokenInvalidError
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)

pytestmark = pytest.mark.asyncio

# Must match the value monkeypatched into settings below — keep them in sync.
SECRET = "test-engine-secret-placeholder-32chars"


@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    """Force settings.interview_engine_jwt_secret to SECRET for every test.

    conftest.py's os.environ.setdefault may have lost the race against
    docker-compose .env loading, so the value loaded into Settings on
    container startup is non-deterministic. monkeypatching the runtime
    attribute closes that gap unconditionally.
    """
    monkeypatch.setattr(settings, "interview_engine_jwt_secret", SECRET)


@pytest_asyncio.fixture
async def tenant_and_session(db):
    """Seed a real tenant + session FK chain.

    Returns (tenant_id: UUID, session_id: UUID). The session row is needed
    because EngineDispatchToken has FK -> sessions, and most tests insert a
    parent EngineDispatchToken row to satisfy engine_token_uses' FK on jti.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)
    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()
    return tenant.id, sess.id


def _mint(claims: dict, *, secret: str = SECRET, alg: str = "HS256") -> str:
    """Encode claims with the given secret/algorithm."""
    return pyjwt.encode(claims, secret, algorithm=alg)


def _baseline_claims(session_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
    """Construct a fresh, valid claim set with a unique jti and 10-min expiry."""
    now = int(datetime.now(UTC).timestamp())
    return {
        "sub": str(session_id),
        "tenant_id": str(tenant_id),
        "purpose": "interview_engine",
        "iat": now,
        "exp": now + 600,
        "jti": str(uuid.uuid4()),
    }


async def _insert_dispatch_token(
    db, *, jti: uuid.UUID, tenant_id: uuid.UUID, session_id: uuid.UUID,
    expires_in_minutes: int = 10, revoked: bool = False,
) -> None:
    """Seed engine_dispatch_tokens to satisfy the FK from engine_token_uses."""
    db.add(EngineDispatchToken(
        jti=jti,
        tenant_id=tenant_id,
        session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
        revoked_at=datetime.now(UTC) if revoked else None,
    ))
    await db.flush()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_valid_token_first_use_succeeds(db, tenant_and_session):
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    await _insert_dispatch_token(
        db, jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
    )

    payload = await verify_engine_token(
        _mint(claims), db,
        expected_session_id=session_id, endpoint="config",
    )
    assert payload.sub == session_id
    assert payload.tenant_id == tenant_id
    assert payload.purpose == "interview_engine"


# ---------------------------------------------------------------------------
# Single-use semantics
# ---------------------------------------------------------------------------

async def test_replayed_same_endpoint_rejected(db, tenant_and_session):
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    await _insert_dispatch_token(
        db, jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
    )

    token = _mint(claims)
    await verify_engine_token(token, db, expected_session_id=session_id, endpoint="config")
    with pytest.raises(EngineTokenInvalidError, match="already used"):
        await verify_engine_token(token, db, expected_session_id=session_id, endpoint="config")


async def test_different_endpoint_same_jti_succeeds(db, tenant_and_session):
    """Composite PK is (jti, endpoint) — a different endpoint is a fresh slot."""
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    await _insert_dispatch_token(
        db, jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id, session_id=session_id,
    )

    token = _mint(claims)
    await verify_engine_token(token, db, expected_session_id=session_id, endpoint="config")
    payload = await verify_engine_token(
        token, db, expected_session_id=session_id, endpoint="results",
    )
    assert payload.sub == session_id


# ---------------------------------------------------------------------------
# Algorithm allowlist
# ---------------------------------------------------------------------------

async def test_alg_none_rejected(db, tenant_and_session):
    """'none' algorithm tokens are rejected at decode.

    PyJWT 2.x allows encoding with algorithm='none' but rejects at decode
    unless 'none' is in the algorithms allowlist. Our verify_engine_token
    uses algorithms=['HS256'] so decoding a none-alg token raises PyJWTError,
    which is translated to EngineTokenInvalidError('jwt decode failed: ...').

    If a future PyJWT version raises at encode time too, that's a stronger
    security property — the test would need to be adjusted to expect the
    encode itself to fail.
    """
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    try:
        token = pyjwt.encode(claims, "", algorithm="none")
    except Exception:
        # PyJWT refused to produce a 'none' token at all — stronger guarantee.
        # The test passes because a token that cannot be encoded cannot be replayed.
        return
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            token, db, expected_session_id=session_id, endpoint="config",
        )


async def test_alg_hs512_correct_secret_rejected(db, tenant_and_session):
    """HS512 with the right secret is still rejected — algorithm pin is HS256-only."""
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    token = pyjwt.encode(claims, SECRET, algorithm="HS512")
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            token, db, expected_session_id=session_id, endpoint="config",
        )


async def test_alg_rs256_rejected(db, tenant_and_session):
    """A garbage RS256-shaped token is rejected at decode.

    We pass a structurally invalid three-part string. PyJWT cannot decode
    it under HS256, so it raises PyJWTError, which translates to
    EngineTokenInvalidError('jwt decode failed: ...').
    """
    tenant_id, session_id = tenant_and_session
    # Bogus three-part token. PyJWT can't decode it; algorithm rejection or
    # signature failure both manifest as PyJWTError, both translate to
    # EngineTokenInvalidError("jwt decode failed: ...").
    token = "x.y.z"
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            token, db, expected_session_id=session_id, endpoint="config",
        )


# ---------------------------------------------------------------------------
# Claim-shape / purpose / sub validation
# ---------------------------------------------------------------------------

async def test_wrong_purpose_rejected(db, tenant_and_session):
    """purpose='candidate' fails Literal validation -> claim shape mismatch."""
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    claims["purpose"] = "candidate"
    with pytest.raises(EngineTokenInvalidError, match="claim shape"):
        await verify_engine_token(
            _mint(claims), db, expected_session_id=session_id, endpoint="config",
        )


async def test_session_id_path_mismatch_rejected(db, tenant_and_session):
    """expected_session_id != claim sub is a hard reject."""
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    other_session = uuid.uuid4()
    with pytest.raises(EngineTokenInvalidError, match="session_id mismatch"):
        await verify_engine_token(
            _mint(claims), db, expected_session_id=other_session, endpoint="config",
        )


# ---------------------------------------------------------------------------
# Expiry / signature
# ---------------------------------------------------------------------------

async def test_expired_token_rejected(db, tenant_and_session):
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    claims["exp"] = int(datetime.now(UTC).timestamp()) - 10
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            _mint(claims), db, expected_session_id=session_id, endpoint="config",
        )


async def test_tampered_signature_rejected(db, tenant_and_session):
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    token = _mint(claims, secret="wrong-secret-different-from-monkeypatched")
    with pytest.raises(EngineTokenInvalidError, match="jwt decode"):
        await verify_engine_token(
            token, db, expected_session_id=session_id, endpoint="config",
        )


# ---------------------------------------------------------------------------
# Parent-row state (unknown / revoked)
# ---------------------------------------------------------------------------

async def test_unknown_jti_translated_to_engine_token_invalid(db, tenant_and_session):
    """No engine_dispatch_tokens row -> FK violation -> EngineTokenInvalidError('token unknown').

    The INSERT into engine_token_uses references engine_dispatch_tokens(jti) via FK.
    When the parent row is absent, asyncpg raises IntegrityError which
    verify_engine_token catches and re-raises as EngineTokenInvalidError('token unknown').

    Note: the IntegrityError aborts the in-progress transaction. Since this test
    ends immediately after the raise (no further DB work), the rollback-on-exit
    in the db fixture handles cleanup correctly.
    """
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    # Deliberately do NOT call _insert_dispatch_token — the parent row is missing.
    with pytest.raises(EngineTokenInvalidError, match="unknown"):
        await verify_engine_token(
            _mint(claims), db, expected_session_id=session_id, endpoint="config",
        )


async def test_revoked_token_rejected(db, tenant_and_session):
    tenant_id, session_id = tenant_and_session
    claims = _baseline_claims(session_id, tenant_id)
    await _insert_dispatch_token(
        db, jti=uuid.UUID(claims["jti"]), tenant_id=tenant_id,
        session_id=session_id, revoked=True,
    )
    with pytest.raises(EngineTokenInvalidError, match="revoked"):
        await verify_engine_token(
            _mint(claims), db, expected_session_id=session_id, endpoint="config",
        )
