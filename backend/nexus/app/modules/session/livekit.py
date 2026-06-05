"""LiveKit provisioning helpers for /start.

All LiveKit server-SDK calls happen here. start_session() in service.py
calls these helpers; tests mock at this module's surface (rather than
patching deeper into the SDK) so unit tests can run without the SDK's
real network behavior.

Phase 3C.2 — replaces the 501 LIVEKIT_INTEGRATION_PENDING stub.

IMPORTANT: `livekit.api` is imported LAZILY (inside each function/helper that
needs it). This module is transitively imported by `app.main` at startup via
`session/__init__.py` → `session/recording.py`; a top-level `from livekit import
api` would pull the entire LiveKit server SDK into the FastAPI process import
graph, violating the livekit-isolation invariant (only the engine container should
load livekit). The lazy pattern costs nothing at call time (the import is cached
by Python's module system after the first call).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.config import settings


def _lk_api():
    """Return the livekit.api module — lazy import so FastAPI never loads livekit at startup."""
    from livekit import api as _api
    return _api


def _lk_client():
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
    livekit_api = _lk_api()
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
    livekit_api = _lk_api()
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


async def create_room(
    *, room_name: str, egress: object | None = None
) -> None:
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

    When ``egress`` is provided, it configures **auto egress**: LiveKit
    starts the recording automatically when the first participant joins
    (no empty-room race) and stops it when the room empties. See
    ``build_room_egress``.
    """
    livekit_api = _lk_api()
    kwargs: dict = {
        "name": room_name,
        "empty_timeout": settings.livekit_room_empty_timeout_seconds,
    }
    if egress is not None:
        kwargs["egress"] = egress
    lk = _lk_client()
    try:
        await lk.room.create_room(livekit_api.CreateRoomRequest(**kwargs))
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
    livekit_api = _lk_api()
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


# --- Session recording (RoomComposite Auto Egress → S3-compatible store) ---


@dataclass(frozen=True)
class EgressSnapshot:
    """Normalized egress state — keeps the LiveKit SDK enum out of callers.

    ``status`` is one of: 'recording' (starting/active/ending), 'ready'
    (complete), 'failed' (failed/aborted/limit). ``egress_id``/``key``/
    ``duration_seconds``/``size_bytes`` are populated as they become known.
    """

    status: str
    egress_id: str | None
    key: str | None
    duration_seconds: int | None
    size_bytes: int | None


def recording_object_key(*, tenant_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Deterministic, tenant-prefixed object key for a session recording.

    Tenant prefix gives per-tenant isolation in the bucket and makes
    lifecycle / retention rules expressible per tenant.
    """
    return f"{settings.recording_key_prefix}/{tenant_id}/{session_id}.mp4"


def _build_recording_file_output(key: str) -> object:
    """Build the MP4 + S3 upload target from provider-agnostic settings.

    The S3Upload speaks the S3 protocol, so the same code targets Cloudflare
    R2, AWS S3, Supabase Storage, or MinIO — only the settings differ.
    """
    livekit_api = _lk_api()
    return livekit_api.EncodedFileOutput(
        file_type=livekit_api.EncodedFileType.MP4,
        filepath=key,
        s3=livekit_api.S3Upload(
            access_key=settings.recording_storage_access_key_id,
            secret=settings.recording_storage_secret_access_key,
            bucket=settings.recording_storage_bucket,
            region=settings.recording_storage_region,
            endpoint=settings.recording_storage_endpoint_url,
            force_path_style=settings.recording_storage_force_path_style,
        ),
    )


def build_room_egress(
    *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[object, str]:
    """Build an auto-egress config + the object key for a session recording.

    Attaching this to ``create_room`` makes LiveKit start a RoomComposite
    egress automatically when the first participant joins — one MP4 with the
    candidate camera full-frame (single video publisher under the configured
    layout) + mixed candidate/agent audio — and stop it when the room empties.
    No ``room_name`` is set on the inner request: the room being created
    supplies it.

    Pure (no I/O); returns the config to attach and the deterministic key.
    """
    livekit_api = _lk_api()
    key = recording_object_key(tenant_id=tenant_id, session_id=session_id)
    room_req = livekit_api.RoomCompositeEgressRequest(
        layout=settings.recording_egress_layout,
        audio_only=False,
        file_outputs=[_build_recording_file_output(key)],
        preset=getattr(
            livekit_api.EncodingOptionsPreset, settings.recording_egress_preset
        ),
    )
    return livekit_api.RoomEgress(room=room_req), key


def _normalize_egress_status(info: object) -> str:
    livekit_api = _lk_api()
    complete = {livekit_api.EgressStatus.EGRESS_COMPLETE}
    failed = {
        livekit_api.EgressStatus.EGRESS_FAILED,
        livekit_api.EgressStatus.EGRESS_ABORTED,
        livekit_api.EgressStatus.EGRESS_LIMIT_REACHED,
    }
    if info.status in complete:  # type: ignore[union-attr]
        return "ready"
    if info.status in failed:  # type: ignore[union-attr]
        return "failed"
    return "recording"


async def get_recording_status(room_name: str) -> EgressSnapshot | None:
    """Poll LiveKit for a room's egress state (pull-based reconcile).

    Looks up the egress by room name (auto-egress assigns the id only once it
    starts). Returns None if LiveKit has no egress for the room yet. On
    completion the file result carries duration (ns → seconds) and size.
    """
    livekit_api = _lk_api()
    lk = _lk_client()
    try:
        resp = await lk.egress.list_egress(
            livekit_api.ListEgressRequest(room_name=room_name)
        )
    finally:
        await lk.aclose()

    if not resp.items:
        return None
    # One room-composite egress per room in our flow; if more, take the
    # latest by start time.
    info = sorted(resp.items, key=lambda i: i.started_at or 0)[-1]
    status = _normalize_egress_status(info)

    key: str | None = None
    duration_seconds: int | None = None
    size_bytes: int | None = None
    if info.file_results:
        f = info.file_results[0]
        key = f.filename or None
        # LiveKit reports duration in nanoseconds.
        duration_seconds = int(f.duration / 1_000_000_000) if f.duration else None
        size_bytes = int(f.size) if f.size else None

    return EgressSnapshot(
        status=status,
        egress_id=info.egress_id or None,
        key=key,
        duration_seconds=duration_seconds,
        size_bytes=size_bytes,
    )


async def cancel_room(room_name: str) -> None:
    """Best-effort room delete — used when /start loses the consume race.

    Failure is silently swallowed by the caller (start_session wraps this
    in contextlib.suppress) — the room may already be partially set up,
    fully torn down, or unreachable due to LiveKit transient failure. We
    still raise the original token-consume error in either case.
    """
    livekit_api = _lk_api()
    lk = _lk_client()
    try:
        await lk.room.delete_room(
            livekit_api.DeleteRoomRequest(room=room_name)
        )
    finally:
        await lk.aclose()
