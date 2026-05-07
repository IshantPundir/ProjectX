"""LiveKit provisioning helpers for /start.

All LiveKit server-SDK calls happen here. start_session() in service.py
calls these helpers; tests mock at this module's surface (rather than
patching deeper into the SDK) so unit tests can run without the SDK's
real network behavior.

Phase 3C.2 — replaces the 501 LIVEKIT_INTEGRATION_PENDING stub.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from livekit import api as livekit_api

from app.config import settings


def _lk_client() -> livekit_api.LiveKitAPI:
    """Construct a LiveKitAPI client bound to this deployment's keys.

    The client is single-use per call site — we close via aclose() in a
    try/finally rather than caching at module scope. The official Python
    SDK example follows the same explicit-construct/aclose pattern.

    IMPORTANT: This function must only be called from within an async
    function. LiveKitAPI.__init__ calls asyncio.get_running_loop()
    internally (via aiohttp.ClientSession); calling it outside an async
    context raises RuntimeError. Both dispatch_agent and cancel_room are
    async, so this constraint is always satisfied at call sites.
    """
    return livekit_api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )


def mint_candidate_lk_token(
    *, room_name: str, identity: str, name: str, ttl_minutes: int
) -> str:
    """Mint a LiveKit AccessToken JWT for the candidate's browser.

    Grants: room_join + can_publish + can_subscribe scoped to this room.
    Explicitly NOT can_publish_data (spec Section 6.4 — defer data-channel
    access to a later round once we actually have a use for it).

    This function is intentionally sync — it does no I/O and does not
    touch the LiveKitAPI client. Callers must supply real api_key and
    api_secret (from settings) for the JWT to be verifiable by LiveKit.
    """
    grants = livekit_api.VideoGrants(
        room=room_name,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=False,
    )
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grants)
        .with_attributes({"role": "candidate"})
        .with_ttl(timedelta(minutes=ttl_minutes))
    )
    return token.to_jwt()


async def create_room(*, room_name: str) -> None:
    """Pre-create the LiveKit room with an explicit ``empty_timeout``.

    LiveKit auto-creates rooms on first dispatch with its default
    ``empty_timeout`` of 5 minutes — so a room idles for 5 min after
    the last participant leaves before LiveKit deletes it. That
    lingering keeps the dashboard "Active" and the agent worker
    process alive far longer than necessary.

    Pre-creating the room lets us shrink that window while still
    leaving enough time for LiveKit Cloud's Agent Insights ingest +
    OTel batch exporters to drain after the conversation ends.
    Idempotent on the LiveKit side — calling create with an existing
    name updates the timeouts in-place.
    """
    lk = _lk_client()
    try:
        await lk.room.create_room(
            livekit_api.CreateRoomRequest(
                name=room_name,
                empty_timeout=settings.livekit_room_empty_timeout_seconds,
            )
        )
    finally:
        await lk.aclose()


async def dispatch_agent(
    *,
    room_name: str,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """Explicitly dispatch the named agent into a room.

    LiveKit auto-creates the room if it doesn't exist. The metadata JSON
    is what reaches the agent worker via JobContext.job.metadata — the
    engine reads session_id + tenant_id + correlation_id and uses the
    tenant_id to scope its DB queries via in-process ``build_session_config``
    / ``record_session_result`` (Phase 3 retired the engine-dispatch JWT;
    RLS + explicit-tenant filters are the new defense layer).

    Exceptions from the SDK propagate to start_session, which translates
    them to AgentDispatchFailedError before the candidate token is
    consumed.
    """
    metadata = json.dumps({
        "session_id": str(session_id),
        "tenant_id": str(tenant_id),
        "correlation_id": correlation_id,
    })
    lk = _lk_client()
    try:
        await lk.agent_dispatch.create_dispatch(
            livekit_api.CreateAgentDispatchRequest(
                agent_name=settings.engine_agent_name,
                room=room_name,
                metadata=metadata,
            )
        )
    finally:
        await lk.aclose()


async def cancel_room(room_name: str) -> None:
    """Best-effort room delete — used when /start loses the consume race.

    Failure is silently swallowed by the caller (start_session wraps this
    in contextlib.suppress) — the room may already be partially set up,
    fully torn down, or unreachable due to LiveKit transient failure. We
    still raise the original token-consume error in either case.
    """
    lk = _lk_client()
    try:
        await lk.room.delete_room(
            livekit_api.DeleteRoomRequest(room=room_name)
        )
    finally:
        await lk.aclose()
