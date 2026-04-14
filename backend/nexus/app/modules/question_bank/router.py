"""Question bank HTTP endpoints.

11 endpoints under /api/jobs/{job_id}/pipeline/... covering CRUD, generation
triggers, bank confirmation, and the SSE status stream.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import (
    JobPipelineInstance,  # noqa: F401  (return type of require_pipeline_access)
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.question_bank import actors as bank_actors
from app.modules.question_bank.authz import (
    require_bank_access,  # noqa: F401  (plan-mandated import; not used in router body)
    require_bank_access_by_stage,
    require_pipeline_access,
    require_question_access,
)
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError,
    BankNotInReviewingError,
    DurationBudgetOutOfRangeError,
    KnockoutUnprobedError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    BankResponse,
    BanksOverviewResponse,
    BankWithQuestionsResponse,
    CreateQuestionBody,
    GenerateResponse,
    QuestionResponse,
    QuestionRubric,
    RegenerateQuestionBody,
    ReorderBody,
    UpdateQuestionBody,
)
from app.modules.question_bank.service import (
    compute_is_stale,
    confirm_bank,
    create_recruiter_question,
    delete_question,
    ensure_bank_exists,
    get_bank_questions,
    get_banks_for_pipeline,
    get_latest_confirmed_snapshot,  # noqa: F401  (plan-mandated import; not used in router body)
    reorder_questions,
    transition_to_generating,
    update_question,
)
from app.modules.question_bank.sse import stream_question_bank_status

router = APIRouter(prefix="/api", tags=["question_bank"])


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
    """Lightweight list of all banks in the pipeline (sidebar data)."""
    instance, _job = await require_pipeline_access(db, job_id, user, "view")

    # Ensure every stage has a bank row (draft if missing)
    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stages_result.scalars().all())
    job_result = await db.execute(
        select(JobPosting).where(JobPosting.id == instance.job_posting_id)
    )
    job = job_result.scalar_one()

    for stage in stages:
        await ensure_bank_exists(db, stage=stage, job=job)
    await db.flush()

    rows = await get_banks_for_pipeline(db, instance)
    banks = [
        _bank_to_response(
            bank,
            question_count=question_count,
            total_minutes=total_minutes,
            is_stale=is_stale,
        )
        for bank, question_count, total_minutes, is_stale in rows
    ]
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
    if bank is None:
        # Create an empty draft bank so the frontend can show "generate" button
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    questions = await get_bank_questions(db, bank.id)
    is_stale = await compute_is_stale(db, bank)
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
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Trigger single-stage generation. Returns 202 with bank id."""
    bank, stage, job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        bank = await ensure_bank_exists(db, stage=stage, job=job)

    try:
        transition_to_generating(bank)
    except BankAlreadyGeneratingError as exc:
        raise HTTPException(409, detail=str(exc))
    bank.generated_by = user.user.id
    await db.flush()
    await db.commit()

    bank_actors.generate_question_bank_stage.send(
        str(bank.id), str(bank.tenant_id), str(user.user.id)
    )
    return GenerateResponse(bank_id=bank.id, status="generating")


@router.post(
    "/jobs/{job_id}/pipeline/questions/generate-all",
    response_model=GenerateResponse,
    status_code=202,
)
async def generate_all_questions(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Trigger sequential generation for all stages in the pipeline."""
    instance, job = await require_pipeline_access(db, job_id, user, "manage")

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

    await db.commit()
    bank_actors.generate_question_bank_pipeline.send(
        str(instance.id), str(job.tenant_id), str(user.user.id)
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
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> GenerateResponse:
    """Regenerate one question slot."""
    question, bank, _stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    await db.commit()

    bank_actors.regenerate_question.send(
        str(question.id),
        str(bank.tenant_id),
        str(user.user.id),
        body.replace_signal_values,
    )
    return GenerateResponse(bank_id=bank.id, status="generating")


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
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> QuestionResponse:
    """Add a hand-written recruiter question to a bank."""
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

    await db.commit()
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
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankWithQuestionsResponse:
    """Reorder questions in a bank."""
    bank, stage, _job = await require_bank_access_by_stage(
        db, job_id, stage_id, user, "manage"
    )
    if bank is None:
        raise HTTPException(404, detail="No bank for this stage")

    try:
        await reorder_questions(
            db,
            bank=bank,
            question_ids=body.question_ids,
            user_id=user.user.id,
            user_email=user.user.email,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

    await db.commit()

    questions = await get_bank_questions(db, bank.id)
    is_stale = await compute_is_stale(db, bank)
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


@router.patch(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
    response_model=QuestionResponse,
)
async def patch_question(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    body: UpdateQuestionBody,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> QuestionResponse:
    """Edit a question in place. Auto-reverts bank confirmed → reviewing."""
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

    await db.commit()
    return _question_to_response(updated)


@router.delete(
    "/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
    status_code=204,
)
async def delete_question_endpoint(
    job_id: UUID,
    stage_id: UUID,
    question_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    """Delete a question and re-pack positions."""
    question, bank, _stage, _job = await require_question_access(
        db, question_id, user, "manage"
    )
    await delete_question(
        db,
        question=question,
        bank=bank,
        user_id=user.user.id,
        user_email=user.user.email,
    )
    await db.commit()


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
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> BankResponse:
    """Confirm a bank after running knockout + budget validators."""
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
    except DurationBudgetOutOfRangeError as exc:
        raise HTTPException(409, detail=str(exc))

    await db.commit()

    questions = await get_bank_questions(db, bank.id)
    is_stale = await compute_is_stale(db, bank)
    total_minutes = float(sum(q.estimated_minutes for q in questions))
    return _bank_to_response(
        bank,
        question_count=len(questions),
        total_minutes=total_minutes,
        is_stale=is_stale,
    )


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/pipeline/questions/status-stream")
async def questions_status_stream(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> StreamingResponse:
    """SSE stream of bank status + question update events."""
    _instance, job = await require_pipeline_access(db, job_id, user, "view")
    return StreamingResponse(
        stream_question_bank_status(tenant_id=job.tenant_id, job_id=job_id),
        media_type="text/event-stream",
    )
