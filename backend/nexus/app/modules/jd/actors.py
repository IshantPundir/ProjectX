"""Dramatiq actor for Call 1 (JD enhancement + signal extraction).

The public actor wraps an inner _run_extraction() coroutine that accepts
a DB session as a parameter. This split makes the coroutine unit-testable
without spinning up Dramatiq's scheduler — tests pass a transactional
session directly and mock get_openai_client()."""

from uuid import UUID

import dramatiq
import structlog
from dramatiq.middleware import CurrentMessage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.ai.schemas import ExtractionOutput
from app.database import get_bypass_session
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.errors import sanitize_error_for_user
from app.modules.jd.state_machine import transition
from app.modules.org_units.service import find_company_profile_in_ancestry

logger = structlog.get_logger()


def _build_user_message(job: JobPosting, profile: dict) -> str:
    """Build the Call 1 user message in the mandatory ordering:
    company profile → raw JD → project scope.

    The context (profile) MUST come before the document (JD) — this primes
    the model correctly from the first token. See feedback_prompt_context_ordering
    in user memory."""
    parts: list[str] = [
        "## Company Profile\n"
        f"- About: {profile['about']}\n"
        f"- Industry: {profile['industry']}\n"
        f"- Company stage: {profile['company_stage']}\n"
        f"- Hiring bar: {profile['hiring_bar']}\n",
        f"## Raw Job Description\n\n{job.description_raw}\n",
    ]
    if job.project_scope_raw:
        parts.append(f"## Project Scope\n\n{job.project_scope_raw}\n")
    return "\n".join(parts)


async def _persist_enriched(
    db: AsyncSession, job: JobPosting, result: ExtractionOutput
) -> None:
    """Write the enriched JD onto the job row and insert a new snapshot."""
    job.description_enriched = result.enriched_jd

    snapshot = JobPostingSignalSnapshot(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        version=1,
        required_skills=[item.model_dump() for item in result.signals.required_skills],
        preferred_skills=[item.model_dump() for item in result.signals.preferred_skills],
        must_haves=[item.model_dump() for item in result.signals.must_haves],
        good_to_haves=[item.model_dump() for item in result.signals.good_to_haves],
        min_experience_years=result.signals.min_experience_years,
        seniority_level=result.signals.seniority_level,
        role_summary=result.signals.role_summary,
    )
    db.add(snapshot)


async def _run_extraction(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Core extraction logic — unit-testable without Dramatiq."""
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.actor.job_not_found")
        return

    if job.status != "signals_extracting":
        # Idempotency guard: don't double-process
        log.warn("jd.actor.skip_unexpected_state", state=job.status)
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        # This should never happen — create_job_posting validated it.
        # Defensive: mark as failed.
        job.status_error = "Company profile missing — create_job_posting should have blocked this"
        await transition(
            db, job,
            to_state="signals_extraction_failed",
            actor_id=None,
            correlation_id=correlation_id,
        )
        return

    try:
        client = get_openai_client()
        prompt = prompt_loader.get("jd_enhancement")
        extraction: ExtractionOutput = await client.chat.completions.create(
            model=ai_config.extraction_model,
            reasoning_effort=ai_config.extraction_effort,
            response_model=ExtractionOutput,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": _build_user_message(job, profile)},
            ],
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        log.error("jd.actor.call1_failed", exc_info=exc)
        if retries_so_far >= 2:
            # Final attempt — sanitize and transition to _failed
            job.status_error = sanitize_error_for_user(exc)
            await transition(
                db, job,
                to_state="signals_extraction_failed",
                actor_id=None,
                correlation_id=correlation_id,
            )
        raise  # Dramatiq retries on all non-final exceptions

    # Success path
    await _persist_enriched(db, job, extraction)
    await transition(
        db, job,
        to_state="signals_extracted",
        actor_id=None,
        correlation_id=correlation_id,
    )
    log.info("jd.actor.completed")


@dramatiq.actor(
    max_retries=3,
    min_backoff=2_000,
    max_backoff=60_000,
    queue_name="jd_extraction",
)
async def extract_and_enhance_jd(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Dramatiq entry point. Opens a bypass DB session (no HTTP request
    context), sets app.current_tenant for RLS, delegates to _run_extraction,
    commits on success."""
    current = CurrentMessage.get_current_message()
    retries_so_far = current.options.get("retries", 0) if current else 0

    async with get_bypass_session() as db:
        await db.execute(
            text("SET LOCAL app.current_tenant = :t"),
            {"t": tenant_id},
        )
        try:
            await _run_extraction(
                db,
                job_posting_id=job_posting_id,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                retries_so_far=retries_so_far,
            )
            await db.commit()
        except Exception:
            # _run_extraction already transitioned to _failed on final retry
            # and staged the changes; commit them so the user sees the failed
            # state. Intermediate retries rollback silently (state unchanged).
            if retries_so_far >= 2:
                await db.commit()
            else:
                await db.rollback()
            raise
