"""Dramatiq actor for post-session report scoring.

Enqueued by ``record_session_result`` after a v2 session completes.
Loads the audit envelope + question bank from storage, calls ``build_report``
(the offline scoring pipeline), and persists the result via ``persist_report``.

Import note: ``build_report`` and ``persist_report`` are imported at MODULE
level so tests can patch ``actors.build_report`` and ``actors.persist_report``
via ``unittest.mock.patch``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import dramatiq
import structlog
from sqlalchemy import desc, select, text

from app.database import get_bypass_session
from app.modules.reporting.models import SessionReport
from app.modules.reporting.service import build_report, persist_report

logger = structlog.get_logger("reporting.actor")


# ---------------------------------------------------------------------------
# Inner async implementation (testable without Dramatiq)
# ---------------------------------------------------------------------------


async def _score_session_report_async(
    session_id: UUID,
    tenant_id: UUID,
    correlation_id: str,
    force: bool = False,
) -> None:
    """Async core — called by the Dramatiq wrapper via asyncio.run().

    The function is intentionally importable and awaitable in tests without
    touching Dramatiq's process model.
    """
    log = logger.bind(
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
        force=force,
    )

    safe_tenant_id = str(tenant_id)

    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'"))

        # ------------------------------------------------------------------
        # Idempotency gate: skip when a ready report exists and force=False.
        # ------------------------------------------------------------------
        existing = (
            await db.execute(
                select(SessionReport).where(
                    SessionReport.session_id == session_id,
                    SessionReport.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

        if existing is not None and existing.status == "ready" and not force:
            log.info("reporting.actor.skip_already_ready", report_id=str(existing.id))
            return

        # ------------------------------------------------------------------
        # Load session row
        # ------------------------------------------------------------------
        from app.modules.session.models import Session as SessionRow

        sess = (
            await db.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

        if sess is None:
            log.warning("reporting.actor.session_not_found")
            return

        # ------------------------------------------------------------------
        # Load question bank + signal snapshot for the session's stage
        # ------------------------------------------------------------------
        from app.modules.candidates.models import CandidateJobAssignment
        from app.modules.interview_runtime.service import _project_signal_metadata
        from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
        from app.modules.pipelines.models import JobPipelineStage
        from app.modules.question_bank.models import StageQuestion, StageQuestionBank

        assignment = (
            await db.execute(
                select(CandidateJobAssignment).where(
                    CandidateJobAssignment.id == sess.assignment_id,
                    CandidateJobAssignment.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

        if assignment is None:
            log.warning("reporting.actor.assignment_not_found",
                        assignment_id=str(sess.assignment_id))
            return

        stage = (
            await db.execute(
                select(JobPipelineStage).where(
                    JobPipelineStage.id == sess.stage_id,
                    JobPipelineStage.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

        if stage is None:
            log.warning("reporting.actor.stage_not_found", stage_id=str(sess.stage_id))
            return

        job = (
            await db.execute(
                select(JobPosting).where(
                    JobPosting.id == assignment.job_posting_id,
                    JobPosting.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()

        if job is None:
            log.warning("reporting.actor.job_not_found",
                        job_id=str(assignment.job_posting_id))
            return

        bank = (
            await db.execute(
                select(StageQuestionBank).where(
                    StageQuestionBank.stage_id == stage.id,
                    StageQuestionBank.tenant_id == tenant_id,
                    StageQuestionBank.status == "confirmed",
                )
            )
        ).scalar_one_or_none()

        if bank is None:
            log.warning(
                "reporting.actor.no_confirmed_bank", stage_id=str(stage.id)
            )
            return

        # Load all questions for the bank ordered by position
        question_rows = (
            await db.execute(
                select(StageQuestion)
                .where(
                    StageQuestion.bank_id == bank.id,
                    StageQuestion.tenant_id == tenant_id,
                )
                .order_by(
                    StageQuestion.is_mandatory.desc(),
                    StageQuestion.position.asc(),
                )
            )
        ).scalars().all()

        # Project StageQuestion ORM rows → plain dict shape build_report expects
        questions: list[dict] = [
            {
                "id": str(q.id),
                "position": q.position,
                "text": q.text,
                "signal_values": list(q.signal_values),
                "estimated_minutes": float(q.estimated_minutes),
                "is_mandatory": q.is_mandatory,
                "follow_ups": list(q.follow_ups),
                "positive_evidence": list(q.positive_evidence),
                "red_flags": list(q.red_flags),
                "rubric": dict(q.rubric),
                "evaluation_hint": q.evaluation_hint or "",
                "question_kind": q.question_kind,
                "difficulty": q.difficulty,
                "primary_signal": q.primary_signal,
            }
            for q in question_rows
        ]

        # Load the latest confirmed signal snapshot for signal_metadata
        snapshot = (
            await db.execute(
                select(JobPostingSignalSnapshot)
                .where(
                    JobPostingSignalSnapshot.job_posting_id == job.id,
                    JobPostingSignalSnapshot.tenant_id == tenant_id,
                    JobPostingSignalSnapshot.confirmed_at.is_not(None),
                )
                .order_by(desc(JobPostingSignalSnapshot.version))
                .limit(1)
            )
        ).scalar_one_or_none()

        if snapshot is None:
            log.warning("reporting.actor.no_confirmed_snapshot",
                        job_id=str(job.id))
            return

        # Project snapshot.signals → list[dict] shape build_report expects.
        # _project_signal_metadata returns list[SignalMetadata] (Pydantic models);
        # build_report wants list[dict] — convert via model_dump.
        signal_metadata_models = _project_signal_metadata(snapshot.signals or [])
        signal_metadata: list[dict] = [
            m.model_dump() for m in signal_metadata_models
        ]

        # ------------------------------------------------------------------
        # Load audit envelope from filesystem (best-effort; degrade gracefully)
        # ------------------------------------------------------------------
        raw_result: dict = sess.raw_result_json or {}
        audit_envelope_ref: str | None = raw_result.get("audit_envelope_ref")
        envelope: dict = {"events": []}
        if audit_envelope_ref:
            try:
                raw_text = await asyncio.to_thread(Path(audit_envelope_ref).read_text)
                envelope = json.loads(raw_text)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "reporting.actor.envelope_unreadable",
                    audit_envelope_ref=audit_envelope_ref,
                    error=type(exc).__name__,
                )

        transcript: list[dict] = list(sess.transcript or [])

        # ------------------------------------------------------------------
        # Mark generating (best-effort) then build
        # ------------------------------------------------------------------
        if existing is not None:
            existing.status = "generating"
        # Don't commit the generating mark — let the session stay open for
        # the LLM call; if the process dies, Dramatiq retries and the mark
        # becomes irrelevant (idempotency gate above handles ready rows).

        try:
            report = await build_report(
                transcript=transcript,
                envelope=envelope,
                questions=questions,
                signal_metadata=signal_metadata,
                correlation_id=correlation_id,
            )

            await persist_report(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                assignment_id=sess.assignment_id,
                report=report,
                rubric_snapshot={"questions": questions, "signal_metadata": signal_metadata},
                force=force,
            )
            await db.commit()
            log.info("reporting.actor.completed", verdict=report.verdict)

        except Exception as exc:
            # Best-effort: mark the report row as failed so the UI can surface
            # the error state. Then re-raise so Dramatiq retries transient errors.
            try:
                failed_row = (
                    await db.execute(
                        select(SessionReport).where(
                            SessionReport.session_id == session_id,
                            SessionReport.tenant_id == tenant_id,
                        )
                    )
                ).scalar_one_or_none()

                if failed_row is None:
                    failed_row = SessionReport(
                        session_id=session_id,
                        tenant_id=tenant_id,
                        assignment_id=sess.assignment_id,
                        version=1,
                        status="failed",
                        generation_error=str(exc)[:500],
                        engine_version="v2",
                        generated_at=datetime.now(UTC),
                    )
                    db.add(failed_row)
                else:
                    failed_row.status = "failed"
                    failed_row.generation_error = str(exc)[:500]
                    # Keep failed_row.version unchanged — avoids unique-key conflict
                    # on retry when a row already exists at a given version.
                await db.commit()
            except Exception as inner_exc:  # noqa: BLE001
                log.warning(
                    "reporting.actor.failed_row_write_error",
                    error=type(inner_exc).__name__,
                )
                with contextlib.suppress(Exception):
                    await db.rollback()

            log.error(
                "reporting.actor.failed",
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                exc_info=exc,
            )
            raise


# ---------------------------------------------------------------------------
# Dramatiq actor — public entry point
# ---------------------------------------------------------------------------


@dramatiq.actor(
    max_retries=2,
    min_backoff=5_000,
    max_backoff=120_000,
    queue_name="report_scoring",
)
def score_session_report(
    session_id: str,
    tenant_id: str,
    correlation_id: str,
    force: bool = False,
) -> None:
    """Score and persist a post-session report for a completed v2 session.

    Sync wrapper required by Dramatiq's process model — delegates immediately
    to the async inner function via ``asyncio.run()``.
    """
    asyncio.run(
        _score_session_report_async(
            UUID(session_id),
            UUID(tenant_id),
            correlation_id,
            force,
        )
    )
