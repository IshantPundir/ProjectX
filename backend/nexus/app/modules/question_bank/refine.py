"""Stateless LLM-mediated Refine + Draft endpoints (sync HTTP, no DB writes).

The recruiter sends an instruction; the LLM returns a proposed question (or
refinement). The frontend then calls the existing PATCH /questions/{id} (for
Refine accept) or POST /questions (for Add accept) to actually persist.
"""
from __future__ import annotations

import json as _json
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client
from app.ai.config import AIConfig
from app.ai.prompts import prompt_loader
from app.ai.tracing import set_llm_span_attributes

_tracer = trace.get_tracer("nexus.ai.openai")
from app.ai.schemas import KeytermExtractionOutput
from app.database import get_tenant_db
from app.modules.auth import UserContext, get_current_user_roles
from app.modules.jd import JobPosting, JobPostingSignalSnapshot, require_job_access
from app.modules.pipelines import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.question_bank.context import build_question_context
from app.modules.question_bank.service import get_latest_confirmed_snapshot

router = APIRouter(prefix="/api", tags=["question_bank"])
_log = structlog.get_logger()


# --- Request / response schemas -----------------------------------------------


class RefineRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class RefineResponse(BaseModel):
    proposed_text: str
    proposed_signal_probed: str
    proposed_mandatory: bool
    rationale: str = ""


class DraftRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class DraftResponse(BaseModel):
    proposed_text: str
    proposed_signal_probed: str
    proposed_mandatory: bool
    proposed_position: int
    rationale: str = ""


# --- LLM call helpers (mockable in tests) ------------------------------------


async def _call_llm_refine(prompt: str) -> RefineResponse:
    """Call the OpenAI client via instructor for a structured RefineResponse."""
    client = get_openai_client()
    config = AIConfig()
    result: RefineResponse = await client.chat.completions.create(
        model=config.question_bank_model,
        reasoning_effort=config.question_bank_effort,
        response_model=RefineResponse,
        messages=[{"role": "user", "content": prompt}],
        max_retries=1,
    )
    return result


async def _call_llm_draft(prompt: str) -> DraftResponse:
    """Call the OpenAI client via instructor for a structured DraftResponse."""
    client = get_openai_client()
    config = AIConfig()
    result: DraftResponse = await client.chat.completions.create(
        model=config.question_bank_model,
        reasoning_effort=config.question_bank_effort,
        response_model=DraftResponse,
        messages=[{"role": "user", "content": prompt}],
        max_retries=1,
    )
    return result


async def extract_bank_keyterms(
    *,
    job_title: str,
    hiring_company_name: str,
    industry: str,
    company_about: str,
    hiring_bar: str,
    role_summary: str,
    signals: list[str],
    questions: list[dict],
    bank_id: str | None = None,
    tenant_id: str | None = None,
) -> KeytermExtractionOutput:
    """Extract STT keyterms for one bank via a single nano-class LLM call.

    See spec docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md.
    Caller (generate_question_bank_stage, Task 6) is responsible for writing the
    result to stage_question_banks.extracted_keyterms and for tolerating exceptions
    (an empty column is acceptable; the engine falls back to candidate-name-only).

    ``bank_id`` and ``tenant_id`` are forwarded to the OTel span attributes so
    this call shows up alongside the other question-bank LLM calls in trace
    inspectors — load-bearing for debugging STT quality regressions later.
    """
    system_prompt = prompt_loader.get("question_bank_keyterms")

    signals_bullet_list = "\n".join(f"- {s}" for s in signals)
    questions_block = "\n\n".join(
        f"Q{i + 1}: {q.get('text', '')}" for i, q in enumerate(questions)
    )

    user_message = (
        f"You are extracting speech-recognition keyterms for a {job_title} interview "
        f"at {hiring_company_name}.\n\n"
        f"Company industry: {industry}\n"
        f"Company about: {company_about}\n"
        f"Hiring bar: {hiring_bar}\n\n"
        f"Role summary:\n{role_summary}\n\n"
        f"Hiring signals (the criteria recruiters care about for this role):\n"
        f"{signals_bullet_list}\n\n"
        f"Final question bank for this interview stage:\n{questions_block}\n\n"
        f"Extract the 20-40 most useful keyterms for Deepgram nova-3 STT recognition. "
        f"Return them in the order most likely to be spoken during the interview."
    )

    client = get_openai_client()
    config = AIConfig()

    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="question_bank_keyterms",
            prompt_version="v1",
            tenant_id=tenant_id or "",
            bank_id=bank_id or "",
            model=config.question_bank_keyterm_model,
        )
        result: KeytermExtractionOutput = await client.chat.completions.create(
            model=config.question_bank_keyterm_model,
            response_model=KeytermExtractionOutput,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_retries=1,
        )
    return result


# --- Helpers ------------------------------------------------------------------


async def _resolve_instance_and_stage(
    db: AsyncSession,
    job: JobPosting,
    stage_id: UUID,
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    """Load instance + stage, verifying the stage belongs to the job's instance."""
    instance_result = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    instance = instance_result.scalar_one_or_none()
    if instance is None:
        raise HTTPException(404, detail="No pipeline for this job")

    stage_result = await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.id == stage_id,
            JobPipelineStage.instance_id == instance.id,
        )
    )
    stage = stage_result.scalar_one_or_none()
    if stage is None:
        raise HTTPException(404, detail="Stage not found in this pipeline")

    return instance, stage


def _build_signals_json(snapshot: JobPostingSignalSnapshot) -> str:
    """Serialize the signal snapshot's signals list to JSON for prompt assembly."""
    return _json.dumps(snapshot.signals, indent=2)


# --- Endpoints ----------------------------------------------------------------


@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/refine",
    response_model=RefineResponse,
)
async def refine_question(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    body: RefineRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> RefineResponse:
    """Stateless LLM-mediated question refinement.

    Returns a proposed rewrite without persisting anything. The recruiter
    must call PATCH /questions/{question_id} to accept the proposal.
    """
    job = await require_job_access(db, job_id, user, "manage")
    instance, stage = await _resolve_instance_and_stage(db, job, stage_id)

    # Load the question — verify it lives in this stage via its bank
    question = await db.get(StageQuestion, question_id)
    if question is None:
        raise HTTPException(404, detail="Question not found in this stage")

    # Verify the question's bank belongs to this stage
    bank_result = await db.execute(
        select(StageQuestionBank).where(
            StageQuestionBank.id == question.bank_id,
            StageQuestionBank.stage_id == stage.id,
        )
    )
    if bank_result.scalar_one_or_none() is None:
        raise HTTPException(404, detail="Question not found in this stage")

    # Get the signal snapshot for signals_json
    snapshot = await get_latest_confirmed_snapshot(db, job.id)
    if snapshot is None:
        raise HTTPException(
            422, detail="No confirmed signal snapshot — cannot refine question"
        )

    ctx = await build_question_context(db, job=job, instance=instance, stage=stage)
    template = prompt_loader.get("question_refine_single")

    # Use explicit .replace() to avoid str.format() ambiguity with JSON braces
    # in the output schema section of the prompt template.
    prompt = (
        template
        .replace("{signals_json}", _build_signals_json(snapshot))
        .replace("{stage_name}", ctx.stage_name)
        .replace("{stage_type}", ctx.stage_type)
        .replace("{stage_difficulty}", ctx.stage_difficulty or "")
        .replace("{stage_duration_minutes}", str(ctx.stage_duration_minutes or 0))
        .replace("{signal_filter_types}", _json.dumps(ctx.signal_filter_types))
        .replace("{pass_criteria_json}", ctx.pass_criteria_json)
        .replace("{existing_bank_json}", ctx.existing_bank_json)
        .replace("{prior_banks_json}", ctx.prior_banks_json)
        .replace("{question_text}", question.text)
        # primary signal probed = first signal value (or empty string)
        .replace(
            "{question_signal_probed}",
            question.signal_values[0] if question.signal_values else "",
        )
        .replace("{question_mandatory}", str(question.is_mandatory))
        .replace("{instruction}", body.instruction)
    )

    _log.info(
        "question_bank.refine.called",
        job_id=str(job_id),
        stage_id=str(stage_id),
        question_id=str(question_id),
    )
    return await _call_llm_refine(prompt)


@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/draft",
    response_model=DraftResponse,
)
async def draft_question(
    job_id: UUID,
    stage_id: UUID,
    body: DraftRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> DraftResponse:
    """Stateless LLM-mediated new-question drafting.

    Returns a proposed question without persisting anything. The recruiter
    must call POST /questions to accept the proposal.
    """
    job = await require_job_access(db, job_id, user, "manage")
    instance, stage = await _resolve_instance_and_stage(db, job, stage_id)

    # Get the signal snapshot for signals_json
    snapshot = await get_latest_confirmed_snapshot(db, job.id)
    if snapshot is None:
        raise HTTPException(
            422, detail="No confirmed signal snapshot — cannot draft question"
        )

    ctx = await build_question_context(db, job=job, instance=instance, stage=stage)
    template = prompt_loader.get("question_create_single")

    prompt = (
        template
        .replace("{signals_json}", _build_signals_json(snapshot))
        .replace("{stage_name}", ctx.stage_name)
        .replace("{stage_type}", ctx.stage_type)
        .replace("{stage_difficulty}", ctx.stage_difficulty or "")
        .replace("{stage_duration_minutes}", str(ctx.stage_duration_minutes or 0))
        .replace("{signal_filter_types}", _json.dumps(ctx.signal_filter_types))
        .replace("{pass_criteria_json}", ctx.pass_criteria_json)
        .replace("{existing_bank_json}", ctx.existing_bank_json)
        .replace("{prior_banks_json}", ctx.prior_banks_json)
        .replace("{instruction}", body.instruction)
    )

    _log.info(
        "question_bank.draft.called",
        job_id=str(job_id),
        stage_id=str(stage_id),
    )
    return await _call_llm_draft(prompt)
