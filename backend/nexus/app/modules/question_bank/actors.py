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
from sqlalchemy import select, update
from sqlalchemy.sql import text

from app import pubsub
from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
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
    GeneratedQuestion,
    QuestionRubric,
    SingleQuestionOutput,
)
from app.modules.question_bank.errors import (
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.service import (
    ensure_bank_exists,
    get_bank_questions,
    get_latest_confirmed_snapshot,
    persist_one_question,
    replace_question_in_place,
    transition_to_failed,
    transition_to_generating,
    transition_to_self_reviewing,
    transition_to_reviewing_after_critic,
    validate_streamed_question,
    wipe_ai_questions,
)
from app.modules.question_bank.context import (
    QuestionContext,
    build_question_context,
)
from app.modules.question_bank.critic import run_bank_critic
from app.modules.question_bank.state_machine import auto_revert_on_edit

logger = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

# Bank-generation prompt loader. The streaming generation path (engine-v2 M2) reads
# the rewritten per-phase prompts from `prompts/v{N}/`, where N is the env-driven
# `question_bank_prompt_version` (AIConfig is the single source of truth — never
# hardcode the version). Mirrors the per-prompt-family loader pattern in
# interview_engine/agent.py. Both the streaming generation path AND the
# regenerate-one path now read through this loader so regenerated questions carry
# primary_signal + difficulty + a new-taxonomy question_kind.
_bank_prompt_loader = PromptLoader(version=ai_config.question_bank_prompt_version)


# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------

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


def _signals_for_generation(snapshot_signals: list[dict], *, stage_type: str) -> list[dict]:
    """The signals the bank generator sees. For an AI skills screen, eligibility signals
    (years/degree/cert — recruiter pre-screened) are excluded; the screen tests SKILLS.
    Legacy signals without a `purpose` default to skill (no regression)."""
    if stage_type != "ai_screening":
        return list(snapshot_signals)
    return [s for s in snapshot_signals if s.get("purpose", "skill") != "eligibility"]


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

    Renders a SOFT-GUIDANCE budget block (the DB does not hard-enforce budget —
    guidance optimizes for signal density). The budget target is the stage's
    duration; a runaway ``ai_config.question_bank_max_questions`` per call is the
    only hard stop, and an over-budget result logs a soft warning downstream.
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

    # Pre-computed eligibility context. The LLM does NOT do budget arithmetic;
    # eligibility-after-include_types is computed here so it doesn't have to
    # filter the snapshot itself. Budget is SOFT GUIDANCE: the DB does not
    # hard-enforce a cap — a runaway ai_config.question_bank_max_questions per call
    # is the only hard stop, and an over-budget result logs a soft warning.
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

    parts.append(
        "\n# BUDGET FOR THIS STAGE "
        "(soft guidance — optimize for signal density, not count)\n"
    )
    parts.append(
        f"Target time: ~{stage.duration_minutes} min "
        f"(sum of estimated_minutes across the questions you generate)\n"
        f"Stage duration overall: {stage.duration_minutes} min\n"
        f"\n"
        f"Eligible signals (after include_types filter):\n"
        f"  - knockouts: {len(eligible_knockouts)} "
        f"(each warrants ONE mandatory question)\n"
        f"  - weight=3 non-knockout: {len(eligible_w3)} "
        f"(high-priority depth probes)\n"
        f"  - weight=2: {len(eligible_w2)} (depth probes)\n"
        f"  - weight=1: {len(eligible_w1)} "
        f"(only if every higher-weight signal is covered)\n"
        f"\n"
        f"Optimize for SIGNAL DENSITY, not question count. Under-using the budget "
        f"is fine; padding shallow questions is not.\n"
    )

    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Core generation function (shared by the stage and pipeline actors)
# ---------------------------------------------------------------------------

def _create_question_iterable(**kwargs):
    """Thin seam over instructor streaming so tests can monkeypatch it.

    Returns an async iterator of ``GeneratedQuestion`` via
    ``client.chat.completions.create_iterable`` (confirmed by the M2 spike: the
    reasoning model + TOOLS_STRICT streams complete objects incrementally).
    ``reasoning_effort`` is forwarded ONLY when set (AIConfig effort-gating
    contract — empty string means "don't send the parameter").
    """
    client = get_openai_client()
    call_kwargs = dict(
        model=ai_config.question_bank_model,
        response_model=GeneratedQuestion,
        messages=kwargs["messages"],
        max_retries=1,
        metadata=kwargs.get("metadata", {}),
        prompt_cache_key=f"qbank-gen-{kwargs['job_id']}",
    )
    if ai_config.question_bank_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_effort
    return client.chat.completions.create_iterable(**call_kwargs)


async def _stream_bank_questions(
    *,
    bank_id: UUID,
    tenant_id: UUID,
    job_id: UUID,
    stage_id: UUID,
    snapshot_id: UUID,
    eligible_signals: list[dict],
    prompt_name: str,
    start_position: int,
    correlation_id: str = "",
) -> list[GeneratedQuestion]:
    """Stream the bank: build the prompt in a SHORT read session (capture primitives,
    then CLOSE it), then stream + persist + publish BANK_QUESTION_ADDED per question.

    Returns the persisted ``GeneratedQuestion`` objects (used by the orchestrator for
    counting).

    NO session is held across the LLM stream:
      1. A short read session loads bank/stage/instance/job/snapshot, builds the
         system + user messages to STRINGS, and captures the snapshot signals (FULL,
         for validation), allowed_types, and stage_difficulty as PRIMITIVES. It then
         closes.
      2. Each streamed question is validated, then persisted + published in its OWN
         short session.
    """
    # ---- Short read session: build prompt strings + capture primitives ----
    async with get_bypass_session() as rdb:
        await rdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        stage = (
            await rdb.execute(
                select(JobPipelineStage).where(JobPipelineStage.id == stage_id)
            )
        ).scalar_one()
        instance = (
            await rdb.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.id == stage.instance_id
                )
            )
        ).scalar_one()
        job = (
            await rdb.execute(
                select(JobPosting).where(JobPosting.id == job_id)
            )
        ).scalar_one()
        snapshot = (
            await rdb.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == snapshot_id
                )
            )
        ).scalar_one()

        ctx = await build_question_context(
            rdb, job=job, instance=instance, stage=stage
        )
        system_prompt = _bank_prompt_loader.load_pair(
            "question_bank_common", prompt_name
        )

        # Show the LLM only `eligible_signals` while building the user message
        # (the swap-build-restore trick) — but validation below runs against the
        # FULL snapshot signals captured as a primitive.
        snapshot_signals = list(snapshot.signals)
        allowed_types = list(stage.signal_filter.get("include_types", []))
        stage_difficulty = stage.difficulty
        stage_type = stage.stage_type

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
    # ---- read session is CLOSED here; nothing held across the stream ----

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    metadata = {
        "bank_id": str(bank_id),
        "stage_id": str(stage_id),
        "stage_type": stage_type,
        "tenant_id": str(tenant_id),
        "job_posting_id": str(job_id),
        "prompt_version": ai_config.question_bank_prompt_version,
    }

    logger.info(
        "question_bank.stream.start",
        bank_id=str(bank_id),
        stage_id=str(stage_id),
        stage_type=stage_type,
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        system_prompt_chars=len(system_prompt),
        user_message_chars=len(user_message),
    )

    persisted: list[GeneratedQuestion] = []
    position = start_position
    effective_corr = correlation_id or f"actor-stream-{bank_id}"
    call_started_at = time.monotonic()

    with _tracer.start_as_current_span("openai.chat.completions.create_iterable"):
        set_llm_span_attributes(
            prompt_name=prompt_name,
            prompt_version=ai_config.question_bank_prompt_version,
            tenant_id=str(tenant_id),
            bank_id=str(bank_id),
            stage_id=str(stage_id),
            stage_type=stage_type,
            job_posting_id=str(job_id),
            model=ai_config.question_bank_model,
            reasoning_effort=ai_config.question_bank_effort,
        )
        try:
            async for q in _create_question_iterable(
                messages=messages, metadata=metadata, job_id=str(job_id),
            ):
                if len(persisted) >= ai_config.question_bank_max_questions:
                    logger.warning(
                        "question_bank.stream.ceiling_hit",
                        bank_id=str(bank_id),
                        ceiling=ai_config.question_bank_max_questions,
                    )
                    break

                try:
                    validate_streamed_question(
                        q,
                        snapshot_signals=snapshot_signals,
                        snapshot_id=snapshot_id,
                        allowed_types=allowed_types,
                    )
                except (
                    SignalValueNotInSnapshotError,
                    SignalTypeNotAllowedError,
                ) as skip_exc:
                    logger.warning(
                        "question_bank.stream.question_skipped",
                        bank_id=str(bank_id),
                        reason=type(skip_exc).__name__,
                    )
                    continue

                async with get_bypass_session() as qdb:
                    await qdb.execute(
                        text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
                    )
                    bank_row = (
                        await qdb.execute(
                            select(StageQuestionBank).where(
                                StageQuestionBank.id == bank_id
                            )
                        )
                    ).scalar_one()
                    await persist_one_question(
                        qdb,
                        bank=bank_row,
                        question=q,
                        source="ai_generated",
                        position=position,
                        stage_difficulty=stage_difficulty,
                    )
                    await qdb.commit()

                await pubsub.publish(
                    pubsub.job_channel(job_id),
                    pubsub.Events.BANK_QUESTION_ADDED,
                    {
                        "job_id": str(job_id),
                        "bank_id": str(bank_id),
                        "stage_id": str(stage_id),
                        "source": "actor",
                    },
                    correlation_id=effective_corr,
                )
                persisted.append(q)
                position += 1
        except Exception as llm_exc:
            _span = trace.get_current_span()
            _span.record_exception(llm_exc)
            _span.set_status(Status(StatusCode.ERROR, type(llm_exc).__name__))
            logger.error(
                "question_bank.stream.failed",
                bank_id=str(bank_id),
                stage_id=str(stage_id),
                duration_sec=round(time.monotonic() - call_started_at, 2),
                error_type=type(llm_exc).__name__,
                error_message=str(llm_exc)[:500],
                persisted_count=len(persisted),
                exc_info=True,
            )
            raise

    logger.info(
        "question_bank.stream.complete",
        bank_id=str(bank_id),
        stage_id=str(stage_id),
        duration_sec=round(time.monotonic() - call_started_at, 2),
        question_count=len(persisted),
    )
    return persisted


async def _generate_one_bank(
    *,
    bank_id: UUID,
    tenant_id: UUID,
    started_by: UUID,
    correlation_id: str = "",
) -> None:
    """Run streaming generation for one bank via a SINGLE streamed call.

    Must be called with the bank already at status='generating'. Takes PRIMITIVES
    (bank_id / tenant_id / started_by) and owns ALL its own sessions — NO session is
    held across the multi-second LLM stream. On success the bank ends in 'reviewing';
    on any streaming failure the failure path wipes ALL AI questions, transitions the
    bank to 'failed', and re-raises the original exception (preserving the caller
    contract: `_run_stage_generation` inspects the terminal state and tests assert
    specific exception types propagate).

    Phases:
      A (short session) — load rows, resolve the single prompt name, capture
        primitives, wipe ALL AI questions for a clean regenerate, ensure 'generating',
        commit, close.
      B (no held session) — ONE streamed generation call emits all question kinds in a
        single pass. Each question persists+publishes in its own short session inside
        `_stream_bank_questions`.
      B2 (short session) — commit `generating → self_reviewing` as its OWN durable phase
        and publish BANK_STATUS_CHANGED so the SSE fast path shows the self-review
        animation. B2 (not Phase C) owns the `→ self_reviewing` transition.
      B3 (no held session across the LLM call) — the bank self-critic: load the draft,
        call `run_bank_critic`, and replace the AI questions with the corrected set. On
        critic FAILURE the streamed draft is kept and a skip marker is recorded into the
        critique note (`critique_note`) — the failure is logged, NEVER silently
        swallowed, and generation still proceeds to 'reviewing'.
      C (short session) — reconcile on the now-final questions: mandatory auto-correction
        in position order, re-pack positions, soft over-budget warning, keyterm
        extraction, write the critique log to `coverage_notes`, stamp generation-time
        metadata, transition `self_reviewing → reviewing`, commit.
    """
    # ---- Phase A: load + capture primitives + wipe (short session) ----
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        bank = (
            await db.execute(
                select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
            )
        ).scalar_one()
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
        # Re-resolve to the latest confirmed snapshot at generation time.
        # After a re-extraction creates a newer confirmed snapshot, a bank whose
        # `signal_snapshot_id` still points to an older version would otherwise
        # generate questions against stale signals.  `get_latest_confirmed_snapshot`
        # is ORDER BY version DESC so the winner is always the most-recent confirm.
        snapshot = await get_latest_confirmed_snapshot(db, bank.job_posting_id)
        if snapshot is None:
            transition_to_failed(
                bank, error="No confirmed signal snapshot to generate from."
            )
            await db.commit()
            raise RuntimeError(
                f"Cannot generate bank {bank.id}: no confirmed signal snapshot."
            )
        # Re-pin the bank to the active snapshot so this and future generations
        # always use up-to-date signals.
        bank.signal_snapshot_id = snapshot.id
        bank.is_stale = False

        prompt_name = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
        if prompt_name is None:
            transition_to_failed(
                bank,
                error=f"No prompt mapped for stage_type={stage.stage_type}",
            )
            await db.commit()
            raise RuntimeError(
                f"No prompt mapped for stage_type={stage.stage_type}"
            )

        # Capture all primitives needed for the stream + reconcile phases.
        job_id = job.id
        stage_id = stage.id
        stage_type = stage.stage_type
        snapshot_id = snapshot.id
        stage_duration = stage.duration_minutes
        stage_difficulty = stage.difficulty
        snapshot_signals = list(snapshot.signals)
        pipeline_version = instance.pipeline_version
        stage_config_snapshot = {
            "signal_filter": stage.signal_filter,
            "difficulty": stage.difficulty,
        }

        # Wipe ALL existing AI questions so a (re)generate starts clean; recruiter
        # rows are preserved. Ensure the bank is 'generating' (the endpoint/actor
        # pre-marks it, but be defensive on a direct re-run).
        await wipe_ai_questions(db, bank=bank)
        if bank.status != "generating":
            transition_to_generating(bank)
        await db.commit()
    # ---- Phase A session CLOSED ----

    try:
        # ---- Phase B: single streamed generation call (NO held session) ----
        await _stream_bank_questions(
            bank_id=bank_id,
            tenant_id=tenant_id,
            job_id=job_id,
            stage_id=stage_id,
            snapshot_id=snapshot_id,
            eligible_signals=_signals_for_generation(snapshot_signals, stage_type=stage_type),
            prompt_name=prompt_name,
            start_position=0,
            correlation_id=correlation_id,
        )

        # ---- Phase B2: enter self-review (durable status drives the UI animation) ----
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            bank = (
                await db.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                )
            ).scalar_one()
            transition_to_self_reviewing(bank)
            await db.commit()
        # Publish the transition so the SSE fast path shows "AI is self-reviewing…".
        await pubsub.publish(
            pubsub.job_channel(job_id),
            pubsub.Events.BANK_STATUS_CHANGED,
            {
                "job_id": str(job_id),
                "bank_id": str(bank_id),
                "stage_id": str(stage_id),
                "new_status": "self_reviewing",
                "source": "actor",
            },
            correlation_id=correlation_id or f"actor-stage-{bank_id}",
        )

        # ---- Phase B3: critic — audit + correct the draft (no held session) ----
        async with get_bypass_session() as rdb:
            await rdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            draft_rows = await get_bank_questions(rdb, bank_id)
            job_row = (
                await rdb.execute(select(JobPosting).where(JobPosting.id == job_id))
            ).scalar_one()
            snapshot_row = (
                await rdb.execute(
                    select(JobPostingSignalSnapshot).where(
                        JobPostingSignalSnapshot.id == snapshot_id
                    )
                )
            ).scalar_one()
            role_title = job_row.title
            seniority = snapshot_row.seniority_level
            draft_questions = [
                GeneratedQuestion(
                    position=r.position, text=r.text, primary_signal=r.primary_signal,
                    signal_values=list(r.signal_values), estimated_minutes=r.estimated_minutes,
                    is_mandatory=r.is_mandatory, follow_ups=list(r.follow_ups),
                    positive_evidence=list(r.positive_evidence), red_flags=list(r.red_flags),
                    rubric=QuestionRubric(**r.rubric), evaluation_hint=r.evaluation_hint,
                    question_kind=r.question_kind, difficulty=r.difficulty,
                )
                for r in draft_rows
            ]
        # rdb closed — no session held across the critic LLM call.

        critique_note: str
        corrected: list[GeneratedQuestion] | None
        try:
            corrected, critique_note = await run_bank_critic(
                draft=draft_questions,
                seniority=seniority,
                role_title=role_title,
                signals=snapshot_signals,
                stage_difficulty=stage_difficulty,
                stage_duration=stage_duration,
                bank_id=bank_id,
                tenant_id=tenant_id,
                job_id=job_id,
            )
        except Exception as critic_exc:
            # FALLBACK (no silent swallow): the CRITIC CALL failed. Keep the streamed
            # draft, mark the skip in coverage_notes (audit trail), proceed to reviewing.
            logger.error(
                "question_bank.critic.skipped",
                bank_id=str(bank_id),
                error_type=type(critic_exc).__name__,
                error_message=str(critic_exc)[:500],
                correlation_id=correlation_id or f"actor-stage-{bank_id}",
                exc_info=True,
            )
            critique_note = (
                f"[critic skipped: {type(critic_exc).__name__}] "
                "draft kept un-critiqued; review manually."
            )
            corrected = None

        # Re-persist the corrected bank OUTSIDE the critic-skip guard: a persistence
        # failure here is a genuine error (critic succeeded) and must propagate to the
        # outer failure path (wipe -> failed), NOT be mislabeled as a critic skip.
        if corrected is not None:
            async with get_bypass_session() as wdb:
                await wdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                wbank = (
                    await wdb.execute(
                        select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                    )
                ).scalar_one()
                await wipe_ai_questions(wdb, bank=wbank)
                for pos, q in enumerate(corrected):
                    await persist_one_question(
                        wdb, bank=wbank, question=q, source="ai_generated",
                        position=pos, stage_difficulty=stage_difficulty,
                    )
                await wdb.commit()

        # ---- Phase C: reconcile + transition (short session) ----
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            bank = (
                await db.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                )
            ).scalar_one()
            job = (
                await db.execute(
                    select(JobPosting).where(JobPosting.id == job_id)
                )
            ).scalar_one()
            rows = await get_bank_questions(db, bank_id)

            # Project persisted rows → GeneratedQuestion, run mandatory correction
            # (flips is_mandatory only) in position order, write flips back, re-pack.
            from app.modules.question_bank.service import (
                _apply_mandatory_correction_in_position_order,
            )

            projected = [
                GeneratedQuestion(
                    position=r.position,
                    text=r.text,
                    primary_signal=r.primary_signal,
                    signal_values=list(r.signal_values),
                    estimated_minutes=r.estimated_minutes,
                    is_mandatory=r.is_mandatory,
                    follow_ups=list(r.follow_ups),
                    positive_evidence=list(r.positive_evidence),
                    red_flags=list(r.red_flags),
                    rubric=QuestionRubric(**r.rubric),
                    evaluation_hint=r.evaluation_hint,
                    question_kind=r.question_kind,
                )
                for r in rows
            ]
            knockout_values = {
                s["value"] for s in snapshot_signals if s.get("knockout", False)
            }
            _apply_mandatory_correction_in_position_order(
                questions=projected, knockout_values=knockout_values,
            )
            by_position = {p.position: p for p in projected}
            for row in rows:
                if row.position in by_position:
                    row.is_mandatory = by_position[row.position].is_mandatory
            # Re-pack positions 0..N-1 (rows are already in position order from the
            # reload, so this is a no-op safety pass — NO reordering: the engine
            # already asks mandatory/knockout questions first at runtime via
            # build_session_config's `is_mandatory DESC` ordering, and reordering
            # stored positions here collided with the unique (bank_id, position) index).
            for i, row in enumerate(rows):
                row.position = i
            await db.flush()

            # Soft over-budget warning (decision D2 — never raise).
            total_minutes = sum(float(r.estimated_minutes) for r in rows)
            if total_minutes > stage_duration:
                logger.warning(
                    "question_bank.budget_soft_warning",
                    bank_id=str(bank_id),
                    observed_minutes=round(total_minutes, 1),
                    stage_duration=stage_duration,
                )

            # Best-effort keyterm extraction (Phase 3D.deepgram-keyterm). Failures
            # are NOT fatal — the engine falls back to candidate-name-only boosting.
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
                    role_summary="",
                    signals=[s["value"] for s in snapshot_signals],
                    questions=[{"text": r.text} for r in rows],
                    bank_id=str(bank_id),
                    tenant_id=str(tenant_id),
                )
                await db.execute(
                    update(StageQuestionBank)
                    .where(StageQuestionBank.id == bank_id)
                    .values(extracted_keyterms=keyterm_output.keyterms),
                )
                logger.info(
                    "question_bank.keyterm_extraction.complete",
                    bank_id=str(bank_id),
                    count=len(keyterm_output.keyterms),
                )
            except Exception:
                logger.exception(
                    "question_bank.keyterm_extraction.failed",
                    bank_id=str(bank_id),
                )

            # The bank is ALREADY in 'self_reviewing' (Phase B2 committed that
            # transition as its own durable phase). Phase C only finalizes:
            # write the critique log + metadata and transition
            # self_reviewing -> reviewing.
            bank.coverage_notes = critique_note
            bank.prompt_version = ai_config.question_bank_prompt_version
            bank.pipeline_version_at_generation = pipeline_version
            bank.stage_config_snapshot = stage_config_snapshot
            bank.is_stale = False
            transition_to_reviewing_after_critic(bank, user_id=started_by)
            await db.commit()
    except Exception as exc:
        # ---- Failure path (decision D7): wipe ALL AI questions → failed ----
        logger.error(
            "question_bank.generation_failed",
            bank_id=str(bank_id),
            error=str(exc),
            exc_info=True,
        )
        async with get_bypass_session() as fdb:
            await fdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
            fbank = (
                await fdb.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                )
            ).scalar_one()
            await wipe_ai_questions(fdb, bank=fbank)
            transition_to_failed(fbank, error=str(exc)[:500])
            await fdb.commit()
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
    correlation_id: str = "",
) -> tuple[UUID, UUID, str] | None:
    """Body of the single-stage actor — separated so tests can pass a session.

    Streaming model (engine-v2 M2): `_generate_one_bank` owns ALL its own short
    sessions (decision D6 — no session held across the LLM stream). This helper does
    NOT hold a session across the stream. It uses the passed `db` only for the id-
    resolution lookup (job_id/stage_id) and the audit `log_event`. Returns
    ``(job_id, stage_id, new_status)`` to publish, or ``None`` if the bank vanished.

    On a permanent failure `_generate_one_bank` wipes + transitions the bank to
    'failed' in its own session and re-raises. This helper re-loads the bank's
    terminal status and returns ``(job_id, stage_id, 'failed')`` so the caller
    publishes the status change (preserving the existing actor contract).
    """
    bank = (
        await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
        )
    ).scalar_one_or_none()
    if bank is None:
        logger.error("question_bank.bank_missing", bank_id=str(bank_id))
        return None

    job_id = bank.job_posting_id
    stage_id = bank.stage_id

    try:
        await _generate_one_bank(
            bank_id=bank_id,
            tenant_id=tenant_id,
            started_by=started_by,
            correlation_id=correlation_id,
        )
        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=started_by,
            actor_email=None,
            action="question_bank.bank_generated",
            resource="stage_question_bank",
            resource_id=bank_id,
        )
        return (job_id, stage_id, "reviewing")
    except Exception:
        # `_generate_one_bank` committed the terminal state in its own session.
        # Re-read it via this session (expire so we re-fetch the committed row).
        db.expire(bank)
        refreshed = (
            await db.execute(
                select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
            )
        ).scalar_one_or_none()
        if refreshed is not None and refreshed.status == "failed":
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

    Streaming model (engine-v2 M2): `_generate_one_bank` owns its own short sessions.
    The outer session here is used only for id resolution + the audit log_event; it is
    NOT held across the multi-second stream. Publishes ``BANK_STATUS_CHANGED``
    post-commit (success and failure paths).
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
                correlation_id=effective_corr,
            )
            if result is None:
                # Bank vanished — nothing to commit, nothing to publish.
                return
            publish_args = result
            await db.commit()
        except Exception:
            # `_run_stage_generation` only re-raises if the bank is NOT in a
            # terminal state (the generation work itself already committed in
            # its own sessions). Roll back this session's audit-log write and
            # let Dramatiq retry.
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
    correlation_id: str = "",
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

    Streaming model (engine-v2 M2): a short session does the structural lookup +
    ensure_bank_exists + pre-mark 'generating' and commits; then `_generate_one_bank`
    runs the stream owning its OWN short sessions (decision D6 — no session held across
    the stream). Stages still run strictly sequentially (the orchestrator awaits each
    call) so stage N sees stages 1..N-1's persisted questions.
    """
    # ---- Short session: structure lookup + ensure bank + pre-mark ----
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
        await db.commit()
    # ---- pre-mark session CLOSED; the stream owns its own sessions ----

    try:
        await _generate_one_bank(
            bank_id=bank_id,
            tenant_id=UUID(tenant_id),
            started_by=started_by,
            correlation_id=correlation_id,
        )
        new_status = "reviewing"
    except Exception as exc:
        # `_generate_one_bank` already wiped + transitioned the bank to 'failed'
        # in its own session before re-raising. Swallow so the pipeline continues.
        logger.error(
            "question_bank.pipeline_stage_failed",
            stage_id=str(stage_id),
            error=str(exc),
        )
        new_status = "failed"

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
            correlation_id=correlation_id,
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
    # Build prompt: common header + regenerate_one + rich user context. Use the v2
    # loader (the same one the streaming gen path uses) so the regenerated question
    # carries primary_signal + difficulty + a new-taxonomy question_kind — NOT the
    # module-level v1 `prompt_loader`.
    system_prompt = _bank_prompt_loader.load_pair(
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

    # Post-validate the one question against the snapshot. validate_streamed_question
    # additionally enforces the D5 invariant that primary_signal ∈ signal_values —
    # without it a regen could set primary_signal to a value outside the snapshot.
    # The session is live here, so passing snapshot.signals as a primitive is fine.
    allowed_types = stage.signal_filter.get("include_types", [])
    validate_streamed_question(
        result.question,
        snapshot_signals=snapshot.signals,
        snapshot_id=snapshot.id,
        allowed_types=allowed_types,
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
