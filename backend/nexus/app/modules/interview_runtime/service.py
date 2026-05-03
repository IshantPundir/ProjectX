"""interview_runtime service — assembles SessionConfig + records SessionResult.

The two helpers nexus and the merged interview_engine call in-process:
- ``build_session_config`` returns a SessionConfig given (session_id, tenant_id).
- ``record_session_result`` atomically transitions the session to completed
  given (session_id, tenant_id, result, correlation_id).

Both run on a bypass-RLS session (the engine has no Supabase user context).
Tenant scope is enforced at the application layer via the explicit
``tenant_id`` parameter — every query in this module MUST filter by it.
This is the post-Phase-3 contract: the HTTP boundary at /api/internal/*
and the engine-dispatch JWT both retired; RLS bypass + explicit-tenant
filtering is the new defense layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("interview_runtime")

from app.modules.audit import log_event
from app.modules.candidates import Candidate, CandidateJobAssignment
from app.modules.jd import JobPosting, JobPostingSignalSnapshot
from app.modules.org_units import find_company_profile_in_ancestry
from app.modules.pipelines import JobPipelineStage
from app.modules.question_bank import StageQuestion, StageQuestionBank
from app.modules.session import Session as SessionRow
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
    SessionNotActiveError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SessionResult,
    StageConfig,
)

_AI_STAGE_TYPES = frozenset({"ai_screening", "phone_screen"})


async def build_session_config(
    db: AsyncSession, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> SessionConfig:
    """Compose the SessionConfig handed to the agent worker.

    Caller MUST be on a bypass-RLS session AND must have already verified
    that the engine JWT's tenant_id matches the ``tenant_id`` argument.
    Cross-tenant inputs return 'session not found' (ValueError) — the same
    shape an unknown session would produce, so no tenant existence is
    leaked through error type.
    """
    sess = (
        await db.execute(
            select(SessionRow).where(
                SessionRow.id == session_id,
                SessionRow.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if sess is None:
        raise ValueError(f"session {session_id} not found")

    assignment = (
        await db.execute(
            select(CandidateJobAssignment).where(
                CandidateJobAssignment.id == sess.assignment_id,
                CandidateJobAssignment.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    candidate = (
        await db.execute(
            select(Candidate).where(
                Candidate.id == assignment.candidate_id,
                Candidate.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    job = (
        await db.execute(
            select(JobPosting).where(
                JobPosting.id == assignment.job_posting_id,
                JobPosting.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    stage = (
        await db.execute(
            select(JobPipelineStage).where(
                JobPipelineStage.id == sess.stage_id,
                JobPipelineStage.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    if stage.stage_type not in _AI_STAGE_TYPES:
        raise StageNotAiDrivenError(stage.stage_type)

    bank = (
        await db.execute(
            select(StageQuestionBank).where(
                StageQuestionBank.stage_id == stage.id,
                StageQuestionBank.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if bank is None or bank.status != "confirmed" or bank.is_stale:
        raise QuestionBankNotReadyError(
            "bank state status="
            f"{getattr(bank, 'status', None)} stale={getattr(bank, 'is_stale', None)}"
        )

    # Latest CONFIRMED signal snapshot. Inlined here (rather than delegating
    # to the question_bank.service helper) so the explicit tenant_id filter
    # is visible at the call site — spec Section 6.3 mandates explicit
    # application-layer tenant scoping on every query in this module since
    # the bypass session leaves RLS off.
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
        raise ValueError(
            f"job {job.id} has no confirmed signal snapshot — bank.status='confirmed' was inconsistent"
        )

    questions = (
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

    company_profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if company_profile is None:
        raise CompanyProfileMissingError(
            f"job {job.id} ancestry has no company_profile"
        )

    config = SessionConfig(
        session_id=str(session_id),
        job_title=job.title,
        role_summary=snapshot.role_summary,
        seniority_level=snapshot.seniority_level,
        company=CompanyContext(
            about=company_profile.get("about", ""),
            industry=company_profile.get("industry", ""),
            company_stage=company_profile.get("company_stage", ""),
            hiring_bar=company_profile.get("hiring_bar", ""),
        ),
        candidate=CandidateContext(name=candidate.name or ""),
        stage=StageConfig(
            stage_id=str(stage.id),
            stage_type=stage.stage_type,
            name=stage.name,
            duration_minutes=stage.duration_minutes or 30,
            difficulty=stage.difficulty,
            questions=[
                QuestionConfig(
                    id=str(q.id),
                    position=q.position,
                    text=q.text,
                    signal_values=list(q.signal_values),
                    estimated_minutes=float(q.estimated_minutes),
                    is_mandatory=q.is_mandatory,
                    follow_ups=list(q.follow_ups),
                    positive_evidence=list(q.positive_evidence),
                    red_flags=list(q.red_flags),
                    rubric=QuestionRubric.model_validate(q.rubric),
                    evaluation_hint=q.evaluation_hint,
                    question_kind=q.question_kind,
                )
                for q in questions
            ],
            advance_behavior=stage.advance_behavior or "manual_review",
        ),
        signals=[
            s["value"] if isinstance(s, dict) and "value" in s else str(s)
            for s in (snapshot.signals or [])
        ],
    )
    logger.info(
        "interview_runtime.session_config.built",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        job_id=str(job.id),
        job_title=job.title,
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        bank_id=str(bank.id),
        bank_pipeline_version=getattr(bank, "pipeline_version_at_generation", None),
        question_count=len(config.stage.questions),
        mandatory_count=sum(1 for q in config.stage.questions if q.is_mandatory),
        optional_count=sum(1 for q in config.stage.questions if not q.is_mandatory),
        duration_minutes=config.stage.duration_minutes,
        signals_total=len(config.signals),
        snapshot_version=snapshot.version,
    )
    return config


async def record_session_result(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    result: SessionResult,
    correlation_id: str,
) -> None:
    """Persist the engine's SessionResult and transition the session to completed.

    Atomic on the active->completed transition: a single UPDATE gated on
    state='active' decides whether the engine's result wins. On rowcount=0
    the function distinguishes idempotent retry (already completed) from a
    real state violation:

    * row missing               -> ValueError('session not found')
    * row exists, completed     -> silent no-op (idempotent retry)
    * row exists, any other     -> SessionNotActiveError

    Audit row written on successful first transition only — the idempotent
    silent-no-op branch does NOT write a duplicate audit entry.

    Caller MUST be on a bypass-RLS session. The ``tenant_id`` argument
    (sourced from the LiveKit dispatch metadata in the engine, or from
    request.state in nexus's own callers) is filter-applied to every
    query — cross-tenant access returns "not found".
    """
    derived_status = "ok" if result.questions_asked > 0 else "partial"
    now = datetime.now(UTC)

    res = await db.execute(
        update(SessionRow)
        .where(
            SessionRow.id == session_id,
            SessionRow.tenant_id == tenant_id,
            SessionRow.state == "active",
        )
        .values(
            raw_result_json=result.model_dump(mode="json"),
            transcript=[t.model_dump(mode="json") for t in result.full_transcript],
            questions_asked=result.questions_asked,
            probes_fired=result.total_probes_fired,
            knockout_failures=[k.model_dump(mode="json") for k in result.knockout_failures],
            agent_completed_at=now,
            result_status=derived_status,
            state="completed",
            state_changed_at=now,
        )
    )
    if res.rowcount == 0:
        existing = (
            await db.execute(
                select(SessionRow).where(
                    SessionRow.id == session_id,
                    SessionRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise ValueError(f"session {session_id} not found")
        if existing.state == "completed" and existing.agent_completed_at is not None:
            return  # idempotent — engine retried after a successful first post
        raise SessionNotActiveError(f"session {session_id} state={existing.state}")

    await log_event(
        db,
        tenant_id=tenant_id,
        actor_id=None,
        actor_email=None,
        action="engine.session.completed",
        resource="session",
        resource_id=session_id,
        payload={
            "correlation_id": correlation_id,
            "questions_asked": result.questions_asked,
            "result_status": derived_status,
        },
    )
