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
import orjson
import structlog
from sqlalchemy import select, update
from sqlalchemy.sql import text

from app import pubsub
from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.ai.tracing import set_llm_span_attributes
from app.database import get_bypass_session
from app.modules.jd import JobPosting, JobPostingSignalSnapshot
from app.modules.org_units import (
    OrganizationalUnit,
    find_company_profile_in_ancestry,
)
from app.modules.pipelines import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.audit import log_event
from app.modules.question_bank.refine import extract_bank_keyterms
from app.modules.question_bank.schemas import (
    SingleQuestionOutput,
    StageQuestionBankOutput,
)
from app.modules.question_bank.errors import BudgetExceededError
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
from app.modules.question_bank.context import (
    QuestionContext,
    build_question_context,
    _load_pipeline_stages,
    _load_prior_stage_questions,
)
from app.modules.question_bank.state_machine import auto_revert_on_edit

logger = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------

# Optional-question buffer added on top of the stage's session duration.
# `mandatory_total ≤ duration_minutes` (HARD CAP).
# `mandatory_total + optional_total ≤ duration_minutes + OPTIONAL_BUDGET_MARGIN_MIN`
# (HARD CAP — gives the screening AI 1–2 fallback probes if the candidate
# moves through mandatory faster than estimated).
OPTIONAL_BUDGET_MARGIN_MIN = 5

# How many additional LLM calls we make if the first one violates the
# budget contract. The first call is attempt 1; with MAX_BUDGET_RETRIES=1
# we make at most 2 LLM calls per generation. Each retry adds the
# previous output + a corrective user message to the conversation so the
# LLM has the violation in its context. After this many retries with
# violations, the bank transitions to 'failed' and the recruiter retries
# (e.g. by extending the stage duration if the configuration is
# structurally infeasible).
MAX_BUDGET_RETRIES = 1

# Behavioral-call mandatory budget cap (minutes). The behavioral phase
# verifies knockout-signal claims; it should not eat technical scenario
# time. Constant for v1; could become per-stage configurable later.
BEHAVIORAL_BUDGET_MIN = 3

# Stage types that support AI question-bank generation. The values are
# the technical_depth prompt names (existing behavior). Keys are kept as
# bare stage_type strings so existing filter code (`s.stage_type in
# STAGE_TYPE_TO_PROMPT`) continues to work without changes.
#
# `human_interview` and `take_home` are intentionally excluded — those
# stage types remain valid in the pipeline schema (and in candidate
# routing / participant assignment), but their questions are authored
# manually by the recruiter, not generated. The corresponding prompt
# files in `prompts/v1/` are retained for future re-enablement but
# have no callers today. Endpoints that try to generate for an
# excluded stage type return HTTP 400 with a clear message; the
# `list_banks` endpoint hides the bank-generation surface entirely
# for those stages (see router.py).
STAGE_TYPE_TO_PROMPT = {
    "phone_screen":    "question_bank_phone_screen",
    "ai_screening":    "question_bank_ai_screening",
}

# Per-stage-type behavioral_star prompt names. Stages NOT in this map
# generate technical_depth only — the behavioral call is skipped for
# those stage types (today: phone_screen has no behavioral prompt yet).
# See docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md.
STAGE_TYPE_TO_BEHAVIORAL_PROMPT = {
    "ai_screening":    "question_bank_ai_screening_behavioral",
}

# Backward-compatible aliases so existing tests (which import these names
# directly from actors) continue to work without modification.
_load_pipeline_context = _load_pipeline_stages
_load_prior_stages_questions = _load_prior_stage_questions


def _filter_behavioral_eligible(signals: list[dict]) -> list[dict]:
    """Return knockout signals voice-verifiable in the behavioral phase.

    The behavioral phase verifies claims (years, platforms, employer scope)
    via open-ended questions. We restrict to `experience` and `behavioral`
    signal types — credentials (degrees, certs) are pre-filtered by ATS,
    and competency signals are depth-flavored (technical phase territory).

    See docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §1.
    """
    return [
        s for s in signals
        if s.get("knockout") is True
        and s.get("type") in ("experience", "behavioral")
    ]


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
        for key in ("about", "industry", "hiring_bar"):
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

    # Pre-computed budget block. The LLM does NOT do budget arithmetic —
    # the server enforces both caps in `validate_llm_output_against_snapshot`
    # (a violation triggers an instructor retry with the validation error
    # in the LLM's context). Eligibility-after-include_types is computed
    # here so the LLM doesn't have to filter the snapshot itself.
    include_types = stage.signal_filter.get("include_types", [])
    eligible_signals = [
        s for s in snapshot.signals if s.get("type") in include_types
    ]
    eligible_knockouts = [s for s in eligible_signals if s.get("knockout", False)]
    eligible_w3 = [
        s for s in eligible_signals
        if int(s.get("weight", 1)) == 3 and not s.get("knockout", False)
    ]
    eligible_w2 = [s for s in eligible_signals if int(s.get("weight", 1)) == 2]
    eligible_w1 = [s for s in eligible_signals if int(s.get("weight", 1)) == 1]

    parts.append("\n# BUDGET FOR THIS STAGE (HARD CAPS — server-enforced)\n")
    parts.append(
        f"Stage duration: {stage.duration_minutes} min\n"
        f"Mandatory budget cap: {stage.duration_minutes} min "
        f"(sum of estimated_minutes across is_mandatory=true questions)\n"
        f"Total budget cap: {stage.duration_minutes + OPTIONAL_BUDGET_MARGIN_MIN} min "
        f"(sum across ALL questions, mandatory + optional combined)\n"
        f"Optional buffer: {OPTIONAL_BUDGET_MARGIN_MIN} min "
        f"(reserved for the screening AI's runtime fallback probes)\n"
        f"\n"
        f"Eligible signals (after include_types filter):\n"
        f"  - knockouts: {len(eligible_knockouts)} "
        f"(each gets ONE mandatory question)\n"
        f"  - weight=3 non-knockout: {len(eligible_w3)} "
        f"(mandatory only if mandatory budget allows; otherwise optional)\n"
        f"  - weight=2: {len(eligible_w2)} (optional depth probes)\n"
        f"  - weight=1: {len(eligible_w1)} "
        f"(skip unless every higher-weight signal is covered AND buffer remains)\n"
        f"\n"
        f"Optimize for SIGNAL DENSITY, not question count. Under-using budget "
        f"by 1–2 minutes is acceptable; padding shallow questions is rejected.\n"
    )

    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Core generation function (shared by the stage and pipeline actors)
# ---------------------------------------------------------------------------

async def _generate_questions_for_kind(
    db,
    *,
    bank: StageQuestionBank,
    stage: JobPipelineStage,
    instance: JobPipelineInstance,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    kind: str,                                   # "behavioral_star" | "technical_depth"
    eligible_signals: list[dict],
    budget_minutes: int,
    prompt_name: str,
) -> list:
    """Run ONE LLM call for ONE question_kind, with retry-on-budget-violation.

    Returns the validated list of GeneratedQuestion objects from the LLM
    output. Caller is responsible for concatenating and writing.

    Skips mandatory auto-correction (caller runs it once on the combined
    list of all kinds via `_apply_mandatory_correction_in_position_order`).
    """
    ctx = await build_question_context(db, job=job, instance=instance, stage=stage)
    system_prompt = prompt_loader.load_pair("question_bank_common", prompt_name)

    # Project the eligible_signals into the user message format used by
    # _build_user_message. The existing builder takes the FULL snapshot,
    # but for per-kind calls we want the LLM to see only the eligible set
    # for THIS kind. Simplest approach: temporarily swap snapshot.signals,
    # call the builder, restore.
    original_signals = snapshot.signals
    snapshot.signals = eligible_signals
    try:
        user_message = _build_user_message(
            job=job,
            snapshot=snapshot,
            company_profile=ctx.company_profile,
            stage=stage,
            pipeline_stages=ctx.pipeline_stages,
            prior_stages_questions=ctx.prior_stages_questions,
        )
    finally:
        snapshot.signals = original_signals

    client = get_openai_client()
    allowed_types = stage.signal_filter.get("include_types", [])

    base_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    feedback_history: list[dict] = []
    validated: list = []

    for attempt in range(MAX_BUDGET_RETRIES + 1):
        messages = list(base_messages) + feedback_history

        logger.info(
            "question_bank.llm_call.start",
            bank_id=str(bank.id),
            stage_id=str(stage.id),
            stage_type=stage.stage_type,
            kind=kind,
            model=ai_config.question_bank_model,
            reasoning_effort=ai_config.question_bank_effort,
            system_prompt_chars=len(system_prompt),
            user_message_chars=len(user_message),
            attempt=attempt + 1,
            feedback_messages=len(feedback_history),
            budget_minutes=budget_minutes,
        )
        call_started_at = time.monotonic()
        with _tracer.start_as_current_span("openai.chat.completions.create"):
            set_llm_span_attributes(
                prompt_name=prompt_name,
                prompt_version=bank.prompt_version,
                tenant_id=str(bank.tenant_id),
                bank_id=str(bank.id),
                stage_id=str(stage.id),
                stage_type=stage.stage_type,
                job_posting_id=str(job.id),
                model=ai_config.question_bank_model,
                reasoning_effort=ai_config.question_bank_effort,
                budget_attempt=attempt + 1,
                question_kind=kind,
            )
            try:
                result: StageQuestionBankOutput = await client.chat.completions.create(
                    model=ai_config.question_bank_model,
                    reasoning_effort=ai_config.question_bank_effort,
                    response_model=StageQuestionBankOutput,
                    messages=messages,
                    max_retries=1,
                    metadata={
                        "bank_id": str(bank.id),
                        "stage_id": str(stage.id),
                        "stage_type": stage.stage_type,
                        "tenant_id": str(bank.tenant_id),
                        "job_posting_id": str(job.id),
                        "prompt_version": bank.prompt_version,
                        "budget_attempt": str(attempt + 1),
                        "question_kind": kind,
                    },
                )
            except Exception as llm_exc:
                _span = trace.get_current_span()
                _span.record_exception(llm_exc)
                _span.set_status(Status(StatusCode.ERROR, type(llm_exc).__name__))
                duration_sec = time.monotonic() - call_started_at
                logger.error(
                    "question_bank.llm_call.failed",
                    bank_id=str(bank.id),
                    stage_id=str(stage.id),
                    stage_type=stage.stage_type,
                    kind=kind,
                    duration_sec=round(duration_sec, 2),
                    error_type=type(llm_exc).__name__,
                    error_message=str(llm_exc)[:500],
                    attempt=attempt + 1,
                    exc_info=True,
                )
                raise

        duration_sec = time.monotonic() - call_started_at
        logger.info(
            "question_bank.llm_call.complete",
            bank_id=str(bank.id),
            stage_id=str(stage.id),
            kind=kind,
            duration_sec=round(duration_sec, 2),
            question_count=len(result.questions),
            attempt=attempt + 1,
        )

        try:
            validated = await validate_llm_output_against_snapshot(
                db,
                snapshot=snapshot,                          # full snapshot for signal validation
                allowed_types=allowed_types,
                questions=result.questions,
                stage=stage,
                optional_budget_margin_min=OPTIONAL_BUDGET_MARGIN_MIN,
                apply_mandatory_correction=False,            # post-merge pass handles this
                budget_minutes_override=budget_minutes,      # per-kind budget cap
            )
            break
        except BudgetExceededError as budget_exc:
            if attempt >= MAX_BUDGET_RETRIES:
                logger.warning(
                    "question_bank.budget_violation_unrecovered",
                    bank_id=str(bank.id),
                    kind=kind,
                    observed_minutes=budget_exc.observed_minutes,
                    cap_minutes=budget_exc.cap_minutes,
                    attempts=attempt + 1,
                )
                raise
            logger.warning(
                "question_bank.budget_violation_retry",
                bank_id=str(bank.id),
                kind=kind,
                observed_minutes=budget_exc.observed_minutes,
                cap_minutes=budget_exc.cap_minutes,
                attempt=attempt + 1,
            )
            feedback_history.append({
                "role": "assistant",
                "content": orjson.dumps(result.model_dump()).decode("utf-8"),
            })
            feedback_history.append({
                "role": "user",
                "content": (
                    f"Your previous output violated the {kind} budget contract. "
                    f"{budget_exc} Regenerate with the cap respected. "
                    f"Do NOT pad shallow questions."
                ),
            })

    return validated


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

    Two-call architecture (2026-05-19): generates behavioral_star questions
    first (knockout-only, capped at BEHAVIORAL_BUDGET_MIN), then
    technical_depth questions with the remaining stage budget. Concatenates,
    runs the post-merge mandatory auto-correction, and writes the combined
    list. Behavioral is skipped when no eligible signals exist or the
    stage type has no behavioral prompt mapping — in that case the bank
    runs as a single technical_depth call (preserving legacy behavior for
    phone_screen stages).

    Tracing:
      Each per-kind LLM call is wrapped in an explicit
      ``with _tracer.start_as_current_span("openai.chat.completions.create")``
      block via `_generate_questions_for_kind`. The span attributes include
      bank_id, stage_id, tenant_id, model/effort, prompt name+version,
      budget_attempt, and question_kind so spans are searchable per-call
      in any OTel-compatible observability backend.
    """
    try:
        eligible_behavioral_signals = _filter_behavioral_eligible(snapshot.signals)
        behavioral_prompt = STAGE_TYPE_TO_BEHAVIORAL_PROMPT.get(stage.stage_type)
        technical_prompt = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
        if technical_prompt is None:
            raise RuntimeError(
                f"No technical prompt mapped for stage_type={stage.stage_type}"
            )

        # ---- Behavioral call (skipped when no eligible signals or no prompt) ----
        behavioral_questions: list = []
        behavioral_status: str
        if not eligible_behavioral_signals or behavioral_prompt is None:
            behavioral_status = "skipped_no_eligible_signals"
            logger.info(
                "question_bank.behavioral_skipped",
                bank_id=str(bank.id),
                stage_type=stage.stage_type,
                reason=(
                    "no_eligible_signals"
                    if not eligible_behavioral_signals
                    else "no_behavioral_prompt_for_stage_type"
                ),
            )
        else:
            try:
                behavioral_questions = await _generate_questions_for_kind(
                    db,
                    bank=bank,
                    stage=stage,
                    instance=instance,
                    job=job,
                    snapshot=snapshot,
                    kind="behavioral_star",
                    eligible_signals=eligible_behavioral_signals,
                    budget_minutes=BEHAVIORAL_BUDGET_MIN,
                    prompt_name=behavioral_prompt,
                )
                behavioral_status = "reviewing"
            except Exception as bh_exc:
                logger.error(
                    "question_bank.behavioral_call_failed",
                    bank_id=str(bank.id),
                    error=str(bh_exc)[:500],
                    exc_info=True,
                )
                behavioral_status = "failed"
                behavioral_questions = []

        behavioral_total = sum(
            float(q.estimated_minutes) for q in behavioral_questions
        )

        # ---- Technical call (always runs; budget shrunk by behavioral total) ----
        technical_mandatory_cap = max(
            1, int(stage.duration_minutes - behavioral_total)
        )
        technical_exception: Exception | None = None
        try:
            technical_questions = await _generate_questions_for_kind(
                db,
                bank=bank,
                stage=stage,
                instance=instance,
                job=job,
                snapshot=snapshot,
                kind="technical_depth",
                eligible_signals=snapshot.signals,            # full set
                budget_minutes=technical_mandatory_cap,
                prompt_name=technical_prompt,
            )
            technical_status = "reviewing"
        except Exception as tc_exc:
            logger.error(
                "question_bank.technical_call_failed",
                bank_id=str(bank.id),
                error=str(tc_exc)[:500],
                exc_info=True,
            )
            technical_status = "failed"
            technical_questions = []
            technical_exception = tc_exc

        # ---- Persist per-kind status ----
        bank.generation_status_by_kind = {
            "behavioral_star": behavioral_status,
            "technical_depth": technical_status,
        }

        # ---- Fail-out when technical did not produce ----
        # Re-raise the original exception (preserves the existing contract:
        # callers — `_run_stage_generation`, `_run_one_pipeline_stage_in_session`
        # — catch the exception type, and tests assert specific exception
        # types like SignalValueNotInSnapshotError / BudgetExceededError
        # propagate out).
        if technical_status == "failed":
            assert technical_exception is not None
            raise technical_exception

        # ---- Concatenate + position + post-merge mandatory correction ----
        validated: list = []
        for i, q in enumerate(behavioral_questions):
            q.position = i
            validated.append(q)
        for j, q in enumerate(
            technical_questions, start=len(behavioral_questions),
        ):
            q.position = j
            validated.append(q)

        from app.modules.question_bank.service import (
            _apply_mandatory_correction_in_position_order,
        )
        knockout_values = {
            s["value"] for s in snapshot.signals if s.get("knockout", False)
        }
        _apply_mandatory_correction_in_position_order(
            questions=validated, knockout_values=knockout_values,
        )

        # ---- Write to DB + stamp generation-time metadata ----
        await write_generated_questions(
            db, bank=bank, questions=validated, source="ai_generated",
        )
        bank.pipeline_version_at_generation = instance.pipeline_version
        bank.stage_config_snapshot = {
            "signal_filter": stage.signal_filter,
            "difficulty": stage.difficulty,
        }
        bank.is_stale = False

        # Final step: extract STT keyterms for Deepgram nova-3 prompting
        # (Phase 3D.deepgram-keyterm, 2026-05-19). Runs ONCE per bank
        # generation; result cached in stage_question_banks.extracted_keyterms.
        # Failures here are NOT fatal — log and continue. The engine falls
        # back to candidate-name-only STT boosting if the column stays NULL.
        try:
            company_profile = (
                await find_company_profile_in_ancestry(db, job.org_unit_id)
                if job.org_unit_id is not None
                else None
            ) or {}
            org_unit_name = ""
            if job.org_unit_id is not None:
                org_unit_row = (
                    await db.execute(
                        select(OrganizationalUnit).where(
                            OrganizationalUnit.id == job.org_unit_id
                        )
                    )
                ).scalar_one_or_none()
                if org_unit_row is not None:
                    org_unit_name = org_unit_row.name or ""
            keyterm_output = await extract_bank_keyterms(
                job_title=job.title,
                hiring_company_name=org_unit_name,
                industry=company_profile.get("industry", "") or "",
                company_about=company_profile.get("about", "") or "",
                hiring_bar=company_profile.get("hiring_bar", "") or "",
                role_summary=snapshot.role_summary or "",
                signals=[s["value"] for s in snapshot.signals],
                questions=[{"text": q.text} for q in validated],
                bank_id=str(bank.id),
                tenant_id=str(bank.tenant_id),
            )
            await db.execute(
                update(StageQuestionBank)
                .where(StageQuestionBank.id == bank.id)
                .values(extracted_keyterms=keyterm_output.keyterms),
            )
            logger.info(
                "question_bank.keyterm_extraction.complete",
                bank_id=str(bank.id),
                count=len(keyterm_output.keyterms),
            )
        except Exception:
            logger.exception(
                "question_bank.keyterm_extraction.failed",
                bank_id=str(bank.id),
            )
            # Do not re-raise — keyterm extraction is best-effort.

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

async def _run_stage_generation(
    db,
    *,
    bank_id: UUID,
    tenant_id: UUID,
    started_by: UUID,
) -> tuple[UUID, UUID, str] | None:
    """Body of the single-stage actor — separated so tests can pass a session.

    Caller manages the transaction: this helper flushes/transitions and lets
    the caller commit. Returns ``(job_id, stage_id, new_status)`` on a write
    that should be committed and published, or ``None`` if the bank could
    not be found.

    Raises on unexpected mid-flight failures (so the caller can rollback +
    Dramatiq retries) — but if `_generate_one_bank` runs to its except
    branch and transitions the bank to 'failed', this returns
    ``(job_id, stage_id, 'failed')`` so the caller commits the failed
    status and does not re-raise. That preserves the existing actor
    contract (the bank is left in a terminal state visible to the
    frontend even on permanent error).
    """
    bank = (
        await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
        )
    ).scalar_one_or_none()
    if bank is None:
        logger.error("question_bank.bank_missing", bank_id=str(bank_id))
        return None

    stage = (
        await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
        )
    ).scalar_one()
    instance = (
        await db.execute(
            select(JobPipelineInstance).where(
                JobPipelineInstance.id == stage.instance_id
            )
        )
    ).scalar_one()
    job = (
        await db.execute(
            select(JobPosting).where(JobPosting.id == bank.job_posting_id)
        )
    ).scalar_one()
    snapshot = (
        await db.execute(
            select(JobPostingSignalSnapshot).where(
                JobPostingSignalSnapshot.id == bank.signal_snapshot_id
            )
        )
    ).scalar_one()

    job_id = job.id
    stage_id = stage.id

    try:
        await _generate_one_bank(
            db,
            bank=bank,
            stage=stage,
            instance=instance,
            job=job,
            snapshot=snapshot,
            started_by=started_by,
        )
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=started_by,
            actor_email=None,
            action="question_bank.bank_generated",
            resource="stage_question_bank",
            resource_id=bank.id,
        )
        return (job_id, stage_id, "reviewing")
    except Exception:
        if bank.status == "failed":
            # _generate_one_bank already transitioned to 'failed' inside its
            # own except branch — return the failed result so the caller
            # commits the terminal state and publishes the status change.
            return (job_id, stage_id, "failed")
        # Unknown state — let the caller rollback + retry.
        raise


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
    correlation_id: str = "",
) -> None:
    """Generate questions for ONE stage's bank. Retries on transient failures.

    Before the first call, the router must have:
    - Ensured the bank exists
    - Set bank.status = 'generating'
    - Committed so the actor sees the updated state

    Publishes ``BANK_STATUS_CHANGED`` post-commit (success and failure
    paths). The transition to 'failed' is reachable on permanent errors
    inside `_generate_one_bank`; transient errors that don't transition
    the bank trigger a rollback + Dramatiq retry without publishing.
    """
    bank_uuid = UUID(bank_id)
    tenant_uuid = UUID(tenant_id)
    started_by_uuid = UUID(started_by)
    effective_corr = correlation_id or f"actor-stage-{bank_id}"

    publish_args: tuple[UUID, UUID, str] | None = None
    async with get_bypass_session() as db:
        safe_tenant_id = str(tenant_uuid)
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        try:
            result = await _run_stage_generation(
                db,
                bank_id=bank_uuid,
                tenant_id=tenant_uuid,
                started_by=started_by_uuid,
            )
            if result is None:
                # Bank vanished — nothing to commit, nothing to publish.
                return
            publish_args = result
            await db.commit()
        except Exception:
            # _run_stage_generation only re-raises if the bank is NOT in a
            # terminal state, so partial writes here would corrupt the
            # bank. Roll back and let Dramatiq retry.
            logger.warning(
                "question_bank.stage_actor_rollback",
                bank_id=bank_id,
                reason="exception_outside_failed_transition",
            )
            await db.rollback()
            raise

    # Post-commit fast-path event. Outside the session context — the data
    # is durable and any Redis outage here is logged but does not fail
    # the task (publish() is fire-and-forget).
    if publish_args is not None:
        job_id_pub, stage_id_pub, new_status_pub = publish_args
        await pubsub.publish(
            pubsub.job_channel(job_id_pub),
            pubsub.Events.BANK_STATUS_CHANGED,
            {
                "job_id": str(job_id_pub),
                "bank_id": bank_id,
                "stage_id": str(stage_id_pub),
                "new_status": new_status_pub,
                "source": "actor",
            },
            correlation_id=effective_corr,
        )


# ---------------------------------------------------------------------------
# Actor: full pipeline (sequential — required for anti-lie coherence)
# ---------------------------------------------------------------------------

async def _run_one_pipeline_stage_in_session(
    *,
    stage_id: UUID,
    job_id: UUID,
    instance_id: UUID,
    started_by: UUID,
    tenant_id: str,
) -> tuple[UUID, str] | None:
    """Run one pipeline stage in its own session/commit cycle.

    Each stage is an independent transaction so a mid-pipeline crash leaves
    earlier stages durably persisted. Returns ``(bank_id, new_status)`` —
    where ``new_status`` is one of ``'reviewing'`` (success), ``'failed'``
    (LLM/validation error caught inside ``_generate_one_bank``), or
    ``'skipped'`` (the bank was already in a non-startable state — typically
    another worker is mid-flight on the same stage). Returns ``None`` if
    the structural lookups (stage / instance / job) failed: those are
    treated as silent skips because the orchestrator already verified the
    structure exists when it built the stage list.

    Never raises — pipeline-level orchestration depends on each stage
    completing or being skipped, not on exceptions propagating.
    """
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))

        stage = (
            await db.execute(
                select(JobPipelineStage).where(JobPipelineStage.id == stage_id)
            )
        ).scalar_one_or_none()
        instance = (
            await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.id == instance_id
                )
            )
        ).scalar_one_or_none()
        job = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == job_id)
            )
        ).scalar_one_or_none()
        if stage is None or instance is None or job is None:
            logger.warning(
                "question_bank.pipeline_stage_structure_missing",
                stage_id=str(stage_id),
                instance_id=str(instance_id),
                job_id=str(job_id),
            )
            return None

        bank = await ensure_bank_exists(db, stage=stage, job=job)
        bank_id = bank.id

        if bank.status == "generating":
            # Already pre-marked (by the endpoint, or by a re-run). Proceed.
            pass
        else:
            try:
                transition_to_generating(bank)
            except Exception as exc:
                # Most commonly BankAlreadyGeneratingError from a concurrent
                # worker. Skip without writing anything; the with-block will
                # commit the (no-op) transaction.
                logger.warning(
                    "question_bank.skip_busy_stage",
                    stage_id=str(stage_id),
                    reason=str(exc),
                )
                return (bank_id, "skipped")

        snapshot = (
            await db.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == bank.signal_snapshot_id
                )
            )
        ).scalar_one()

        try:
            await _generate_one_bank(
                db,
                bank=bank,
                stage=stage,
                instance=instance,
                job=job,
                snapshot=snapshot,
                started_by=started_by,
            )
            new_status = "reviewing"
        except Exception as exc:
            # _generate_one_bank already transitioned the bank to 'failed'
            # before re-raising — caller-bug guard in state_machine.py
            # ensures this. Swallow the exception so the pipeline continues.
            logger.error(
                "question_bank.pipeline_stage_failed",
                stage_id=str(stage_id),
                error=str(exc),
            )
            new_status = "failed"

        # session.begin().__aexit__ commits on with-block exit. The
        # 'reviewing' or 'failed' transition is durable from this point.

    return (bank_id, new_status)


async def _run_pipeline_generation(
    *,
    instance_id: str,
    tenant_id: str,
    started_by: str,
    correlation_id: str,
) -> None:
    """Body of generate_question_bank_pipeline — separated for testability.

    Two-phase flow:
      1. Read the pipeline structure in a single read-only session, capture
         the eligible stage UUIDs, then close the session.
      2. For each eligible stage, run `_run_one_pipeline_stage_in_session`
         which gives the stage its own transaction. After each stage's
         commit, publish ``BANK_STATUS_CHANGED`` so the SSE fast path
         delivers the transition with sub-100ms latency.
      3. Write the final audit log entry and publish
         ``PIPELINE_GENERATION_COMPLETE``.

    Per-stage commit semantics: a mid-pipeline crash leaves prior stages
    durably persisted. The user can retry just the failed stages via the
    single-stage endpoint without losing earlier work.
    """
    safe_tenant_id = str(UUID(tenant_id))
    instance_uuid = UUID(instance_id)
    started_by_uuid = UUID(started_by)

    # Phase 1: load pipeline structure ────────────────────────────────────
    async with get_bypass_session() as db:
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )

        instance = (
            await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.id == instance_uuid
                )
            )
        ).scalar_one_or_none()
        if instance is None:
            logger.error(
                "question_bank.instance_missing", instance_id=instance_id
            )
            return

        job = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == instance.job_posting_id)
            )
        ).scalar_one()
        all_stages = list(
            (
                await db.execute(
                    select(JobPipelineStage)
                    .where(JobPipelineStage.instance_id == instance.id)
                    .order_by(JobPipelineStage.position)
                )
            )
            .scalars()
            .all()
        )
        # Filter to question-bank-eligible stages only. Intake / debrief have
        # no signal_filter, no duration, and produce no questions — including
        # them in the loop crashes build_question_context and leaves orphan
        # failed banks in the DB.
        eligible_stage_ids = [
            s.id for s in all_stages if s.stage_type in STAGE_TYPE_TO_PROMPT
        ]
        job_id = job.id
        instance_uuid_captured = instance.id

    # Phase 2: per-stage generation ───────────────────────────────────────
    succeeded = 0
    failed = 0
    for stage_id in eligible_stage_ids:
        result = await _run_one_pipeline_stage_in_session(
            stage_id=stage_id,
            job_id=job_id,
            instance_id=instance_uuid_captured,
            started_by=started_by_uuid,
            tenant_id=safe_tenant_id,
        )
        if result is None:
            # Structure missing for this stage — already logged inside.
            continue
        bank_id, new_status = result
        if new_status == "reviewing":
            succeeded += 1
        elif new_status == "failed":
            failed += 1
        # 'skipped' counts toward neither — the bank was busy elsewhere.

        if new_status in ("reviewing", "failed"):
            await pubsub.publish(
                pubsub.job_channel(job_id),
                pubsub.Events.BANK_STATUS_CHANGED,
                {
                    "job_id": str(job_id),
                    "bank_id": str(bank_id),
                    "stage_id": str(stage_id),
                    "new_status": new_status,
                    "source": "actor",
                },
                correlation_id=correlation_id,
            )

    # Phase 3: pipeline-level audit log + completion event ────────────────
    async with get_bypass_session() as db:
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )
        await log_event(
            db,
            tenant_id=UUID(tenant_id),
            actor_id=started_by_uuid,
            actor_email=None,
            action="question_bank.pipeline_generation_complete",
            resource="job_pipeline_instance",
            resource_id=instance_uuid_captured,
            payload={
                "succeeded": succeeded,
                "failed": failed,
                "total": len(eligible_stage_ids),
            },
        )

    await pubsub.publish(
        pubsub.job_channel(job_id),
        pubsub.Events.PIPELINE_GENERATION_COMPLETE,
        {
            "job_id": str(job_id),
            "instance_id": instance_id,
            "succeeded": succeeded,
            "failed": failed,
            "total": len(eligible_stage_ids),
            "source": "actor",
        },
        correlation_id=correlation_id,
    )


@dramatiq.actor(
    max_retries=0,
    time_limit=1_800_000,  # 30 minutes
    queue_name="question_bank_generation",
)
async def generate_question_bank_pipeline(
    instance_id: str,
    tenant_id: str,
    started_by: str,
    correlation_id: str = "",
) -> None:
    """Generate banks for ALL stages in a pipeline, sequentially.

    Sequential is REQUIRED — stage N needs to see stages 1..N-1's questions.
    On mid-pipeline failure: marks that stage failed, CONTINUES to next stage.
    User retries failed stages individually via the single-stage endpoint.

    Each stage commits in its own transaction (`_run_one_pipeline_stage_in_session`)
    so the worker process can crash mid-pipeline without rolling back earlier
    stages. Pub/sub ``BANK_STATUS_CHANGED`` is published after each per-stage
    commit; ``PIPELINE_GENERATION_COMPLETE`` fires at the end. The SSE
    backstop poll is a correctness backstop only — under normal operation
    the frontend gets sub-100ms updates via these publishes.
    """
    effective_corr = correlation_id or f"actor-pipeline-{instance_id}"
    await _run_pipeline_generation(
        instance_id=instance_id,
        tenant_id=tenant_id,
        started_by=started_by,
        correlation_id=effective_corr,
    )


# ---------------------------------------------------------------------------
# Actor: single question regeneration
# ---------------------------------------------------------------------------

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

    Separated from the Dramatiq actor so the LLM call is the only thing
    inside the auto-instrumented OTel span (not the actor's session
    bootstrap). Matches the jd/actors.py inner-coroutine pattern (e.g.,
    `_run_reenrichment`).
    """
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
    with _tracer.start_as_current_span("openai.chat.completions.create"):
        set_llm_span_attributes(
            prompt_name="question_bank_regenerate_one",
            prompt_version=bank.prompt_version,
            tenant_id=str(bank.tenant_id),
            bank_id=str(bank.id),
            stage_id=str(stage.id),
            stage_type=stage.stage_type,
            job_posting_id=str(job.id),
            question_id=str(question.id),
            model=ai_config.question_bank_model,
            reasoning_effort=ai_config.question_bank_effort,
        )
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
            # Tag the active span with error status so failed LLM calls
            # render as errors in OTel backends (the auto-instrumentor
            # we replaced did this automatically).
            _span = trace.get_current_span()
            _span.record_exception(llm_exc)
            _span.set_status(Status(StatusCode.ERROR, type(llm_exc).__name__))
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
    correlation_id: str = "",
) -> None:
    """Regenerate a single question slot, preserving its UUID.

    Uses the regenerate-one prompt which takes other questions in the bank
    as 'do not duplicate' context. Delegates the LLM + DB write path to
    `_regenerate_one_question` so the OTel span wraps just that portion
    (matching jd/actors.py).

    Publishes bank.question_updated post-commit. Actors don't have FastAPI
    BackgroundTasks so publish is called inline after the session commits.
    publish() is best-effort and never raises.
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

        # Capture IDs needed for the publish BEFORE the session commits.
        _job_id = job.id
        _bank_id = bank.id
        _stage_id = stage.id

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
        # session.begin() has exited (commit issued) — publish post-commit.
        # publish() is fire-and-forget and never raises; a Redis outage here
        # does not fail the regeneration.
        await pubsub.publish(
            pubsub.job_channel(_job_id),
            pubsub.Events.BANK_QUESTION_UPDATED,
            {
                "job_id": str(_job_id),
                "bank_id": str(_bank_id),
                "stage_id": str(_stage_id),
                "question_id": question_id,
                "mutation": "regenerate",
            },
            correlation_id=correlation_id or str(UUID(question_id)),
        )


# ---------------------------------------------------------------------------
# Actor: per-kind regenerate (behavioral_star OR technical_depth alone)
# ---------------------------------------------------------------------------

@dramatiq.actor(
    max_retries=2,
    min_backoff=2_000,
    max_backoff=60_000,
    queue_name="question_bank_generation",
)
async def regenerate_kind_actor(
    bank_id: str,
    tenant_id: str,
    started_by: str,
    kind: str,
    correlation_id: str = "",
) -> None:
    """Re-run one kind's generation call on an existing bank.

    Pre-conditions enforced by the router: bank.status == 'generating',
    bank's existing AI questions of the targeted kind already wiped.
    """
    bank_uuid = UUID(bank_id)
    tenant_uuid = UUID(tenant_id)
    started_by_uuid = UUID(started_by)
    effective_corr = correlation_id or f"actor-regenerate-kind-{bank_id}"

    publish_args: tuple[UUID, UUID, str] | None = None
    async with get_bypass_session() as db:
        safe_tenant_id = str(tenant_uuid)
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'"))

        bank = (
            await db.execute(
                select(StageQuestionBank).where(StageQuestionBank.id == bank_uuid)
            )
        ).scalar_one_or_none()
        if bank is None:
            logger.error("question_bank.bank_missing", bank_id=str(bank_uuid))
            return

        stage = (
            await db.execute(
                select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
            )
        ).scalar_one()
        instance = (
            await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.id == stage.instance_id
                )
            )
        ).scalar_one()
        job = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == bank.job_posting_id)
            )
        ).scalar_one()
        snapshot = (
            await db.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == bank.signal_snapshot_id
                )
            )
        ).scalar_one()

        job_id = job.id
        stage_id = stage.id

        try:
            # Select prompt + eligible signals + budget per kind
            if kind == "behavioral_star":
                prompt_name = STAGE_TYPE_TO_BEHAVIORAL_PROMPT.get(stage.stage_type)
                eligible = _filter_behavioral_eligible(snapshot.signals)
                budget = BEHAVIORAL_BUDGET_MIN
                if prompt_name is None or not eligible:
                    # Nothing to regenerate — record skipped and exit clean
                    bank.generation_status_by_kind = {
                        **(bank.generation_status_by_kind or {}),
                        kind: "skipped_no_eligible_signals",
                    }
                    transition_to_reviewing_after_generation(
                        bank, user_id=started_by_uuid,
                    )
                    await db.commit()
                    publish_args = (job_id, stage_id, "reviewing")
                    return
            elif kind == "technical_depth":
                prompt_name = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
                eligible = snapshot.signals
                # Reduce by the OTHER kind's persisted total minutes
                other_q = (
                    await db.execute(
                        select(StageQuestion).where(
                            StageQuestion.bank_id == bank.id,
                            StageQuestion.question_kind == "behavioral_star",
                        )
                    )
                ).scalars().all()
                other_total = sum(float(q.estimated_minutes) for q in other_q)
                budget = max(1, int(stage.duration_minutes - other_total))
            else:
                raise RuntimeError(f"Unknown kind: {kind!r}")

            new_questions = await _generate_questions_for_kind(
                db,
                bank=bank,
                stage=stage,
                instance=instance,
                job=job,
                snapshot=snapshot,
                kind=kind,
                eligible_signals=eligible,
                budget_minutes=budget,
                prompt_name=prompt_name,
            )

            # Merge: keep existing questions of the OTHER kind (and recruiter
            # rows of any kind), append the regenerated ones, re-pack
            # positions, run post-merge correction. write_generated_questions
            # handles AI-question replacement + position re-pack.
            await write_generated_questions(
                db, bank=bank, questions=new_questions, source="ai_generated",
            )

            # Re-fetch combined list for post-merge correction
            combined = await get_bank_questions(db, bank.id)
            from app.modules.question_bank.service import (
                _apply_mandatory_correction_in_position_order,
            )
            from app.modules.question_bank.schemas import (
                GeneratedQuestion,
                QuestionRubric,
            )
            # Project ORM rows into GeneratedQuestion shape for the helper
            projected = [
                GeneratedQuestion(
                    position=q.position,
                    text=q.text,
                    signal_values=list(q.signal_values),
                    estimated_minutes=q.estimated_minutes,
                    is_mandatory=q.is_mandatory,
                    follow_ups=list(q.follow_ups),
                    positive_evidence=list(q.positive_evidence),
                    red_flags=list(q.red_flags),
                    rubric=QuestionRubric(**q.rubric),
                    evaluation_hint=q.evaluation_hint,
                    question_kind=q.question_kind,
                )
                for q in combined
            ]
            knockout_values = {
                s["value"] for s in snapshot.signals if s.get("knockout", False)
            }
            _apply_mandatory_correction_in_position_order(
                questions=projected, knockout_values=knockout_values,
            )
            # Apply corrections back to ORM rows
            by_position = {p.position: p for p in projected}
            for row in combined:
                if row.position in by_position:
                    row.is_mandatory = by_position[row.position].is_mandatory
            await db.flush()

            # Update per-kind status
            bank.generation_status_by_kind = {
                **(bank.generation_status_by_kind or {}),
                kind: "reviewing",
            }
            bank.pipeline_version_at_generation = instance.pipeline_version
            bank.stage_config_snapshot = {
                "signal_filter": stage.signal_filter,
                "difficulty": stage.difficulty,
            }
            bank.is_stale = False

            transition_to_reviewing_after_generation(bank, user_id=started_by_uuid)
            await log_event(
                db,
                tenant_id=tenant_uuid,
                actor_id=started_by_uuid,
                actor_email=None,
                action="question_bank.kind_regenerated",
                resource="stage_question_bank",
                resource_id=bank.id,
                payload={"kind": kind},
            )
            await db.commit()
            publish_args = (job_id, stage_id, "reviewing")
        except Exception as exc:
            logger.error(
                "question_bank.regenerate_kind_failed",
                bank_id=bank_id,
                kind=kind,
                error=str(exc)[:500],
                exc_info=True,
            )
            bank.generation_status_by_kind = {
                **(bank.generation_status_by_kind or {}),
                kind: "failed",
            }
            transition_to_failed(
                bank, error=f"{kind} regenerate failed: {str(exc)[:200]}"
            )
            await db.commit()
            publish_args = (job_id, stage_id, "failed")

    if publish_args is not None:
        job_id_pub, stage_id_pub, new_status_pub = publish_args
        await pubsub.publish(
            pubsub.job_channel(job_id_pub),
            pubsub.Events.BANK_STATUS_CHANGED,
            {
                "job_id": str(job_id_pub),
                "bank_id": bank_id,
                "stage_id": str(stage_id_pub),
                "new_status": new_status_pub,
                "source": "actor",
            },
            correlation_id=effective_corr,
        )
