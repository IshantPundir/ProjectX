"""Public, unauthenticated, token-gated recordings endpoint.

GET /api/public/recordings/{token} — resolve an opaque capability token to a
session's full playback envelope (report + recording + proctoring + reel).

This router's prefix is allowlisted in app/middleware/auth.py::_PUBLIC_PREFIXES,
so no Supabase JWT is required. Tenant isolation is enforced by resolving the
tenant from the token's row and scoping every downstream read by that tenant_id
on a bypass-RLS session (the interview_runtime defense pattern). Any invalid /
revoked / expired / unknown token returns a UNIFORM 404 (no enumeration oracle).

Rate limiting: this codebase enforces limits via global middleware only; the
intended 'public share' class (30/min per-IP + 60/hour per-token) is documented
in the design spec + root CLAUDE.md.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.modules.reporting.public_share import (
    build_public_envelope,
    resolve_share_token,
)

router = APIRouter(prefix="/api/public/recordings", tags=["public-recordings"])
_log = structlog.get_logger("reporting.public_router")


@router.get("/{token}", summary="Public recordings playback by share token")
async def get_public_recordings(
    token: str,
    db: AsyncSession = Depends(get_bypass_db),
) -> Any:
    share = await resolve_share_token(db, token)
    if share is None:
        raise HTTPException(status_code=404, detail="Not found")

    envelope = await build_public_envelope(db, share)
    if envelope is None:
        raise HTTPException(status_code=404, detail="Not found")

    # View tracking — best-effort: incrementing an int + timestamp on a row we
    # just read. Persisted by the get_bypass_db transaction on clean exit. A
    # flush failure here must never turn a successful playback fetch into a 500.
    try:
        share.view_count = (share.view_count or 0) + 1
        share.last_viewed_at = datetime.now(UTC)
        await db.flush()
    except Exception:  # noqa: BLE001
        _log.warning("reporting.public_recordings.view_bump_failed",
                     share_id=str(share.id))

    _log.info("reporting.public_recordings.served",
              share_id=str(share.id), session_id=str(share.session_id))
    return envelope.model_dump(mode="json")
