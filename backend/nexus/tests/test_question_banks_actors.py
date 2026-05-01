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
from app.modules.question_bank.actors import (
    _build_user_message,
    _generate_one_bank,
    _load_pipeline_context,
    _load_prior_stages_questions,
    _run_pipeline_generation,
    _run_stage_generation,
)
from app import pubsub
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
    )


def _mock_llm_output_with_questions(
    questions: list[GeneratedQuestion],
) -> StageQuestionBankOutput:
    return StageQuestionBankOutput(
        questions=questions,
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

    # Stage 3 — still generates regardless. Uses ai_screening (one of the
    # two stage types currently eligible for AI generation; human_interview
    # was deliberately removed from STAGE_TYPE_TO_PROMPT — the recruiter
    # authors those questions manually).
    _instance, stage3 = await _make_pipeline_and_stage(
        db, job=job, position=2, name="Final AI Round", stage_type="ai_screening",
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


# ===========================================================================
# Pub/sub publish behaviour for the generation actors
#
# These tests exercise the new fast-path publishes added on top of the
# correctness-only SSE backstop poll. They verify:
#   - `_run_stage_generation` returns the correct (job_id, stage_id, status)
#     tuple so the wrapping actor can publish BANK_STATUS_CHANGED post-commit.
#   - `_run_pipeline_generation` publishes one BANK_STATUS_CHANGED per stage
#     plus a final PIPELINE_GENERATION_COMPLETE — all carrying the supplied
#     correlation_id.
# ===========================================================================


@pytest.mark.asyncio
async def test_run_stage_generation_returns_reviewing_on_success(db, monkeypatch):
    """Happy path: success → returns (job_id, stage_id, 'reviewing')."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Apigee")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(monkeypatch, _mock_llm_output(["Apigee"]))

    result = await _run_stage_generation(
        db,
        bank_id=bank.id,
        tenant_id=tenant.id,
        started_by=user.id,
    )

    assert result is not None
    job_id_out, stage_id_out, new_status = result
    assert job_id_out == job.id
    assert stage_id_out == stage.id
    assert new_status == "reviewing"
    assert bank.status == "reviewing"


@pytest.mark.asyncio
async def test_run_stage_generation_returns_failed_on_validation_error(db, monkeypatch):
    """Permanent error (hallucinated signal) → returns (job_id, stage_id, 'failed').

    The bank must be in 'failed' status so the wrapping actor commits the
    terminal state and publishes the failure event. _generate_one_bank's
    own try/except is what guarantees the transition before re-raising.
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Apigee")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(monkeypatch, _mock_llm_output(["Hallucinated"]))

    result = await _run_stage_generation(
        db,
        bank_id=bank.id,
        tenant_id=tenant.id,
        started_by=user.id,
    )

    assert result is not None
    job_id_out, stage_id_out, new_status = result
    assert job_id_out == job.id
    assert stage_id_out == stage.id
    assert new_status == "failed"
    assert bank.status == "failed"
    assert bank.generation_error is not None


@pytest.mark.asyncio
async def test_run_pipeline_generation_publishes_per_stage_and_completion(
    db, monkeypatch, capture_publishes,
):
    """`_run_pipeline_generation` publishes BANK_STATUS_CHANGED per generated
    stage and a single PIPELINE_GENERATION_COMPLETE at the end.

    All envelopes carry the same supplied correlation_id (CLAUDE.md
    observability standard: every session/request flows end-to-end with
    one ID through the entire pipeline).
    """
    from contextlib import asynccontextmanager

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage1 = await _make_pipeline_and_stage(
        db, job=job, position=0, name="Phone Screen", stage_type="phone_screen",
    )
    _instance, stage2 = await _make_pipeline_and_stage(
        db, job=job, position=1, name="AI Screening", stage_type="ai_screening",
        instance=instance,
    )
    # Pre-mark stage1's bank to mirror the endpoint behaviour. Stage2's bank
    # is created on first iteration by ensure_bank_exists.
    bank1 = await ensure_bank_exists(db, stage=stage1, job=job)
    transition_to_generating(bank1)
    await db.flush()

    _patch_llm(monkeypatch, _mock_llm_output(["Python"]))

    @asynccontextmanager
    async def _fake_session():
        yield db
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_bypass_session", _fake_session
    )

    corr_id = "corr-pipeline-happy-path"
    await _run_pipeline_generation(
        instance_id=str(instance.id),
        tenant_id=str(tenant.id),
        started_by=str(user.id),
        correlation_id=corr_id,
    )

    bank_events = [
        p for p in capture_publishes if p.event == pubsub.Events.BANK_STATUS_CHANGED
    ]
    completion_events = [
        p
        for p in capture_publishes
        if p.event == pubsub.Events.PIPELINE_GENERATION_COMPLETE
    ]

    assert len(bank_events) == 2, (
        f"Expected one BANK_STATUS_CHANGED per stage (2), got {len(bank_events)}: "
        f"{[(p.event, p.payload) for p in capture_publishes]}"
    )
    assert len(completion_events) == 1
    assert all(p.correlation_id == corr_id for p in capture_publishes), (
        "Every event must carry the supplied correlation_id end-to-end"
    )
    assert all(p.channel == pubsub.job_channel(job.id) for p in capture_publishes), (
        "Every event must be published on the per-job channel"
    )
    # Completion payload reflects the run summary
    completion_payload = completion_events[0].payload
    assert completion_payload["succeeded"] == 2
    assert completion_payload["failed"] == 0
    assert completion_payload["total"] == 2
    assert completion_payload["job_id"] == str(job.id)
    assert completion_payload["source"] == "actor"
    # Per-stage payloads carry new_status and stage_id
    for p in bank_events:
        assert p.payload["new_status"] == "reviewing"
        assert p.payload["job_id"] == str(job.id)
        assert "stage_id" in p.payload
        assert "bank_id" in p.payload
        assert p.payload["source"] == "actor"


@pytest.mark.asyncio
async def test_run_pipeline_generation_publishes_failed_status_on_stage_error(
    db, monkeypatch, capture_publishes,
):
    """A failing stage publishes BANK_STATUS_CHANGED with new_status='failed'.

    Pipeline does NOT abort — subsequent stages still run, the completion
    event reflects the mixed succeeded/failed counts, and every envelope
    carries the supplied correlation_id.
    """
    from contextlib import asynccontextmanager

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
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
    transition_to_generating(bank1)
    await db.flush()

    # Stage 1 returns a valid signal; stage 2 hallucinates.
    call_count = {"n": 0}
    valid_output = _mock_llm_output(["Python"])
    bad_output = _mock_llm_output(["Hallucinated"])

    fake_client = MagicMock()

    async def _flaky_create(**_kwargs):
        call_count["n"] += 1
        return valid_output if call_count["n"] == 1 else bad_output
    fake_client.chat.completions.create = AsyncMock(side_effect=_flaky_create)
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_openai_client",
        lambda: fake_client,
    )

    @asynccontextmanager
    async def _fake_session():
        yield db
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_bypass_session", _fake_session
    )

    corr_id = "corr-pipeline-mixed"
    await _run_pipeline_generation(
        instance_id=str(instance.id),
        tenant_id=str(tenant.id),
        started_by=str(user.id),
        correlation_id=corr_id,
    )

    bank_events = [
        p for p in capture_publishes if p.event == pubsub.Events.BANK_STATUS_CHANGED
    ]
    completion_events = [
        p
        for p in capture_publishes
        if p.event == pubsub.Events.PIPELINE_GENERATION_COMPLETE
    ]
    statuses = sorted(p.payload["new_status"] for p in bank_events)

    assert statuses == ["failed", "reviewing"], (
        f"Expected one reviewing + one failed, got {statuses}"
    )
    assert len(completion_events) == 1
    cp = completion_events[0].payload
    assert cp["succeeded"] == 1
    assert cp["failed"] == 1
    assert cp["total"] == 2
    assert all(p.correlation_id == corr_id for p in capture_publishes)


@pytest.mark.asyncio
async def test_run_stage_generation_reraises_when_bank_not_terminal(db, monkeypatch):
    """If an exception happens AFTER `_generate_one_bank` succeeds (e.g.
    `log_event` raises), the bank is in 'reviewing' — not 'failed'. The
    helper must re-raise so the wrapping actor rolls back and Dramatiq
    retries. Publishing 'reviewing' here would falsely tell the frontend
    the work is done while the audit log is missing.
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Apigee")],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    _patch_llm(monkeypatch, _mock_llm_output(["Apigee"]))

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated audit log outage")
    monkeypatch.setattr(
        "app.modules.question_bank.actors.log_event", _boom
    )

    with pytest.raises(RuntimeError, match="simulated audit log outage"):
        await _run_stage_generation(
            db,
            bank_id=bank.id,
            tenant_id=tenant.id,
            started_by=user.id,
        )
    # `_generate_one_bank` succeeded → bank transitioned to 'reviewing'.
    # Then log_event raised. Bank is in 'reviewing' (a terminal state from
    # a state-machine perspective) but the work is not durable yet — the
    # caller must rollback. The contract: "re-raise when bank.status is
    # not 'failed'" intentionally covers this case.
    assert bank.status == "reviewing"


# ===========================================================================
# Generation-time budget enforcement
#
# The budget contract:
#   mandatory_total ≤ duration_minutes                    (hard cap)
#   mandatory_total + optional_total ≤ duration_minutes + 5  (hard cap)
#
# `validate_llm_output_against_snapshot(stage=...)` enforces both. On
# violation, `_generate_one_bank` retries the LLM once with the violation
# fed back into the conversation; on the second violation, the bank fails.
# ===========================================================================


@pytest.mark.asyncio
async def test_validator_rejects_mandatory_overrun_at_generation(db):
    """Mandatory minutes > duration → BudgetExceededError(kind='mandatory').

    Direct test of the validator (not the actor): 3 mandatory questions
    @ 6 min = 18 min mandatory, against a 15-min stage duration. The
    validator must surface the violation immediately and identify it as
    a 'mandatory' kind so the actor retry loop can produce the right
    LLM feedback message.
    """
    from app.modules.question_bank.errors import BudgetExceededError
    from app.modules.question_bank.service import validate_llm_output_against_snapshot

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value=v) for v in ("A", "B", "C")],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=15,
    )

    questions = [
        _build_question(
            position=i, text=f"Question {i+1} about {v}", signal_values=[v],
            is_mandatory=True, estimated_minutes=6.0,
        )
        for i, v in enumerate(("A", "B", "C"))
    ]

    with pytest.raises(BudgetExceededError) as exc_info:
        await validate_llm_output_against_snapshot(
            db,
            snapshot=snapshot,
            allowed_types=["competency", "experience", "credential", "behavioral"],
            questions=questions,
            stage=stage,
        )
    assert exc_info.value.kind == "mandatory"
    assert exc_info.value.observed_minutes == 18.0
    assert exc_info.value.cap_minutes == 15.0


@pytest.mark.asyncio
async def test_validator_rejects_total_overrun_at_generation(db):
    """Total minutes > duration + margin → BudgetExceededError(kind='total').

    1 mandatory @ 5 min + 4 optional @ 5 min = 25 min total against a
    15-min stage with 5-min margin (cap = 20 min). Mandatory itself fits
    (5 ≤ 15) so the FIRST cap passes; the TOTAL cap is what trips.
    """
    from app.modules.question_bank.errors import BudgetExceededError
    from app.modules.question_bank.service import validate_llm_output_against_snapshot

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value=v) for v in ("A", "B", "C", "D", "E")],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=15,
    )

    questions = [
        _build_question(
            position=0, text="Mandatory question about A",
            signal_values=["A"], is_mandatory=True, estimated_minutes=5.0,
        ),
    ] + [
        _build_question(
            position=i, text=f"Optional question {i} about {v}",
            signal_values=[v], is_mandatory=False, estimated_minutes=5.0,
        )
        for i, v in enumerate(("B", "C", "D", "E"), start=1)
    ]

    with pytest.raises(BudgetExceededError) as exc_info:
        await validate_llm_output_against_snapshot(
            db,
            snapshot=snapshot,
            allowed_types=["competency", "experience", "credential", "behavioral"],
            questions=questions,
            stage=stage,
        )
    assert exc_info.value.kind == "total"
    assert exc_info.value.observed_minutes == 25.0
    assert exc_info.value.cap_minutes == 20.0  # duration 15 + margin 5


@pytest.mark.asyncio
async def test_validator_passes_when_within_budget(db):
    """Mandatory + optional all fit → no exception."""
    from app.modules.question_bank.service import validate_llm_output_against_snapshot

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value=v) for v in ("A", "B", "C")],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=15,
    )

    # 2 mandatory @ 4 min = 8 min mandatory (≤ 15 ✓)
    # 2 optional @ 4 min, plus the mandatory 8 min = 16 min total (≤ 20 ✓)
    questions = [
        _build_question(
            position=0, text="Mandatory A", signal_values=["A"],
            is_mandatory=True, estimated_minutes=4.0,
        ),
        _build_question(
            position=1, text="Mandatory B", signal_values=["B"],
            is_mandatory=True, estimated_minutes=4.0,
        ),
        _build_question(
            position=2, text="Optional C", signal_values=["C"],
            is_mandatory=False, estimated_minutes=4.0,
        ),
        _build_question(
            position=3, text="Optional A depth probe", signal_values=["A"],
            is_mandatory=False, estimated_minutes=4.0,
        ),
    ]

    validated = await validate_llm_output_against_snapshot(
        db,
        snapshot=snapshot,
        allowed_types=["competency", "experience", "credential", "behavioral"],
        questions=questions,
        stage=stage,
    )
    assert len(validated) == 4
    mandatory_total = sum(q.estimated_minutes for q in validated if q.is_mandatory)
    total = sum(q.estimated_minutes for q in validated)
    assert mandatory_total <= stage.duration_minutes
    assert total <= stage.duration_minutes + 5


@pytest.mark.asyncio
async def test_validator_skips_budget_check_when_stage_omitted(db):
    """Backwards compat: callers not passing `stage` skip the budget check.

    Existing callers (and tests that only cover signal validation) must
    not break — the budget check is opt-in via the optional `stage` kwarg.
    """
    from app.modules.question_bank.service import validate_llm_output_against_snapshot

    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="A")],
    )
    questions = [
        _build_question(
            position=0, text="Wildly over-budget mandatory question",
            signal_values=["A"], is_mandatory=True, estimated_minutes=15.0,
        ),
    ]
    # No stage argument → no budget check → no exception even though 15 min
    # mandatory would clearly violate any reasonable stage's duration cap.
    validated = await validate_llm_output_against_snapshot(
        db,
        snapshot=snapshot,
        allowed_types=["competency", "experience", "credential", "behavioral"],
        questions=questions,
    )
    assert len(validated) == 1


@pytest.mark.asyncio
async def test_generate_one_bank_retries_on_budget_violation_then_succeeds(
    db, monkeypatch
):
    """First LLM call returns over-budget output → second call returns valid output.

    Verifies the retry-with-feedback loop: the actor catches the budget
    violation, appends the offending output + a corrective user message
    to the conversation, calls the LLM again, and persists the second
    result. The bank must end up in 'reviewing', not 'failed'.
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="A"), _signal(value="B")],
    )
    instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=15,
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    # First call: over-budget mandatory (3 × 8 min = 24 > 15)
    bad_output = StageQuestionBankOutput(
        questions=[
            _build_question(
                position=i, text=f"Mandatory {v}", signal_values=[v],
                is_mandatory=True, estimated_minutes=8.0,
            )
            for i, v in enumerate(("A", "B", "A"))
        ],
    )
    # Second call: within budget
    good_output = _mock_llm_output(["A", "B"], estimated_minutes=4.0)

    call_count = {"n": 0}

    async def _flaky_create(**_kwargs):
        call_count["n"] += 1
        return bad_output if call_count["n"] == 1 else good_output

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_flaky_create)
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_openai_client",
        lambda: fake_client,
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

    assert call_count["n"] == 2, "actor must retry exactly once on budget violation"
    assert bank.status == "reviewing"
    questions = await get_bank_questions(db, bank.id)
    # The good output's signals are A and B at 4 min each — second call wins.
    mandatory_total = sum(q.estimated_minutes for q in questions if q.is_mandatory)
    assert mandatory_total <= stage.duration_minutes


@pytest.mark.asyncio
async def test_generate_one_bank_fails_after_repeated_budget_violations(
    db, monkeypatch
):
    """If the LLM produces over-budget output on EVERY attempt, the bank fails.

    With MAX_BUDGET_RETRIES=1, the actor calls the LLM at most twice. If
    both outputs violate the budget, the second BudgetExceededError
    propagates → outer except → bank.status='failed' with the violation
    in the error message.
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="A"), _signal(value="B")],
    )
    instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=15,
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    transition_to_generating(bank)
    await db.flush()

    # Both calls return the same over-budget output.
    over_budget = StageQuestionBankOutput(
        questions=[
            _build_question(
                position=i, text=f"Mandatory {v}", signal_values=[v],
                is_mandatory=True, estimated_minutes=10.0,
            )
            for i, v in enumerate(("A", "B"))
        ],
    )

    call_count = {"n": 0}

    async def _always_overbudget(**_kwargs):
        call_count["n"] += 1
        return over_budget

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_always_overbudget)
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_openai_client",
        lambda: fake_client,
    )

    from app.modules.question_bank.errors import BudgetExceededError
    with pytest.raises(BudgetExceededError):
        await _generate_one_bank(
            db,
            bank=bank,
            stage=stage,
            instance=instance,
            job=job,
            snapshot=snapshot,
            started_by=user.id,
        )

    # Two LLM calls (initial + 1 retry, MAX_BUDGET_RETRIES=1).
    assert call_count["n"] == 2
    assert bank.status == "failed"
    assert bank.generation_error is not None
    assert "Budget violation" in bank.generation_error


@pytest.mark.asyncio
async def test_generate_stage_disallowed_for_human_interview(db):
    """STAGE_TYPE_TO_PROMPT no longer includes human_interview / take_home.

    Direct test of the constant — the API guard at the endpoint level
    relies on this mapping; the SSE list_banks endpoint hides bank cards
    for stages whose type isn't in the mapping; the pipeline actor
    filters out non-eligible stages before generation. All three rely
    on this single source of truth.
    """
    from app.modules.question_bank.actors import STAGE_TYPE_TO_PROMPT

    assert "phone_screen" in STAGE_TYPE_TO_PROMPT
    assert "ai_screening" in STAGE_TYPE_TO_PROMPT
    assert "human_interview" not in STAGE_TYPE_TO_PROMPT
    assert "take_home" not in STAGE_TYPE_TO_PROMPT
