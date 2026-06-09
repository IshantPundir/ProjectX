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
from app.modules.session.livekit import get_recording_status, recording_object_key
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


async def _reconcile_without_egress(db: AsyncSession, sess: Session) -> None:
    """Resolve a still-"recording" row when LiveKit has no egress record.

    Either the egress never started (e.g. a quota rejection) or LiveKit purged
    a finished egress. The recording in object storage is the authoritative
    ground truth, so check it directly; only when it's genuinely absent do we
    apply the stuck-timeout. Best-effort — never raises into the playback path.
    """
    key = recording_object_key(tenant_id=sess.tenant_id, session_id=sess.id)
    try:
        meta = await get_object_storage().head(key)
    except Exception:  # noqa: BLE001 — storage hiccup leaves the row for next read
        log.warning("recording.storage_head_failed", session_id=str(sess.id), exc_info=True)
        return
    if meta is not None:
        # The egress record (which carries duration) is gone, and ObjectMeta has
        # no duration — so recording_duration_seconds stays None on this path.
        # RecordingPlayback.duration_seconds is nullable; the UI handles None.
        sess.recording_status = "ready"
        sess.recording_ready_at = datetime.now(UTC)
        sess.recording_s3_key = key
        sess.recording_bytes = meta.size_bytes
        await db.flush()
        return
    # Object truly absent — handled by the stuck-timeout in Task 3.
    if _recording_stuck_expired(sess):
        sess.recording_status = "failed"
        await db.flush()


def _recording_stuck_expired(sess: Session) -> bool:
    """True once the grace window past agent_completed_at has elapsed.

    Returns False when the session never recorded a completion timestamp — we
    don't fail a recording we can't time.
    """
    completed = sess.agent_completed_at
    if completed is None:
        return False
    grace = timedelta(seconds=settings.recording_stuck_timeout_seconds)
    return (datetime.now(UTC) - completed) > grace


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
        await _reconcile_without_egress(db, sess)
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
    if not settings.auto_analyze_proctoring:
        log.info(
            "session.recording.vision_analysis_disabled",
            session_id=session_id,
            reason="auto_analyze_proctoring=false",
        )
        return
    # Imported here (not module top) to keep the import graph obviously light
    # and to make monkeypatching in tests trivial.
    from app.modules.vision import analyze_session_proctoring

    analyze_session_proctoring.send(session_id, tenant_id)


async def _vision_analysis_needs_enqueue(
    db: AsyncSession, session_id: UUID, tenant_id: UUID
) -> bool:
    """Decide whether to (re)enqueue vision analysis, given the current row.

    The report page calls this on every read, so it must NOT pile work onto an
    in-flight or settled analysis. Re-enqueue only when:
      - there is no row yet (never analyzed), or
      - a running/pending row has gone stale (the worker that owned it is
        presumed dead — a crash leaves the row running/pending, never failed).
    A ``ready``/``unscorable`` row is done; a ``failed`` row has already
    exhausted Dramatiq's own per-message retries, so re-driving it here would
    slow-loop a genuinely-broken recording — recovery is an explicit re-trigger,
    not a side effect of viewing the report. All three are left alone.
    """
    from app.modules.vision.models import SessionProctoringAnalysis  # noqa: PLC0415

    row = (
        await db.execute(
            select(
                SessionProctoringAnalysis.status,
                SessionProctoringAnalysis.updated_at,
            ).where(
                SessionProctoringAnalysis.session_id == session_id,
                SessionProctoringAnalysis.tenant_id == tenant_id,
            )
        )
    ).first()
    if row is None:
        return True  # never analyzed
    status, updated_at = row
    # Terminal from the report-read side — never auto-re-enqueue (see docstring).
    if status in ("ready", "unscorable", "failed"):
        return False
    # running / pending: only re-drive if stale (the in-flight worker is gone).
    if updated_at is None:
        return True
    stale_after = timedelta(seconds=settings.vision_reenqueue_stale_after_seconds)
    return (datetime.now(UTC) - updated_at) > stale_after


async def _maybe_enqueue_vision(db: AsyncSession, sess: Session) -> None:
    """Best-effort: enqueue post-session vision analysis once the recording is
    ready — but only when it genuinely needs to run (see
    ``_vision_analysis_needs_enqueue``). Never raises into the playback path.
    """
    if sess.recording_status != "ready" or not sess.recording_s3_key:
        return
    try:
        if not await _vision_analysis_needs_enqueue(db, sess.id, sess.tenant_id):
            return
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
    await _maybe_enqueue_vision(db, sess)

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
