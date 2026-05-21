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

from app.ai.config import AIConfig
from app.modules.audit import log_event
from app.modules.candidates import Candidate, CandidateJobAssignment
from app.modules.jd import (
    JobPosting,
    JobPostingSignalSnapshot,
    default_evaluation_method,
)
from app.modules.org_units import (
    find_company_profile_in_ancestry,
    get_org_unit_ancestry,
)
from app.modules.pipelines import JobPipelineStage
from app.modules.question_bank import StageQuestion, StageQuestionBank
from app.modules.session import Session as SessionRow
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    EmptySignalMetadataError,
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
    SignalMetadata,
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

    # The closest org_unit to the job is the hiring company. For agency
    # tenants the parent (depth 1) is the tenant; for in-house tenants
    # both are the same legal entity. Either way, the closest unit is
    # what the candidate is interviewing FOR. Used by the intro_brief
    # Speaker turn — see spec §2 "Schema additions" (hiring company name
    # is NOT the ProjectX tenant name).
    org_unit_ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    hiring_company_name: str | None = None
    if org_unit_ancestry:
        hiring_company_name = org_unit_ancestry[0].name

    # v2 cutover selector: per-job override falls back to the global default.
    # Construct a fresh AIConfig() so a monkeypatched env is honored in tests
    # (the module-level singleton caches the boot-time Settings).
    resolved_engine_version = (
        job.interview_engine_version
        or AIConfig().interview_engine_default_version
    )

    config = SessionConfig(
        session_id=str(session_id),
        job_id=str(job.id),
        candidate_id=str(assignment.candidate_id),
        job_title=job.title,
        hiring_company_name=hiring_company_name,
        role_summary=snapshot.role_summary,
        # Enriched JD when available; raw JD as the fallback. Threaded
        # through so the Speaker can answer candidate meta-questions
        # via clarify(role_context). Never used by intro_brief.
        jd_text=job.description_enriched or job.description_raw,
        seniority_level=snapshot.seniority_level,
        company=CompanyContext(
            about=company_profile.get("about", ""),
            industry=company_profile.get("industry", ""),
            company_stage=company_profile.get("company_stage", ""),
            hiring_bar=company_profile.get("hiring_bar", ""),
        ),
        # First name only — the Speaker addresses the candidate by first
        # name ("Hi Ishant", not "Hi Ishant Pundir"). Surnames sound formal
        # at the start of a friendly interview and TTS often mispronounces
        # them. The full name remains on `candidates.name` in the DB; we
        # just project the first whitespace-separated token here.
        candidate=CandidateContext(
            name=(candidate.name or "").strip().split()[0] if candidate.name else "",
        ),
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
                    difficulty=(q.difficulty or stage.difficulty),
                    primary_signal=q.primary_signal,
                )
                for q in questions
            ],
            advance_behavior=stage.advance_behavior or "manual_review",
        ),
        signals=[
            s["value"] if isinstance(s, dict) and "value" in s else str(s)
            for s in (snapshot.signals or [])
        ],
        signal_metadata=_project_signal_metadata(snapshot.signals or []),
        keyterms=list(bank.extracted_keyterms) if bank.extracted_keyterms is not None else [],
        interview_engine_version=resolved_engine_version,
    )
    if not config.signal_metadata:
        # Engine-boundary fence — see EmptySignalMetadataError docstring.
        # Upstream `ExtractedSignals.signals` enforces min_length=5, so a
        # confirmed snapshot reaching this point with zero valid metadata
        # rows is a data-integrity bug. Refuse to start the session
        # rather than dispatch an agent with nothing to track.
        raise EmptySignalMetadataError(
            f"session {session_id} produced empty signal_metadata "
            f"from snapshot version={snapshot.version} "
            f"(raw signals count={len(snapshot.signals or [])})"
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
        signal_metadata_total=len(config.signal_metadata),
        snapshot_version=snapshot.version,
        interview_engine_version=resolved_engine_version,
    )
    return config


def _project_signal_metadata(raw_signals: list[object]) -> list[SignalMetadata]:
    """Project the snapshot.signals JSONB into SignalMetadata models.

    Snapshots written by the initial-extraction actor (`extract_and_enhance_jd`)
    persist `SignalItemV2.model_dump()` which has NO `evaluation_method` —
    that field is filled at read-time by `default_evaluation_method(type, stage)`,
    matching the recruiter-facing read path in `jd/router.py::_snapshot_to_response`.

    Snapshots written by `save_signals` (recruiter edits) persist
    `SignalItemInput.model_dump()` which DOES have `evaluation_method` (or
    explicit None). Either way the same `or default_evaluation_method(...)`
    fallback applies.

    Provenance fields (`source`, `inference_basis`) are deliberately dropped:
    they are recruiter-facing, not agent decision inputs. Order is preserved
    so `signal_metadata[i]` aligns with `signals[i]`.
    """
    # TODO(post-v1): consider plumbing source / inference_basis if Report Builder
    # wants signal-confidence weighting (e.g. weighting `ai_extracted` evidence
    # higher than `ai_inferred` when the candidate's quote is ambiguous).
    # Decision deferred 2026-05-04 — agent makes flow decisions, not scoring.
    out: list[SignalMetadata] = []
    for s in raw_signals:
        if not isinstance(s, dict):
            # Defensive — pre-v2 snapshots are off-spec and shouldn't survive
            # to a confirmed snapshot, but log and skip rather than crash a
            # session start on a single bad row.
            logger.warning(
                "interview_runtime.signal_metadata.dropped_non_dict",
                signal_repr=repr(s)[:200],
            )
            continue
        eval_method = s.get("evaluation_method") or default_evaluation_method(
            s["type"], s["stage"],
        )
        out.append(
            SignalMetadata(
                value=s["value"],
                type=s["type"],
                priority=s["priority"],
                weight=s.get("weight", 2),
                knockout=s.get("knockout", False),
                stage=s["stage"],
                evaluation_method=eval_method,
                evaluation_hint=s.get("evaluation_hint"),
            )
        )
    return out


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
            audio_tuning_summary=result.audio_tuning_summary,
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
