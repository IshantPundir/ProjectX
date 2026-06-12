"""Question bank service layer.

Bank lifecycle, question CRUD, coverage/budget validators, and post-LLM
validation checks. All mutations call auto_revert_on_edit to keep the bank
status in sync after recruiter-side changes.

Audit logging: every state transition and every recruiter mutation calls
log_event so EEOC audits can trace who did what when.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import log_event
from app.modules.jd import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.question_bank.errors import (
    BudgetExceededError,
    KnockoutUnprobedError,
    MandatoryOverrunError,
    ReorderDuplicateError,
    ReorderMismatchError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    GeneratedQuestion,
    QuestionRubric,
    UpdateQuestionBody,
    followups_to_jsonb,
)
from app.modules.question_bank.state_machine import (
    auto_revert_on_edit,
    transition_to_confirmed,
    transition_to_failed,
    transition_to_generating,
    transition_to_self_reviewing,
    transition_to_reviewing_after_critic,
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


async def _latest_confirmed_signal_snapshot_id_for_bank(
    db: AsyncSession, bank: StageQuestionBank
) -> "UUID | None":
    """Return the id of the latest confirmed signal snapshot for the bank's job.

    Returns None when no confirmed snapshot exists (bank cannot be stale).
    """
    latest = await get_latest_confirmed_snapshot(db, bank.job_posting_id)
    if latest is None:
        return None
    return latest.id


async def recompute_and_persist_stale(
    db: AsyncSession,
    bank: StageQuestionBank,
    *,
    current_stage_config: dict | None = None,
) -> bool:
    """Recompute is_stale and persist to the column. Returns the new value.

    Predicate: stale if the latest confirmed signal_snapshot id differs from
    the bank's pinned snapshot, OR the stage's signal_filter/difficulty
    differs from stage_config_snapshot.

    Per spec §11.5: when a confirmed bank goes stale, it drops back to
    'generated' state (clears confirmed_at/confirmed_by).
    """
    latest_snapshot_id = await _latest_confirmed_signal_snapshot_id_for_bank(db, bank)
    signal_drift = (
        latest_snapshot_id is not None
        and bank.signal_snapshot_id != latest_snapshot_id
    )

    config_drift = False
    if current_stage_config is not None and bank.stage_config_snapshot is not None:
        for key in ("signal_filter", "difficulty"):
            if current_stage_config.get(key) != bank.stage_config_snapshot.get(key):
                config_drift = True
                break

    new_stale = signal_drift or config_drift

    if new_stale and bank.status == "confirmed":
        # Per spec §11.5: a stale confirmed bank drops back to the
        # post-generation state ('reviewing') so the recruiter is
        # prompted to re-review before re-confirming.
        bank.status = "reviewing"
        bank.confirmed_at = None
        bank.confirmed_by = None

    bank.is_stale = new_stale
    await db.flush()
    return new_stale


async def compute_is_stale(
    db: AsyncSession, bank: StageQuestionBank
) -> bool:
    """Backward-compat shim: returns the persisted bank.is_stale column.

    Tests that previously created a bank, edited a signal, then called
    compute_is_stale will now need to call recompute_and_persist_stale on the
    write side first. Update those tests if they fail.

    New code should NOT call this; read bank.is_stale directly.
    """
    return bank.is_stale


async def get_banks_for_pipeline(
    db: AsyncSession, instance: JobPipelineInstance
) -> list[tuple[StageQuestionBank, int, float, bool]]:
    """Return (bank, question_count, total_minutes, is_stale) tuples for every
    bank in the pipeline, ordered by stage position. Missing banks are NOT
    included — caller is expected to handle 'no bank yet' states separately.

    Query shape: 4 statements total (stages + banks + questions + latest
    snapshot), regardless of pipeline size. Previously this was a 1 + 2N
    loop that fired two extra SELECTs per stage on every pipeline overview
    load — painful under concurrency for a pipeline with 8 stages
    (1 + 16 = 17 queries per page load). (B7 fix.)
    """
    # 1. Load stages in position order.
    stage_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stage_result.scalars().all())
    if not stages:
        return []

    stage_ids = [s.id for s in stages]

    # 2. Bulk-load all banks for these stages in one statement.
    bank_result = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id.in_(stage_ids))
    )
    banks_by_stage: dict[UUID, StageQuestionBank] = {
        b.stage_id: b for b in bank_result.scalars().all()
    }

    bank_ids = [b.id for b in banks_by_stage.values()]

    # 3. Bulk-load all questions for those banks in one statement.
    questions_by_bank: dict[UUID, list[StageQuestion]] = {}
    if bank_ids:
        q_result = await db.execute(
            select(StageQuestion).where(StageQuestion.bank_id.in_(bank_ids))
        )
        for q in q_result.scalars().all():
            questions_by_bank.setdefault(q.bank_id, []).append(q)

    out: list[tuple[StageQuestionBank, int, float, bool]] = []
    for stage in stages:
        bank = banks_by_stage.get(stage.id)
        if bank is None:
            continue
        questions = questions_by_bank.get(bank.id, [])
        question_count = len(questions)
        total_minutes = float(sum(q.estimated_minutes for q in questions))
        # Read persisted column directly — no recompute on read path (§11.5).
        is_stale = bank.is_stale
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


async def validate_mandatory_fits_session(
    db: AsyncSession,
    bank: StageQuestionBank,
) -> None:
    """Raise MandatoryOverrunError if mandatory question minutes exceed stage duration.

    Only mandatory questions are time-budgeted. Optional depth probes may exceed
    the stage duration in aggregate — the session bot skips them when the clock
    runs out. But the bot cannot skip mandatory questions, so mandatory total
    MUST fit within the stage's session time limit.
    """
    stage_result = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == bank.stage_id)
    )
    stage = stage_result.scalar_one()

    questions = await get_bank_questions(db, bank.id)
    mandatory_total = float(
        sum(q.estimated_minutes for q in questions if q.is_mandatory)
    )

    if mandatory_total > stage.duration_minutes:
        raise MandatoryOverrunError(
            bank_id=bank.id,
            mandatory_minutes=mandatory_total,
            stage_minutes=stage.duration_minutes,
        )


def _validate_budget_against_stage(
    *,
    questions: list[GeneratedQuestion],
    duration_minutes: int,
    margin_min: int,
) -> None:
    """Generation-time budget enforcement.

    Two hard caps, both server-enforced regardless of what the LLM was asked
    to do (the system prompt already told it the rules; this is the gate):

      - mandatory_total ≤ duration_minutes
      - mandatory_total + optional_total ≤ duration_minutes + margin_min

    Raises ``BudgetExceededError`` on violation. The actor catches this and
    feeds the violation back into the LLM context for one retry pass before
    failing the bank.
    """
    mandatory_total = sum(
        float(q.estimated_minutes) for q in questions if q.is_mandatory
    )
    if mandatory_total > duration_minutes:
        raise BudgetExceededError(
            kind="mandatory",
            observed_minutes=mandatory_total,
            cap_minutes=float(duration_minutes),
            duration_minutes=duration_minutes,
            margin_min=margin_min,
        )

    total = sum(float(q.estimated_minutes) for q in questions)
    cap = duration_minutes + margin_min
    if total > cap:
        raise BudgetExceededError(
            kind="total",
            observed_minutes=total,
            cap_minutes=float(cap),
            duration_minutes=duration_minutes,
            margin_min=margin_min,
        )


def _apply_mandatory_correction_in_position_order(
    *,
    questions: list[GeneratedQuestion],
    knockout_values: set[str],
) -> None:
    """Auto-correct is_mandatory in position order.

    For each knockout signal, the earliest question (by position) probing
    it claims the mandatory slot; later questions probing the same
    knockout are demoted to optional. Mutates `questions` in place.

    This is the post-merge pass for two-call generation — once behavioral
    and technical questions are concatenated in their final position order,
    this single pass enforces the "exactly one mandatory probe per knockout"
    rule across both kinds.
    """
    knockouts_covered: set[str] = set()
    for q in sorted(questions, key=lambda x: x.position):
        knockouts_in_q = set(q.signal_values) & knockout_values
        if not knockouts_in_q:
            # Non-knockout question — leave is_mandatory as-is (trust the LLM)
            continue

        unclaimed = knockouts_in_q - knockouts_covered
        if unclaimed:
            # Earliest question covering these knockouts — must be mandatory
            if not q.is_mandatory:
                logger.warning(
                    "question_bank.upgraded_to_mandatory",
                    signal_values=q.signal_values,
                    reason="earliest_knockout_question_must_be_mandatory",
                )
                q.is_mandatory = True
            knockouts_covered.update(unclaimed)
        else:
            # All knockouts in this question are already covered by earlier
            # mandatory questions — demote this one to optional depth probe
            if q.is_mandatory:
                logger.info(
                    "question_bank.demoted_to_optional",
                    signal_values=q.signal_values,
                    reason="duplicate_knockout_coverage",
                )
                q.is_mandatory = False


def validate_streamed_question(
    question: GeneratedQuestion,
    *,
    snapshot_signals: list[dict],
    snapshot_id,
    allowed_types: list[str],
) -> None:
    """Validate ONE streamed/regenerated question. Raises on a hallucinated signal
    or a bad primary_signal.

    Takes PRIMITIVES (snapshot_signals + snapshot_id + allowed_types) rather than an
    ORM snapshot so the streaming caller can validate each question AFTER closing its
    short read session (decision D6 — no session is held across the LLM stream).

    Checks:
      - every signal_value must exist in snapshot_signals and be an allowed type
      - primary_signal must be one of signal_values — this is the ONLY place that
        invariant is enforced (decision D5; GeneratedQuestion carries no validator).

    The streaming caller SKIPS a question that raises here; budget reconciliation and
    mandatory auto-correction are post-stream passes, not per-question.
    """
    snapshot_by_value = {s["value"]: s for s in snapshot_signals}
    for value in question.signal_values:
        if value not in snapshot_by_value:
            raise SignalValueNotInSnapshotError(
                signal_value=value, snapshot_id=snapshot_id
            )
        if snapshot_by_value[value]["type"] not in allowed_types:
            raise SignalTypeNotAllowedError(
                signal_value=value,
                signal_type=snapshot_by_value[value]["type"],
                allowed_types=allowed_types,
            )
    if question.primary_signal not in question.signal_values:
        raise SignalValueNotInSnapshotError(
            signal_value=question.primary_signal, snapshot_id=snapshot_id
        )


async def validate_llm_output_against_snapshot(
    db: AsyncSession,
    *,
    snapshot: JobPostingSignalSnapshot,
    allowed_types: list[str],
    questions: list[GeneratedQuestion],
    stage: JobPipelineStage | None = None,
    optional_budget_margin_min: int = 5,
    apply_mandatory_correction: bool = True,
    budget_minutes_override: int | None = None,
) -> list[GeneratedQuestion]:
    """Run post-LLM validation checks. Returns the (possibly auto-corrected) list.

    - signal_values must all exist in the snapshot → SignalValueNotInSnapshotError
    - signal types must be in allowed_types → SignalTypeNotAllowedError
    - Budget caps (when ``stage`` is provided): mandatory_total ≤ duration,
      total ≤ duration + ``optional_budget_margin_min`` → BudgetExceededError
    - Mandatory knockout auto-correction: for each knockout signal, the EARLIEST
      question probing it (by position) must be mandatory. Subsequent questions
      probing the same knockout signal are auto-demoted to optional (depth probes).
      This guarantees exactly one mandatory verification per knockout; the rest
      become session-bot-adaptive optional probes.

    The budget check runs AFTER signal validation (so we don't waste an LLM
    retry slot on a bank that has hallucinated signals — those are unrecoverable)
    but BEFORE mandatory auto-correction (so the recruiter sees the LLM's
    intended mandatory split when interpreting the budget violation).
    The ``stage`` argument is optional so existing callers (and tests that
    only exercise signal validation) continue to work unchanged.
    """
    snapshot_by_value = {s["value"]: s for s in snapshot.signals}
    knockout_values = {
        s["value"] for s in snapshot.signals if s.get("knockout", False)
    }

    # First pass: signal validation on every question
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

    # Second pass: budget caps. Use budget_minutes_override when the caller
    # passes a per-kind budget (different from stage.duration_minutes — e.g.,
    # the behavioral call gets 3 min, the technical call gets stage - behavioral).
    # Falls back to stage.duration_minutes when override is None.
    if stage is not None or budget_minutes_override is not None:
        duration = (
            budget_minutes_override
            if budget_minutes_override is not None
            else stage.duration_minutes
        )
        _validate_budget_against_stage(
            questions=questions,
            duration_minutes=duration,
            margin_min=optional_budget_margin_min,
        )

    # Third pass: mandatory auto-correction in position order.
    # Skipped per-call by the two-call bank generator; runs once on the
    # COMBINED behavioral+technical list after both calls return. See
    # docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §1.
    if apply_mandatory_correction:
        _apply_mandatory_correction_in_position_order(
            questions=questions, knockout_values=knockout_values,
        )
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
    stage_difficulty: str | None = None,
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
                follow_ups=followups_to_jsonb(incoming.follow_ups),
                positive_evidence=list(incoming.positive_evidence),
                red_flags=list(incoming.red_flags),
                rubric=incoming.rubric.model_dump(),
                evaluation_hint=incoming.evaluation_hint,
                question_kind=incoming.question_kind,
                primary_signal=incoming.primary_signal,
                difficulty=stage_difficulty,
            )
        )
    await db.flush()

    # Re-pack positions to 0..N-1 so the final ordering is clean
    final = await get_bank_questions(db, bank.id)
    for i, q in enumerate(final):
        q.position = i
    await db.flush()


async def persist_one_question(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    question: GeneratedQuestion,
    source: str,
    position: int,
    stage_difficulty: str | None,
) -> uuid.UUID:
    """Insert ONE generated question and return its id.

    Streaming-path primitive (engine-v2 M2): the actor calls this per question so it
    can commit + publish BANK_QUESTION_ADDED incrementally. Position is assigned by
    the caller in stream order; a final re-pack happens once the stream completes.
    Per-question difficulty falls back to the stage difficulty when the generator
    leaves it null.
    """
    row = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=position,
        source=source,
        text=question.text,
        signal_values=list(question.signal_values),
        estimated_minutes=question.estimated_minutes,
        is_mandatory=question.is_mandatory,
        follow_ups=followups_to_jsonb(question.follow_ups),
        positive_evidence=list(question.positive_evidence),
        red_flags=list(question.red_flags),
        rubric=question.rubric.model_dump(),
        evaluation_hint=question.evaluation_hint,
        question_kind=question.question_kind,
        primary_signal=question.primary_signal,
        difficulty=question.difficulty or stage_difficulty,
    )
    db.add(row)
    await db.flush()
    return row.id


async def wipe_ai_questions(db: AsyncSession, *, bank: StageQuestionBank) -> int:
    """Delete ALL AI-sourced questions for a bank (recruiter rows preserved); re-pack.

    Standalone version of the delete that write_generated_questions does inline. Used by
    the streaming path: Phase A wipe-at-start (clean regenerate) and the failure-path
    wipe (so a failed bank shows zero, not a confusing partial set). Returns count deleted.
    """
    deleted = await db.execute(
        delete(StageQuestion).where(
            StageQuestion.bank_id == bank.id,
            StageQuestion.source.in_(["ai_generated", "ai_regenerated"]),
        )
    )
    await db.flush()
    remaining = await get_bank_questions(db, bank.id)
    for i, q in enumerate(remaining):
        q.position = i
    await db.flush()
    return deleted.rowcount or 0


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
    question.follow_ups = followups_to_jsonb(new_data.follow_ups)
    question.positive_evidence = list(new_data.positive_evidence)
    question.red_flags = list(new_data.red_flags)
    question.rubric = new_data.rubric.model_dump()
    question.evaluation_hint = new_data.evaluation_hint
    question.question_kind = new_data.question_kind
    question.primary_signal = new_data.primary_signal
    question.difficulty = new_data.difficulty or question.difficulty
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
        follow_ups=followups_to_jsonb(body.follow_ups),
        positive_evidence=list(body.positive_evidence),
        red_flags=list(body.red_flags),
        rubric=body.rubric.model_dump(),
        evaluation_hint=body.evaluation_hint,
        question_kind="technical_scenario",  # CreateQuestionBody intentionally has no kind field
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
    """Set positions 0..N-1 from the given order. Validates the set matches.

    Raises:
      ReorderDuplicateError — the list contains the same ID twice.
      ReorderMismatchError — the set of IDs doesn't exactly equal the
        bank's current question set (missing IDs, unknown IDs, both).
    """
    existing = await get_bank_questions(db, bank.id)
    existing_ids = {q.id for q in existing}
    incoming_ids = set(question_ids)

    # Duplicate check first — a list like [A, A, B] would pass the set-
    # equality check against {A, B} while still being invalid.
    if len(question_ids) != len(incoming_ids):
        raise ReorderDuplicateError(bank_id=bank.id)

    if existing_ids != incoming_ids:
        raise ReorderMismatchError(
            bank_id=bank.id,
            expected=existing_ids,
            received=incoming_ids,
        )

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
    await validate_mandatory_fits_session(db, bank)
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
    "recompute_and_persist_stale",
    "get_latest_confirmed_snapshot",
    "validate_knockout_coverage",
    "validate_mandatory_fits_session",
    "validate_llm_output_against_snapshot",
    "validate_streamed_question",
    "write_generated_questions",
    "persist_one_question",
    "wipe_ai_questions",
    "replace_question_in_place",
    "create_recruiter_question",
    "update_question",
    "delete_question",
    "reorder_questions",
    "confirm_bank",
    "transition_to_generating",
    "transition_to_self_reviewing",
    "transition_to_reviewing_after_critic",
    "transition_to_failed",
]
