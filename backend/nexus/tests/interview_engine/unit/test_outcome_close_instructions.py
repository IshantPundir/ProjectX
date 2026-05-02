"""Unit tests for closing_instructions_for — per-outcome closing strings."""

from __future__ import annotations

from typing import get_args

import pytest

from app.modules.interview_engine.outcome_close import (
    SessionOutcome,
    closing_instructions_for,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)


def make_config() -> SessionConfig:
    q = QuestionConfig(
        id="q1",
        position=0,
        text="Some sufficiently long question text here.",
        signal_values=["python"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["evidence_a", "evidence_b", "evidence_c"],
        red_flags=["red_flag_a", "red_flag_b"],
        rubric=QuestionRubric(
            excellent="excellent rubric body",
            meets_bar="meets-bar rubric body",
            below_bar="below-bar rubric body",
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
    )
    return SessionConfig(
        session_id="11111111-1111-1111-1111-111111111111",
        job_title="Senior Engineer",
        role_summary="A summary that is at least thirty characters long for the schema validators.",
        seniority_level="senior",
        candidate=CandidateContext(name="Test Candidate"),
        company=CompanyContext(
            about="Acme Co. is a long-enough about-company description for the schema validators.",
            industry="software",
            company_stage="growth",
            hiring_bar="A long-enough hiring bar description for the schema validators required length.",
        ),
        stage=StageConfig(
            stage_id="22222222-2222-2222-2222-222222222222",
            stage_type="ai_screening",
            name="Bot Screening",
            duration_minutes=15,
            difficulty="medium",
            questions=[q],
        ),
        signals=[],
    )


def test_every_outcome_returns_non_empty_string() -> None:
    cfg = make_config()
    for outcome in get_args(SessionOutcome):
        instructions = closing_instructions_for(outcome, cfg)
        assert isinstance(instructions, str)
        assert len(instructions.strip()) > 0


def test_completed_mentions_thank() -> None:
    cfg = make_config()
    out = closing_instructions_for("completed", cfg).lower()
    assert "thank" in out


def test_candidate_unresponsive_acknowledges_no_response() -> None:
    cfg = make_config()
    out = closing_instructions_for("candidate_unresponsive", cfg).lower()
    assert "respon" in out or "reach" in out  # "responded" or "reach you"


def test_error_keeps_message_short() -> None:
    cfg = make_config()
    out = closing_instructions_for("error", cfg)
    assert len(out) < 300


def test_unknown_outcome_raises() -> None:
    cfg = make_config()
    with pytest.raises(ValueError):
        closing_instructions_for("not_a_real_outcome", cfg)  # type: ignore[arg-type]
