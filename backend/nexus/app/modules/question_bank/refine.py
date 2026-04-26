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
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client
from app.ai.config import AIConfig
from app.ai.prompts import prompt_loader
from app.database import get_tenant_db
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.jd.authz import require_job_access
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
        name="question_refine_call",
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
        name="question_draft_call",
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
