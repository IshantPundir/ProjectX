"""Verifies the validator split supports per-call and post-merge modes."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.modules.question_bank.service import (
    validate_llm_output_against_snapshot,
    _apply_mandatory_correction_in_position_order,
)
from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric


def _q(position, is_mandatory, signal_values, kind="technical_scenario"):
    return GeneratedQuestion(
        position=position,
        text="Walk through your approach to X in detail to test something.",
        primary_signal=signal_values[0],
        signal_values=signal_values,
        estimated_minutes=2.0,
        is_mandatory=is_mandatory,
        follow_ups=[],
        positive_evidence=["a" * 25, "b" * 25, "c" * 25],
        red_flags=["red flag one example", "red flag two example"],
        rubric=QuestionRubric(
            excellent="excellent anchor a a a a a a",
            meets_bar="meets bar anchor b b b b b",
            below_bar="below bar anchor c c c c c",
        ),
        evaluation_hint="evaluation hint for this question item",
        question_kind=kind,
    )


def test_mandatory_correction_helper_promotes_earliest_knockout_to_mandatory():
    """Earliest question covering a knockout is upgraded to mandatory."""
    questions = [
        _q(0, is_mandatory=False, signal_values=["sig_a"]),
        _q(1, is_mandatory=True, signal_values=["sig_b"]),
    ]
    knockouts = {"sig_a"}
    _apply_mandatory_correction_in_position_order(
        questions=questions, knockout_values=knockouts,
    )
    assert questions[0].is_mandatory is True


def test_mandatory_correction_helper_demotes_duplicate_knockout_coverage():
    """Second question probing already-claimed knockout is demoted to optional."""
    questions = [
        _q(0, is_mandatory=True, signal_values=["sig_a"], kind="behavioral"),
        _q(1, is_mandatory=True, signal_values=["sig_a"], kind="technical_scenario"),
    ]
    knockouts = {"sig_a"}
    _apply_mandatory_correction_in_position_order(
        questions=questions, knockout_values=knockouts,
    )
    assert questions[0].is_mandatory is True
    assert questions[1].is_mandatory is False
