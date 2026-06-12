"""Bank self-critic — a single LLM pass that audits + corrects a streamed draft bank.

Permanent stage of generation (NOT feature-flagged). Given the draft questions + the
pinned context, returns the CORRECTED full bank + a short critique log persisted to
stage_question_banks.coverage_notes (the scoring audit trail).
"""
from __future__ import annotations

import time
from uuid import UUID

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.question_bank.schemas import BankCritiqueOutput, GeneratedQuestion

logger = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

_critic_prompt_loader = PromptLoader(version=ai_config.question_bank_prompt_version)


def _build_critic_user_message(
    *,
    draft: list[GeneratedQuestion],
    seniority: str,
    role_title: str,
    signals: list[dict],
    stage_difficulty: str,
    stage_duration: int,
) -> str:
    parts: list[str] = []
    parts.append("# ROLE\n\n")
    parts.append(f"Title: {role_title}\nSeniority: {seniority}\n")
    parts.append(f"Stage difficulty: {stage_difficulty}\nStage duration: {stage_duration} min\n")

    parts.append("\n# SIGNAL SNAPSHOT (pinned — values are verbatim)\n\n")
    for s in signals:
        parts.append(
            f"- value: {s['value']!r}\n"
            f"  type: {s.get('type')}\n"
            f"  priority: {s.get('priority')}\n"
            f"  weight: {s.get('weight')}\n"
            f"  knockout: {s.get('knockout', False)}\n"
        )

    parts.append("\n# DRAFT BANK TO AUDIT\n\n")
    parts.append(
        "Each question below is the draft. Return the corrected full list + a critique.\n\n"
    )
    parts.append(BankCritiqueOutput(critique="(draft — to be replaced)", questions=draft).model_dump_json(indent=2))
    parts.append("\n\nNow return a BankCritiqueOutput with the corrected bank.\n")
    return "".join(parts)


async def _create_critic_completion(**kwargs) -> BankCritiqueOutput:
    """Mockable seam over the instructor completion call."""
    client = get_openai_client()
    call_kwargs = dict(
        model=ai_config.question_bank_critic_model,
        response_model=BankCritiqueOutput,
        messages=kwargs["messages"],
        max_retries=1,
        metadata=kwargs.get("metadata", {}),
        prompt_cache_key=f"qbank-critic-{kwargs['job_id']}",
    )
    if ai_config.question_bank_critic_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_critic_effort
    return await client.chat.completions.create(**call_kwargs)


async def run_bank_critic(
    *,
    draft: list[GeneratedQuestion],
    seniority: str,
    role_title: str,
    signals: list[dict],
    stage_difficulty: str,
    stage_duration: int,
    bank_id: UUID,
    tenant_id: UUID,
    job_id: UUID,
) -> tuple[list[GeneratedQuestion], str]:
    """Audit + correct the draft. Returns (corrected_questions, critique_log).

    Raises the underlying exception on LLM/validation failure — the CALLER decides the
    fallback (keep the draft, mark coverage_notes, still reach 'reviewing').
    """
    system_prompt = _critic_prompt_loader.load("question_bank_critic")
    user_message = _build_critic_user_message(
        draft=draft, seniority=seniority, role_title=role_title, signals=signals,
        stage_difficulty=stage_difficulty, stage_duration=stage_duration,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    metadata = {
        "bank_id": str(bank_id),
        "tenant_id": str(tenant_id),
        "job_posting_id": str(job_id),
        "prompt_version": ai_config.question_bank_prompt_version,
    }
    started_at = time.monotonic()
    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="question_bank_critic",
            prompt_version=ai_config.question_bank_prompt_version,
            tenant_id=str(tenant_id),
            bank_id=str(bank_id),
            job_posting_id=str(job_id),
            model=ai_config.question_bank_critic_model,
            reasoning_effort=ai_config.question_bank_critic_effort,
        )
        try:
            result = await _create_critic_completion(
                messages=messages, metadata=metadata, job_id=str(job_id),
            )
        except Exception as exc:
            _span = trace.get_current_span()
            _span.record_exception(exc)
            _span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            logger.error(
                "question_bank.critic.failed",
                bank_id=str(bank_id),
                duration_sec=round(time.monotonic() - started_at, 2),
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                exc_info=True,
            )
            raise

    logger.info(
        "question_bank.critic.complete",
        bank_id=str(bank_id),
        duration_sec=round(time.monotonic() - started_at, 2),
        in_count=len(draft),
        out_count=len(result.questions),
    )
    # Re-pack positions defensively so downstream reconcile sees 0..N-1.
    corrected = list(result.questions)
    for i, q in enumerate(corrected):
        q.position = i
    return corrected, result.critique
