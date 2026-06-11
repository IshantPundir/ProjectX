"""Question bank HTTP endpoints.

11 endpoints under /api/jobs/{job_id}/pipeline/... covering CRUD, generation
triggers, bank confirmation, and the SSE status stream.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db, get_tenant_session
from app.modules.auth import UserContext, get_current_user_roles
from app.modules.jd import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.question_bank import actors as bank_actors
from app.modules.question_bank.actors import STAGE_TYPE_TO_PROMPT
from app.modules.question_bank.authz import (
    require_bank_access_by_stage,
    require_pipeline_access,
    require_question_access,
)
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError,
    BankNotInReviewingError,
    KnockoutUnprobedError,
    MandatoryOverrunError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    BankResponse,
    BanksOverviewResponse,
    BankWithQuestionsResponse,
    CreateQuestionBody,
    GenerateResponse,
    PlaceholderBankResponse,
    QuestionResponse,
    QuestionRubric,
    RegenerateQuestionBody,
    ReorderBody,
    UpdateQuestionBody,
)
from app.modules.question_bank.service import (
    confirm_bank,
    create_recruiter_question,
    delete_question,
    ensure_bank_exists,
    get_bank_questions,
    get_banks_for_pipeline,
    reorder_questions,
    transition_to_generating,
    update_question,
)
from app.modules.question_bank.sse import stream_question_bank_status
from app.modules.question_bank.state_machine import transition_to_failed
from app import pubsub

router = APIRouter(prefix="/api", tags=["question_bank"])
_log = structlog.get_logger()

# Max length for an inbound x-correlation-id header. 128 is generous — uuid4
# is 36 chars — but caps log-field growth and blocks pathological values.
_MAX_CORRELATION_ID_LEN = 128


def _get_correlation_id(request: Request) -> str:
    """Extract x-correlation-id or mint a fresh uuid4.

    The header is untrusted input, so we validate before propagating it to
    logs and pubsub envelopes:
      - must be non-empty and <= 128 chars
      - must be printable ASCII (no control chars, no unicode)
    Invalid values are discarded and replaced with a fresh uuid4 so a
    forensic trail is still preserved per-request.
    """
    raw = request.headers.get("x-correlation-id")
    if raw and 0 < len(raw) <= _MAX_CORRELATION_ID_LEN and raw.isascii() and raw.isprintable():
        return raw
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Safe Dramatiq dispatch wrappers
# ---------------------------------------------------------------------------
#
# Without these, a Redis outage at the moment of `.send()` raises *after* the
# request-scoped transaction has already been committed. The bank is left
# stuck in "generating" with no actor running, and the user sees a spinner
# forever. Each helper:
#   1. Attempts the enqueue.
#   2. On failure: opens a fresh bypass session (the request session is gone),
#      transitions the stranded resource, commits, and raises 503.
# Modelled after app/modules/jd/router.py::_safe_dispatch_extraction.


async def _safe_dispatch_generate_stage(
    bank_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    correlation_id: str = "",
) -> None:
    """Enqueue generate_question_bank_stage; on failure, flip the bank to
    'failed' with an operator-friendly error so the UI stops spinning.

    `correlation_id` is forwarded so the actor's pub/sub publishes carry
    the originating request's correlation ID end-to-end (required by
    CLAUDE.md observability standards).
    """
    try:
        bank_actors.generate_question_bank_stage.send(
            str(bank_id), str(tenant_id), str(user_id), correlation_id
        )
    except Exception as exc:
        _log.error(
            "question_bank.dispatch_stage_failed",
            bank_id=str(bank_id),
            tenant_id=str(tenant_id),
            exc_info=exc,
        )
        # Open a new tenant session — the request's session is already
        # committed and closed. transition_to_failed asserts the bank is in
        # 'generating', which is exactly the state the router just set it to.
        try:
            async with get_tenant_session(str(tenant_id)) as db:
                bank_result = await db.execute(
                    select(StageQuestionBank).where(StageQuestionBank.id == bank_id)
                )
                bank = bank_result.scalar_one_or_none()
                if bank is not None and bank.status == "generating":
                    transition_to_failed(
                        bank,
                        error="Failed to enqueue generation job — please retry",
                    )
        except Exception as rollback_exc:
            _log.error(
                "question_bank.dispatch_rollback_failed",
                bank_id=str(bank_id),
                exc_info=rollback_exc,
            )
        raise HTTPException(
            status_code=503,
            detail=(
                "Failed to enqueue question generation job — please retry. "
                "If this persists, contact support."
            ),
        )


async def _safe_dispatch_generate_pipeline(
    instance_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    correlation_id: str = "",
) -> None:
    """Enqueue generate_question_bank_pipeline. The endpoint pre-marks the first
    eligible bank as 'generating' before calling this helper, but that commit has
    already been issued — there is no in-flight transaction to roll back here.
    The first bank is left in 'generating'; if the actor never runs it will stay
    stuck. Acceptable: the actor has max_retries=0, so a Redis outage is logged
    loudly and the user can retry via the single-stage endpoint. Log loudly and
    raise 503.

    `correlation_id` is forwarded so per-stage and pipeline-completion
    pub/sub events carry the originating request's correlation ID
    end-to-end (CLAUDE.md observability standards).
    """
    try:
        bank_actors.generate_question_bank_pipeline.send(
            str(instance_id), str(tenant_id), str(user_id), correlation_id
        )
    except Exception as exc:
        _log.error(
            "question_bank.dispatch_pipeline_failed",
            instance_id=str(instance_id),
            tenant_id=str(tenant_id),
            exc_info=exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Failed to enqueue pipeline generation job — please retry. "
                "If this persists, contact support."
            ),
        )


async def _safe_dispatch_regenerate_question(
    question_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    replace_signal_values: list[str] | None,
    correlation_id: str = "",
) -> None:
    """Enqueue regenerate_question. Single-question regen does not
    pre-transition the bank, so there is nothing to roll back. Log loudly
    and raise 503."""
    try:
        bank_actors.regenerate_question.send(
            str(question_id),
            str(tenant_id),
            str(user_id),
            replace_signal_values,
            correlation_id,
        )
    except Exception as exc:
        _log.error(
            "question_bank.dispatch_regenerate_failed",
            question_id=str(question_id),
            tenant_id=str(tenant_id),
            exc_info=exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Failed to enqueue question regeneration job — please retry. "
                "If this persists, contact support."
            ),
        )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _question_to_response(q: StageQuestion) -> QuestionResponse:
    return QuestionResponse(
        id=q.id,
        bank_id=q.bank_id,
        position=q.position,
        source=q.source,  # type: ignore[arg-type]
        text=q.text,
        signal_values=list(q.signal_values),
        estimated_minutes=float(q.estimated_minutes),
        is_mandatory=q.is_mandatory,
        follow_ups=list(q.follow_ups),
        positive_evidence=list(q.positive_evidence),
        red_flags=list(q.red_flags),
        rubric=QuestionRubric(**q.rubric),
        evaluation_hint=q.evaluation_hint,
        edited_by_recruiter=q.edited_by_recruiter,
        question_kind=q.question_kind,
        primary_signal=q.primary_signal,
        difficulty=q.difficulty,
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


def _bank_to_response(
    bank: StageQuestionBank,
    *,
    question_count: int,
    total_minutes: float,
    is_stale: bool,
) -> BankResponse:
    return BankResponse(
        id=bank.id,
        stage_id=bank.stage_id,
        job_posting_id=bank.job_posting_id,
        signal_snapshot_id=bank.signal_snapshot_id,
        status=bank.status,  # type: ignore[arg-type]
        prompt_version=bank.prompt_version,
        generation_error=bank.generation_error,
        coverage_notes=bank.coverage_notes,
        generated_at=bank.generated_at,
        generated_by=bank.generated_by,
        confirmed_at=bank.confirmed_at,
        confirmed_by=bank.confirmed_by,
        question_count=question_count,
        total_minutes=total_minutes,
        is_stale=is_stale,
        created_at=bank.created_at,
        updated_at=bank.updated_at,
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/jobs/{job_id}/pipeline/questions",
    response_model=BanksOverviewResponse,
)
async def list_banks(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BanksOverviewResponse:
    """Lightweight list of all banks in the pipeline (sidebar data).

    MUST be read-idempotent. Previously this endpoint called
    `ensure_bank_exists` in a loop, which created a draft StageQuestionBank
    row for every stage on every GET. A single sidebar poll against an
    8-stage pipeline would write 8 rows — a violation of HTTP GET semantics
    and a slow leak into stage_question_banks on every tab refresh.

    Now: return a real BankResponse for every stage where a bank row
    already exists, and a synthetic PlaceholderBankResponse
    (`status = "not_generated"`) for every stage without one. The POST
    /questions/generate endpoint still calls ensure_bank_exists — that's
    the single legal write path.
    """
    instance, _job = await require_pipeline_access(db, job_id, user, "view")

    # Load every stage in position order. We render one entry per stage
    # regardless of whether a bank row exists.
    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stages_result.scalars().all())

    # Pull all existing bank rows for this pipeline in one round trip.
    rows = await get_banks_for_pipeline(db, instance)
    banks_by_stage: dict[UUID, tuple[StageQuestionBank, int, float, bool]] = {
        bank.stage_id: (bank, qc, tm, stale)
        for bank, qc, tm, stale in rows
    }

    banks: list[BankResponse | PlaceholderBankResponse] = []
    for stage in stages:
        if stage.stage_type not in STAGE_TYPE_TO_PROMPT:
            # intake / debrief — no question bank for these stage types.
            continue
        row = banks_by_stage.get(stage.id)
        if row is None:
            banks.append(PlaceholderBankResponse(stage_id=stage.id))
            continue
        bank, question_count, total_minutes, is_stale = row
        banks.append(
            _bank_to_response(
                bank,
                question_count=question_count,
                total_minutes=total_minutes,
                is_stale=is_stale,
            )
        )
    return BanksOverviewResponse(banks=banks)


@router.get(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions",
    response_model=BankWithQuestionsResponse,
)
async def get_bank(
    job_id: UUID,
    stage_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankWithQuestionsResponse:
    """Full bank detail including all questions for the main pane."""
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "view"
    )
    if stage.stage_type not in STAGE_TYPE_TO_PROMPT:
        raise HTTPException(
            status_code=409,
            detail="Stage type does not support question banks",
        )
    if bank is None:
        # Create an empty draft bank so the frontend can show "generate" button
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    questions = await get_bank_questions(db, bank.id)
    is_stale = bank.is_stale
    total_minutes = float(sum(q.estimated_minutes for q in questions))

    return BankWithQuestionsResponse(
        **_bank_to_response(
            bank,
            question_count=len(questions),
            total_minutes=total_minutes,
            is_stale=is_stale,
        ).model_dump(),
        questions=[_question_to_response(q) for q in questions],
    )


# ---------------------------------------------------------------------------
# Generation endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/generate",
    response_model=GenerateResponse,
    status_code=202,
)
async def generate_stage_questions(
    job_id: UUID,
    stage_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Trigger single-stage generation. Returns 202 with bank id."""
    correlation_id = _get_correlation_id(request)
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    # Guard: only the stage types in STAGE_TYPE_TO_PROMPT are eligible for
    # AI generation. Mirrors the same check `generate_all_questions` runs at
    # pipeline level — keeps the 400 error consistent regardless of entry
    # point and prevents the actor from raising a less-helpful runtime error
    # later when the prompt-file lookup fails.
    if stage.stage_type not in STAGE_TYPE_TO_PROMPT:
        raise HTTPException(
            400,
            detail=(
                f"Stage type '{stage.stage_type}' does not support AI question "
                "generation. Add questions manually."
            ),
        )
    if bank is None:
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    try:
        transition_to_generating(bank)
    except BankAlreadyGeneratingError as exc:
        raise HTTPException(409, detail=str(exc))
    bank.generated_by = user.user.id
    # Capture values BEFORE commit — after commit the request session has
    # app.current_tenant unset and attribute refreshes will fail RLS.
    bank_id = bank.id
    bank_tenant_id = bank.tenant_id
    await db.flush()
    await db.commit()

    await _safe_dispatch_generate_stage(
        bank_id=bank_id,
        tenant_id=bank_tenant_id,
        user_id=user.user.id,
        correlation_id=correlation_id,
    )
    return GenerateResponse(bank_id=bank_id, status="generating")


@router.post(
    "/jobs/{job_id}/pipeline/questions/generate-all",
    response_model=GenerateResponse,
    status_code=202,
)
async def generate_all_questions(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Trigger sequential generation for all stages in the pipeline."""
    correlation_id = _get_correlation_id(request)
    instance, job = await require_pipeline_access(db, job_id, user, "manage")

    # Acquire a row-level lock on the pipeline instance BEFORE the 409 check
    # so that two concurrent requests can't both observe status != 'generating'
    # and both dispatch — the second one blocks here until the first transaction
    # commits, after which it re-reads the (now 'generating') status and 409s.
    locked_result = await db.execute(
        select(JobPipelineInstance)
        .where(JobPipelineInstance.id == instance.id)
        .with_for_update()
    )
    locked_instance = locked_result.scalar_one()

    # Check no bank is currently generating
    existing_result = await db.execute(
        select(StageQuestionBank).where(
            StageQuestionBank.job_posting_id == job_id,
            StageQuestionBank.status == "generating",
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            409, detail="Another bank is currently generating in this pipeline"
        )

    # Pre-create banks for all eligible stages so that GET /questions returns
    # real bank rows immediately after this response — before the actor has had
    # a chance to call ensure_bank_exists itself.  Without this, there is a
    # 1–3 s window where GET returns no banks → anyBankGenerating=false →
    # the "Generate all" button appears active on a refresh.
    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == locked_instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages_list = list(stages_result.scalars().all())

    eligible_stages = [
        s for s in stages_list if s.stage_type in STAGE_TYPE_TO_PROMPT
    ]
    if not eligible_stages:
        raise HTTPException(
            400, detail="No generation-eligible stages in this pipeline"
        )

    # Idempotent — ensure_bank_exists returns the existing bank if it is already
    # present, or creates a fresh draft row.
    for stage in eligible_stages:
        await ensure_bank_exists(db, stage=stage, job=job)

    # Pre-mark the first eligible stage's bank as 'generating' so any GET
    # issued between now and the actor's first iteration already shows the
    # correct status.  The actor will see status='generating' for this stage
    # and must skip the transition (handled in actors.py).
    first_bank = await ensure_bank_exists(db, stage=eligible_stages[0], job=job)
    if first_bank.status != "generating":
        transition_to_generating(first_bank)
        first_bank.generated_by = user.user.id

    # Capture IDs BEFORE commit — after commit the request session has
    # app.current_tenant unset and further attribute access is unsafe.
    instance_id = locked_instance.id
    tenant_id = job.tenant_id
    await db.commit()
    await _safe_dispatch_generate_pipeline(
        instance_id=instance_id,
        tenant_id=tenant_id,
        user_id=user.user.id,
        correlation_id=correlation_id,
    )
    return GenerateResponse(bank_id=None, status="generating")


@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/regenerate",
    response_model=GenerateResponse,
    status_code=202,
)
async def regenerate_one_question(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    body: RegenerateQuestionBody,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Regenerate one question slot."""
    correlation_id = _get_correlation_id(request)
    question, bank, _stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    # Capture IDs BEFORE commit — after commit the request session has
    # app.current_tenant unset and further attribute access is unsafe.
    question_id_val = question.id
    bank_id = bank.id
    bank_tenant_id = bank.tenant_id
    await db.commit()

    await _safe_dispatch_regenerate_question(
        question_id=question_id_val,
        tenant_id=bank_tenant_id,
        user_id=user.user.id,
        replace_signal_values=body.replace_signal_values,
        correlation_id=correlation_id,
    )
    return GenerateResponse(bank_id=bank_id, status="generating")


# ---------------------------------------------------------------------------
# Mutation endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions",
    response_model=QuestionResponse,
    status_code=201,
)
async def create_question(
    job_id: UUID,
    stage_id: UUID,
    body: CreateQuestionBody,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> QuestionResponse:
    """Add a hand-written recruiter question to a bank."""
    correlation_id = _get_correlation_id(request)
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    snap_result = await db.execute(
        select(JobPostingSignalSnapshot).where(
            JobPostingSignalSnapshot.id == bank.signal_snapshot_id
        )
    )
    snapshot = snap_result.scalar_one()

    try:
        question = await create_recruiter_question(
            db,
            bank=bank,
            body=body,
            user_id=user.user.id,
            user_email=user.user.email,
            snapshot=snapshot,
            allowed_types=stage.signal_filter.get("include_types", []),
        )
    except SignalValueNotInSnapshotError as exc:
        raise HTTPException(400, detail=str(exc))
    except SignalTypeNotAllowedError as exc:
        raise HTTPException(400, detail=str(exc))

    # Capture IDs before commit — post-commit attribute access is unsafe under RLS.
    bank_id = bank.id
    stage_id_val = stage.id
    question_id = question.id

    await db.commit()

    background_tasks.add_task(
        pubsub.publish,
        pubsub.job_channel(job_id),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {
            "job_id": str(job_id),
            "bank_id": str(bank_id),
            "stage_id": str(stage_id_val),
            "question_id": str(question_id),
            "mutation": "create",
        },
        correlation_id=correlation_id,
    )
    return _question_to_response(question)


# NOTE: reorder_questions_endpoint is intentionally declared BEFORE
# patch_question so FastAPI registers the literal-path route first. Otherwise
# a PATCH to /reorder would try to parse "reorder" as a UUID for {question_id}
# and 422.
@router.patch(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/reorder",
    response_model=BankWithQuestionsResponse,
)
async def reorder_questions_endpoint(
    job_id: UUID,
    stage_id: UUID,
    body: ReorderBody,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankWithQuestionsResponse:
    """Reorder questions in a bank."""
    correlation_id = _get_correlation_id(request)
    bank, stage, _job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        raise HTTPException(404, detail="No bank for this stage")

    # ReorderMismatchError / ReorderDuplicateError are handled globally in
    # app/main.py as 400 responses. Letting them bubble keeps the message
    # specific ("contains duplicates" vs "must match the existing set").
    await reorder_questions(
        db,
        bank=bank,
        question_ids=body.question_ids,
        user_id=user.user.id,
        user_email=user.user.email,
    )

    # IMPORTANT: build the response BEFORE db.commit().
    #
    # get_tenant_db's `SET LOCAL app.current_tenant` is scoped to the outer
    # transaction. Once we commit, the session implicitly starts a new
    # transaction with app.current_tenant unset, and subsequent queries
    # return zero rows under RLS. The integration-test harness wraps each
    # test in a nested transaction that hides this — but production does
    # not, so the response would be populated with empty questions.
    questions = await get_bank_questions(db, bank.id)
    is_stale = bank.is_stale
    total_minutes = float(sum(q.estimated_minutes for q in questions))
    response = BankWithQuestionsResponse(
        **_bank_to_response(
            bank,
            question_count=len(questions),
            total_minutes=total_minutes,
            is_stale=is_stale,
        ).model_dump(),
        questions=[_question_to_response(q) for q in questions],
    )

    # Capture bank_id before commit — post-commit attribute access is unsafe under RLS.
    bank_id = bank.id

    await db.commit()

    background_tasks.add_task(
        pubsub.publish,
        pubsub.job_channel(job_id),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {
            "job_id": str(job_id),
            "bank_id": str(bank_id),
            "stage_id": str(stage_id),
            "question_id": None,
            "mutation": "reorder",
        },
        correlation_id=correlation_id,
    )
    return response


@router.patch(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
    response_model=QuestionResponse,
)
async def patch_question(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    body: UpdateQuestionBody,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> QuestionResponse:
    """Edit a question in place. Auto-reverts bank confirmed → reviewing."""
    correlation_id = _get_correlation_id(request)
    question, bank, stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    snap_result = await db.execute(
        select(JobPostingSignalSnapshot).where(
            JobPostingSignalSnapshot.id == bank.signal_snapshot_id
        )
    )
    snapshot = snap_result.scalar_one()

    try:
        updated = await update_question(
            db,
            question=question,
            bank=bank,
            body=body,
            user_id=user.user.id,
            user_email=user.user.email,
            snapshot=snapshot,
            allowed_types=stage.signal_filter.get("include_types", []),
        )
    except SignalValueNotInSnapshotError as exc:
        raise HTTPException(400, detail=str(exc))
    except SignalTypeNotAllowedError as exc:
        raise HTTPException(400, detail=str(exc))

    # Capture IDs before commit — post-commit attribute access is unsafe under RLS.
    bank_id = bank.id
    stage_id_val = stage.id
    question_id_val = updated.id

    await db.commit()

    background_tasks.add_task(
        pubsub.publish,
        pubsub.job_channel(job_id),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {
            "job_id": str(job_id),
            "bank_id": str(bank_id),
            "stage_id": str(stage_id_val),
            "question_id": str(question_id_val),
            "mutation": "update",
        },
        correlation_id=correlation_id,
    )
    return _question_to_response(updated)


@router.delete(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
    status_code=204,
)
async def delete_question_endpoint(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    """Delete a question and re-pack positions."""
    correlation_id = _get_correlation_id(request)
    question, bank, _stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )

    # Capture IDs before deletion — the question row will be gone after the call.
    bank_id = bank.id
    question_id_val = question.id

    await delete_question(
        db,
        question=question,
        bank=bank,
        user_id=user.user.id,
        user_email=user.user.email,
    )
    await db.commit()

    background_tasks.add_task(
        pubsub.publish,
        pubsub.job_channel(job_id),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {
            "job_id": str(job_id),
            "bank_id": str(bank_id),
            "stage_id": str(stage_id),
            "question_id": str(question_id_val),
            "mutation": "delete",
        },
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# State transition
# ---------------------------------------------------------------------------

@router.post(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/confirm",
    response_model=BankResponse,
)
async def confirm_bank_endpoint(
    job_id: UUID,
    stage_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankResponse:
    """Confirm a bank after running knockout + budget validators."""
    correlation_id = _get_correlation_id(request)
    bank, _stage, _job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        raise HTTPException(404, detail="No bank for this stage")

    try:
        await confirm_bank(
            db, bank=bank, user_id=user.user.id, user_email=user.user.email
        )
    except BankNotInReviewingError as exc:
        raise HTTPException(409, detail=str(exc))
    except KnockoutUnprobedError as exc:
        raise HTTPException(
            409,
            detail=(
                f"Cannot confirm: knockout signal '{exc.signal_value}' has "
                f"no mandatory question"
            ),
        )
    except MandatoryOverrunError as exc:
        raise HTTPException(409, detail=str(exc))

    # IMPORTANT: build the response BEFORE db.commit() — same reasoning as
    # reorder_questions_endpoint. Post-commit queries on the tenant session
    # run with app.current_tenant unset and RLS returns zero rows.
    questions = await get_bank_questions(db, bank.id)
    is_stale = bank.is_stale
    total_minutes = float(sum(q.estimated_minutes for q in questions))
    response = _bank_to_response(
        bank,
        question_count=len(questions),
        total_minutes=total_minutes,
        is_stale=is_stale,
    )

    # Capture IDs and new status before commit.
    bank_id = bank.id
    new_status = bank.status

    await db.commit()

    background_tasks.add_task(
        pubsub.publish,
        pubsub.job_channel(job_id),
        pubsub.Events.BANK_STATUS_CHANGED,
        {
            "job_id": str(job_id),
            "bank_id": str(bank_id),
            "stage_id": str(stage_id),
            "new_status": new_status,
        },
        correlation_id=correlation_id,
    )
    return response


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/pipeline/questions/status-stream")
async def questions_status_stream(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> StreamingResponse:
    """SSE stream of bank status + question update events.

    `request` is injected so the generator can call `is_disconnected()`
    each poll iteration and bail out when the browser tab closes — without
    this, orphaned streams hold DB connections until the 10-minute idle
    timeout and can exhaust the pool under concurrency.
    """
    _instance, job = await require_pipeline_access(db, job_id, user, "view")
    return StreamingResponse(
        stream_question_bank_status(
            request=request,
            tenant_id=job.tenant_id,
            job_id=job_id,
        ),
        media_type="text/event-stream",
    )
