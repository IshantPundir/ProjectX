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
import json
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
from app.modules.reel import render
from app.modules.reel.director import edl_to_dict, generate_edl, validate_edl
from app.modules.reel.models import SessionReel
from app.storage import get_object_storage

logger = structlog.get_logger("reel.actor")


def _resolve_events(session_id: str, stored_ref: str | None) -> list[dict]:
    """Load the engine event log (audio.user.state / turn.captured / dispatch).

    Resolves by session_id against this process's configured event-log dir first
    (the engine stores an absolute container path not mounted in the worker), then
    the stored ref; degrades to empty events.
    """
    candidates: list[Path] = []
    if settings.engine_event_log_dir:
        candidates.append(Path(settings.engine_event_log_dir) / f"{session_id}.json")
    if stored_ref:
        candidates.append(Path(stored_ref))
        if settings.engine_event_log_dir:
            candidates.append(Path(settings.engine_event_log_dir) / Path(stored_ref).name)
    # Repo-mounted event-log dir (dev + portable fallback). NOTE: durable
    # cross-container event-log storage in production is a known open concern
    # shared with the reporting actor (the reel has a HARD dependency on it for
    # VAD timing, unlike the report which degrades to empty events).
    repo_dir = Path(__file__).resolve().parents[3] / "engine-events"
    candidates.append(repo_dir / f"{session_id}.json")
    for path in candidates:
        try:
            return json.loads(path.read_text()).get("events", [])
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    return []


async def _load_inputs(db, session_id: UUID, tenant_id: UUID) -> dict:
    """Single-query load of every input the EDL + render need."""
    row = (await db.execute(text(
        "SELECT r.verdict, r.verdict_reason, r.summary, r.question_scorecards, "
        "       r.signal_scorecards, j.title AS role_title, c.name AS candidate_name, "
        "       s.recording_s3_key, s.recording_started_at, s.transcript, "
        "       s.raw_result_json "
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

    summary = inp["summary"] or {}
    why_positive = (summary.get("decision") or {}).get("why_positive")
    if isinstance(why_positive, dict):
        why_positive = why_positive.get("body")
    events = _resolve_events(
        str(session_id), (inp["raw_result_json"] or {}).get("audit_envelope_ref"))
    rec_start_ms = int(inp["recording_started_at"].timestamp() * 1000)

    raw = await generate_edl(
        candidate_name=inp["candidate_name"], role_title=inp["role_title"],
        verdict=inp["verdict"], verdict_reason=inp["verdict_reason"],
        why_positive=why_positive, strengths=summary.get("strengths", []),
        question_scorecards=inp["question_scorecards"] or [],
        signal_scorecards=inp["signal_scorecards"] or [],
        transcript=list(inp["transcript"] or []), correlation_id=correlation_id,
    )
    vedl = validate_edl(raw, list(inp["transcript"] or []))
    log.info("reel.actor.edl_validated", n_beats=len(vedl.beats),
             duration_ms=vedl.duration_ms)

    storage = get_object_storage()
    r2_key = f"reels/{tenant_id}/{session_id}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        rec_path = os.path.join(tmp, "recording.mp4")
        await storage.download_to_path(inp["recording_s3_key"], rec_path)
        anchor, speaking = await render.prepare_anchor(events, rec_path, rec_start_ms)
        out_path = os.path.join(tmp, "reel.mp4")
        _, chapters = await render.render_reel(
            beats=vedl.beats, recording_path=rec_path, events=events,
            speaking=speaking, anchor=anchor, tmp_dir=tmp, out_path=out_path,
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
def generate_session_reel(session_id: str, tenant_id: str, correlation_id: str,
                          force: bool = False) -> None:
    """Render + persist a Candidate Reel for a session (sync Dramatiq wrapper)."""
    asyncio.run(_generate_session_reel_async(
        UUID(session_id), UUID(tenant_id), correlation_id, force))
