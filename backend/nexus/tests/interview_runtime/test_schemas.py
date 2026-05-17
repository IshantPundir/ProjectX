"""Schema-level tests for interview_runtime models."""

from __future__ import annotations

import pytest

from app.modules.interview_runtime.schemas import (
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
)


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


class TestCompanyContextFreeText:
    """CompanyContext is prompt context — accept whatever the recruiter wrote.

    Regression for an outage where the wire-contract caps (about ≤500,
    hiring_bar ≤280) silently diverged from the recruiter-edit form,
    crashing the engine entrypoint after the candidate had already joined
    the LiveKit room.
    """

    def test_accepts_long_about(self) -> None:
        ctx = CompanyContext(
            about="x" * 5000,
            industry="software",
            hiring_bar="ok",
        )
        assert len(ctx.about) == 5000

    def test_accepts_long_hiring_bar(self) -> None:
        ctx = CompanyContext(
            about="ok",
            industry="software",
            hiring_bar="y" * 2000,
        )
        assert len(ctx.hiring_bar) == 2000

    def test_accepts_short_about_and_hiring_bar(self) -> None:
        # No min_length either — recruiter writing a terse profile must
        # not crash the engine. Non-emptiness is gated upstream by
        # find_company_profile_in_ancestry.
        ctx = CompanyContext(about="a", industry="x", hiring_bar="b")
        assert ctx.about == "a"
        assert ctx.hiring_bar == "b"


class TestSignalMetadataType:
    """Signal metadata `type` field is required and validated at runtime."""

    def test_signal_metadata_has_type_field(self) -> None:
        from app.modules.interview_runtime.schemas import SignalMetadata
        sm = SignalMetadata(
            value="sig1",
            type="competency",
            priority="required",
            weight=2,
            knockout=False,
            stage="screen",
            evaluation_method="verbal_response",
        )
        assert sm.type == "competency"

    def test_signal_metadata_type_must_be_in_literal(self) -> None:
        from pydantic import ValidationError
        from app.modules.interview_runtime.schemas import SignalMetadata
        with pytest.raises(ValidationError):
            SignalMetadata(
                value="sig1",
                type="invalid_type",
                priority="required",
                weight=2,
                knockout=False,
                stage="screen",
                evaluation_method="verbal_response",
            )

    def test_signal_metadata_type_accepts_all_four_values(self) -> None:
        from app.modules.interview_runtime.schemas import SignalMetadata
        for t in ("experience", "credential", "competency", "behavioral"):
            sm = SignalMetadata(
                value="sig1",
                type=t,
                priority="required",
                weight=2,
                knockout=False,
                stage="screen",
                evaluation_method="verbal_response",
            )
            assert sm.type == t
