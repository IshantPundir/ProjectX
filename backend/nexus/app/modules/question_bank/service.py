"""Question bank service layer.

Bank lifecycle, question CRUD, coverage/budget validators, and post-LLM
validation checks. All mutations call auto_revert_on_edit to keep the bank
status in sync after recruiter-side changes.

Audit logging: every state transition and every recruiter mutation calls
log_event so EEOC audits can trace who did what when.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.audit.service import log_event
from app.modules.question_bank.errors import (
    DurationBudgetOutOfRangeError,
    KnockoutUnprobedError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    GeneratedQuestion,
    QuestionRubric,
    UpdateQuestionBody,
)
from app.modules.question_bank.state_machine import (
    auto_revert_on_edit,
    transition_to_confirmed,
    transition_to_failed,
    transition_to_generating,
    transition_to_reviewing_after_generation,
)

logger = structlog.get_logger()


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

async def get_latest_confirmed_snapshot(
    db: AsyncSession, job_id: UUID
) -> JobPostingSignalSnapshot | None:
    """Latest confirmed signal snapshot for a job, or None if none confirmed.

    Query: ORDER BY version DESC WHERE confirmed_at IS NOT NULL LIMIT 1.
    """
    result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(
            JobPostingSignalSnapshot.job_posting_id == job_id,
            JobPostingSignalSnapshot.confirmed_at.is_not(None),
        )
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    return result.scalar_one_or_none()


def _signal_by_value(
    snapshot: JobPostingSignalSnapshot, value: str
) -> dict[str, Any] | None:
    """Find a signal dict by its `value` field inside the snapshot's signals JSONB array."""
    for signal in snapshot.signals:
        if signal.get("value") == value:
            return signal
    return None


# ---------------------------------------------------------------------------
# Bank CRUD
# ---------------------------------------------------------------------------

async def ensure_bank_exists(
    db: AsyncSession,
    *,
    stage: JobPipelineStage,
    job: JobPosting,
) -> StageQuestionBank:
    """Get or create the bank for a given stage. Creates in 'draft' state
    pinned to the latest confirmed signal snapshot."""
    result = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
    )
    bank = result.scalar_one_or_none()
    if bank is not None:
        return bank

    snapshot = await get_latest_confirmed_snapshot(db, job.id)
    if snapshot is None:
        raise RuntimeError(
            f"Cannot create bank for job {job.id}: no confirmed signal snapshot exists. "
            "Generation should not be triggered until signals are confirmed."
        )

    bank = StageQuestionBank(
        tenant_id=job.tenant_id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="draft",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()
    logger.info(
        "question_bank.bank_created",
        bank_id=str(bank.id),
        stage_id=str(stage.id),
        job_id=str(job.id),
        snapshot_id=str(snapshot.id),
    )
    return bank


async def get_bank_questions(
    db: AsyncSession, bank_id: UUID
) -> list[StageQuestion]:
    """Load all questions in a bank, ordered by position."""
    result = await db.execute(
        select(StageQuestion)
        .where(StageQuestion.bank_id == bank_id)
        .order_by(StageQuestion.position)
    )
    return list(result.scalars().all())


async def compute_is_stale(
    db: AsyncSession, bank: StageQuestionBank
) -> bool:
    """True if the bank's pinned snapshot is not the job's latest confirmed one."""
    latest = await get_latest_confirmed_snapshot(db, bank.job_posting_id)
    if latest is None:
        return False  # no confirmed snapshot; bank can't be stale
    return bank.signal_snapshot_id != latest.id


async def get_banks_for_pipeline(
    db: AsyncSession, instance: JobPipelineInstance
) -> list[tuple[StageQuestionBank, int, float, bool]]:
    """Return (bank, question_count, total_minutes, is_stale) tuples for every
    bank in the pipeline, ordered by stage position. Missing banks are NOT
    included — caller is expected to handle 'no bank yet' states separately.
    """
    # Load stages in position order
    stage_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stage_result.scalars().all())

    # Cache latest confirmed snapshot once for staleness
    latest = await get_latest_confirmed_snapshot(db, instance.job_posting_id)
    latest_id = latest.id if latest else None

    out: list[tuple[StageQuestionBank, int, float, bool]] = []
    for stage in stages:
        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
        )
        bank = bank_result.scalar_one_or_none()
        if bank is None:
            continue

        q_result = await db.execute(
            select(StageQuestion).where(StageQuestion.bank_id == bank.id)
        )
        questions = list(q_result.scalars().all())
        question_count = len(questions)
        total_minutes = float(sum(q.estimated_minutes for q in questions))
        is_stale = latest_id is not None and bank.signal_snapshot_id != latest_id
        out.append((bank, question_count, total_minutes, is_stale))
    return out


# ---------------------------------------------------------------------------
# Validators (used at confirm time and by LLM post-validation)
# ---------------------------------------------------------------------------

async def validate_knockout_coverage(
    db: AsyncSession,
    bank: StageQuestionBank,
) -> None:
    """Raise KnockoutUnprobedError if any knockout signal lacks a mandatory question.

    Knockouts are determined by loading the pinned snapshot and checking the
    stage's signal_filter.include_types (only knockouts of matching type count
    — a behavioral knockout doesn't need to be covered in an ai_interview stage).
    """
    snapshot_result = await db.execute(
        select(JobPostingSignalSnapshot).where(
            JobPostingSignalSnapshot.id == bank.signal_snapshot_id
        )
    )
    snapshot = snapshot_result.scalar_one_or_none()
    if snapshot is None:
        raise RuntimeError(f"Pinned snapshot {bank.signal_snapshot_id} missing")

    stage_result = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
    )
    stage = stage_result.scalar_one()
    allowed_types = stage.signal_filter.get("include_types", [])

    questions = await get_bank_questions(db, bank.id)
    # Build set of signal values covered by mandatory questions
    mandatory_values: set[str] = set()
    for q in questions:
        if q.is_mandatory:
            mandatory_values.update(q.signal_values)

    for signal in snapshot.signals:
        if not signal.get("knockout", False):
            continue
        if signal.get("type") not in allowed_types:
            continue
        if signal["value"] not in mandatory_values:
            raise KnockoutUnprobedError(
                signal_value=signal["value"], bank_id=bank.id
            )


async def validate_duration_budget(
    db: AsyncSession,
    bank: StageQuestionBank,
) -> None:
    """Raise DurationBudgetOutOfRangeError if sum outside 50–150% of stage duration."""
    stage_result = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
    )
    stage = stage_result.scalar_one()

    questions = await get_bank_questions(db, bank.id)
    total = float(sum(q.estimated_minutes for q in questions))
    min_allowed = stage.duration_minutes * 0.5
    max_allowed = stage.duration_minutes * 1.5

    if total < min_allowed or total > max_allowed:
        raise DurationBudgetOutOfRangeError(
            bank_id=bank.id,
            total_minutes=total,
            stage_minutes=stage.duration_minutes,
        )


async def validate_llm_output_against_snapshot(
    db: AsyncSession,
    *,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
    questions: list[GeneratedQuestion],
) -> list[GeneratedQuestion]:
    """Run post-LLM validation checks. Returns the (possibly auto-corrected) list.

    - signal_values must all exist in the snapshot → SignalValueNotInSnapshotError
    - signal types must be in allowed_types → SignalTypeNotAllowedError
    - knockout signals → is_mandatory auto-corrected to True (warning logged)
    """
    snapshot_by_value = {s["value"]: s for s in snapshot.signals}

    for q in questions:
        for value in q.signal_values:
            if value not in snapshot_by_value:
                raise SignalValueNotInSnapshotError(
                    signal_value=value, snapshot_id=snapshot.id
                )
            signal = snapshot_by_value[value]
            if signal["type"] not in allowed_types:
                raise SignalTypeNotAllowedError(
                    signal_value=value,
                    signal_type=signal["type"],
                    allowed_types=allowed_types,
                )

        # Auto-correct is_mandatory for knockouts
        probes_knockout = any(
            snapshot_by_value[v].get("knockout", False) for v in q.signal_values
        )
        if probes_knockout and not q.is_mandatory:
            logger.warning(
                "question_bank.auto_corrected_mandatory",
                signal_values=q.signal_values,
                reason="knockout_signal_without_mandatory",
            )
            q.is_mandatory = True
    return questions


# ---------------------------------------------------------------------------
# Write questions (used by actors after LLM success)
# ---------------------------------------------------------------------------

async def write_generated_questions(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    questions: list[GeneratedQuestion],
    source: str = "ai_generated",
) -> None:
    """Delete existing AI-sourced questions, keep recruiter-sourced ones,
    write the new generated questions. Called by the generate actors.
    """
    # Delete all AI-sourced questions (ai_generated + ai_regenerated)
    await db.execute(
        delete(StageQuestion).where(
            StageQuestion.bank_id == bank.id,
            StageQuestion.source.in_(["ai_generated", "ai_regenerated"]),
        )
    )
    await db.flush()

    # Find the max position among remaining (recruiter) questions so new ones
    # slot in after them — simpler than re-packing
    existing = await get_bank_questions(db, bank.id)
    existing_max_pos = max((q.position for q in existing), default=-1)
    offset = existing_max_pos + 1

    for incoming in questions:
        db.add(
            StageQuestion(
                tenant_id=bank.tenant_id,
                bank_id=bank.id,
                position=offset + incoming.position,
                source=source,
                text=incoming.text,
                signal_values=list(incoming.signal_values),
                estimated_minutes=incoming.estimated_minutes,
                is_mandatory=incoming.is_mandatory,
                follow_ups=list(incoming.follow_ups),
                positive_evidence=list(incoming.positive_evidence),
                red_flags=list(incoming.red_flags),
                rubric=incoming.rubric.model_dump(),
                evaluation_hint=incoming.evaluation_hint,
            )
        )
    await db.flush()

    # Re-pack positions to 0..N-1 so the final ordering is clean
    final = await get_bank_questions(db, bank.id)
    for i, q in enumerate(final):
        q.position = i
    await db.flush()


async def replace_question_in_place(
    db: AsyncSession,
    *,
    question: StageQuestion,
    new_data: GeneratedQuestion,
) -> None:
    """Update an existing question row with new LLM-generated data. Preserves id."""
    question.text = new_data.text
    question.signal_values = list(new_data.signal_values)
    question.estimated_minutes = new_data.estimated_minutes
    question.is_mandatory = new_data.is_mandatory
    question.follow_ups = list(new_data.follow_ups)
    question.positive_evidence = list(new_data.positive_evidence)
    question.red_flags = list(new_data.red_flags)
    question.rubric = new_data.rubric.model_dump()
    question.evaluation_hint = new_data.evaluation_hint
    question.source = "ai_regenerated"
    question.edited_by_recruiter = False
    question.updated_at = _now_utc()
    await db.flush()


# ---------------------------------------------------------------------------
# Recruiter mutations
# ---------------------------------------------------------------------------

async def create_recruiter_question(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    body: CreateQuestionBody,
    user_id: UUID,
    user_email: str | None,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
) -> StageQuestion:
    """Add a hand-written question. source='recruiter'."""
    # Validate signals exist + types allowed
    for value in body.signal_values:
        signal = _signal_by_value(snapshot, value)
        if signal is None:
            raise SignalValueNotInSnapshotError(
                signal_value=value, snapshot_id=snapshot.id
            )
        if signal["type"] not in allowed_types:
            raise SignalTypeNotAllowedError(
                signal_value=value,
                signal_type=signal["type"],
                allowed_types=allowed_types,
            )

    # Determine position
    existing = await get_bank_questions(db, bank.id)
    if body.position is None:
        position = len(existing)
    else:
        position = min(body.position, len(existing))
        # Shift existing questions down
        for q in existing:
            if q.position >= position:
                q.position += 1

    question = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=position,
        source="recruiter",
        text=body.text,
        signal_values=list(body.signal_values),
        estimated_minutes=body.estimated_minutes,
        is_mandatory=body.is_mandatory,
        follow_ups=list(body.follow_ups),
        positive_evidence=list(body.positive_evidence),
        red_flags=list(body.red_flags),
        rubric=body.rubric.model_dump(),
        evaluation_hint=body.evaluation_hint,
        edited_by_recruiter=False,
    )
    db.add(question)
    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.recruiter_question_created",
        resource="stage_question",
        resource_id=question.id,
        payload={"bank_id": str(bank.id), "position": position},
    )
    return question


async def update_question(
    db: AsyncSession,
    *,
    question: StageQuestion,
    bank: StageQuestionBank,
    body: UpdateQuestionBody,
    user_id: UUID,
    user_email: str | None,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
) -> StageQuestion:
    """Partial update of a question. Validates signal_values if provided."""
    data = body.model_dump(exclude_unset=True)

    if "signal_values" in data:
        for value in data["signal_values"]:
            signal = _signal_by_value(snapshot, value)
            if signal is None:
                raise SignalValueNotInSnapshotError(
                    signal_value=value, snapshot_id=snapshot.id
                )
            if signal["type"] not in allowed_types:
                raise SignalTypeNotAllowedError(
                    signal_value=value,
                    signal_type=signal["type"],
                    allowed_types=allowed_types,
                )

    # Handle position changes separately (may need to shift others)
    new_position = data.pop("position", None)

    # Apply simple scalar + list updates
    for key, value in data.items():
        if key == "rubric" and value is not None:
            question.rubric = QuestionRubric(**value).model_dump()
        else:
            setattr(question, key, value)

    if new_position is not None and new_position != question.position:
        await _move_question_to_position(db, bank.id, question, new_position)

    question.edited_by_recruiter = True
    question.updated_at = _now_utc()
    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.question_edited",
        resource="stage_question",
        resource_id=question.id,
        payload={"bank_id": str(bank.id), "fields": list(data.keys())},
    )
    return question


async def _move_question_to_position(
    db: AsyncSession,
    bank_id: UUID,
    question: StageQuestion,
    new_position: int,
) -> None:
    """Move a question to a new position, re-packing the rest to 0..N-1."""
    siblings = await get_bank_questions(db, bank_id)
    siblings = [q for q in siblings if q.id != question.id]
    new_position = max(0, min(new_position, len(siblings)))
    siblings.insert(new_position, question)
    for i, q in enumerate(siblings):
        q.position = i
    await db.flush()


async def delete_question(
    db: AsyncSession,
    *,
    question: StageQuestion,
    bank: StageQuestionBank,
    user_id: UUID,
    user_email: str | None,
) -> None:
    """Delete a question and re-pack remaining positions."""
    await db.delete(question)
    await db.flush()

    # Re-pack
    remaining = await get_bank_questions(db, bank.id)
    for i, q in enumerate(remaining):
        q.position = i

    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.question_deleted",
        resource="stage_question",
        resource_id=question.id,
        payload={"bank_id": str(bank.id)},
    )


async def reorder_questions(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    question_ids: list[UUID],
    user_id: UUID,
    user_email: str | None,
) -> None:
    """Set positions 0..N-1 from the given order. Validates the set matches."""
    existing = await get_bank_questions(db, bank.id)
    existing_ids = {q.id for q in existing}
    incoming_ids = set(question_ids)

    if existing_ids != incoming_ids:
        raise ValueError(
            "Reorder list must contain exactly the existing question IDs"
        )
    if len(question_ids) != len(incoming_ids):
        raise ValueError("Reorder list contains duplicates")

    by_id = {q.id: q for q in existing}
    for i, qid in enumerate(question_ids):
        by_id[qid].position = i
    auto_revert_on_edit(bank)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.questions_reordered",
        resource="stage_question_bank",
        resource_id=bank.id,
    )


async def confirm_bank(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    user_id: UUID,
    user_email: str | None,
) -> StageQuestionBank:
    """Transition bank to 'confirmed' after running all validators."""
    await validate_knockout_coverage(db, bank)
    await validate_duration_budget(db, bank)
    transition_to_confirmed(bank, user_id=user_id)
    await db.flush()

    await log_event(
        db,
        tenant_id=bank.tenant_id,
        actor_id=user_id,
        actor_email=user_email,
        action="question_bank.bank_confirmed",
        resource="stage_question_bank",
        resource_id=bank.id,
    )
    return bank


# Re-export state transitions for convenience
__all__ = [
    "ensure_bank_exists",
    "get_bank_questions",
    "get_banks_for_pipeline",
    "compute_is_stale",
    "get_latest_confirmed_snapshot",
    "validate_knockout_coverage",
    "validate_duration_budget",
    "validate_llm_output_against_snapshot",
    "write_generated_questions",
    "replace_question_in_place",
    "create_recruiter_question",
    "update_question",
    "delete_question",
    "reorder_questions",
    "confirm_bank",
    "transition_to_generating",
    "transition_to_reviewing_after_generation",
    "transition_to_failed",
]
