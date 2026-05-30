"""Session recording playback — read + pull-based reconcile.

The report page's player calls this to get a streamable recording. Design
choice: **pull-based reconcile**, not inbound webhooks. When the recording is
still ``recording``, we poll LiveKit for the egress's current state and
advance the row to ``ready``/``failed`` on read. This works in any
environment (no public webhook URL / tunnel needed), is idempotent, and
doubles as a backstop if a webhook is ever added. Once ``ready``, no more
polling happens — we just mint a short-lived presigned GET URL.

Security: the recording is candidate PII. The bucket is private; the only
way to view it is this endpoint minting a time-limited signed URL under the
caller's tenant scope (RLS-bound session + explicit tenant filter). Signed
URLs are never logged.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.session.errors import SessionNotFoundError
from app.modules.session.livekit import get_recording_status
from app.modules.session.models import Session
from app.storage import get_object_storage

log = structlog.get_logger("session.recording")


class TranscriptSegment(BaseModel):
    """One transcript utterance, timed relative to interview start."""

    role: str
    text: str
    t_ms: int


class RecordingPlayback(BaseModel):
    """What the report-page player needs to render the recording.

    ``status`` is the source of truth for the UI:
      absent    — no recording was made (or recording disabled)
      recording — still capturing / uploading; player shows "processing"
      ready     — ``signed_url`` is set; play it
      failed    — recording failed; player shows an honest "unavailable"
    """

    status: str
    signed_url: str | None = None
    expires_at: datetime | None = None
    duration_seconds: int | None = None
    # Offset to map transcript timestamps (ms since interview start) onto the
    # video timeline (which begins at egress start). 0 for now — the lead-in
    # gap is a few seconds; calibrate here once measured against a real
    # recording. Exposed so the frontend applies it without a code change.
    offset_ms: int = 0
    transcript: list[TranscriptSegment] = []


def _build_transcript(raw: list | None) -> list[TranscriptSegment]:
    """Map the persisted transcript JSONB into player-friendly segments.

    The engine stores entries as {role, text, timestamp_ms, question_id}.
    Malformed/partial entries are skipped rather than failing the response.
    """
    segments: list[TranscriptSegment] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        text = entry.get("text")
        t_ms = entry.get("timestamp_ms")
        if role is None or text is None or t_ms is None:
            continue
        segments.append(TranscriptSegment(role=str(role), text=str(text), t_ms=int(t_ms)))
    return segments


async def _reconcile(db: AsyncSession, sess: Session) -> None:
    """Advance a still-recording session to ready/failed by polling LiveKit.

    Best-effort: a transient LiveKit error leaves the row as-is so the next
    read retries. Never raises.
    """
    if sess.recording_status != "recording" or not sess.livekit_room_name:
        return
    try:
        snap = await get_recording_status(sess.livekit_room_name)
    except Exception:
        log.warning(
            "recording.reconcile_failed",
            session_id=str(sess.id),
            exc_info=True,
        )
        return

    if snap is None:
        return

    # Capture the egress id once LiveKit has assigned it (auto-egress assigns
    # it only after the recording starts).
    if snap.egress_id and not sess.recording_egress_id:
        sess.recording_egress_id = snap.egress_id

    if snap.status == "recording":
        if snap.egress_id:
            await db.flush()
        return

    sess.recording_status = snap.status
    if snap.status == "ready":
        sess.recording_ready_at = datetime.now(UTC)
        sess.recording_duration_seconds = snap.duration_seconds
        sess.recording_bytes = snap.size_bytes
        if snap.key:
            sess.recording_s3_key = snap.key
    await db.flush()


def _enqueue_vision_analysis(session_id: str, tenant_id: str) -> None:
    # Imported here (not module top) to keep the import graph obviously light
    # and to make monkeypatching in tests trivial.
    from app.modules.vision import analyze_session_proctoring

    analyze_session_proctoring.send(session_id, tenant_id)


def _maybe_enqueue_vision(sess: Session) -> None:
    """Best-effort: enqueue post-session vision analysis once the recording is
    ready. The actor is idempotent (its own status row), so re-enqueue on every
    report read is safe. Never raises into the playback path.
    """
    if sess.recording_status != "ready" or not sess.recording_s3_key:
        return
    try:
        _enqueue_vision_analysis(str(sess.id), str(sess.tenant_id))
    except Exception:  # noqa: BLE001
        log.warning("recording.vision_enqueue_failed", session_id=str(sess.id), exc_info=True)


async def get_session_recording_playback(
    db: AsyncSession, *, session_id: UUID, tenant_id: UUID
) -> RecordingPlayback:
    """Return playback info for a session's recording (tenant-scoped).

    Raises SessionNotFoundError if the session does not exist for this tenant
    (cross-tenant access returns 0 rows → 404 at the endpoint).
    """
    sess = (
        await db.execute(
            select(Session).where(
                Session.id == session_id,
                Session.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if sess is None:
        raise SessionNotFoundError()

    await _reconcile(db, sess)
    _maybe_enqueue_vision(sess)

    transcript = _build_transcript(sess.transcript)

    if sess.recording_status == "ready" and sess.recording_s3_key:
        ttl = settings.recording_signed_url_ttl_seconds
        url = await get_object_storage().presign_get_url(
            sess.recording_s3_key, ttl_seconds=ttl
        )
        return RecordingPlayback(
            status="ready",
            signed_url=url,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            duration_seconds=sess.recording_duration_seconds,
            transcript=transcript,
        )

    return RecordingPlayback(status=sess.recording_status, transcript=transcript)
