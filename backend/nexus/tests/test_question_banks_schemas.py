"""Pydantic schema validation tests for question_bank."""

import pytest
from pydantic import ValidationError

from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    GeneratedQuestion,
    QuestionRubric,
    ReorderBody,
    StageQuestionBankOutput,
    UpdateQuestionBody,
)


def _valid_rubric() -> QuestionRubric:
    return QuestionRubric(
        excellent="A strong answer names specific tools and describes hypothesis-verify flow.",
        meets_bar="An acceptable answer mentions at least one tool and shows structure.",
        below_bar="A weak answer is vague with no tools and no structure.",
    )


def _valid_generated_question(**overrides) -> dict:
    base = dict(
        position=0,
        text="Walk me through a production incident you handled.",
        signal_values=["Incident response"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=["What tools did you use?"],
        positive_evidence=[
            "Names specific tools",
            "Describes hypothesis-verify",
            "Mentions post-mortem",
        ],
        red_flags=["No specific tools", "Blames team"],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
        question_kind="technical_depth",
    )
    base.update(overrides)
    return base


def test_valid_generated_question_parses():
    q = GeneratedQuestion(**_valid_generated_question())
    assert q.position == 0
    assert len(q.positive_evidence) >= 3


def test_generated_question_rejects_too_many_signal_values():
    with pytest.raises(ValidationError):
        GeneratedQuestion(
            **_valid_generated_question(signal_values=["A", "B", "C", "D"]),
        )


def test_generated_question_rejects_too_few_positive_evidence():
    with pytest.raises(ValidationError):
        GeneratedQuestion(
            **_valid_generated_question(positive_evidence=["only one"]),
        )


def test_generated_question_rejects_estimated_minutes_too_large():
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(estimated_minutes=20.0))


def test_stage_question_bank_output_requires_at_least_one_question():
    with pytest.raises(ValidationError):
        StageQuestionBankOutput(
            questions=[],
        )


def test_create_question_body_forbids_extra_fields():
    with pytest.raises(ValidationError):
        CreateQuestionBody(
            **_valid_generated_question(),
            unknown_field="oops",
        )


def test_update_question_body_accepts_partial():
    # All fields optional in UpdateQuestionBody
    body = UpdateQuestionBody(text="New question text")
    assert body.text == "New question text"
    assert body.signal_values is None


def test_reorder_body_rejects_empty_list():
    with pytest.raises(ValidationError):
        ReorderBody(question_ids=[])


def test_generated_question_requires_question_kind():
    """The strict Literal field must be present — instructor relies on this
    to reject any LLM output that omits the kind."""
    base = _valid_generated_question()
    base.pop("question_kind", None)  # ensure it's not present
    with pytest.raises(ValidationError):
        GeneratedQuestion(**base)


@pytest.mark.parametrize(
    "kind", ["technical_depth", "behavioral_star", "compliance_binary"]
)
def test_generated_question_accepts_each_generator_kind(kind):
    """All 3 generator-allowed kinds parse cleanly."""
    q = GeneratedQuestion(**_valid_generated_question(question_kind=kind))
    assert q.question_kind == kind


def test_generated_question_rejects_open_culture():
    """`open_culture` is reserved for the engine-side Literal only — the
    generator must not emit it. instructor enforces this on every LLM call."""
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(question_kind="open_culture"))


def test_generated_question_rejects_unknown_kind():
    """Any out-of-Literal value is rejected."""
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(question_kind="not_a_kind"))
