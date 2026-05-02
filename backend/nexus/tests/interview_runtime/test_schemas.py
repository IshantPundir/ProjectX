"""Schema-level tests for interview_runtime models."""

from __future__ import annotations

import pytest

from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _make_question(**overrides):
    base = dict(
        id="q-test",
        position=0,
        text="A long enough placeholder question text body goes here.",
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
    base.update(overrides)
    return QuestionConfig(**base)


class TestQuestionKindField:
    def test_question_kind_defaults_to_technical_depth(self) -> None:
        q = _make_question()
        assert q.question_kind == "technical_depth"

    def test_question_kind_accepts_behavioral_star(self) -> None:
        q = _make_question(question_kind="behavioral_star")
        assert q.question_kind == "behavioral_star"

    def test_question_kind_accepts_compliance_binary(self) -> None:
        q = _make_question(question_kind="compliance_binary")
        assert q.question_kind == "compliance_binary"

    def test_question_kind_accepts_open_culture(self) -> None:
        q = _make_question(question_kind="open_culture")
        assert q.question_kind == "open_culture"

    def test_question_kind_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError):
            _make_question(question_kind="not_a_real_kind")  # type: ignore[arg-type]
