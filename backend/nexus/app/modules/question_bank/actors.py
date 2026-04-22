"""Dramatiq actors for question bank generation.

Three actors:
- generate_question_bank_stage: generate ONE stage's bank
- generate_question_bank_pipeline: generate ALL stages sequentially
- regenerate_question: replace ONE question in an existing bank

All actors use get_bypass_session and SET LOCAL app.current_tenant for RLS,
matching the pattern from app/modules/jd/actors.py.
"""

from __future__ import annotations

import time
from uuid import UUID

import dramatiq
import structlog
from langfuse.decorators import langfuse_context, observe
from sqlalchemy import select
from sqlalchemy.sql import text

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.database import get_bypass_session
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.audit.service import log_event
from app.modules.org_units.service import find_company_profile_in_ancestry
from app.modules.question_bank.schemas import (
    SingleQuestionOutput,
    StageQuestionBankOutput,
)
from app.modules.question_bank.service import (
    ensure_bank_exists,
    get_bank_questions,
    replace_question_in_place,
    transition_to_failed,
    transition_to_generating,
    transition_to_reviewing_after_generation,
    validate_llm_output_against_snapshot,
    write_generated_questions,
)
from app.modules.question_bank.state_machine import auto_revert_on_edit

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------

STAGE_TYPE_TO_PROMPT = {
    "phone_screen":    "question_bank_phone_screen",
    "ai_screening":    "question_bank_ai_screening",
    "human_interview": "question_bank_human_interview",
    "take_home":       "question_bank_take_home",
}


async def _load_pipeline_context(
    db, *, instance_id: UUID
) -> list[dict]:
    """Load all stages in the instance with their metadata, ordered by position."""
    result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance_id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(result.scalars().all())
    return [
        {
            "id": str(s.id),
            "position": s.position,
            "name": s.name,
            "stage_type": s.stage_type,
            "duration_minutes": s.duration_minutes,
            "difficulty": s.difficulty,
            "advance_behavior": s.advance_behavior,
        }
        for s in stages
    ]


async def _load_prior_stages_questions(
    db, *, instance_id: UUID, current_position: int
) -> list[dict]:
    """Load questions from stages with position < current_position, grouped by stage."""
    stage_result = await db.execute(
        select(JobPipelineStage)
        .where(
            JobPipelineStage.instance_id == instance_id,
            JobPipelineStage.position < current_position,
        )
        .order_by(JobPipelineStage.position)
    )
    prior_stages = list(stage_result.scalars().all())

    out = []
    for stage in prior_stages:
        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
        )
        bank = bank_result.scalar_one_or_none()
        questions: list[dict] = []
        if bank is not None:
            q_result = await db.execute(
                select(StageQuestion)
                .where(StageQuestion.bank_id == bank.id)
                .order_by(StageQuestion.position)
            )
            for q in q_result.scalars().all():
                questions.append(
                    {
                        "position": q.position,
                        "text": q.text,
                        "signal_values": q.signal_values,
                        "is_mandatory": q.is_mandatory,
                        "rubric_meets_bar": q.rubric.get("meets_bar", ""),
                    }
                )
        out.append(
            {
                "stage_name": stage.name,
                "stage_type": stage.stage_type,
                "duration_minutes": stage.duration_minutes,
                "difficulty": stage.difficulty,
                "questions": questions,
            }
        )
    return out


def _build_user_message(
    *,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    company_profile: dict | None,
    stage: JobPipelineStage,
    pipeline_stages: list[dict],
    prior_stages_questions: list[dict],
) -> str:
    """Build the user message — all context for the LLM.

    Order matters: context (company profile + JD + signals) BEFORE the stage-
    specific instructions. This matches the 'prompt_context_ordering' rule
    established in Phase 2A.
    """
    parts = []

    parts.append("# JOB CONTEXT\n")
    parts.append(f"Job title: {job.title}\n")
    parts.append(f"Role summary: {snapshot.role_summary}\n")
    parts.append(f"Seniority: {snapshot.seniority_level}\n")
    if job.description_enriched:
        parts.append(
            f"\n## Enriched JD\n\n{job.description_enriched}\n"
        )

    if company_profile:
        parts.append("\n# COMPANY PROFILE\n")
        for key in ("about", "industry", "company_stage", "hiring_bar"):
            if key in company_profile:
                parts.append(f"{key}: {company_profile[key]}\n")

    parts.append("\n# SIGNALS TO ASSESS (pinned snapshot)\n")
    parts.append(
        "Each signal is listed with its metadata. Use the `value` field exactly "
        "as-is in your question's `signal_values` output.\n\n"
    )
    for signal in snapshot.signals:
        parts.append(
            f"- value: {signal['value']!r}\n"
            f"  type: {signal['type']}\n"
            f"  priority: {signal['priority']}\n"
            f"  weight: {signal['weight']}\n"
            f"  knockout: {signal.get('knockout', False)}\n"
            f"  stage_tag: {signal['stage']}\n"
        )

    parts.append("\n# PIPELINE CONTEXT\n")
    current_idx = next(
        (i for i, s in enumerate(pipeline_stages) if s["id"] == str(stage.id)),
        0,
    )
    parts.append(
        f"This pipeline has {len(pipeline_stages)} stages. "
        f"You are generating questions for STAGE {current_idx + 1}.\n\n"
    )

    for i, s in enumerate(pipeline_stages):
        is_current = s["id"] == str(stage.id)
        marker = " (CURRENT — you are generating this)" if is_current else ""
        parts.append(
            f"## Stage {i + 1} — {s['name']}{marker}\n"
            f"  Type: {s['stage_type']}, Duration: {s['duration_minutes']} min, "
            f"Difficulty: {s['difficulty']}\n"
        )

        if not is_current and i < current_idx and i < len(prior_stages_questions):
            prior = prior_stages_questions[i]
            if prior["questions"]:
                parts.append(
                    f"  Already generated questions ({len(prior['questions'])}):\n"
                )
                for q in prior["questions"]:
                    mandatory = " [MANDATORY]" if q["is_mandatory"] else ""
                    parts.append(
                        f"    Q{q['position']}{mandatory} "
                        f"(probes: {q['signal_values']}):\n"
                        f"      {q['text']}\n"
                        f"      Rubric meets_bar: {q['rubric_meets_bar']}\n"
                    )

    parts.append("\n# THIS STAGE'S METADATA\n")
    parts.append(
        f"Name: {stage.name}\n"
        f"Type: {stage.stage_type}\n"
        f"Duration: {stage.duration_minutes} min\n"
        f"Difficulty: {stage.difficulty}\n"
        f"Signal type filter (include_types): "
        f"{stage.signal_filter.get('include_types', [])}\n"
        f"Advance behavior: {stage.advance_behavior}\n"
    )
    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Core generation function (shared by the stage and pipeline actors)
# ---------------------------------------------------------------------------

@observe(name="question_bank_generate")
async def _generate_one_bank(
    db,
    *,
    bank: StageQuestionBank,
    stage: JobPipelineStage,
    instance: JobPipelineInstance,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    started_by: UUID,
) -> None:
    """Run generation for one bank. Must be called with bank.status='generating'.
    On success → transitions to reviewing. On error → transitions to failed.
    Caller must commit or rollback.

    Tracing:
      @observe creates a Langfuse trace named 'question_bank_generate'.
      The OpenAI call inside (via langfuse.openai.AsyncOpenAI) is
      auto-captured as a nested generation span. Trace metadata includes
      bank_id, stage_id, tenant_id, and model/effort so traces are
      searchable per-bank in the Langfuse dashboard. session_id groups
      all retries of the same bank into one Langfuse session (matching
      jd/actors.py). (B13 wiring.)
    """
    # Attach trace metadata for Langfuse dashboard search / grouping.
    langfuse_context.update_current_trace(
        session_id=str(bank.id),
        tags=["question_bank_generate", f"stage_type:{stage.stage_type}"],
        metadata={
            "bank_id": str(bank.id),
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "tenant_id": str(bank.tenant_id),
            "job_posting_id": str(job.id),
            "model": ai_config.question_bank_model,
            "reasoning_effort": ai_config.question_bank_effort,
            "prompt_version": bank.prompt_version,
        },
    )

    try:
        company_profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
        pipeline_stages = await _load_pipeline_context(
            db, instance_id=instance.id
        )
        prior_stages_questions = await _load_prior_stages_questions(
            db, instance_id=instance.id, current_position=stage.position
        )

        type_prompt = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
        if type_prompt is None:
            raise RuntimeError(f"No prompt file mapped for stage_type={stage.stage_type}")

        system_prompt = prompt_loader.load_pair("question_bank_common", type_prompt)
        user_message = _build_user_message(
            job=job,
            snapshot=snapshot,
            company_profile=company_profile,
            stage=stage,
            pipeline_stages=pipeline_stages,
            prior_stages_questions=prior_stages_questions,
        )

        client = get_openai_client()

        logger.info(
            "question_bank.llm_call.start",
            bank_id=str(bank.id),
            stage_id=str(stage.id),
            stage_type=stage.stage_type,
            model=ai_config.question_bank_model,
            reasoning_effort=ai_config.question_bank_effort,
            system_prompt_chars=len(system_prompt),
            user_message_chars=len(user_message),
        )
        call_started_at = time.monotonic()
        try:
            result: StageQuestionBankOutput = await client.chat.completions.create(
                model=ai_config.question_bank_model,
                reasoning_effort=ai_config.question_bank_effort,
                response_model=StageQuestionBankOutput,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_retries=1,
                name="question_bank_generate_call1",
                metadata={
                    "bank_id": str(bank.id),
                    "stage_id": str(stage.id),
                    "stage_type": stage.stage_type,
                    "tenant_id": str(bank.tenant_id),
                    "job_posting_id": str(job.id),
                    "prompt_version": bank.prompt_version,
                },
            )
        except Exception as llm_exc:
            duration_sec = time.monotonic() - call_started_at
            logger.error(
                "question_bank.llm_call.failed",
                bank_id=str(bank.id),
                stage_id=str(stage.id),
                stage_type=stage.stage_type,
                duration_sec=round(duration_sec, 2),
                error_type=type(llm_exc).__name__,
                error_message=str(llm_exc)[:500],
                exc_info=True,
            )
            raise

        duration_sec = time.monotonic() - call_started_at
        logger.info(
            "question_bank.llm_call.complete",
            bank_id=str(bank.id),
            stage_id=str(stage.id),
            stage_type=stage.stage_type,
            duration_sec=round(duration_sec, 2),
            question_count=len(result.questions),
            coverage_notes_chars=len(result.coverage_notes),
            coverage_notes_preview=result.coverage_notes[:100],
        )

        # Post-validate
        allowed_types = stage.signal_filter.get("include_types", [])
        validated = await validate_llm_output_against_snapshot(
            db,
            snapshot=snapshot,
            allowed_types=allowed_types,
            questions=result.questions,
        )

        # Persist the LLM's allocation reasoning
        bank.coverage_notes = result.coverage_notes

        # Write questions to the DB (wipes prior AI-sourced, keeps recruiter-sourced)
        await write_generated_questions(
            db, bank=bank, questions=validated, source="ai_generated"
        )

        # Transition bank → reviewing
        transition_to_reviewing_after_generation(bank, user_id=started_by)
    except Exception as exc:
        logger.error(
            "question_bank.generation_failed",
            bank_id=str(bank.id),
            error=str(exc),
            exc_info=True,
        )
        transition_to_failed(bank, error=str(exc)[:500])
        raise


# ---------------------------------------------------------------------------
# Actor: single stage
# ---------------------------------------------------------------------------

@dramatiq.actor(
    max_retries=2,
    min_backoff=2_000,
    max_backoff=60_000,
    queue_name="question_bank_generation",
)
async def generate_question_bank_stage(
    bank_id: str,
    tenant_id: str,
    started_by: str,
) -> None:
    """Generate questions for ONE stage's bank. Retries on transient failures.

    Before the first call, the router must have:
    - Ensured the bank exists
    - Set bank.status = 'generating'
    - Committed so the actor sees the updated state
    """
    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == UUID(bank_id))
        )
        bank = bank_result.scalar_one_or_none()
        if bank is None:
            logger.error("question_bank.bank_missing", bank_id=bank_id)
            return

        # Load stage, instance, job, snapshot
        stage_result = await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
        )
        stage = stage_result.scalar_one()
        instance_result = await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == stage.instance_id
            )
        )
        instance = instance_result.scalar_one()
        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == bank.job_posting_id)
        )
        job = job_result.scalar_one()
        snap_result = await db.execute(
            select(JobPostingSignalSnapshot).where(
                JobPostingSignalSnapshot.id == bank.signal_snapshot_id
            )
        )
        snapshot = snap_result.scalar_one()

        try:
            await _generate_one_bank(
                db,
                bank=bank,
                stage=stage,
                instance=instance,
                job=job,
                snapshot=snapshot,
                started_by=UUID(started_by),
            )
            await log_event(
                db,
                tenant_id=UUID(tenant_id),
                actor_id=UUID(started_by),
                actor_email=None,
                action="question_bank.bank_generated",
                resource="stage_question_bank",
                resource_id=bank.id,
            )
            await db.commit()
        except Exception:
            # Only commit if _generate_one_bank ran its `except` branch and
            # already transitioned the bank to 'failed'. Anything else
            # (e.g. a DB outage between the LLM call and the status write,
            # or a bug higher up in the stack) would commit partially-
            # written state. Roll back and re-raise so Dramatiq can retry
            # or dead-letter the task cleanly.
            if bank.status == "failed":
                await db.commit()
            else:
                logger.warning(
                    "question_bank.stage_actor_rollback",
                    bank_id=str(bank.id),
                    bank_status=bank.status,
                    reason="exception_outside_failed_transition",
                )
                await db.rollback()
            raise


# ---------------------------------------------------------------------------
# Actor: full pipeline (sequential — required for anti-lie coherence)
# ---------------------------------------------------------------------------

@dramatiq.actor(
    max_retries=0,
    time_limit=1_800_000,  # 30 minutes
    queue_name="question_bank_generation",
)
async def generate_question_bank_pipeline(
    instance_id: str,
    tenant_id: str,
    started_by: str,
) -> None:
    """Generate banks for ALL stages in a pipeline, sequentially.

    Sequential is REQUIRED — stage N needs to see stages 1..N-1's questions.
    On mid-pipeline failure: marks that stage failed, CONTINUES to next stage.
    User retries failed stages individually via the single-stage endpoint.
    """
    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        instance_result = await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == UUID(instance_id)
            )
        )
        instance = instance_result.scalar_one_or_none()
        if instance is None:
            logger.error("question_bank.instance_missing", instance_id=instance_id)
            return

        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == instance.job_posting_id)
        )
        job = job_result.scalar_one()

        stages_result = await db.execute(
            select(JobPipelineStage)
            .where(JobPipelineStage.instance_id == instance.id)
            .order_by(JobPipelineStage.position)
        )
        stages = list(stages_result.scalars().all())

        succeeded = 0
        failed = 0
        for stage in stages:
            # Ensure bank exists and is in generating state
            bank = await ensure_bank_exists(db, stage=stage, job=job)
            try:
                transition_to_generating(bank)
                await db.flush()
            except Exception as exc:
                logger.warning(
                    "question_bank.skip_busy_stage",
                    stage_id=str(stage.id),
                    reason=str(exc),
                )
                continue

            snap_result = await db.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == bank.signal_snapshot_id
                )
            )
            snapshot = snap_result.scalar_one()

            try:
                await _generate_one_bank(
                    db,
                    bank=bank,
                    stage=stage,
                    instance=instance,
                    job=job,
                    snapshot=snapshot,
                    started_by=UUID(started_by),
                )
                succeeded += 1
                await db.flush()
            except Exception as exc:
                logger.error(
                    "question_bank.pipeline_stage_failed",
                    stage_id=str(stage.id),
                    error=str(exc),
                )
                failed += 1
                # _generate_one_bank already transitioned the bank to failed
                await db.flush()
                continue  # move to next stage

        await log_event(
            db,
            tenant_id=UUID(tenant_id),
            actor_id=UUID(started_by),
            actor_email=None,
            action="question_bank.pipeline_generation_complete",
            resource="job_pipeline_instance",
            resource_id=instance.id,
            payload={"succeeded": succeeded, "failed": failed, "total": len(stages)},
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Actor: single question regeneration
# ---------------------------------------------------------------------------

@observe(name="question_bank_regenerate")
async def _regenerate_one_question(
    db,
    *,
    question: StageQuestion,
    bank: StageQuestionBank,
    stage: JobPipelineStage,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    replace_signal_values: list[str] | None,
) -> None:
    """Inner helper for regenerate_question that owns the LLM + DB write.

    Separated from the Dramatiq actor so @observe() can wrap only the
    observable path (not the actor's session bootstrap). Matches the
    jd/actors.py `_run_extraction` pattern. (B13 wiring.)
    """
    # Attach trace metadata for Langfuse dashboard search / grouping.
    langfuse_context.update_current_trace(
        session_id=str(bank.id),
        tags=["question_bank_regenerate", f"stage_type:{stage.stage_type}"],
        metadata={
            "bank_id": str(bank.id),
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "tenant_id": str(bank.tenant_id),
            "job_posting_id": str(job.id),
            "question_id": str(question.id),
            "model": ai_config.question_bank_model,
            "reasoning_effort": ai_config.question_bank_effort,
            "prompt_version": bank.prompt_version,
        },
    )

    # Build prompt: common header + regenerate_one + rich user context
    system_prompt = prompt_loader.load_pair(
        "question_bank_common", "question_bank_regenerate_one"
    )

    other_questions = await get_bank_questions(db, bank.id)
    other_questions = [q for q in other_questions if q.id != question.id]
    target_signals = replace_signal_values or question.signal_values

    user_parts = [
        f"# JOB CONTEXT\n\nJob: {job.title}\nSeniority: {snapshot.seniority_level}\n\n",
        "# SIGNALS (pinned snapshot)\n",
    ]
    for signal in snapshot.signals:
        user_parts.append(
            f"- {signal['value']!r} (type: {signal['type']}, "
            f"weight: {signal['weight']}, knockout: {signal.get('knockout', False)})\n"
        )

    user_parts.append("\n# CURRENT QUESTION BEING REPLACED\n")
    user_parts.append(
        f"Text: {question.text}\n"
        f"Probes: {question.signal_values}\n"
        f"Rubric meets_bar: {question.rubric.get('meets_bar', '')}\n"
        f"Estimated minutes: {question.estimated_minutes}\n"
    )

    user_parts.append("\n# TARGET SIGNALS (probe these)\n")
    for v in target_signals:
        user_parts.append(f"- {v!r}\n")

    user_parts.append("\n# OTHER QUESTIONS IN THIS STAGE'S BANK — DO NOT DUPLICATE\n")
    for q in other_questions:
        user_parts.append(
            f"- Q{q.position}: {q.text} (probes: {q.signal_values})\n"
        )

    user_parts.append(
        f"\n# STAGE METADATA\n"
        f"Type: {stage.stage_type}, Duration: {stage.duration_minutes} min, "
        f"Difficulty: {stage.difficulty}\n"
    )

    user_parts.append(
        "\nNow generate ONE replacement question as a SingleQuestionOutput.\n"
    )

    client = get_openai_client()

    logger.info(
        "question_bank.llm_call.start",
        call_type="regenerate_question",
        question_id=str(question.id),
        bank_id=str(bank.id),
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        system_prompt_chars=len(system_prompt),
        user_message_chars=sum(len(p) for p in user_parts),
    )
    call_started_at = time.monotonic()
    try:
        result: SingleQuestionOutput = await client.chat.completions.create(
            model=ai_config.question_bank_model,
            reasoning_effort=ai_config.question_bank_effort,
            response_model=SingleQuestionOutput,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "".join(user_parts)},
            ],
            max_retries=1,
            name="question_bank_regenerate_call1",
            metadata={
                "bank_id": str(bank.id),
                "stage_id": str(stage.id),
                "stage_type": stage.stage_type,
                "tenant_id": str(bank.tenant_id),
                "job_posting_id": str(job.id),
                "question_id": str(question.id),
                "prompt_version": bank.prompt_version,
            },
        )
    except Exception as llm_exc:
        duration_sec = time.monotonic() - call_started_at
        logger.error(
            "question_bank.llm_call.failed",
            call_type="regenerate_question",
            question_id=str(question.id),
            bank_id=str(bank.id),
            duration_sec=round(duration_sec, 2),
            error_type=type(llm_exc).__name__,
            error_message=str(llm_exc)[:500],
            exc_info=True,
        )
        raise

    duration_sec = time.monotonic() - call_started_at
    logger.info(
        "question_bank.llm_call.complete",
        call_type="regenerate_question",
        question_id=str(question.id),
        bank_id=str(bank.id),
        duration_sec=round(duration_sec, 2),
        reasoning_chars=len(result.reasoning),
    )

    # Post-validate the one question against the snapshot
    allowed_types = stage.signal_filter.get("include_types", [])
    await validate_llm_output_against_snapshot(
        db,
        snapshot=snapshot,
        allowed_types=allowed_types,
        questions=[result.question],
    )

    await replace_question_in_place(
        db, question=question, new_data=result.question
    )
    # Auto-revert on edit (confirmed → reviewing if needed)
    auto_revert_on_edit(bank)
    await db.flush()


@dramatiq.actor(
    max_retries=2,
    min_backoff=2_000,
    max_backoff=30_000,
    queue_name="question_bank_generation",
)
async def regenerate_question(
    question_id: str,
    tenant_id: str,
    started_by: str,
    replace_signal_values: list[str] | None = None,
) -> None:
    """Regenerate a single question slot, preserving its UUID.

    Uses the regenerate-one prompt which takes other questions in the bank
    as 'do not duplicate' context. Delegates the observable LLM + DB write
    path to `_regenerate_one_question` so @observe() wraps just that
    portion (matching jd/actors.py).
    """
    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        q_result = await db.execute(
            select(StageQuestion).where(StageQuestion.id == UUID(question_id))
        )
        question = q_result.scalar_one_or_none()
        if question is None:
            logger.error("question_bank.question_missing", question_id=question_id)
            return

        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == question.bank_id)
        )
        bank = bank_result.scalar_one()
        stage_result = await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
        )
        stage = stage_result.scalar_one()
        instance_result = await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == stage.instance_id
            )
        )
        _instance = instance_result.scalar_one()
        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == bank.job_posting_id)
        )
        job = job_result.scalar_one()
        snap_result = await db.execute(
            select(JobPostingSignalSnapshot).where(
                JobPostingSignalSnapshot.id == bank.signal_snapshot_id
            )
        )
        snapshot = snap_result.scalar_one()

        await _regenerate_one_question(
            db,
            question=question,
            bank=bank,
            stage=stage,
            job=job,
            snapshot=snapshot,
            replace_signal_values=replace_signal_values,
        )

        await log_event(
            db,
            tenant_id=UUID(tenant_id),
            actor_id=UUID(started_by),
            actor_email=None,
            action="question_bank.question_regenerated",
            resource="stage_question",
            resource_id=question.id,
            payload={"bank_id": str(bank.id)},
        )
        await db.commit()
