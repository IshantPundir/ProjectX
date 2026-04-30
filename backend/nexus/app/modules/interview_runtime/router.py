"""interview_runtime — internal endpoints called by the LiveKit agent worker.

Auth: HS256 engine JWT verified per-request via verify_engine_token().
The reverse proxy (Railway / ECS LB) is configured to NOT route /api/internal/*
from the public hostname; the JWT is still the load-bearing gate.

The auth middleware skips this prefix entirely (see _PUBLIC_PREFIXES in
app/middleware/auth.py) — without that exemption every request would be
rejected before reaching this router because the engine sends an HS256
bearer, not a Supabase ES256 token.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.modules.audit.service import log_event
from app.modules.auth.service import verify_engine_token
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    EngineTokenInvalidError,
    QuestionBankNotReadyError,
    SessionNotActiveError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.schemas import SessionConfig, SessionResult
from app.modules.interview_runtime.service import (
    build_session_config,
    record_session_result,
)

logger = structlog.get_logger()

interview_runtime_router = APIRouter(
    prefix="/api/internal/sessions", tags=["interview-runtime-internal"]
)


def _bearer(request: Request) -> str:
    """Extract the Bearer token, return 401 with opaque body on any failure.

    Per spec Section 6.2 the 401 body never differentiates failure modes —
    only ``{"code": "ENGINE_TOKEN_INVALID"}``.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail={"code": "ENGINE_TOKEN_INVALID"}
        )
    return auth[7:].strip()


@interview_runtime_router.get("/{session_id}/config", response_model=SessionConfig)
async def get_config(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> SessionConfig:
    """Return the SessionConfig for an active session.

    The engine worker calls this once after dispatch metadata arrives.
    Single-use per (jti, 'config') — replays return 401.
    """
    token = _bearer(request)
    ip = request.client.host if request.client else None
    try:
        payload = await verify_engine_token(
            token, db,
            expected_session_id=session_id, endpoint="config", used_ip=ip,
        )
    except EngineTokenInvalidError:
        # Do NOT log str(exc) — message can include claim values that PyJWT /
        # Pydantic put there. Log only the safe fields.
        logger.warning(
            "engine.config.token_rejected", endpoint="config", session_id=str(session_id),
        )
        raise HTTPException(
            status_code=401, detail={"code": "ENGINE_TOKEN_INVALID"}
        )

    try:
        config = await build_session_config(
            db, session_id=session_id, tenant_id=payload.tenant_id
        )
    except StageNotAiDrivenError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "STAGE_TYPE_NOT_AI_DRIVEN",
                "stage_type": exc.stage_type,
            },
        )
    except QuestionBankNotReadyError:
        raise HTTPException(status_code=409, detail={"code": "BANK_NOT_READY"})
    except CompanyProfileMissingError:
        raise HTTPException(
            status_code=422, detail={"code": "COMPANY_PROFILE_MISSING"}
        )
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})

    # Audit on success — spec Section 6.2.
    await log_event(
        db,
        tenant_id=payload.tenant_id,
        actor_id=None,
        actor_email=None,
        action="engine.config.fetch",
        resource="session",
        resource_id=session_id,
        payload={"jti_prefix": str(payload.jti)[:8]},
        ip_address=ip,
    )
    return config


@interview_runtime_router.post(
    "/{session_id}/results", status_code=status.HTTP_204_NO_CONTENT
)
async def post_results(
    session_id: uuid.UUID,
    payload: SessionResult,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> None:
    """Record the engine's SessionResult and complete the session.

    record_session_result writes its own audit row
    (action='engine.session.completed') on the successful active->completed
    transition, so this handler does NOT write a second audit entry.
    """
    token = _bearer(request)
    ip = request.client.host if request.client else None
    try:
        claims = await verify_engine_token(
            token, db,
            expected_session_id=session_id, endpoint="results", used_ip=ip,
        )
    except EngineTokenInvalidError:
        logger.warning(
            "engine.result.token_rejected", endpoint="results", session_id=str(session_id),
        )
        raise HTTPException(
            status_code=401, detail={"code": "ENGINE_TOKEN_INVALID"}
        )

    try:
        await record_session_result(
            db,
            session_id=session_id,
            tenant_id=claims.tenant_id,
            result=payload,
            jti=claims.jti,
        )
    except SessionNotActiveError:
        raise HTTPException(
            status_code=409, detail={"code": "SESSION_NOT_ACTIVE"}
        )
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})
