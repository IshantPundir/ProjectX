# app/modules/vision/actors.py
"""Dramatiq actors: post-session vision proctoring + session thumbnail generation.

Idempotent on session_id (status row for proctoring). Runs on a bypass-RLS
session with an explicit tenant_id filter on every query (RLS-only defense,
mirrors interview_runtime.service). Persists FEATURES ONLY — never frames (§16).

Two actors live here:
  analyze_session_proctoring  — gaze analysis + flag thumbnails (vision queue)
  generate_session_thumbnails — per-question thumbnails for the report (vision queue),
                                decoupled from proctoring so they run for every
                                session that has a recording.
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid

import dramatiq
import structlog
from sqlalchemy import select, text

from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_runtime import asked_at_ms_by_question_evidence
from app.modules.session.models import Session
from app.modules.vision.analysis import (  # light: cv2 imports lazily inside grab_thumbnails
    grab_thumbnails,
    run_analysis,
    select_flag_targets,
)
from app.modules.vision.config import vision_config
from app.modules.vision.models import SessionProctoringAnalysis, SessionTimelineThumbnail
from app.storage import get_object_storage

# NOTE: run_analysis is re-exported here so tests can monkeypatch
# `vision_actors.run_analysis`. The heavy gaze import stays inside _run.

log = structlog.get_logger("vision.actor")

# Genuinely-finished SUCCESS states. A row in one of these is never re-analyzed.
# A 'running'/'failed'/'pending' row is RECLAIMED and re-analyzed — so Dramatiq's
# own retries (and a re-enqueue after a worker crash) actually re-run, instead of
# being silently swallowed by the idempotency gate.
_DONE = {"ready", "unscorable"}

# Process-level gaze estimator. Model load + RetinaFace init are costly; build
# ONCE per worker process (was previously rebuilt on every actor call). With
# --threads 1 there is no intra-process race, but the lock keeps it correct if
# worker concurrency is ever raised.
_estimator = None
_estimator_lock = threading.Lock()


def _get_estimator():
    global _estimator
    if _estimator is None:
        with _estimator_lock:
            if _estimator is None:
                from app.modules.vision.gaze.mobilegaze import (  # noqa: PLC0415
                    MobileGazeEstimator,
                )

                _estimator = MobileGazeEstimator(
                    weights_path=vision_config.gaze_weights_path,
                    input_size=vision_config.gaze_input_size,
                    pitch_sign=vision_config.gaze_pitch_sign,
                    yaw_sign=vision_config.gaze_yaw_sign,
                    intra_op_threads=vision_config.ort_intra_op_threads,
                    providers=vision_config.onnx_providers,
                )
    return _estimator


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


async def _grab_and_upsert_thumbnails(
    db, *, session_id: str, tenant_id: str, local_video_path: str,
    targets: list[tuple[str, str, int]],  # (kind, ref_id, t_ms)
) -> None:
    """Grab frames at the target timestamps, upload to R2, upsert rows.

    Best-effort: grab/upload failures are swallowed. Deterministic keys →
    re-runs overwrite the same objects/rows.
    """
    if not targets:
        return
    sid = uuid.UUID(session_id)
    tid = uuid.UUID(tenant_id)
    unique_ms = sorted({t_ms for _, _, t_ms in targets})
    try:
        frames = grab_thumbnails(
            local_video_path, unique_ms,
            width=vision_config.thumbnail_width_px,
            webp_quality=vision_config.thumbnail_webp_quality,
        )
    except Exception:  # noqa: BLE001
        log.warning("vision.thumbnails.grab_failed", session_id=session_id, exc_info=True)
        return
    prefix = settings.thumbnail_key_prefix
    storage = get_object_storage()
    for kind, ref_id, t_ms in targets:
        blob = frames.get(t_ms)
        if not blob:
            continue
        key = f"{prefix}/{tenant_id}/{session_id}/{kind}_{ref_id}.webp"
        try:
            await storage.upload_bytes(key, blob, content_type="image/webp")
        except Exception:  # noqa: BLE001
            log.warning("vision.thumbnails.upload_failed",
                        session_id=session_id, key=key, exc_info=True)
            continue
        existing = (await db.execute(
            select(SessionTimelineThumbnail).where(
                SessionTimelineThumbnail.session_id == sid,
                SessionTimelineThumbnail.tenant_id == tid,
                SessionTimelineThumbnail.kind == kind,
                SessionTimelineThumbnail.ref_id == ref_id,
            )
        )).scalar_one_or_none()
        if existing is None:
            db.add(SessionTimelineThumbnail(
                tenant_id=tid, session_id=sid, kind=kind, ref_id=ref_id,
                t_ms=t_ms, s3_key=key))
        else:
            existing.t_ms = t_ms
            existing.s3_key = key


async def _persist_flag_thumbnails(
    db, *, session_id: str, tenant_id: str, local_video_path: str,
    flagged_intervals: list[dict],
) -> None:
    """Proctoring-VIOLATION frames only (kind='flag'). One per flagged interval."""
    targets: list[tuple[str, str, int]] = []
    for flag in select_flag_targets(
        flagged_intervals, max_count=vision_config.thumbnail_max_flag_count
    ):
        start = flag.get("start_ms")
        if start is None:
            continue
        start = int(start)
        targets.append(("flag", str(start), start))
    await _grab_and_upsert_thumbnails(
        db, session_id=session_id, tenant_id=tenant_id,
        local_video_path=local_video_path, targets=targets)


async def _persist_question_thumbnails(
    db, *, session_id: str, tenant_id: str, local_video_path: str,
    transcript: list[dict],
) -> None:
    """Per-QUESTION frames only (kind='question'), at each question's asked-at ms."""
    q_times = asked_at_ms_by_question_evidence(transcript)
    targets = [("question", qid, t_ms) for qid, t_ms in q_times.items()]
    await _grab_and_upsert_thumbnails(
        db, session_id=session_id, tenant_id=tenant_id,
        local_video_path=local_video_path, targets=targets)


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
        estimator = _get_estimator()
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "recording.mp4")
            await get_object_storage().download_to_path(recording_key, dest)
            result, frames = run_analysis(estimator, local_video_path=dest)
            final_status = (
                "unscorable" if result.risk_band == "insufficient_data" else "ready"
            )
            # Persist gaze features first (own transaction), then thumbnails
            # (best-effort) — both while the recording is still on local disk.
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
                await _persist(db, session_id, tenant_id,
                               status=final_status, result=result, frames=frames)
                await db.commit()
            # Flag thumbnails are best-effort: the gaze result is already committed
            # above, so a thumbnail failure (grab, upload, or DB) must never
            # propagate to the actor's failure handler or burn a retry.
            # NOTE: per-question thumbnails are now handled by the separate
            # generate_session_thumbnails actor (decoupled from proctoring).
            try:
                async with get_bypass_session() as db:
                    await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
                    await _persist_flag_thumbnails(
                        db, session_id=session_id, tenant_id=tenant_id,
                        local_video_path=dest,
                        flagged_intervals=result.flagged_intervals or [],
                    )
                    await db.commit()
            except Exception:  # noqa: BLE001 — best-effort; gaze result already durable
                log.warning("vision.thumbnails.step_failed",
                            session_id=session_id, exc_info=True)
    except Exception as exc:  # noqa: BLE001
        log.error("vision.actor.failed", session_id=session_id, exc_info=exc)
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
            await _persist(db, session_id, tenant_id, status="failed", error=str(exc)[:500])
            await db.commit()
        raise

    log.info("vision.actor.done", session_id=session_id,
             band=result.risk_band, frames=frames)


@dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=120_000, queue_name="vision")
async def analyze_session_proctoring(session_id: str, tenant_id: str) -> None:
    await _run(session_id, tenant_id)


# ---------------------------------------------------------------------------
# generate_session_thumbnails — per-question report thumbnails, decoupled from
# proctoring. Runs for EVERY session with a ready recording; enqueued by
# session/recording.py regardless of AUTO_ANALYZE_PROCTORING.
# ---------------------------------------------------------------------------


async def _run_thumbnails(session_id: str, tenant_id: str) -> None:
    safe_tid = str(uuid.UUID(tenant_id))
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
        sess = (await db.execute(
            select(Session).where(
                Session.id == uuid.UUID(session_id),
                Session.tenant_id == uuid.UUID(tenant_id),
            )
        )).scalar_one_or_none()
        recording_key = (
            sess.recording_s3_key
            if sess and sess.recording_status == "ready" and sess.recording_s3_key
            else None
        )
        transcript = list(
            ((sess.session_evidence_json or {}).get("transcript") or []) if sess else []
        )
        await db.commit()

    if not recording_key:
        log.info("vision.thumbnails.no_recording", session_id=session_id)
        return

    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "recording.mp4")
        await get_object_storage().download_to_path(recording_key, dest)
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
            await _persist_question_thumbnails(
                db, session_id=session_id, tenant_id=tenant_id,
                local_video_path=dest, transcript=transcript)
            await db.commit()
    log.info("vision.thumbnails.done", session_id=session_id)


@dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=120_000, queue_name="vision")
async def generate_session_thumbnails(session_id: str, tenant_id: str) -> None:
    await _run_thumbnails(session_id, tenant_id)
