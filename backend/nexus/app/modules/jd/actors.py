"""Dramatiq actor for Call 1 (JD enhancement + signal extraction).

The public actor wraps an inner _run_extraction() coroutine that accepts
a DB session as a parameter. This split makes the coroutine unit-testable
without spinning up Dramatiq's scheduler — tests pass a transactional
session directly and mock get_openai_client().

Tracing:
  _run_extraction is decorated with @observe() which creates a parent
  Langfuse trace. The OpenAI call (via langfuse.openai.AsyncOpenAI) is
  auto-captured as a child generation, so each extraction job becomes a
  single trace with the LLM call nested inside. Metadata (tenant_id,
  correlation_id, job_posting_id, prompt_version) is propagated to all
  child spans via propagate_attributes."""

import asyncio
import json
from uuid import UUID

import dramatiq
import openai
import structlog
from dramatiq.middleware import CurrentMessage
from instructor.core import InstructorRetryException
from langfuse.decorators import langfuse_context, observe
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import flush_langfuse, get_openai_client, langfuse_enabled
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.ai.schemas import ExtractionOutput, ReEnrichmentOutput
from app.database import get_bypass_session
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.errors import sanitize_error_for_user
from app.modules.jd.state_machine import transition
from app.modules.org_units.service import find_company_profile_in_ancestry

logger = structlog.get_logger()

# --- Retry classification ---------------------------------------------------
# Permanent exceptions will never succeed on retry — the input is bad, the
# key is wrong, or instructor already exhausted its own retry budget.
# Transient exceptions (rate limit, timeout, connection) may succeed later.
_PERMANENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.BadRequestError,        # 400 — malformed input, won't change on retry
    openai.AuthenticationError,    # 401 — wrong API key
    openai.PermissionDeniedError,  # 403 — no access to this model
    openai.NotFoundError,          # 404 — model doesn't exist
    InstructorRetryException,      # instructor exhausted its own internal retries
)


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
    """Write the enriched JD onto the job row and insert a new snapshot.

    The version is auto-incremented from the latest existing snapshot for
    this job. On first extraction it starts at 1; on retry after failure it
    increments (2, 3, …) so the unique constraint (job_posting_id, version)
    is never violated."""
    job.description_enriched = result.enriched_jd
    job.enrichment_status = "completed"

    # Determine next snapshot version
    max_version_result = await db.execute(
        select(func.max(JobPostingSignalSnapshot.version)).where(
            JobPostingSignalSnapshot.job_posting_id == job.id
        )
    )
    current_max = max_version_result.scalar() or 0

    snapshot = JobPostingSignalSnapshot(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        version=current_max + 1,
        signals=[item.model_dump() for item in result.signals.signals],
        seniority_level=result.signals.seniority_level,
        role_summary=result.signals.role_summary,
        prompt_version="v1",
    )
    db.add(snapshot)


@observe(name="jd_extraction_call1")
async def _run_extraction(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Core extraction logic — unit-testable without Dramatiq.

    @observe() creates a Langfuse trace for this function. The OpenAI call
    inside (via langfuse.openai.AsyncOpenAI) is auto-captured as a nested
    generation span. propagate_attributes attaches tenant/job/correlation
    metadata to the trace and all child spans."""
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

    # Attach trace metadata so every span in this extraction job is
    # searchable in the Langfuse dashboard by tenant, job, or correlation_id.
    # session_id groups all retries of the same job into one Langfuse session.
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_extraction", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_enhancement",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "retries_so_far": retries_so_far,
        },
    )

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
            name="jd_enhancement_call1",
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
        log.error(
            "jd.actor.call1_failed",
            exc_info=exc,
            permanent=is_permanent,
            retries_so_far=retries_so_far,
        )

        if is_permanent or retries_so_far >= 2:
            # Permanent error → fail immediately, no retry.
            # Final transient retry → fail with sanitized message.
            job.status_error = sanitize_error_for_user(exc)
            await transition(
                db, job,
                to_state="signals_extraction_failed",
                actor_id=None,
                correlation_id=correlation_id,
            )
            if is_permanent:
                # Return normally — Dramatiq won't retry. The outer function
                # commits the failed state via the normal success path.
                return
        raise  # Transient error — Dramatiq retries with backoff

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
    max_retries=2,
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
        # SET LOCAL does NOT accept bind parameters in PostgreSQL — the
        # value must be a literal. asyncpg translates :t → $1 which
        # fails with "syntax error at or near $1". f-string interpolation
        # is the correct pattern (same as app/database.py::get_tenant_db).
        # Defensive UUID round-trip rejects any malformed tenant_id before
        # it reaches the SQL; the result is always a canonical UUID string
        # with no injection vectors.
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
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
        finally:
            # Flush Langfuse traces after each actor invocation so they
            # reach the server promptly. The worker process may not shut
            # down cleanly, so we can't rely on atexit hooks alone.
            # flush_langfuse() is a synchronous HTTP call — run it in a
            # thread pool to avoid blocking the async event loop.
            if langfuse_enabled():
                await asyncio.to_thread(flush_langfuse)


# --- Call 2: Re-enrichment after recruiter signal edits -----------------------


def _build_reenrich_user_message(
    job: JobPosting, profile: dict, snapshot: JobPostingSignalSnapshot
) -> str:
    """Build the Call 2 user message in the mandatory ordering:
    company profile -> raw JD -> current enriched JD -> updated signal snapshot.

    Context (profile) MUST come before documents (JD). See
    feedback_prompt_context_ordering in user memory."""
    snapshot_data = {
        "signals": snapshot.signals,
        "seniority_level": snapshot.seniority_level,
        "role_summary": snapshot.role_summary,
    }

    parts: list[str] = [
        "## Company Profile\n"
        f"- About: {profile['about']}\n"
        f"- Industry: {profile['industry']}\n"
        f"- Company stage: {profile['company_stage']}\n"
        f"- Hiring bar: {profile['hiring_bar']}\n",
        f"## Original Raw Job Description\n\n{job.description_raw}\n",
        f"## Current Enriched Job Description\n\n{job.description_enriched or '(none)'}\n",
        f"## Updated Signal Snapshot\n\n```json\n{json.dumps(snapshot_data, indent=2)}\n```\n",
    ]
    return "\n".join(parts)


@observe(name="jd_reenrichment_call2")
async def _run_reenrichment(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Core re-enrichment logic — unit-testable without Dramatiq.

    @observe() creates a Langfuse trace. The OpenAI call is auto-captured
    as a nested generation span."""
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.reenrich.job_not_found")
        return

    if job.enrichment_status != "streaming":
        log.warn("jd.reenrich.skip_unexpected_status", enrichment_status=job.enrichment_status)
        return

    # Load latest snapshot by version DESC
    snap_result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job.id)
        .order_by(JobPostingSignalSnapshot.version.desc())
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    if snapshot is None:
        log.error("jd.reenrich.no_snapshot")
        job.enrichment_status = "failed"
        job.enrichment_error = "No signal snapshot found — cannot re-enrich"
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        job.enrichment_status = "failed"
        job.enrichment_error = "Company profile missing — cannot re-enrich"
        return

    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_reenrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_reenrichment",
            "prompt_version": "v1",
            "model": ai_config.reenrichment_model,
            "reasoning_effort": ai_config.reenrichment_effort,
            "retries_so_far": retries_so_far,
        },
    )

    try:
        client = get_openai_client()
        prompt = prompt_loader.get("jd_reenrichment")
        reenriched: ReEnrichmentOutput = await client.chat.completions.create(
            model=ai_config.reenrichment_model,
            reasoning_effort=ai_config.reenrichment_effort,
            response_model=ReEnrichmentOutput,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": _build_reenrich_user_message(job, profile, snapshot)},
            ],
            name="jd_reenrichment_call2",
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
        log.error(
            "jd.reenrich.call2_failed",
            exc_info=exc,
            permanent=is_permanent,
            retries_so_far=retries_so_far,
        )

        if is_permanent or retries_so_far >= 1:
            job.enrichment_status = "failed"
            job.enrichment_error = sanitize_error_for_user(exc)
            if is_permanent:
                return
        raise  # Transient error — Dramatiq retries with backoff

    # Success path
    job.description_enriched = reenriched.enriched_jd
    job.enrichment_status = "completed"
    job.enriched_manually_edited = True
    log.info("jd.reenrich.completed")


@dramatiq.actor(
    max_retries=1,
    min_backoff=2_000,
    max_backoff=30_000,
    queue_name="jd_reenrichment",
)
async def reenrich_jd(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Dramatiq entry point for Call 2 (re-enrichment after signal edits).
    Opens a bypass DB session, sets RLS tenant, delegates to _run_reenrichment."""
    current = CurrentMessage.get_current_message()
    retries_so_far = current.options.get("retries", 0) if current else 0

    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )
        try:
            await _run_reenrichment(
                db,
                job_posting_id=job_posting_id,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                retries_so_far=retries_so_far,
            )
            await db.commit()
        except Exception:
            if retries_so_far >= 1:
                await db.commit()
            else:
                await db.rollback()
            raise
        finally:
            if langfuse_enabled():
                await asyncio.to_thread(flush_langfuse)
