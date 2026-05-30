# app/modules/vision/actors.py
"""Dramatiq actor: post-session vision proctoring analysis (vision queue).

Idempotent on session_id (status row). Runs on a bypass-RLS session with an
explicit tenant_id filter on every query (RLS-only defense, mirrors
interview_runtime.service). Persists FEATURES ONLY — never frames (spec §16).
"""
from __future__ import annotations

import os
import tempfile
import uuid

import dramatiq
import structlog
from sqlalchemy import select, text

from app.database import get_bypass_session
from app.modules.session.models import Session
from app.modules.vision.analysis import run_analysis  # light: no cv2/torch at import
from app.modules.vision.config import vision_config
from app.modules.vision.models import SessionProctoringAnalysis
from app.storage import get_object_storage

# NOTE: run_analysis is re-exported here so tests can monkeypatch
# `vision_actors.run_analysis`. The heavy gaze import stays inside _run.

log = structlog.get_logger("vision.actor")

# Genuinely-finished SUCCESS states. A row in one of these is never re-analyzed.
# A 'running'/'failed'/'pending' row is RECLAIMED and re-analyzed — so Dramatiq's
# own retries (and a re-enqueue after a worker crash) actually re-run, instead of
# being silently swallowed by the idempotency gate.
_DONE = {"ready", "unscorable"}


async def _load_state(db, session_id: str, tenant_id: str):
    """Return ``(action, recording_key)`` where action is:
      "skip" — an existing row is already done (ready/unscorable); do nothing.
      "run"  — (re)analyze; recording_key is the R2 object key. A pre-existing
               running/failed/pending row is RECLAIMED to 'running' (not
               duplicated) so retries/crash-recovery re-run cleanly.
      "none" — the session has no usable recording yet; do nothing.
    """
    sid = uuid.UUID(session_id)
    tid = uuid.UUID(tenant_id)
    existing = (
        await db.execute(
            select(SessionProctoringAnalysis).where(
                SessionProctoringAnalysis.session_id == sid,
                SessionProctoringAnalysis.tenant_id == tid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status in _DONE:
        return "skip", None

    sess = (
        await db.execute(
            select(Session).where(Session.id == sid, Session.tenant_id == tid)
        )
    ).scalar_one_or_none()
    if sess is None or sess.recording_status != "ready" or not sess.recording_s3_key:
        return "none", None

    if existing is not None:
        # Reclaim a crashed/failed/pending row — re-drive it rather than insert
        # a duplicate (session_id is UNIQUE).
        existing.status = "running"
        existing.error = None
    else:
        db.add(SessionProctoringAnalysis(tenant_id=tid, session_id=sid, status="running"))
    return "run", sess.recording_s3_key


async def _persist(
    db, session_id: str, tenant_id: str, *, status: str, result=None, frames=0, error=None
):
    sid = uuid.UUID(session_id)
    tid = uuid.UUID(tenant_id)
    row = (
        await db.execute(
            select(SessionProctoringAnalysis).where(
                SessionProctoringAnalysis.session_id == sid,
                SessionProctoringAnalysis.tenant_id == tid,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Phase-1 insert never committed (rare: DB error between create + commit).
        # Don't raise here — that would mask the original exception on the error
        # path. The re-enqueue will recreate the row.
        log.warning("vision.actor.persist_no_row", session_id=session_id)
        return
    row.status = status
    row.error = error
    row.frames_analyzed = frames
    if result is not None:
        row.risk_band = result.risk_band
        row.detector_summary = result.detector_summary
        row.gaze_heatmap = result.gaze_heatmap
        row.flagged_intervals = result.flagged_intervals
        row.gaze_signal_quality = result.gaze_signal_quality
        row.unscorable_pct = result.unscorable_pct
        row.model_versions = {
            "gaze": "mobilegaze-gaze360",
            "weights_path": vision_config.gaze_weights_path,
            "arch": vision_config.gaze_arch,
            "pipeline": "v1",
        }


async def _run(session_id: str, tenant_id: str) -> None:
    safe_tid = str(uuid.UUID(tenant_id))

    # Phase 1: idempotency gate + claim/reclaim a 'running' row (own transaction).
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
        action, recording_key = await _load_state(db, session_id, tenant_id)
        await db.commit()

    if action == "skip":
        log.info("vision.actor.skip_already_done", session_id=session_id)
        return
    if action == "none":
        log.info("vision.actor.no_recording", session_id=session_id)
        return
    # action == "run" → recording_key is set; proceed.

    # Phase 2: heavy work OUTSIDE the DB transaction.
    try:
        from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator  # noqa: PLC0415

        estimator = MobileGazeEstimator(
            weights_path=vision_config.gaze_weights_path,
            input_size=vision_config.gaze_input_size,
            pitch_sign=vision_config.gaze_pitch_sign,
            yaw_sign=vision_config.gaze_yaw_sign,
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "recording.mp4")
            await get_object_storage().download_to_path(recording_key, dest)
            result, frames = run_analysis(estimator, local_video_path=dest)
        final_status = "unscorable" if result.risk_band == "insufficient_data" else "ready"
    except Exception as exc:  # noqa: BLE001
        log.error("vision.actor.failed", session_id=session_id, exc_info=exc)
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
            await _persist(db, session_id, tenant_id, status="failed", error=str(exc)[:500])
            await db.commit()
        raise

    # Phase 3: persist results (own transaction).
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
        await _persist(db, session_id, tenant_id, status=final_status, result=result, frames=frames)
        await db.commit()
    log.info("vision.actor.done", session_id=session_id, band=result.risk_band, frames=frames)


@dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=120_000, queue_name="vision")
async def analyze_session_proctoring(session_id: str, tenant_id: str) -> None:
    await _run(session_id, tenant_id)
