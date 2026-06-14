"""Dramatiq actor that renders a Candidate Reel (queue ``reel``).

Runs in the vision image (ffmpeg + Pillow + TTS plugins). Enqueued by the reel
generate/regenerate endpoints. State lifecycle:

    pending → generating → ready
                         ↘ failed   (re-raise → Dramatiq retries transient errors)

The ``generating`` mark is committed (visible to the polling UI) BEFORE the long
render, then the DB connection is released for the minutes-long LLM + render work
(no connection held open). A crashed worker leaves a ``generating`` row that is
re-claimed on Dramatiq redelivery (the idempotency gate only short-circuits
``ready``) or on an explicit regenerate.

Import note: ``render`` + ``director`` are import-light (Pillow / livekit / ffmpeg
load lazily or shell out), so the API process can import this module to ``.send()``
without pulling heavy deps; the heavy work happens only when the actor RUNS.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import dramatiq
import structlog
from sqlalchemy import select, text

from app.config import settings
from app.database import get_bypass_session
from app.modules.reel import render, timing
from app.modules.reel.director import edl_to_dict, generate_edl, validate_edl
from app.modules.reel.models import SessionReel
from app.storage import get_object_storage

logger = structlog.get_logger("reel.actor")


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 ``meta.started_at`` (the engine span origin) → tz-aware datetime.

    Tolerates the trailing ``Z`` (UTC) form ``datetime.fromisoformat`` rejected
    before 3.11. Returns ``None`` on missing/unparseable input.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _candidate_speech_intervals(transcript: list[dict], offset_ms: int) -> list[tuple[int, int]]:
    """Candidate speech envelope on the VIDEO clock — input to sync calibration.

    Each candidate turn carries ``span.start_ms`` (the first word's absolute STT
    time, session-relative) + ``words[{start_ms,end_ms}]`` (turn-relative). A
    word's video position (pre-correction) = ``span.start_ms + word.start_ms +
    offset_ms``. Returns one interval per word across ALL candidate turns.
    """
    intervals: list[tuple[int, int]] = []
    for turn in transcript:
        if turn.get("speaker") != "candidate":
            continue
        turn_start = int((turn.get("span") or {}).get("start_ms") or 0)
        for w in turn.get("words") or []:
            try:
                start = turn_start + int(w["start_ms"]) + offset_ms
                end = turn_start + int(w["end_ms"]) + offset_ms
            except (KeyError, TypeError, ValueError):
                continue
            if end > start:
                intervals.append((start, end))
    return intervals


async def _load_inputs(db, session_id: UUID, tenant_id: UUID) -> dict:
    """Single-query load of every input the EDL + render need."""
    row = (await db.execute(text(
        "SELECT r.verdict, r.verdict_reason, r.summary, r.question_scorecards, "
        "       r.signal_scorecards, j.title AS role_title, c.name AS candidate_name, "
        "       s.recording_s3_key, s.recording_started_at, "
        "       s.session_evidence_json "
        "FROM sessions s "
        "LEFT JOIN session_reports r ON r.session_id = s.id AND r.tenant_id = s.tenant_id "
        "LEFT JOIN candidate_job_assignments a ON a.id = s.assignment_id "
        "LEFT JOIN candidates c ON c.id = a.candidate_id "
        "LEFT JOIN job_postings j ON j.id = a.job_posting_id "
        "WHERE s.id = :sid AND s.tenant_id = :tid"
    ), {"sid": str(session_id), "tid": str(tenant_id)})).mappings().one()
    return dict(row)


def _model_versions() -> dict:
    from app.ai.config import ai_config
    return {
        "director_model": ai_config.reel_director_model,
        "director_prompt_version": ai_config.reel_director_prompt_version,
        "tts_provider": settings.interview_tts_provider,
        "tts_model": settings.interview_tts_model,
        "tts_voice": settings.interview_tts_voice,
    }


async def _build_and_upload(session_id: UUID, tenant_id: UUID,
                            correlation_id: str, log) -> dict:
    """Build the EDL, render the reel, upload to R2. Returns the persist payload."""
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        inp = await _load_inputs(db, session_id, tenant_id)

    if not inp["recording_s3_key"]:
        raise RuntimeError("recording not ready")

    # Gen-3 durable evidence is the timing + transcript spine. The transcript is
    # session-relative ms; the offset shifts it onto the recording (video) clock.
    evidence = inp["session_evidence_json"] or {}
    transcript = evidence.get("transcript") or []
    meta_started_at = _parse_iso((evidence.get("meta") or {}).get("started_at"))
    if not transcript or meta_started_at is None:
        # Fail honestly rather than cutting an empty/mis-timed clip.
        raise RuntimeError("session evidence not ready")
    offset_ms = timing.recording_offset_ms(
        meta_started_at, inp["recording_started_at"])

    summary = inp["summary"] or {}
    why_positive = (summary.get("decision") or {}).get("why_positive")
    if isinstance(why_positive, dict):
        why_positive = why_positive.get("body")

    raw = await generate_edl(
        candidate_name=inp["candidate_name"], role_title=inp["role_title"],
        verdict=inp["verdict"], verdict_reason=inp["verdict_reason"],
        why_positive=why_positive, strengths=summary.get("strengths", []),
        question_scorecards=inp["question_scorecards"] or [],
        signal_scorecards=inp["signal_scorecards"] or [],
        transcript=transcript, correlation_id=correlation_id,
    )
    vedl = validate_edl(raw, transcript)
    log.info("reel.actor.edl_validated", n_beats=len(vedl.beats),
             duration_ms=vedl.duration_ms)

    storage = get_object_storage()
    r2_key = f"reels/{tenant_id}/{session_id}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        rec_path = os.path.join(tmp, "recording.mp4")
        await storage.download_to_path(inp["recording_s3_key"], rec_path)

        # Per-session sync calibration (best-effort): the static offset leaves a
        # residual lag (STT-stream start vs meta.started_at + pipeline latency).
        # Cross-correlate the candidate's STT word envelope (already on the video
        # clock via offset_ms) against the recording's actual speech to recover δ;
        # render at static + δ. Any ffmpeg/measure failure → keep the static offset
        # (NEVER fail the reel over calibration).
        final_offset = offset_ms
        try:
            cand_intervals = _candidate_speech_intervals(transcript, offset_ms)
            rec_speech = await timing.recording_speech_intervals(rec_path)
            delta = timing.measure_offset_correction(cand_intervals, rec_speech)
            final_offset = offset_ms + delta
            log.info("reel.actor.sync_calibrated", static_offset_ms=offset_ms,
                     delta_ms=delta, final_offset_ms=final_offset,
                     cand_intervals=len(cand_intervals), rec_intervals=len(rec_speech))
        except Exception as exc:
            log.warning("reel.actor.sync_calibration_failed",
                        error_type=type(exc).__name__, error_message=str(exc)[:300],
                        static_offset_ms=offset_ms)

        out_path = os.path.join(tmp, "reel.mp4")
        _, chapters = await render.render_reel(
            beats=vedl.beats, recording_path=rec_path, offset_ms=final_offset,
            tmp_dir=tmp, out_path=out_path,
        )
        duration_ms = await render.probe_duration_ms(out_path)
        data = await asyncio.to_thread(Path(out_path).read_bytes)
        await storage.upload_bytes(r2_key, data, content_type="video/mp4")

    return {
        "edl": edl_to_dict(vedl), "chapters": chapters, "r2_key": r2_key,
        "duration_seconds": round(duration_ms / 1000.0, 2),
        "model_versions": _model_versions(),
    }


async def _generate_session_reel_async(session_id: UUID, tenant_id: UUID,
                                       correlation_id: str, force: bool) -> None:
    log = logger.bind(session_id=str(session_id), tenant_id=str(tenant_id),
                      correlation_id=correlation_id, force=force)

    # --- claim: mark generating (committed → visible to the polling UI) ---
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        reel = (await db.execute(select(SessionReel).where(
            SessionReel.session_id == session_id,
            SessionReel.tenant_id == tenant_id,
        ))).scalar_one_or_none()
        if reel is None:
            log.warning("reel.actor.no_row")   # endpoint must create the row first
            return
        if reel.status == "ready" and not force:
            log.info("reel.actor.skip_ready", reel_id=str(reel.id))
            return
        reel.status = "generating"
        reel.generation_started_at = datetime.now(UTC)
        reel.attempts = (reel.attempts or 0) + 1
        reel.generation_error = None
        await db.commit()

    # --- heavy work (no DB connection held) ---
    try:
        payload = await _build_and_upload(session_id, tenant_id, correlation_id, log)
    except Exception as exc:
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            failed = (await db.execute(select(SessionReel).where(
                SessionReel.session_id == session_id,
                SessionReel.tenant_id == tenant_id,
            ))).scalar_one_or_none()
            if failed is not None:
                failed.status = "failed"
                failed.generation_error = str(exc)[:500]
                await db.commit()
        log.error("reel.actor.failed", error_type=type(exc).__name__,
                  error_message=str(exc)[:500], exc_info=exc)
        raise

    # --- persist ready ---
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        reel = (await db.execute(select(SessionReel).where(
            SessionReel.session_id == session_id,
            SessionReel.tenant_id == tenant_id,
        ))).scalar_one()
        reel.status = "ready"
        reel.edl = payload["edl"]
        reel.chapters = payload["chapters"]
        reel.r2_key = payload["r2_key"]
        reel.duration_seconds = payload["duration_seconds"]
        reel.model_versions = payload["model_versions"]
        reel.generation_error = None
        reel.generated_at = datetime.now(UTC)
        await db.commit()
    log.info("reel.actor.completed", duration_seconds=payload["duration_seconds"])


@dramatiq.actor(max_retries=2, min_backoff=10_000, max_backoff=300_000,
                queue_name="reel", time_limit=900_000)
async def generate_session_reel(session_id: str, tenant_id: str, correlation_id: str,
                                force: bool = False) -> None:
    """Render + persist a Candidate Reel for a session.

    Async actor — runs on Dramatiq's AsyncIO middleware loop (the same loop the
    module-global async DB engine pool binds to). A sync ``asyncio.run()`` wrapper
    creates a throwaway loop → cross-loop asyncpg reuse error.
    """
    await _generate_session_reel_async(
        UUID(session_id), UUID(tenant_id), correlation_id, force)
