"""FastAPI router for the Candidate Reel — /api/reports/session/{id}/reel*.

  GET  .../reel             — playback envelope (absent|pending|generating|ready|
                              failed) + eligibility, for the report page
  POST .../reel/generate    — create/reset pending + enqueue render (first trigger)
  POST .../reel/regenerate  — re-enqueue with force (version bump)

RBAC: reports.view or super-admin — the reel is a self-serve viewing aid (design
D8), so the same role set that views reports can generate one. Rate limiting is the
global middleware (no per-route limiter in this codebase). Triggering is audited.
"""
from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.modules.audit import log_event
from app.modules.auth import UserContext, get_current_user_roles
from app.modules.reel import service

router = APIRouter(prefix="/api/reports", tags=["reel"])
_log = structlog.get_logger("reel.router")

_MAX_CORRELATION_ID_LEN = 128


def _get_correlation_id(request: Request) -> str:
    raw = request.headers.get("x-correlation-id")
    if raw and 0 < len(raw) <= _MAX_CORRELATION_ID_LEN and raw.isascii() and raw.isprintable():
        return raw
    return str(uuid_mod.uuid4())


def _require_reports_view(user: UserContext) -> None:
    if "reports.view" not in user.all_permissions() and not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Missing reports.view")


@router.get("/session/{session_id}/reel", summary="Get the candidate reel playback")
async def get_reel_endpoint(
    session_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Return the reel playback envelope. ``status='absent'`` (+ eligibility) when
    no reel has been generated yet. RBAC: reports.view or super-admin."""
    _require_reports_view(user)
    tenant_id = user.user.tenant_id
    eligible, reason = await service.check_eligibility(
        db, session_id=session_id, tenant_id=tenant_id)
    reel = await service.get_reel(db, session_id=session_id, tenant_id=tenant_id)
    playback = await service.build_playback(reel, eligible=eligible, reason=reason)
    return playback.model_dump(mode="json")


async def _trigger(session_id: uuid_mod.UUID, request: Request, db: AsyncSession,
                   user: UserContext, *, force: bool, action: str) -> dict[str, str]:
    _require_reports_view(user)
    tenant_id = user.user.tenant_id
    correlation_id = _get_correlation_id(request)

    eligible, reason = await service.check_eligibility(
        db, session_id=session_id, tenant_id=tenant_id)
    if not eligible:
        raise HTTPException(status_code=422, detail=reason or "Reel not available")

    reel = await service.request_reel(
        db, session_id=session_id, tenant_id=tenant_id, created_by=user.user.id)
    await log_event(
        db, tenant_id=tenant_id, actor_id=user.user.id, actor_email=user.user.email,
        action=action, resource="session_reel", resource_id=reel.id,
        payload={"correlation_id": correlation_id, "version": reel.version},
    )
    await db.commit()

    # Lazy import (mirrors reporting/proctoring): keeps the API import graph light
    # and makes the enqueue trivially monkeypatchable in tests.
    from app.modules.reel.actors import generate_session_reel  # noqa: PLC0415

    generate_session_reel.send(str(session_id), str(tenant_id), correlation_id, force)
    _log.info("reel.trigger.enqueued", action=action, session_id=str(session_id),
              tenant_id=str(tenant_id), correlation_id=correlation_id, force=force)
    return {"status": "accepted", "session_id": str(session_id)}


@router.post("/session/{session_id}/reel/generate",
             status_code=status.HTTP_202_ACCEPTED, summary="Generate the candidate reel")
async def generate_reel_endpoint(
    session_id: uuid_mod.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    return await _trigger(session_id, request, db, user, force=False, action="reel.generate")


@router.post("/session/{session_id}/reel/regenerate",
             status_code=status.HTTP_202_ACCEPTED, summary="Regenerate the candidate reel")
async def regenerate_reel_endpoint(
    session_id: uuid_mod.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    return await _trigger(session_id, request, db, user, force=True, action="reel.regenerate")
