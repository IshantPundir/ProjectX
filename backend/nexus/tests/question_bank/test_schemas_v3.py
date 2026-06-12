import pytest
from pydantic import ValidationError
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    FollowUpDimension,
    BankCritiqueOutput,
)


def _mk_question(kind: str) -> GeneratedQuestion:
    return GeneratedQuestion(
        position=0,
        text="Tell me about a project you personally drove end to end.",
        primary_signal="Distributed systems design",
        signal_values=["Distributed systems design"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[
            FollowUpDimension(
                dimension="decision_ownership",
                intent="verify they made the call, not just executed",
                seed_probe="What did you decide, and what did you choose it over?",
                listen_for=["a named alternative", "a concrete tradeoff"],
            )
        ],
        positive_evidence=["names a real decision", "states a number", "owns 'I did X'"],
        red_flags=["says 'we' with no 'I'", "cannot name a tradeoff against"],
        rubric=QuestionRubric(
            excellent="owns a real decision with a named alternative and a tradeoff",
            meets_bar="describes a real project with at least one concrete decision",
            below_bar="vague, 'we' framing, no recoverable decision",
        ),
        evaluation_hint="tests whether they drove decisions vs merely executed",
        question_kind=kind,
    )


def test_project_deepdive_is_a_valid_kind():
    q = _mk_question("project_deepdive")
    assert q.question_kind == "project_deepdive"


def test_unknown_kind_still_rejected():
    with pytest.raises(ValidationError):
        _mk_question("totally_made_up")


def test_bank_critique_output_carries_corrected_questions_and_log():
    out = BankCritiqueOutput(
        critique="Knockout 'X' was uncovered; added a compliance_binary. Sharpened 2 anchors.",
        questions=[_mk_question("project_deepdive")],
    )
    assert out.questions[0].question_kind == "project_deepdive"
    assert out.critique == (
        "Knockout 'X' was uncovered; added a compliance_binary. Sharpened 2 anchors."
    )


def test_bank_critique_rejects_too_short_critique():
    with pytest.raises(ValidationError):
        BankCritiqueOutput(critique="too short", questions=[_mk_question("behavioral")])
