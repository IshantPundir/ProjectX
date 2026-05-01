"""Dramatiq actor for Call 1 (JD enrichment + signal extraction).

The public actor wraps two inner coroutines (_run_enrichment and
_run_signal_extraction) that each accept a DB session as a parameter.
This split makes the coroutines unit-testable without spinning up
Dramatiq's scheduler — tests pass a transactional session directly and
mock get_openai_client().

Tracing:
  Each phase coroutine's OpenAI call is wrapped in an explicit
  ``with _tracer.start_as_current_span("openai.chat.completions.create")``
  block. set_llm_span_attributes() inside that block adds prompt metadata
  (tenant_id, correlation_id, job_posting_id, prompt_version) to the active
  span. Exceptions tag the span with StatusCode.ERROR before the re-raise."""

import asyncio
import json
import time
from uuid import UUID

import dramatiq
import openai
import structlog
from dramatiq.middleware import CurrentMessage
from instructor.core import InstructorRetryException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.ai.client import get_openai_client
from app.ai.tracing import set_llm_span_attributes
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.ai.schemas import (
    EnrichmentOutput,
    ExtractedSignals,
    ReEnrichmentOutput,
    SignalExtractionOutput,
)
from app import pubsub
from app.database import get_bypass_session
from app.modules.jd.errors import sanitize_error_for_user
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.service import get_job_status
from app.modules.jd.state_machine import transition
from app.modules.org_units import find_company_profile_in_ancestry

logger = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

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


async def _persist_enriched_jd_only(
    db: AsyncSession, job: JobPosting, enriched_jd: str
) -> None:
    """Phase 1 persistence — write enriched JD onto the job row.

    Sets enrichment_status='completed'. Does NOT touch signal snapshots.
    """
    job.description_enriched = enriched_jd
    job.enrichment_status = "completed"


async def _persist_signal_snapshot(
    db: AsyncSession, job: JobPosting, signals: "ExtractedSignals"
) -> None:
    """Phase 2 persistence — insert a new snapshot at MAX(version)+1.

    The version is auto-incremented from the latest existing snapshot for
    this job. On first extraction it starts at 1; on retry after failure it
    increments (2, 3, …) so the unique constraint (job_posting_id, version)
    is never violated.
    """
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
        signals=[item.model_dump() for item in signals.signals],
        seniority_level=signals.seniority_level,
        role_summary=signals.role_summary,
        prompt_version="v1",
    )
    db.add(snapshot)


async def _run_enrichment(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Phase 1 — JD enrichment only.

    Reads job.description_raw + company profile, calls jd_enrichment.txt,
    writes job.description_enriched, sets enrichment_status='completed'
    on success. Idempotent: skipped if enrichment_status is already
    'completed'.

    On permanent error or final retry: sets enrichment_status='failed'
    and transitions main status to signals_extraction_failed.
    """
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
        phase="enrichment",
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.actor.job_not_found")
        return

    if job.status != "signals_extracting":
        log.warn("jd.actor.skip_unexpected_state", state=job.status)
        return

    if job.enrichment_status == "completed":
        # Already enriched on a previous attempt — skip phase 1, save tokens.
        log.info("jd.enrichment.skip_already_complete")
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        job.status_error = "Company profile missing — create_job_posting should have blocked this"
        job.enrichment_status = "failed"
        await transition(
            db, job, to_state="signals_extraction_failed",
            actor_id=None, correlation_id=correlation_id,
        )
        return

    client = get_openai_client()
    prompt = prompt_loader.get("jd_enrichment")
    user_message = _build_user_message(job, profile)

    log.info(
        "jd.llm_call.start", call_type="enrichment",
        model=ai_config.extraction_model,
        reasoning_effort=ai_config.extraction_effort,
        system_prompt_chars=len(prompt),
        user_message_chars=len(user_message),
    )
    call_started_at = time.monotonic()
    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="jd_enrichment",
            prompt_version="v1",
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            job_posting_id=job_posting_id,
            model=ai_config.extraction_model,
            reasoning_effort=ai_config.extraction_effort,
            retries_so_far=retries_so_far,
        )
        try:
            enrichment: EnrichmentOutput = await client.chat.completions.create(
                model=ai_config.extraction_model,
                reasoning_effort=ai_config.extraction_effort,
                response_model=EnrichmentOutput,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_message},
                ],
                name="jd_enrichment_call",
                metadata={
                    "correlation_id": correlation_id,
                    "job_posting_id": job_posting_id,
                    "tenant_id": tenant_id,
                    "prompt_version": "v1",
                },
            )
        except Exception as exc:
            # Tag the active span with error status so failed LLM calls
            # render as errors in OTel backends (the auto-instrumentor
            # we replaced did this automatically).
            _span = trace.get_current_span()
            _span.record_exception(exc)
            _span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            duration_sec = time.monotonic() - call_started_at
            is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
            log.error(
                "jd.llm_call.failed", call_type="enrichment",
                duration_sec=round(duration_sec, 2),
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                permanent=is_permanent,
                retries_so_far=retries_so_far,
                exc_info=exc,
            )
            if is_permanent or retries_so_far >= 2:
                job.enrichment_status = "failed"
                job.status_error = sanitize_error_for_user(exc)
                await transition(
                    db, job, to_state="signals_extraction_failed",
                    actor_id=None, correlation_id=correlation_id,
                )
                if is_permanent:
                    return
            raise

    duration_sec = time.monotonic() - call_started_at
    log.info(
        "jd.llm_call.complete", call_type="enrichment",
        duration_sec=round(duration_sec, 2),
        enriched_jd_chars=len(enrichment.enriched_jd),
    )
    await _persist_enriched_jd_only(db, job, enrichment.enriched_jd)
    log.info("jd.enrichment.completed")


async def _run_signal_extraction(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Phase 2 — signal extraction only.

    Reads either job.description_enriched (if phase 1 ran) or
    job.description_raw (if skip_enrichment), calls jd_signal_extraction.txt,
    writes a new JobPostingSignalSnapshot v1 row, transitions main state
    signals_extracting → signals_extracted on success.

    Idempotent: skipped if main status is no longer 'signals_extracting'.
    """
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        retries_so_far=retries_so_far,
        phase="signal_extraction",
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.actor.job_not_found")
        return

    if job.status != "signals_extracting":
        log.warn("jd.actor.skip_unexpected_state", state=job.status)
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        job.status_error = "Company profile missing — create_job_posting should have blocked this"
        await transition(
            db, job, to_state="signals_extraction_failed",
            actor_id=None, correlation_id=correlation_id,
        )
        return

    # Use enriched JD if phase 1 ran; otherwise use raw JD.
    source_is_enriched = (
        job.enrichment_status == "completed" and job.description_enriched is not None
    )
    source_jd = job.description_enriched if source_is_enriched else job.description_raw

    client = get_openai_client()
    prompt = prompt_loader.get("jd_signal_extraction")
    # Build the user message with whichever JD applies.
    user_message_parts: list[str] = [
        "## Company Profile\n"
        f"- About: {profile['about']}\n"
        f"- Industry: {profile['industry']}\n"
        f"- Company stage: {profile['company_stage']}\n"
        f"- Hiring bar: {profile['hiring_bar']}\n",
        f"## Job Description\n\n{source_jd}\n",
    ]
    if job.project_scope_raw:
        user_message_parts.append(f"## Project Scope\n\n{job.project_scope_raw}\n")
    user_message = "\n".join(user_message_parts)

    log.info(
        "jd.llm_call.start", call_type="signal_extraction",
        source="enriched" if source_is_enriched else "raw",
        model=ai_config.extraction_model,
        reasoning_effort=ai_config.extraction_effort,
        system_prompt_chars=len(prompt),
        user_message_chars=len(user_message),
    )
    call_started_at = time.monotonic()
    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="jd_signal_extraction",
            prompt_version="v1",
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            job_posting_id=job_posting_id,
            model=ai_config.extraction_model,
            reasoning_effort=ai_config.extraction_effort,
            source_jd="enriched" if source_is_enriched else "raw",
            retries_so_far=retries_so_far,
        )
        try:
            signal_output: SignalExtractionOutput = await client.chat.completions.create(
                model=ai_config.extraction_model,
                reasoning_effort=ai_config.extraction_effort,
                response_model=SignalExtractionOutput,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_message},
                ],
                name="jd_signal_extraction_call",
                metadata={
                    "correlation_id": correlation_id,
                    "job_posting_id": job_posting_id,
                    "tenant_id": tenant_id,
                    "prompt_version": "v1",
                },
            )
        except Exception as exc:
            # Tag the active span with error status so failed LLM calls
            # render as errors in OTel backends (the auto-instrumentor
            # we replaced did this automatically).
            _span = trace.get_current_span()
            _span.record_exception(exc)
            _span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            duration_sec = time.monotonic() - call_started_at
            is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
            log.error(
                "jd.llm_call.failed", call_type="signal_extraction",
                duration_sec=round(duration_sec, 2),
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                permanent=is_permanent,
                retries_so_far=retries_so_far,
                exc_info=exc,
            )
            if is_permanent or retries_so_far >= 2:
                job.status_error = sanitize_error_for_user(exc)
                await transition(
                    db, job, to_state="signals_extraction_failed",
                    actor_id=None, correlation_id=correlation_id,
                )
                if is_permanent:
                    return
            raise

    duration_sec = time.monotonic() - call_started_at
    log.info(
        "jd.llm_call.complete", call_type="signal_extraction",
        duration_sec=round(duration_sec, 2),
        signal_count=len(signal_output.signals.signals),
    )
    await _persist_signal_snapshot(db, job, signal_output.signals)
    await transition(
        db, job, to_state="signals_extracted",
        actor_id=None, correlation_id=correlation_id,
    )
    log.info("jd.signal_extraction.completed")


async def _publish_status(
    job_posting_id: str, tenant_id: str, correlation_id: str
) -> None:
    """Open a fresh session, read the committed JobStatusEvent, publish it.

    Used between phases so each commit is followed by an SSE event.
    Best-effort — failures are logged but never raised (consistent with
    pubsub.publish() semantics).
    """
    try:
        async with get_bypass_session() as pub_db:
            safe_tenant_id = str(UUID(tenant_id))
            await pub_db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
            )
            status_event = await get_job_status(pub_db, UUID(job_posting_id))
    except Exception as exc:
        logger.warning(
            "actors.extract_and_enhance_jd.publish_read_failed",
            job_posting_id=job_posting_id, error=str(exc),
        )
        return

    if status_event is not None:
        await pubsub.publish(
            pubsub.job_channel(job_posting_id),
            pubsub.Events.JD_STATUS_CHANGED,
            status_event.model_dump(mode="json"),
            correlation_id=correlation_id,
        )


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
    skip_enrichment: bool = False,
) -> None:
    """Two-phase JD processing.

    Phase 1 (conditional on `skip_enrichment`): enrichment LLM call →
    write description_enriched, commit, publish status event.
    Phase 2 (always): signal extraction LLM call → write snapshot,
    transition to signals_extracted, commit, publish status event.

    Each phase opens its own DB session and commits independently so
    the intermediate state is visible to SSE subscribers. On retry,
    phase 1 is skipped automatically when enrichment_status='completed'.
    """
    current = CurrentMessage.get_current_message()
    retries_so_far = current.options.get("retries", 0) if current else 0

    safe_tenant_id = str(UUID(tenant_id))
    _exc_to_reraise: BaseException | None = None

    # ---- Phase 1: Enrichment (conditional) ----
    if not skip_enrichment:
        # Pre-phase-1 mark: write enrichment_status='streaming' visibly so the
        # frontend can render the phase-1-in-flight loading UI. This is a tiny
        # additional commit+publish per non-skipped job; it's the load-bearing
        # signal that disambiguates "phase 1 in flight" from "skip_enrichment=true".
        pre_mark_committed = False
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
            )
            try:
                result = await db.execute(
                    select(JobPosting).where(JobPosting.id == UUID(job_posting_id))
                )
                job = result.scalar_one_or_none()
                if (
                    job is not None
                    and job.status == "signals_extracting"
                    and job.enrichment_status != "completed"
                ):
                    # Idempotent: only mark if we haven't already enriched on a
                    # previous attempt (otherwise we'd undo the 'completed' state).
                    job.enrichment_status = "streaming"
                    await db.commit()
                    pre_mark_committed = True
            except Exception as exc:
                # Pre-mark is best-effort; if it fails, the actual phase 1 commit
                # below will still publish the eventual completed/failed state.
                await db.rollback()
                logger.warning(
                    "actors.extract_and_enhance_jd.pre_mark_failed",
                    job_posting_id=job_posting_id, error=str(exc),
                )

        if pre_mark_committed:
            await _publish_status(job_posting_id, tenant_id, correlation_id)

        # ---- Phase 1 LLM call ----
        phase_1_committed = False
        async with get_bypass_session() as db:
            await db.execute(
                text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
            )
            try:
                await _run_enrichment(
                    db, job_posting_id=job_posting_id,
                    tenant_id=tenant_id, correlation_id=correlation_id,
                    retries_so_far=retries_so_far,
                )
                await db.commit()
                phase_1_committed = True
            except Exception as exc:
                if retries_so_far >= 2:
                    await db.commit()
                    phase_1_committed = True
                else:
                    await db.rollback()
                _exc_to_reraise = exc

        if phase_1_committed:
            await _publish_status(job_posting_id, tenant_id, correlation_id)

        if _exc_to_reraise is not None:
            raise _exc_to_reraise

    # ---- Phase 2: Signal extraction (always) ----
    phase_2_committed = False
    async with get_bypass_session() as db:
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )
        try:
            await _run_signal_extraction(
                db, job_posting_id=job_posting_id,
                tenant_id=tenant_id, correlation_id=correlation_id,
                retries_so_far=retries_so_far,
            )
            await db.commit()
            phase_2_committed = True
        except Exception as exc:
            if retries_so_far >= 2:
                await db.commit()
                phase_2_committed = True
            else:
                await db.rollback()
            _exc_to_reraise = exc

    if phase_2_committed:
        await _publish_status(job_posting_id, tenant_id, correlation_id)

    if _exc_to_reraise is not None:
        raise _exc_to_reraise


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


async def _run_reenrichment(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Core re-enrichment logic — unit-testable without Dramatiq.

    The OpenAI call is wrapped in an explicit
    ``with _tracer.start_as_current_span("openai.chat.completions.create")``
    block. set_llm_span_attributes() inside that block adds prompt
    metadata; exceptions tag the span with StatusCode.ERROR before the
    re-raise."""
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

    client = get_openai_client()
    prompt = prompt_loader.get("jd_reenrichment")
    user_message = _build_reenrich_user_message(job, profile, snapshot)

    log.info(
        "jd.llm_call.start",
        call_type="reenrichment",
        model=ai_config.reenrichment_model,
        reasoning_effort=ai_config.reenrichment_effort,
        system_prompt_chars=len(prompt),
        user_message_chars=len(user_message),
    )
    call_started_at = time.monotonic()
    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="jd_reenrichment",
            prompt_version="v1",
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            job_posting_id=job_posting_id,
            model=ai_config.reenrichment_model,
            reasoning_effort=ai_config.reenrichment_effort,
            retries_so_far=retries_so_far,
        )
        try:
            reenriched: ReEnrichmentOutput = await client.chat.completions.create(
                model=ai_config.reenrichment_model,
                reasoning_effort=ai_config.reenrichment_effort,
                response_model=ReEnrichmentOutput,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_message},
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
            # Tag the active span with error status so failed LLM calls
            # render as errors in OTel backends (the auto-instrumentor
            # we replaced did this automatically).
            _span = trace.get_current_span()
            _span.record_exception(exc)
            _span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            duration_sec = time.monotonic() - call_started_at
            is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
            log.error(
                "jd.llm_call.failed",
                call_type="reenrichment",
                duration_sec=round(duration_sec, 2),
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                permanent=is_permanent,
                retries_so_far=retries_so_far,
                exc_info=exc,
            )

            if is_permanent or retries_so_far >= 1:
                job.enrichment_status = "failed"
                job.enrichment_error = sanitize_error_for_user(exc)
                if is_permanent:
                    return
            raise  # Transient error — Dramatiq retries with backoff

    # Success path
    duration_sec = time.monotonic() - call_started_at
    log.info(
        "jd.llm_call.complete",
        call_type="reenrichment",
        duration_sec=round(duration_sec, 2),
        enriched_jd_chars=len(reenriched.enriched_jd),
    )
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

    # Same deferred-reraise pattern as extract_and_enhance_jd: capture the
    # exception so the post-commit publish runs before Dramatiq retries.
    _committed = False
    _exc_to_reraise: BaseException | None = None

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
            _committed = True
        except Exception as exc:
            if retries_so_far >= 1:
                await db.commit()
                _committed = True
            else:
                await db.rollback()
            _exc_to_reraise = exc

    # Post-commit publish — identical pattern to extract_and_enhance_jd.
    if _committed:
        try:
            async with get_bypass_session() as pub_db:
                safe_tenant_id = str(UUID(tenant_id))
                await pub_db.execute(
                    text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
                )
                status_event = await get_job_status(pub_db, UUID(job_posting_id))
        except Exception as exc:
            logger.warning(
                "actors.reenrich_jd.publish_read_failed",
                job_posting_id=job_posting_id,
                error=str(exc),
            )
            status_event = None

        if status_event is not None:
            await pubsub.publish(
                pubsub.job_channel(job_posting_id),
                pubsub.Events.JD_STATUS_CHANGED,
                status_event.model_dump(mode="json"),
                correlation_id=correlation_id,
            )

    if _exc_to_reraise is not None:
        raise _exc_to_reraise
