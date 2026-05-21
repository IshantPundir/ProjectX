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
        primary_signal="Incident response",
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
        question_kind="technical_scenario",
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
    "kind", ["experience_check", "behavioral", "technical_scenario", "compliance_binary"]
)
def test_generated_question_accepts_each_generator_kind(kind):
    """All 4 generator-allowed kinds parse cleanly."""
    q = GeneratedQuestion(**_valid_generated_question(question_kind=kind))
    assert q.question_kind == kind


def test_generated_question_rejects_open_culture():
    """`open_culture` is not a valid kind in the new taxonomy."""
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(question_kind="open_culture"))


def test_generated_question_rejects_unknown_kind():
    """Any out-of-Literal value is rejected."""
    with pytest.raises(ValidationError):
        GeneratedQuestion(**_valid_generated_question(question_kind="not_a_kind"))


def test_generated_question_new_kind_and_primary_signal():
    from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric

    q = GeneratedQuestion(
        position=0,
        text="Walk me through a REST connector you built — how did you handle auth?",
        primary_signal="rest_api_integration",
        signal_values=["rest_api_integration", "auth_flows"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=["How did you handle pagination?", "What about retries on 5xx?"],
        positive_evidence=["names a real auth scheme", "describes token refresh", "mentions error handling"],
        red_flags=["vague 'just used the SDK'", "no error handling"],
        rubric=QuestionRubric(
            excellent="Names scheme, refresh, and failure handling concretely.",
            meets_bar="Describes the auth scheme and basic error handling.",
            below_bar="Cannot describe how auth worked at all.",
        ),
        evaluation_hint="Looking for hands-on connector ownership, not SDK hand-waving.",
        question_kind="technical_scenario",
    )
    assert q.primary_signal == "rest_api_integration"
    assert q.question_kind == "technical_scenario"


def test_generated_question_rejects_old_kind():
    import pytest
    from pydantic import ValidationError
    from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric

    with pytest.raises(ValidationError):
        GeneratedQuestion(
            position=0, text="x" * 20, primary_signal="s", signal_values=["s"],
            estimated_minutes=1.0, is_mandatory=False, follow_ups=[],
            positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
            rubric=QuestionRubric(excellent="a" * 20, meets_bar="b" * 20, below_bar="c" * 20),
            evaluation_hint="e" * 10, question_kind="technical_depth",  # old → rejected
        )
