"""Service layer tests for question_bank.

Covers:
- Bank lifecycle (ensure_bank_exists, state transitions)
- Coverage + budget validators (confirm_bank)
- Auto-revert on edit
- Recruiter question CRUD (create / update / delete / reorder)
- write_generated_questions (preserve recruiter, wipe AI)
- replace_question_in_place (preserves UUID)
- compute_is_stale (snapshot supersession)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy
from sqlalchemy import select

from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.question_bank.models import (
    StageQuestion,
    StageQuestionBank,
)
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError,
    BankNotInReviewingError,
    KnockoutUnprobedError,
    MandatoryOverrunError,
    ReorderMismatchError,
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    GeneratedQuestion,
    QuestionRubric,
    UpdateQuestionBody,
)
from app.modules.question_bank.service import (
    auto_revert_on_edit,
    compute_is_stale,
    confirm_bank,
    create_recruiter_question,
    delete_question,
    ensure_bank_exists,
    get_bank_questions,
    recompute_and_persist_stale,
    replace_question_in_place,
    reorder_questions,
    transition_to_failed,
    transition_to_generating,
    transition_to_reviewing_after_generation,
    update_question,
    write_generated_questions,
)
from app.modules.question_bank.state_machine import transition_to_confirmed
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "Fintech / Financial Services",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db, tenant_id) -> None:
    """Set the RLS tenant context for the current transaction."""
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


async def _setup_tenant_user_unit(db):
    """Create a tenant + user + company org unit, set RLS, return all three."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


def _signal(
    *,
    value: str,
    signal_type: str = "competency",
    priority: str = "required",
    weight: int = 2,
    knockout: bool = False,
    stage: str = "screen",
) -> dict:
    return {
        "value": value,
        "type": signal_type,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage,
        "evaluation_method": "verification",
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


async def _make_job_with_signals(
    db,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
    *,
    signals: list[dict],
    version: int = 1,
    confirm: bool = True,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    """Create a JobPosting + JobPostingSignalSnapshot pinned to it.

    The snapshot is marked confirmed by default so get_latest_confirmed_snapshot
    will pick it up.
    """
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=version,
        signals=signals,
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id if confirm else None,
        confirmed_at=datetime.now(UTC) if confirm else None,
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_and_stage(
    db,
    *,
    job: JobPosting,
    stage_type: str = "phone_screen",
    duration_minutes: int = 30,
    signal_filter: dict | None = None,
    pass_criteria: dict | None = None,
    advance_behavior: str = "auto_advance",
    difficulty: str = "medium",
    name: str = "Phone Screen",
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    """Create a JobPipelineInstance + a single JobPipelineStage for the job."""
    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=job.tenant_id,
        instance_id=instance.id,
        position=0,
        name=name,
        stage_type=stage_type,
        duration_minutes=duration_minutes,
        difficulty=difficulty,
        signal_filter=signal_filter
        or {"include_types": ["competency", "experience", "credential", "behavioral"]},
        pass_criteria=pass_criteria or {"type": "all_knockouts_pass"},
        advance_behavior=advance_behavior,
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _valid_rubric() -> QuestionRubric:
    return QuestionRubric(
        excellent="A strong answer names specific tools and describes hypothesis-verify flow.",
        meets_bar="An acceptable answer mentions at least one tool and shows structure.",
        below_bar="A weak answer is vague with no tools and no structure.",
    )


def _make_generated_question(
    *,
    position: int = 0,
    text: str = "Walk me through a production incident you handled.",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
    question_kind: str = "technical_scenario",
    primary_signal: str | None = None,
) -> GeneratedQuestion:
    _signal_values = signal_values or ["Python"]
    return GeneratedQuestion(
        position=position,
        text=text,
        primary_signal=primary_signal or _signal_values[0],
        signal_values=_signal_values,
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=["What tools did you use?"],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
        question_kind=question_kind,
    )


def _make_create_body(
    *,
    text: str = "What is your favorite production debugging tool?",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
    position: int | None = None,
) -> CreateQuestionBody:
    return CreateQuestionBody(
        text=text,
        signal_values=signal_values or ["Python"],
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
        position=position,
    )


async def _add_recruiter_question(
    db,
    *,
    bank: StageQuestionBank,
    snapshot: JobPostingSignalSnapshot,
    user_id: UUID,
    text: str = "Recruiter question?",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
    position: int | None = None,
    allowed_types: list[str] | None = None,
) -> StageQuestion:
    body = _make_create_body(
        text=text,
        signal_values=signal_values,
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        position=position,
    )
    return await create_recruiter_question(
        db,
        bank=bank,
        body=body,
        user_id=user_id,
        user_email="r@test.com",
        snapshot=snapshot,
        allowed_types=allowed_types
        or ["competency", "experience", "credential", "behavioral"],
    )


# ---------------------------------------------------------------------------
# 1-2. ensure_bank_exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_bank_exists_creates_draft_pinned_to_latest_snapshot(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)

    bank = await ensure_bank_exists(db, stage=stage, job=job)
    assert bank.status == "draft"
    assert bank.signal_snapshot_id == snapshot.id
    assert bank.job_posting_id == job.id
    assert bank.stage_id == stage.id
    assert bank.prompt_version == "v1"


@pytest.mark.asyncio
async def test_ensure_bank_exists_returns_existing_when_already_present(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)

    first = await ensure_bank_exists(db, stage=stage, job=job)
    second = await ensure_bank_exists(db, stage=stage, job=job)
    assert first.id == second.id


# ---------------------------------------------------------------------------
# 3-7. State transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_to_generating_succeeds_from_draft(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    assert bank.status == "draft"

    transition_to_generating(bank)
    assert bank.status == "generating"
    assert bank.generation_error is None


@pytest.mark.asyncio
async def test_transition_to_generating_rejects_if_already_generating(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    transition_to_generating(bank)
    with pytest.raises(BankAlreadyGeneratingError):
        transition_to_generating(bank)


@pytest.mark.asyncio
async def test_transition_to_reviewing_after_generation_sets_timestamps(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    transition_to_generating(bank)
    transition_to_reviewing_after_generation(bank, user_id=user.id)
    assert bank.status == "reviewing"
    assert bank.generated_at is not None
    assert bank.generated_by == user.id


@pytest.mark.asyncio
async def test_transition_to_failed_records_error_message(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    transition_to_generating(bank)
    transition_to_failed(bank, error="LLM timeout")
    assert bank.status == "failed"
    assert bank.generation_error == "LLM timeout"


@pytest.mark.asyncio
async def test_transition_to_confirmed_rejects_from_draft(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    assert bank.status == "draft"

    # Direct state-machine call: transitioning to confirmed from draft
    # is illegal regardless of validator outcomes.
    with pytest.raises(BankNotInReviewingError):
        transition_to_confirmed(bank, user_id=user.id)


# ---------------------------------------------------------------------------
# 8-10. Confirm bank validators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_bank_rejects_uncovered_knockout(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)

    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[
            _signal(
                value="Apigee",
                signal_type="competency",
                knockout=True,
            ),
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)

    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    # No questions yet → knockout is uncovered
    with pytest.raises(KnockoutUnprobedError) as excinfo:
        await confirm_bank(
            db, bank=bank, user_id=user.id, user_email=user.email
        )
    assert excinfo.value.signal_value == "Apigee"


@pytest.mark.asyncio
async def test_confirm_bank_rejects_mandatory_overrun(db):
    """Mandatory questions whose estimated_minutes sum exceeds stage duration → 409."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[
            _signal(
                value="TestSignal",
                signal_type="competency",
                knockout=True,
                weight=3,
            )
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, stage_type="phone_screen", duration_minutes=10,
    )

    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    # Add a mandatory question whose estimated_minutes exceeds stage duration
    q = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Test question that takes too long",
        signal_values=["TestSignal"],
        estimated_minutes=15.0,  # > 10 min stage
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["observable 1", "observable 2", "observable 3"],
        red_flags=["red flag 1", "red flag 2"],
        rubric={
            "excellent": "Strong answer with concrete specifics and examples.",
            "meets_bar": "Acceptable answer with general correctness.",
            "below_bar": "Weak answer lacking specifics or incorrect.",
        },
        evaluation_hint="Look for concrete specifics.",
    )
    db.add(q)
    await db.flush()

    with pytest.raises(MandatoryOverrunError) as excinfo:
        await confirm_bank(
            db, bank=bank, user_id=user.id, user_email=user.email
        )
    assert excinfo.value.mandatory_minutes == 15.0
    assert excinfo.value.stage_minutes == 10


@pytest.mark.asyncio
async def test_confirm_bank_success_sets_confirmed_at_and_confirmed_by(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=30,
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    # Add 20 minutes of questions (within 15-45 range)
    await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="Tell me about a Python project you've shipped.",
        signal_values=["Python"],
        estimated_minutes=10.0,
    )
    await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="What's the trickiest Python bug you've ever fixed?",
        signal_values=["Python"],
        estimated_minutes=10.0,
    )
    bank.status = "reviewing"
    await db.flush()

    await confirm_bank(
        db, bank=bank, user_id=user.id, user_email=user.email
    )
    assert bank.status == "confirmed"
    assert bank.confirmed_at is not None
    assert bank.confirmed_by == user.id


# ---------------------------------------------------------------------------
# 11-13. auto_revert_on_edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_revert_on_edit_flips_confirmed_to_reviewing(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "confirmed"
    bank.confirmed_at = datetime.now(UTC)
    bank.confirmed_by = user.id
    await db.flush()

    changed = auto_revert_on_edit(bank)
    assert changed is True
    assert bank.status == "reviewing"
    assert bank.confirmed_at is None
    assert bank.confirmed_by is None


@pytest.mark.asyncio
async def test_auto_revert_on_edit_flips_draft_to_reviewing(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    assert bank.status == "draft"

    changed = auto_revert_on_edit(bank)
    assert changed is True
    assert bank.status == "reviewing"


@pytest.mark.asyncio
async def test_auto_revert_on_edit_leaves_reviewing_unchanged(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    changed = auto_revert_on_edit(bank)
    assert changed is False
    assert bank.status == "reviewing"


# ---------------------------------------------------------------------------
# 14-17. create_recruiter_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_recruiter_question_sets_source_and_position(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="What is your favorite Python feature?",
        signal_values=["Python"],
    )
    assert q.source == "recruiter"
    assert q.position == 0  # first question

    q2 = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="Tell me about Python decorators in production.",
        signal_values=["Python"],
    )
    assert q2.position == 1  # appended at the end


@pytest.mark.asyncio
async def test_create_recruiter_question_shifts_existing_down_when_position_provided(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q0 = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="Question at original position zero?",
    )
    q1 = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="Question at original position one?",
    )
    assert q0.position == 0
    assert q1.position == 1

    # Insert at position 0 — should shift the others down
    q_new = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="A brand new head question text here?",
        position=0,
    )
    await db.refresh(q0)
    await db.refresh(q1)
    assert q_new.position == 0
    assert q0.position == 1
    assert q1.position == 2


@pytest.mark.asyncio
async def test_create_recruiter_question_rejects_invalid_signal_value(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    with pytest.raises(SignalValueNotInSnapshotError):
        await _add_recruiter_question(
            db,
            bank=bank,
            snapshot=snapshot,
            user_id=user.id,
            text="Unknown signal question",
            signal_values=["NotASignal"],
        )


@pytest.mark.asyncio
async def test_create_recruiter_question_rejects_signal_type_outside_include_types(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    # Snapshot has a behavioral signal
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Teamwork", signal_type="behavioral")],
    )
    # Stage only allows competency/experience/credential
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job,
        signal_filter={"include_types": ["competency", "experience", "credential"]},
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    body = _make_create_body(
        text="A question on teamwork.",
        signal_values=["Teamwork"],
    )
    with pytest.raises(SignalTypeNotAllowedError):
        await create_recruiter_question(
            db,
            bank=bank,
            body=body,
            user_id=user.id,
            user_email=user.email,
            snapshot=snapshot,
            allowed_types=["competency", "experience", "credential"],
        )


# ---------------------------------------------------------------------------
# 18-19. update_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_question_sets_edited_by_recruiter_flag(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
    )
    assert q.edited_by_recruiter is False

    body = UpdateQuestionBody(text="An updated question text here.")
    await update_question(
        db,
        question=q,
        bank=bank,
        body=body,
        user_id=user.id,
        user_email=user.email,
        snapshot=snapshot,
        allowed_types=["competency", "experience", "credential", "behavioral"],
    )
    assert q.edited_by_recruiter is True
    assert q.text == "An updated question text here."


@pytest.mark.asyncio
async def test_update_question_rejects_invalid_signal_value(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
    )

    body = UpdateQuestionBody(signal_values=["NotASignal"])
    with pytest.raises(SignalValueNotInSnapshotError):
        await update_question(
            db,
            question=q,
            bank=bank,
            body=body,
            user_id=user.id,
            user_email=user.email,
            snapshot=snapshot,
            allowed_types=["competency", "experience", "credential", "behavioral"],
        )


# ---------------------------------------------------------------------------
# 20. delete_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_question_repacks_positions_to_zero_based(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q0 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question zero text here?",
    )
    q1 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question one text here?",
    )
    q2 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question two text here?",
    )
    assert (q0.position, q1.position, q2.position) == (0, 1, 2)

    await delete_question(
        db,
        question=q1,
        bank=bank,
        user_id=user.id,
        user_email=user.email,
    )
    remaining = await get_bank_questions(db, bank.id)
    assert len(remaining) == 2
    assert [q.position for q in remaining] == [0, 1]
    assert {q.id for q in remaining} == {q0.id, q2.id}


# ---------------------------------------------------------------------------
# 21-22. reorder_questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_questions_sets_positions_from_list_order(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q0 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question alpha text here?",
    )
    q1 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question bravo text here?",
    )
    q2 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question charlie text here?",
    )

    await reorder_questions(
        db,
        bank=bank,
        question_ids=[q2.id, q0.id, q1.id],
        user_id=user.id,
        user_email=user.email,
    )
    await db.refresh(q0)
    await db.refresh(q1)
    await db.refresh(q2)
    assert q2.position == 0
    assert q0.position == 1
    assert q1.position == 2


@pytest.mark.asyncio
async def test_reorder_questions_rejects_mismatched_id_set(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    q0 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question alpha text here?",
    )
    _q1 = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Question bravo text here?",
    )

    # B9: reorder now raises typed ReorderMismatchError (was bare ValueError).
    with pytest.raises(ReorderMismatchError):
        await reorder_questions(
            db,
            bank=bank,
            question_ids=[q0.id, uuid4()],  # second id doesn't exist
            user_id=user.id,
            user_email=user.email,
        )


# ---------------------------------------------------------------------------
# 23. write_generated_questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_generated_questions_wipes_ai_sourced_preserves_recruiter(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # Recruiter question that must be preserved
    recruiter_q = await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        text="A recruiter-authored question.",
    )
    recruiter_id = recruiter_q.id

    # Pre-existing AI question (simulate previous generation result)
    ai_q = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=99,
        source="ai_generated",
        text="An old AI question.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric=_valid_rubric().model_dump(),
        evaluation_hint="Hint here for evaluation.",
        edited_by_recruiter=False,
    )
    db.add(ai_q)
    await db.flush()
    old_ai_id = ai_q.id

    # Run generation: should wipe ai_q, keep recruiter_q, add new AI questions
    new_questions = [
        _make_generated_question(
            position=0,
            text="A brand new generated question one here.",
            signal_values=["Python"],
        ),
        _make_generated_question(
            position=1,
            text="A brand new generated question two here.",
            signal_values=["Python"],
        ),
    ]
    await write_generated_questions(
        db, bank=bank, questions=new_questions, source="ai_generated",
    )

    final = await get_bank_questions(db, bank.id)
    final_ids = {q.id for q in final}
    assert recruiter_id in final_ids  # preserved
    assert old_ai_id not in final_ids  # wiped

    sources = {q.source for q in final}
    assert "recruiter" in sources
    assert "ai_generated" in sources

    # Positions are 0..N-1
    positions = sorted(q.position for q in final)
    assert positions == list(range(len(final)))


# ---------------------------------------------------------------------------
# 24. replace_question_in_place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_question_in_place_preserves_uuid(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # Seed with an AI question
    ai_q = StageQuestion(
        tenant_id=bank.tenant_id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Old text question here.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric=_valid_rubric().model_dump(),
        evaluation_hint="Old hint for evaluation here.",
        edited_by_recruiter=True,
    )
    db.add(ai_q)
    await db.flush()
    original_id = ai_q.id

    new_data = _make_generated_question(
        text="Brand new question text replaces the old one.",
        signal_values=["Python"],
    )
    await replace_question_in_place(db, question=ai_q, new_data=new_data)

    assert ai_q.id == original_id
    assert ai_q.text == "Brand new question text replaces the old one."
    assert ai_q.source == "ai_regenerated"
    assert ai_q.edited_by_recruiter is False


# ---------------------------------------------------------------------------
# 25. compute_is_stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_is_stale_returns_true_when_snapshot_superseded(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot_v1 = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    assert bank.signal_snapshot_id == snapshot_v1.id

    # Initially not stale (column is False)
    assert await compute_is_stale(db, bank) is False

    # Add a newer confirmed snapshot for the same job
    snapshot_v2 = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=2,
        signals=[_signal(value="Python"), _signal(value="Go")],
        seniority_level="senior",
        role_summary="Updated summary.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot_v2)
    await db.flush()

    # Write-side: recompute and persist the column so readers see the new value.
    await recompute_and_persist_stale(db, bank)

    # compute_is_stale shim now reads the persisted column — True.
    assert await compute_is_stale(db, bank) is True


@pytest.mark.asyncio
async def test_write_generated_questions_persists_question_kind(db):
    """write_generated_questions writes question_kind from each
    GeneratedQuestion to the persisted StageQuestion row. Each of the
    3 generator-allowed kinds round-trips."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[
            _signal(value="UK shift", knockout=True),
            _signal(value="Conflict resolution", signal_type="behavioral"),
            _signal(value="Python"),
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    incoming = [
        _make_generated_question(
            position=0, signal_values=["UK shift"], is_mandatory=True,
            question_kind="compliance_binary",
        ),
        _make_generated_question(
            position=1, signal_values=["Conflict resolution"],
            question_kind="behavioral",
        ),
        _make_generated_question(
            position=2, signal_values=["Python"],
            question_kind="technical_scenario",
        ),
    ]
    await write_generated_questions(
        db, bank=bank, questions=incoming, source="ai_generated",
    )
    persisted = await get_bank_questions(db, bank.id)
    by_signal = {p.signal_values[0]: p for p in persisted}
    assert by_signal["UK shift"].question_kind == "compliance_binary"
    assert by_signal["Conflict resolution"].question_kind == "behavioral"
    assert by_signal["Python"].question_kind == "technical_scenario"


@pytest.mark.asyncio
async def test_replace_question_in_place_updates_question_kind(db):
    """replace_question_in_place writes the new GeneratedQuestion's
    question_kind onto the existing row. Tests the regen-one path."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[
            _signal(value="Python"),
            _signal(value="UK shift", knockout=True),
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # Seed with a technical_scenario question
    await write_generated_questions(
        db, bank=bank,
        questions=[
            _make_generated_question(
                position=0, signal_values=["Python"],
                question_kind="technical_scenario",
            ),
        ],
        source="ai_generated",
    )
    seeded = (await get_bank_questions(db, bank.id))[0]
    assert seeded.question_kind == "technical_scenario"

    # Regen with a compliance_binary
    new_data = _make_generated_question(
        position=0,
        text="Can you work the UK shift (1pm-9pm UK time)?",
        signal_values=["UK shift"],
        is_mandatory=True,
        question_kind="compliance_binary",
    )
    await replace_question_in_place(db, question=seeded, new_data=new_data)
    await db.refresh(seeded)
    assert seeded.question_kind == "compliance_binary"
    assert seeded.source == "ai_regenerated"


@pytest.mark.asyncio
async def test_create_recruiter_question_lands_with_default_kind(db):
    """Recruiter-authored questions take 'technical_depth' as their kind.
    CreateQuestionBody has no question_kind field — the service writes
    the default explicitly so the in-memory row state matches the DB
    without needing a session refresh."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    question = await _add_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="What testing tools have you used in production?",
        signal_values=["Python"],
    )
    # The Python-level attribute reads back as 'technical_scenario' WITHOUT
    # needing a session refresh — the explicit kwarg in service.py
    # establishes that.
    assert question.question_kind == "technical_scenario"
