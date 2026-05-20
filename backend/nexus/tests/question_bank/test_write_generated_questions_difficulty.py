"""Test that write_generated_questions stamps the stage difficulty onto every
AI-generated row it inserts.

Harness mirrors tests/test_question_banks_service.py — same helpers, same
fixture names. The `db` fixture is auto-discovered from tests/conftest.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.question_bank.models import StageQuestionBank
from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric
from app.modules.question_bank.service import (
    ensure_bank_exists,
    get_bank_questions,
    write_generated_questions,
)

# Re-use the helpers from the main service test module — no duplication.
from tests.test_question_banks_service import (
    _make_generated_question,
    _make_job_with_signals,
    _make_pipeline_and_stage,
    _setup_tenant_user_unit,
    _signal,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_generated_questions_stamps_stage_difficulty(db: AsyncSession) -> None:
    """Every AI-generated row written by write_generated_questions must carry
    the difficulty that was passed as stage_difficulty."""
    # Build the prerequisite chain: tenant -> user -> org-unit -> job -> stage -> bank
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db,
        tenant.id,
        unit.id,
        user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, difficulty="hard"
    )
    bank: StageQuestionBank = await ensure_bank_exists(db, stage=stage, job=job)

    # Construct a GeneratedQuestion and call write_generated_questions with
    # stage_difficulty="hard".
    q = _make_generated_question(position=0, text="Describe a hard Python challenge.")

    await write_generated_questions(
        db,
        bank=bank,
        questions=[q],
        source="ai_generated",
        stage_difficulty="hard",
    )

    rows = await get_bank_questions(db, bank.id)
    ai_rows = [r for r in rows if r.source == "ai_generated"]
    assert len(ai_rows) == 1, "Expected exactly one ai_generated row"
    assert ai_rows[0].difficulty == "hard", (
        f"Expected difficulty='hard', got {ai_rows[0].difficulty!r}"
    )


@pytest.mark.asyncio
async def test_write_generated_questions_null_difficulty_when_not_passed(db: AsyncSession) -> None:
    """When stage_difficulty is not passed (default None), the column stays NULL
    — existing callers are unaffected."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db,
        tenant.id,
        unit.id,
        user.id,
        signals=[_signal(value="Python")],
    )
    _instance, stage = await _make_pipeline_and_stage(
        db, job=job, difficulty="medium"
    )
    bank: StageQuestionBank = await ensure_bank_exists(db, stage=stage, job=job)

    q = _make_generated_question(position=0, text="Standard Python question.")

    # Intentionally do NOT pass stage_difficulty — old call signature.
    await write_generated_questions(
        db,
        bank=bank,
        questions=[q],
        source="ai_generated",
    )

    rows = await get_bank_questions(db, bank.id)
    ai_rows = [r for r in rows if r.source == "ai_generated"]
    assert len(ai_rows) == 1
    assert ai_rows[0].difficulty is None, (
        f"Expected difficulty=None, got {ai_rows[0].difficulty!r}"
    )
