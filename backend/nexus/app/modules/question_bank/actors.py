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
    SingleQuestionOutput,
)
from app.modules.question_bank.errors import (
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.service import (
    ensure_bank_exists,
    get_bank_questions,
    persist_one_question,
    replace_question_in_place,
    transition_to_failed,
    transition_to_generating,
    transition_to_reviewing_after_generation,
    validate_streamed_question,
    wipe_ai_questions,
    wipe_ai_questions_of_phase,
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

# Behavioral-call budget guidance (minutes). SOFT guidance only — decision D2 made
# the budget soft (prompt guidance + a STREAM_QUESTION_CEILING runaway stop, NO hard
# cap). Sized to fit the knockout claim-checks PLUS at least one true STAR behavioral
# question. The technical phase budget = stage duration − behavioral total, so this
# value slightly favors behavioral breadth — intended; the recruiter raises stage
# duration when they want more technical room. Could become per-stage configurable
# later.
BEHAVIORAL_BUDGET_MIN = 6

# Inline runaway ceiling per streamed generation call (decision D2) — a safety stop,
# NOT a time-budget cap. Only fires on a pathological runaway.
STREAM_QUESTION_CEILING = 12

# Generation phase ↔ allowed question_kind partition (decision D3). Each phase's
# rewritten prompt may emit only its phase's kinds; wipe/count/section-grouping use
# this map so the per-phase regen + UI sections survive the taxonomy switch.
PHASE_QUESTION_KINDS: dict[str, set[str]] = {
    "behavioral": {"experience_check", "behavioral", "compliance_binary"},
    "technical": {"technical_scenario"},
}

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
    """Signals the behavioral phase covers:
      - knockout experience/behavioral CLAIMS to verify (years, platform, scope), AND
      - behavioral-TYPE required signals that warrant a true STAR question
        (collaboration, documentation, mentoring, communication, etc.).
    Competency/credential signals stay in the technical phase / ATS pre-filter.
    Deduped by value, order-preserving.

    See docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §1
    (broadened for engine-v2 M2 so behavioral-type signals get true STAR coverage,
    not just knockout claim-checks).
    """
    out: list[dict] = []
    seen: set = set()
    for s in signals:
        v = s.get("value")
        is_knockout_claim = (
            s.get("knockout") is True and s.get("type") in ("experience", "behavioral")
        )
        is_behavioral_star = (
            s.get("type") == "behavioral" and s.get("priority") == "required"
        )
        if (is_knockout_claim or is_behavioral_star) and v not in seen:
            out.append(s)
            seen.add(v)
    return out


def _build_user_message(
    *,
    job: JobPosting,
    snapshot: JobPostingSignalSnapshot,
    company_profile: dict | None,
    stage: JobPipelineStage,
    pipeline_stages: list[dict],
    prior_stages_questions: list[dict],
    prior_phase_questions: list[dict] | None = None,
    budget_minutes: int | None = None,
) -> str:
    """Build the user message — all context for the LLM.

    Order matters: context (company profile + JD + signals) BEFORE the stage-
    specific instructions. This matches the 'prompt_context_ordering' rule
    established in Phase 2A.

    `prior_phase_questions` (engine-v2 M2 chaining): when non-empty (the technical
    phase receives the behavioral phase's already-persisted questions), renders an
    ``# ALREADY-GENERATED BEHAVIORAL QUESTIONS — DO NOT OVERLAP`` block. The heading
    string MUST match the v2 technical prompt's reference verbatim.

    `budget_minutes` (decision D2): when set, renders a SOFT-GUIDANCE budget block
    (the DB no longer hard-enforces budget — guidance optimizes for signal density).
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

    # ---- Chaining block (engine-v2 M2) ----
    # When the technical phase runs, it receives the behavioral phase's
    # already-persisted questions so it can cover DIFFERENT angles. The heading
    # below MUST match prompts/v2/question_bank_ai_screening.txt verbatim.
    if prior_phase_questions:
        parts.append(
            "\n# ALREADY-GENERATED BEHAVIORAL QUESTIONS — DO NOT OVERLAP\n"
        )
        parts.append(
            "These questions were authored by the behavioral phase for THIS stage. "
            "Do NOT restate them. Re-probe their signals only at greater DEPTH and "
            "from a genuinely different cognitive path.\n\n"
        )
        for i, q in enumerate(prior_phase_questions):
            parts.append(
                f"  B{i + 1} (probes: {q.get('signal_values', [])}):\n"
                f"      {q.get('text', '')}\n"
            )
            # Surface the behavioral phase's follow-up dimensions so the
            # technical phase knows which probe angles are already covered.
            # The engine fires each dimension at most once per thread and tracks
            # coverage across the whole screen, so the technical phase must not
            # author a follow-up with the same dimension slug or underlying intent.
            dims = q.get("follow_ups") or []
            dim_labels = [
                f"{d.get('dimension')} ({d.get('intent')})"
                for d in dims
                if isinstance(d, dict) and d.get("dimension")
            ]
            if dim_labels:
                parts.append(
                    "      covered dimensions: " + "; ".join(dim_labels) + "\n"
                )

    # Pre-computed eligibility context. The LLM does NOT do budget arithmetic;
    # eligibility-after-include_types is computed here so it doesn't have to
    # filter the snapshot itself. Budget is SOFT GUIDANCE (decision D2): the DB
    # no longer hard-enforces a cap — a runaway STREAM_QUESTION_CEILING per call
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

    budget_target = budget_minutes if budget_minutes is not None else stage.duration_minutes

    parts.append(
        "\n# BUDGET FOR THIS STAGE "
        "(soft guidance — optimize for signal density, not count)\n"
    )
    parts.append(
        f"Target time for this phase: ~{budget_target} min "
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
    )
    if ai_config.question_bank_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_effort
    return client.chat.completions.create_iterable(**call_kwargs)


async def _generate_questions_for_kind(
    *,
    bank_id: UUID,
    tenant_id: UUID,
    job_id: UUID,
    stage_id: UUID,
    snapshot_id: UUID,
    phase: str,                       # "behavioral" | "technical" (decision D3)
    eligible_signals: list[dict],
    budget_minutes: int,
    prompt_name: str,
    start_position: int,
    prior_phase_questions: list[dict],
    correlation_id: str = "",
) -> list[GeneratedQuestion]:
    """Stream ONE phase: build the prompt in a SHORT read session (capture primitives,
    then CLOSE it), then stream + persist + publish BANK_QUESTION_ADDED per question.

    Returns the persisted ``GeneratedQuestion`` objects (used by the orchestrator for
    behavioral→technical chaining and counting).

    Decision D6 — NO session is held across the LLM stream:
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
                prior_phase_questions=prior_phase_questions,
                budget_minutes=budget_minutes,
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
        "question_phase": phase,
    }

    logger.info(
        "question_bank.stream.start",
        bank_id=str(bank_id),
        stage_id=str(stage_id),
        stage_type=stage_type,
        phase=phase,
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        system_prompt_chars=len(system_prompt),
        user_message_chars=len(user_message),
        budget_minutes=budget_minutes,
    )

    persisted: list[GeneratedQuestion] = []
    position = start_position
    effective_corr = correlation_id or f"actor-stream-{bank_id}-{phase}"
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
            question_kind=phase,
        )
        try:
            async for q in _create_question_iterable(
                messages=messages, metadata=metadata,
            ):
                if len(persisted) >= STREAM_QUESTION_CEILING:
                    logger.warning(
                        "question_bank.stream.ceiling_hit",
                        bank_id=str(bank_id),
                        phase=phase,
                        ceiling=STREAM_QUESTION_CEILING,
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
                        phase=phase,
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
                        "phase": phase,
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
                phase=phase,
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
        phase=phase,
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
    """Run streaming generation for one bank (engine-v2 M2 — 3-phase model, D6).

    Must be called with the bank already at status='generating'. Takes PRIMITIVES
    (bank_id / tenant_id / started_by) and owns ALL its own sessions — NO session is
    held across the multi-second LLM stream (decision D6). On success the bank ends in
    'reviewing'; on any streaming failure the failure path wipes ALL AI questions (D7),
    transitions the bank to 'failed', and re-raises the original exception (preserving
    the caller contract: `_run_stage_generation` inspects the terminal state and
    tests assert specific exception types propagate).

    Three phases:
      A (short session) — load rows, compute eligible signals + prompt names, capture
        primitives, wipe ALL AI questions for a clean regenerate, ensure 'generating',
        commit, close.
      B (no held session) — behavioral phase (skippable), then the technical phase
        with the behavioral questions chained in. Each question persists+publishes in
        its own short session inside `_generate_questions_for_kind`.
      C (short session) — reconcile: mandatory auto-correction in position order,
        re-pack positions, soft over-budget warning, keyterm extraction, stamp
        generation-time metadata, transition to 'reviewing', commit.
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
        snapshot = (
            await db.execute(
                select(JobPostingSignalSnapshot).where(
                    JobPostingSignalSnapshot.id == bank.signal_snapshot_id
                )
            )
        ).scalar_one()

        eligible_behavioral_signals = _filter_behavioral_eligible(snapshot.signals)
        behavioral_prompt = STAGE_TYPE_TO_BEHAVIORAL_PROMPT.get(stage.stage_type)
        technical_prompt = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
        if technical_prompt is None:
            transition_to_failed(
                bank,
                error=f"No technical prompt mapped for stage_type={stage.stage_type}",
            )
            await db.commit()
            raise RuntimeError(
                f"No technical prompt mapped for stage_type={stage.stage_type}"
            )

        # Capture all primitives needed for the stream + reconcile phases.
        job_id = job.id
        stage_id = stage.id
        snapshot_id = snapshot.id
        stage_duration = stage.duration_minutes
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
        # ---- Phase B: behavioral then technical (NO held session) ----
        behavioral_questions: list[GeneratedQuestion] = []
        behavioral_status: str
        if not eligible_behavioral_signals or behavioral_prompt is None:
            behavioral_status = "skipped_no_eligible_signals"
            logger.info(
                "question_bank.behavioral_skipped",
                bank_id=str(bank_id),
                reason=(
                    "no_eligible_signals"
                    if not eligible_behavioral_signals
                    else "no_behavioral_prompt_for_stage_type"
                ),
            )
        else:
            try:
                behavioral_questions = await _generate_questions_for_kind(
                    bank_id=bank_id,
                    tenant_id=tenant_id,
                    job_id=job_id,
                    stage_id=stage_id,
                    snapshot_id=snapshot_id,
                    phase="behavioral",
                    eligible_signals=eligible_behavioral_signals,
                    budget_minutes=BEHAVIORAL_BUDGET_MIN,
                    prompt_name=behavioral_prompt,
                    start_position=0,
                    prior_phase_questions=[],
                    correlation_id=correlation_id,
                )
                behavioral_status = "reviewing"
            except Exception as bh_exc:
                logger.error(
                    "question_bank.behavioral_phase_failed",
                    bank_id=str(bank_id),
                    error=str(bh_exc)[:500],
                    exc_info=True,
                )
                behavioral_status = "failed"
                behavioral_questions = []

        prior = [
            {
                "text": q.text,
                "signal_values": q.signal_values,
            }
            for q in behavioral_questions
        ]
        behavioral_total = sum(
            float(q.estimated_minutes) for q in behavioral_questions
        )

        technical_status = "reviewing"
        try:
            await _generate_questions_for_kind(
                bank_id=bank_id,
                tenant_id=tenant_id,
                job_id=job_id,
                stage_id=stage_id,
                snapshot_id=snapshot_id,
                phase="technical",
                eligible_signals=snapshot_signals,           # full set
                budget_minutes=max(1, int(stage_duration - behavioral_total)),
                prompt_name=technical_prompt,
                start_position=len(behavioral_questions),
                prior_phase_questions=prior,
                correlation_id=correlation_id,
            )
        except Exception:
            technical_status = "failed"
            raise

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

            bank.generation_status_by_kind = {
                "behavioral": behavioral_status,
                "technical": technical_status,
            }

            # Project persisted rows → GeneratedQuestion, run mandatory correction
            # (flips is_mandatory only) in position order, write flips back, re-pack.
            from app.modules.question_bank.schemas import QuestionRubric
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

            bank.prompt_version = ai_config.question_bank_prompt_version
            bank.pipeline_version_at_generation = pipeline_version
            bank.stage_config_snapshot = stage_config_snapshot
            bank.is_stale = False
            transition_to_reviewing_after_generation(bank, user_id=started_by)
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
            fbank.generation_status_by_kind = {
                "behavioral": locals().get("behavioral_status", "failed"),
                "technical": locals().get("technical_status", "failed"),
            }
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


# ---------------------------------------------------------------------------
# Actor: per-phase regenerate (behavioral OR technical phase alone)
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
    """Re-stream ONE phase's generation on an existing bank (engine-v2 M2, D6).

    `kind` is a PHASE label — one of ``"behavioral"`` / ``"technical"`` (decision
    D3). Pre-conditions enforced by the router: bank.status == 'generating' and
    this phase's existing AI questions already wiped (`wipe_ai_questions_of_phase`).

    Mirrors `_generate_one_bank`'s session discipline but for a single phase — NO
    `get_bypass_session()` is held across the multi-second LLM stream:

      A (short session) — load rows; resolve this phase's prompt + eligible signals
        + budget; (idempotently re-)wipe this phase; compute the start_position from
        the surviving rows; capture the OTHER phase's persisted questions as the
        chaining payload (technical phase only); capture primitives; commit; close.
      B (no held session) — `_generate_questions_for_kind` streams + persists +
        publishes BANK_QUESTION_ADDED per question in its own short sessions.
      C (short session) — reconcile: set generation_status_by_kind[phase]='reviewing'
        (merged with the other phase's existing key), run mandatory auto-correction
        over ALL questions, re-pack positions, stamp generation-time metadata,
        transition to 'reviewing', commit.

    Failure path mirrors `_generate_one_bank`'s contract — but because this actor
    publishes its own status event (rather than re-raising to a wrapper that does),
    it SWALLOWS the exception, sets generation_status_by_kind[phase]='failed',
    transitions to 'failed' in a short session, and publishes BANK_STATUS_CHANGED
    'failed' (so the SSE fast path delivers the terminal state).
    """
    bank_uuid = UUID(bank_id)
    tenant_uuid = UUID(tenant_id)
    started_by_uuid = UUID(started_by)
    phase = kind
    effective_corr = correlation_id or f"actor-regenerate-kind-{bank_id}"

    publish_args: tuple[UUID, UUID, str] | None = None

    # ---- Phase A: load + resolve phase config + capture primitives ----
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid}'"))

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
        snapshot_id = snapshot.id
        snapshot_signals = list(snapshot.signals)
        stage_duration = stage.duration_minutes
        pipeline_version = instance.pipeline_version
        stage_config_snapshot = {
            "signal_filter": stage.signal_filter,
            "difficulty": stage.difficulty,
        }

        if phase == "behavioral":
            prompt_name = STAGE_TYPE_TO_BEHAVIORAL_PROMPT.get(stage.stage_type)
            eligible = _filter_behavioral_eligible(snapshot_signals)
            budget = BEHAVIORAL_BUDGET_MIN
            if prompt_name is None or not eligible:
                # Nothing to regenerate — record skipped and exit clean (preserves
                # the original skip semantics; the router already wiped this phase).
                bank.generation_status_by_kind = {
                    **(bank.generation_status_by_kind or {}),
                    "behavioral": "skipped_no_eligible_signals",
                }
                transition_to_reviewing_after_generation(
                    bank, user_id=started_by_uuid,
                )
                await db.commit()
                await pubsub.publish(
                    pubsub.job_channel(job_id),
                    pubsub.Events.BANK_STATUS_CHANGED,
                    {
                        "job_id": str(job_id),
                        "bank_id": bank_id,
                        "stage_id": str(stage_id),
                        "new_status": "reviewing",
                        "source": "actor",
                    },
                    correlation_id=effective_corr,
                )
                return
            prior_phase_questions: list[dict] = []
        elif phase == "technical":
            prompt_name = STAGE_TYPE_TO_PROMPT.get(stage.stage_type)
            eligible = snapshot_signals  # full set
            if prompt_name is None:
                raise RuntimeError(
                    f"No technical prompt mapped for stage_type={stage.stage_type}"
                )
        else:
            raise RuntimeError(f"Unknown phase: {phase!r}")

        # Idempotent re-wipe of THIS phase's AI rows (the router already wiped them
        # before dispatch; this is a defensive no-op on a clean re-run).
        await wipe_ai_questions_of_phase(db, bank=bank, phase=phase)

        # Surviving rows = the OTHER phase's AI questions + all recruiter rows.
        surviving = await get_bank_questions(db, bank_uuid)
        start_position = len(surviving)

        if phase == "technical":
            # Chain the surviving behavioral questions in for non-overlap. The
            # builder's chaining block reads `text`, `signal_values`, and the
            # follow-up `dimension`/`intent` pairs so the technical phase can
            # avoid repeating probe dimensions already authored in the behavioral
            # phase (the engine fires each dimension at most once per thread).
            behavioral_kinds = PHASE_QUESTION_KINDS["behavioral"]
            prior_phase_questions = [
                {
                    "text": r.text,
                    "signal_values": list(r.signal_values),
                    "follow_ups": list(r.follow_ups) if r.follow_ups else [],
                }
                for r in surviving
                if r.question_kind in behavioral_kinds
            ]
            # Budget = stage duration minus the OTHER (behavioral) phase's persisted
            # total minutes. Under the new taxonomy that phase is the set of
            # behavioral kinds in PHASE_QUESTION_KINDS — NOT a single kind string.
            other_total = sum(
                float(r.estimated_minutes)
                for r in surviving
                if r.question_kind in behavioral_kinds
            )
            budget = max(1, int(stage_duration - other_total))

        await db.commit()
    # ---- Phase A session CLOSED; the stream owns its own sessions ----

    try:
        # ---- Phase B: stream this phase (NO held session) ----
        await _generate_questions_for_kind(
            bank_id=bank_uuid,
            tenant_id=tenant_uuid,
            job_id=job_id,
            stage_id=stage_id,
            snapshot_id=snapshot_id,
            phase=phase,
            eligible_signals=eligible,
            budget_minutes=budget,
            prompt_name=prompt_name,
            start_position=start_position,
            prior_phase_questions=prior_phase_questions,
            correlation_id=effective_corr,
        )

        # ---- Phase C: reconcile + transition (short session) ----
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid}'"))
            bank = (
                await db.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_uuid)
                )
            ).scalar_one()
            rows = await get_bank_questions(db, bank_uuid)

            bank.generation_status_by_kind = {
                **(bank.generation_status_by_kind or {}),
                phase: "reviewing",
            }

            from app.modules.question_bank.schemas import QuestionRubric
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

            bank.prompt_version = ai_config.question_bank_prompt_version
            bank.pipeline_version_at_generation = pipeline_version
            bank.stage_config_snapshot = stage_config_snapshot
            bank.is_stale = False
            transition_to_reviewing_after_generation(bank, user_id=started_by_uuid)
            await log_event(
                db,
                tenant_id=tenant_uuid,
                actor_id=started_by_uuid,
                actor_email=None,
                action="question_bank.kind_regenerated",
                resource="stage_question_bank",
                resource_id=bank_uuid,
                payload={"phase": phase},
            )
            await db.commit()
        publish_args = (job_id, stage_id, "reviewing")
    except Exception as exc:
        # ---- Failure path: failed status + transition in a short session ----
        logger.error(
            "question_bank.regenerate_kind_failed",
            bank_id=bank_id,
            phase=phase,
            error=str(exc)[:500],
            exc_info=True,
        )
        async with get_bypass_session() as fdb:
            await fdb.execute(text(f"SET LOCAL app.current_tenant = '{tenant_uuid}'"))
            fbank = (
                await fdb.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_uuid)
                )
            ).scalar_one()
            fbank.generation_status_by_kind = {
                **(fbank.generation_status_by_kind or {}),
                phase: "failed",
            }
            transition_to_failed(
                fbank, error=f"{phase} regenerate failed: {str(exc)[:200]}"
            )
            await fdb.commit()
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
