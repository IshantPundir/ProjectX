"""Dramatiq actor tests for question_bank — mocked LLM client.

Tests exercise the inner `_generate_one_bank` helper directly (not the
decorated actor wrapper). This avoids needing a real Dramatiq broker while
still exercising the full prompt assembly + LLM call + post-validation +
DB write path.

The OpenAI client is patched via `app.modules.question_bank.actors.get_openai_client`
to return a MagicMock whose `chat.completions.create` is an AsyncMock that
yields a pre-built `StageQuestionBankOutput`. This is the same pattern used
in tests/test_jd_actor.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy
from sqlalchemy import select
from uuid import UUID

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.question_bank.actors import (
    _build_user_message,
    _generate_one_bank,
    _load_pipeline_context,
    _load_prior_stages_questions,
)
from app.modules.question_bank.errors import (
    SignalTypeNotAllowedError,
    SignalValueNotInSnapshotError,
)
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    StageQuestionBankOutput,
)
from app.modules.question_bank.service import (
    create_recruiter_question,
    ensure_bank_exists,
    get_bank_questions,
    transition_to_generating,
    write_generated_questions,
)
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers (copied from test_question_banks_service.py)
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db, tenant_id) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


async def _setup_tenant_user_unit(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
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
    position: int = 0,
    instance: JobPipelineInstance | None = None,
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    if instance is None:
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
        position=position,
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


def _mock_llm_output(
    signal_values: list[str],
    *,
    is_mandatory: bool = True,
    estimated_minutes: float = 5.0,
) -> StageQuestionBankOutput:
    """Build a canned LLM response that passes all validations."""
    return StageQuestionBankOutput(
        stage_summary="Stage tests core competencies for the role assessment.",
        questions=[
            GeneratedQuestion(
                position=i,
                text=f"Tell me about your experience with {v} in production systems.",
                signal_values=[v],
                estimated_minutes=estimated_minutes,
                is_mandatory=is_mandatory,
                follow_ups=[f"What specifically did you use {v} for?"],
                positive_evidence=[
                    f"Names specific {v} tooling clearly",
                    "Describes production usage in detail",
                    "Mentions metrics or incidents handled",
                ],
                red_flags=[
                    f"Cannot describe {v} specifics or details",
                    "Only tutorial-level experience",
                ],
                rubric=QuestionRubric(
                    excellent=f"Strong {v} experience with production incidents handled.",
                    meets_bar=f"Basic {v} experience with one production deployment.",
                    below_bar=f"Only tutorial or POC {v} exposure with no real use.",
                ),
                evaluation_hint=f"Strong = production {v} usage with specific incidents.",
            )
            for i, v in enumerate(signal_values)
        ],
        coverage_notes="Allocated one question per signal based on weight and priority.",
    )


def _mock_llm_output_with_questions(
    questions: list[GeneratedQuestion],
) -> StageQuestionBankOutput:
    return StageQuestionBankOutput(
        stage_summary="Stage tests core competencies for the role assessment.",
        questions=questions,
        coverage_notes="Allocated one question per signal based on weight and priority.",
    )


def _build_question(
    *,
    position: int,
    text: str,
    signal_values: list[str],
    is_mandatory: bool = False,
    estimated_minutes: float = 5.0,
) -> GeneratedQuestion:
    return GeneratedQuestion(
        position=position,
        text=text,
        signal_values=signal_values,
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=["Tell me more about that part."],
        positive_evidence=[
            "Names specific tools or systems",
            "Describes incident details clearly",
            "Mentions outcome and metrics",
        ],
        red_flags=["No specific examples", "Vague tutorial-level answer"],
        rubric=QuestionRubric(
            excellent="A strong answer names specific tools and shows ownership.",
            meets_bar="An acceptable answer mentions one tool and structured approach.",
            below_bar="A weak answer is vague with no tools and no structure.",
        ),
        evaluation_hint="Strong answer names tools and describes structure.",
    )


def _patch_llm(monkeypatch, output: StageQuestionBankOutput) -> AsyncMock:
    """Patch get_openai_client to return a mock that yields `output`."""
    fake_client = MagicMock()
    fake_create = AsyncMock(return_value=output)
    fake_client.chat.completions.create = fake_create
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_openai_client",
        lambda: fake_client,
    )
    return fake_create


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_stage_success_writes_questions_and_sets_reviewing(
    db, monkeypatch
):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Apigee")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(monkeypatch, _mock_llm_output(["Apigee"]))

    await _generate_one_bank(
        db,
        bank=bank,
        stage=stage,
        instance=instance,
        job=job,
        snapshot=snapshot,
        started_by=user.id,
    )

    assert bank.status == "reviewing"
    assert bank.generated_at is not None
    assert bank.generated_by == user.id

    questions = await get_bank_questions(db, bank.id)
    assert len(questions) == 1
    assert questions[0].source == "ai_generated"
    assert questions[0].signal_values == ["Apigee"]


# ---------------------------------------------------------------------------
# 2. Reject hallucinated signal_value → bank transitions to failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_stage_rejects_hallucinated_signal_value(
    db, monkeypatch
):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Apigee")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    # LLM returns a signal value not in the snapshot
    _patch_llm(monkeypatch, _mock_llm_output(["Hallucinated"]))

    with pytest.raises(SignalValueNotInSnapshotError):
        await _generate_one_bank(
            db,
            bank=bank,
            stage=stage,
            instance=instance,
            job=job,
            snapshot=snapshot,
            started_by=user.id,
        )

    assert bank.status == "failed"
    assert bank.generation_error is not None
    assert "Hallucinated" in bank.generation_error


# ---------------------------------------------------------------------------
# 3. Auto-correct knockout without is_mandatory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_stage_auto_corrects_knockout_without_mandatory(
    db, monkeypatch
):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Apigee", knockout=True)],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    # LLM forgets to mark mandatory for a knockout signal
    _patch_llm(
        monkeypatch,
        _mock_llm_output(["Apigee"], is_mandatory=False),
    )

    await _generate_one_bank(
        db,
        bank=bank,
        stage=stage,
        instance=instance,
        job=job,
        snapshot=snapshot,
        started_by=user.id,
    )

    assert bank.status == "reviewing"
    questions = await get_bank_questions(db, bank.id)
    assert len(questions) == 1
    # Server must have flipped is_mandatory → True
    assert questions[0].is_mandatory is True


# ---------------------------------------------------------------------------
# 4. Reject signal type outside include_types → failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_stage_rejects_signal_outside_include_types(
    db, monkeypatch
):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    # Snapshot has a behavioral signal
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Teamwork", signal_type="behavioral")],
    )
    # Stage only allows competency/experience/credential
    instance, stage = await _make_pipeline_and_stage(
        db,
        job=job,
        signal_filter={"include_types": ["competency", "experience", "credential"]},
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(monkeypatch, _mock_llm_output(["Teamwork"]))

    with pytest.raises(SignalTypeNotAllowedError):
        await _generate_one_bank(
            db,
            bank=bank,
            stage=stage,
            instance=instance,
            job=job,
            snapshot=snapshot,
            started_by=user.id,
        )

    assert bank.status == "failed"
    assert "Teamwork" in (bank.generation_error or "")


# ---------------------------------------------------------------------------
# 5. Pipeline sequentially sees prior stages — assert prior questions in prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_pipeline_sequentially_sees_prior_stages(
    db, monkeypatch
):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage1 = await _make_pipeline_and_stage(
        db, job=job, position=0, name="Phone Screen", stage_type="phone_screen",
    )
    _instance, stage2 = await _make_pipeline_and_stage(
        db, job=job, position=1, name="AI Screening", stage_type="ai_screening",
        instance=instance,
    )

    # Set up stage 1 with already-generated question via direct insertion
    bank1 = await ensure_bank_exists(db, stage=stage1, job=job)
    bank1.status = "reviewing"
    await db.flush()
    await write_generated_questions(
        db,
        bank=bank1,
        questions=[
            _build_question(
                position=0,
                text="Walk me through your most recent Python production deploy.",
                signal_values=["Python"],
            )
        ],
        source="ai_generated",
    )

    # Now generate stage 2, using captured LLM call to inspect the user message
    bank2 = await ensure_bank_exists(db, stage=stage2, job=job)
    transition_to_generating(bank2)
    await db.flush()

    fake_create = _patch_llm(monkeypatch, _mock_llm_output(["Python"]))

    await _generate_one_bank(
        db,
        bank=bank2,
        stage=stage2,
        instance=instance,
        job=job,
        snapshot=snapshot,
        started_by=user.id,
    )
    assert bank2.status == "reviewing"

    # Inspect the captured user message
    call_kwargs = fake_create.await_args.kwargs
    messages = call_kwargs["messages"]
    user_msg = messages[1]["content"]
    # Stage 1 question should appear under "Already generated questions"
    assert "Walk me through your most recent Python production deploy" in user_msg
    assert "Already generated questions" in user_msg


# ---------------------------------------------------------------------------
# 6. Pipeline continues on stage failure — stage 3 still generates after stage 2 fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_pipeline_continues_on_stage_failure(db, monkeypatch):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage1 = await _make_pipeline_and_stage(
        db, job=job, position=0, name="Phone Screen", stage_type="phone_screen",
    )
    _instance, stage2 = await _make_pipeline_and_stage(
        db, job=job, position=1, name="AI Screening", stage_type="ai_screening",
        instance=instance,
    )

    # Stage 1 succeeds, stage 2 fails (hallucinated signal)
    bank1 = await ensure_bank_exists(db, stage=stage1, job=job)
    transition_to_generating(bank1)
    await db.flush()
    _patch_llm(monkeypatch, _mock_llm_output(["Python"]))
    await _generate_one_bank(
        db, bank=bank1, stage=stage1, instance=instance, job=job,
        snapshot=snapshot, started_by=user.id,
    )
    assert bank1.status == "reviewing"

    # Now stage 2 fails
    bank2 = await ensure_bank_exists(db, stage=stage2, job=job)
    transition_to_generating(bank2)
    await db.flush()
    _patch_llm(monkeypatch, _mock_llm_output(["Hallucinated"]))
    with pytest.raises(SignalValueNotInSnapshotError):
        await _generate_one_bank(
            db, bank=bank2, stage=stage2, instance=instance, job=job,
            snapshot=snapshot, started_by=user.id,
        )
    assert bank2.status == "failed"

    # Stage 3 — still generates regardless
    _instance, stage3 = await _make_pipeline_and_stage(
        db, job=job, position=2, name="Final", stage_type="human_interview",
        instance=instance,
    )
    bank3 = await ensure_bank_exists(db, stage=stage3, job=job)
    transition_to_generating(bank3)
    await db.flush()
    _patch_llm(monkeypatch, _mock_llm_output(["Python"]))
    await _generate_one_bank(
        db, bank=bank3, stage=stage3, instance=instance, job=job,
        snapshot=snapshot, started_by=user.id,
    )
    assert bank3.status == "reviewing"


# ---------------------------------------------------------------------------
# 7. Regenerate question preserves UUID and flips source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_question_preserves_uuid_and_flips_source(db):
    """Direct test of replace_question_in_place — covers the actor's core
    regeneration write path without invoking the dramatiq wrapper."""
    from app.modules.question_bank.service import replace_question_in_place

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    # Insert one ai_generated question
    await write_generated_questions(
        db,
        bank=bank,
        questions=[
            _build_question(
                position=0,
                text="The original Python question text here.",
                signal_values=["Python"],
            )
        ],
        source="ai_generated",
    )
    questions = await get_bank_questions(db, bank.id)
    original_id = questions[0].id

    # Now regenerate — replace_question_in_place mimics what the actor does
    new_data = _build_question(
        position=0,
        text="A different and updated Python question text.",
        signal_values=["Python"],
    )
    await replace_question_in_place(db, question=questions[0], new_data=new_data)

    refreshed = await get_bank_questions(db, bank.id)
    assert len(refreshed) == 1
    assert refreshed[0].id == original_id  # UUID preserved
    assert refreshed[0].source == "ai_regenerated"
    assert refreshed[0].text == "A different and updated Python question text."


# ---------------------------------------------------------------------------
# 8. Regenerate question auto-reverts confirmed bank → reviewing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_question_auto_reverts_confirmed_bank(db):
    from app.modules.question_bank.service import replace_question_in_place
    from app.modules.question_bank.state_machine import auto_revert_on_edit

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    await write_generated_questions(
        db,
        bank=bank,
        questions=[
            _build_question(
                position=0,
                text="The original Python question text here.",
                signal_values=["Python"],
            )
        ],
        source="ai_generated",
    )

    # Mark bank confirmed (simulate post-confirm state)
    bank.status = "confirmed"
    bank.confirmed_at = datetime.now(UTC)
    bank.confirmed_by = user.id
    await db.flush()

    questions = await get_bank_questions(db, bank.id)
    new_data = _build_question(
        position=0,
        text="A regenerated Python question text replacement.",
        signal_values=["Python"],
    )
    await replace_question_in_place(db, question=questions[0], new_data=new_data)
    auto_revert_on_edit(bank)
    await db.flush()

    assert bank.status == "reviewing"
    assert bank.confirmed_at is None
    assert bank.confirmed_by is None


# ---------------------------------------------------------------------------
# 9. write_generated_questions preserves recruiter, deletes ai_generated/ai_regenerated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_generated_questions_preserves_recruiter_questions(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    # Add a recruiter question first
    from app.modules.question_bank.schemas import CreateQuestionBody
    body = CreateQuestionBody(
        text="Hand-written recruiter Python question for the candidate.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric=QuestionRubric(
            excellent="A strong answer names specific tools and shows ownership.",
            meets_bar="An acceptable answer mentions one tool and structured approach.",
            below_bar="A weak answer is vague with no tools and no structure.",
        ),
        evaluation_hint="Strong answer names tools and structure.",
    )
    recruiter_q = await create_recruiter_question(
        db, bank=bank, body=body, user_id=user.id, user_email=user.email,
        snapshot=snapshot,
        allowed_types=["competency", "experience", "credential", "behavioral"],
    )

    # Also add an ai_generated question via write_generated_questions
    await write_generated_questions(
        db,
        bank=bank,
        questions=[
            _build_question(
                position=0,
                text="An AI-generated Python question text first version.",
                signal_values=["Python"],
            )
        ],
        source="ai_generated",
    )

    questions_before = await get_bank_questions(db, bank.id)
    assert len(questions_before) == 2  # recruiter + AI

    # Now overwrite with a different AI batch — recruiter must survive
    await write_generated_questions(
        db,
        bank=bank,
        questions=[
            _build_question(
                position=0,
                text="A second AI-generated Python question text replacement.",
                signal_values=["Python"],
            ),
            _build_question(
                position=1,
                text="A third AI-generated Python question text in same batch.",
                signal_values=["Python"],
            ),
        ],
        source="ai_generated",
    )

    questions_after = await get_bank_questions(db, bank.id)
    sources = sorted(q.source for q in questions_after)
    assert sources == ["ai_generated", "ai_generated", "recruiter"]
    # The recruiter question must still be in the list with its original id
    recruiter_after = [q for q in questions_after if q.source == "recruiter"]
    assert len(recruiter_after) == 1
    assert recruiter_after[0].id == recruiter_q.id


# ---------------------------------------------------------------------------
# 10. Failed bank can be re-generated → error cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_stage_failed_output_retained_on_retry(db, monkeypatch):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    # First attempt fails
    _patch_llm(monkeypatch, _mock_llm_output(["Hallucinated"]))
    with pytest.raises(SignalValueNotInSnapshotError):
        await _generate_one_bank(
            db, bank=bank, stage=stage, instance=instance, job=job,
            snapshot=snapshot, started_by=user.id,
        )
    assert bank.status == "failed"
    assert bank.generation_error is not None

    # Retry: failed → generating → success
    transition_to_generating(bank)
    assert bank.generation_error is None  # cleared on transition
    await db.flush()
    _patch_llm(monkeypatch, _mock_llm_output(["Python"]))
    await _generate_one_bank(
        db, bank=bank, stage=stage, instance=instance, job=job,
        snapshot=snapshot, started_by=user.id,
    )
    assert bank.status == "reviewing"
    assert bank.generation_error is None
    questions = await get_bank_questions(db, bank.id)
    assert len(questions) == 1


# ---------------------------------------------------------------------------
# 11. _build_user_message includes prior stage's questions in pipeline context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_context_section_contains_prior_questions(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage1 = await _make_pipeline_and_stage(
        db, job=job, position=0, name="Phone Screen", stage_type="phone_screen",
    )
    _instance, stage2 = await _make_pipeline_and_stage(
        db, job=job, position=1, name="AI Screening", stage_type="ai_screening",
        instance=instance,
    )

    bank1 = await ensure_bank_exists(db, stage=stage1, job=job)
    bank1.status = "reviewing"
    await db.flush()
    await write_generated_questions(
        db,
        bank=bank1,
        questions=[
            _build_question(
                position=0,
                text="A signature Python question from the phone screen stage.",
                signal_values=["Python"],
                is_mandatory=True,
            )
        ],
        source="ai_generated",
    )

    pipeline_stages = await _load_pipeline_context(db, instance_id=instance.id)
    prior = await _load_prior_stages_questions(
        db, instance_id=instance.id, current_position=stage2.position
    )
    msg = _build_user_message(
        job=job,
        snapshot=snapshot,
        company_profile=_VALID_PROFILE,
        stage=stage2,
        pipeline_stages=pipeline_stages,
        prior_stages_questions=prior,
    )
    assert "A signature Python question from the phone screen stage" in msg
    assert "[MANDATORY]" in msg
    assert "Phone Screen" in msg


# ---------------------------------------------------------------------------
# 12. Current stage's own questions don't appear in 'prior stages'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_context_section_omits_self_from_prior(db):
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage1 = await _make_pipeline_and_stage(
        db, job=job, position=0, name="Phone Screen", stage_type="phone_screen",
    )

    bank1 = await ensure_bank_exists(db, stage=stage1, job=job)
    bank1.status = "reviewing"
    await db.flush()
    await write_generated_questions(
        db,
        bank=bank1,
        questions=[
            _build_question(
                position=0,
                text="The current stage own self-referential Python question.",
                signal_values=["Python"],
            )
        ],
        source="ai_generated",
    )

    pipeline_stages = await _load_pipeline_context(db, instance_id=instance.id)
    # Stage 1 generating itself — current_position = 0 → no prior stages
    prior = await _load_prior_stages_questions(
        db, instance_id=instance.id, current_position=stage1.position
    )
    assert prior == []

    msg = _build_user_message(
        job=job,
        snapshot=snapshot,
        company_profile=_VALID_PROFILE,
        stage=stage1,
        pipeline_stages=pipeline_stages,
        prior_stages_questions=prior,
    )
    # The current stage's own question text should NOT appear under
    # "Already generated questions" — there are no prior stages.
    assert "Already generated questions" not in msg
    assert "(CURRENT — you are generating this)" in msg
