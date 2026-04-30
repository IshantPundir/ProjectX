"""interview_runtime service — assembles SessionConfig + records SessionResult.

The two halves of the engine ↔ Nexus internal API:
- ``build_session_config`` powers GET /api/internal/sessions/{id}/config.
- ``record_session_result`` (Task 3.7) powers POST /api/internal/sessions/{id}/results.

Both run on a bypass-RLS session (the engine has no Supabase user context).
Tenant scope is enforced at the application layer via the JWT's tenant_id
claim — every query in this module MUST filter explicitly by tenant_id.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Candidate,
    CandidateJobAssignment,
    JobPipelineStage,
    JobPosting,
    Session as SessionRow,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)
from app.modules.org_units.service import find_company_profile_in_ancestry
from app.modules.question_bank.service import get_latest_confirmed_snapshot

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
    if bank is None or bank.status != "ready" or bank.is_stale:
        raise QuestionBankNotReadyError(
            "bank state status="
            f"{getattr(bank, 'status', None)} stale={getattr(bank, 'is_stale', None)}"
        )

    # Latest CONFIRMED signal snapshot — same predicate used in
    # app/modules/question_bank/service.py (get_latest_confirmed_snapshot,
    # lines 64-80) to find the snapshot that gates question-bank generation.
    # role_summary, seniority_level, and signals all come from here, not
    # from JobPosting (which has no such columns).
    snapshot = await get_latest_confirmed_snapshot(db, job.id)
    if snapshot is None:
        raise ValueError(
            f"job {job.id} has no confirmed signal snapshot — bank.status='ready' was inconsistent"
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

    return SessionConfig(
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
