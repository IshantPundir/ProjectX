"""Unit tests for ComplianceBinaryTask construction + @function_tool behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_engine.tasks.compliance_binary import ComplianceBinaryTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


@pytest.fixture
def sample_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-comp-1",
        position=0,
        text="Are you able to work UK shift hours, roughly 2pm to 10pm Pacific?",
        signal_values=["uk_shift_availability"],
        estimated_minutes=2.0,  # > 1 minute on purpose so the 60s cap is meaningful
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["confirms_availability", "no_caveats", "clear_yes"],
        red_flags=["declines_with_no_alternative", "hedging_answer"],
        rubric=QuestionRubric(
            excellent="excellent rubric body",
            meets_bar="meets-bar rubric body",
            below_bar="below-bar rubric body",
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
        question_kind="compliance_binary",
    )


def _make_task(question, controller=None) -> ComplianceBinaryTask:
    return ComplianceBinaryTask(
        question_config=question,
        controller=controller,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal="<<INTERNAL_RUBRIC>>...stub...<<END_INTERNAL_RUBRIC>>",
    )


class TestConstruction:
    def test_kind_is_compliance_binary(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.kind == "compliance_binary"

    def test_max_probes_is_zero(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.max_probes == 0

    def test_budget_seconds_hard_cap_is_60(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.budget_seconds_hard_cap == 60.0

    def test_class_attribute_budget_cap_inspectable_without_instance(self) -> None:
        # The factory's effective_budget_seconds_for reads this off the class.
        assert ComplianceBinaryTask.budget_seconds_hard_cap == 60.0

    def test_initial_clarification_used_is_false(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t._clarification_used is False

    def test_instructions_contains_question_text(self, sample_question) -> None:
        t = _make_task(sample_question)
        body = t.build_task_instructions()
        assert sample_question.text in body
        assert "<<INTERNAL_RUBRIC>>" in body


class TestRecordComplianceAttestation:
    async def test_confirmed_true_resolves_with_correct_result(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        msg = await t.record_compliance_attestation(
            ctx, confirmed=True, reason_or_example="Worked UK hours before",
        )
        result = captured["r"]
        assert isinstance(result, TaskResult)
        assert result.kind == "compliance_binary"
        assert result.compliance_confirmed is True
        assert result.compliance_reason_or_example == "Worked UK hours before"
        assert result.compliance_clarification_used is False
        assert result.knockout is False
        assert "complete" in msg.lower() or "controller" in msg.lower()

    async def test_confirmed_false_resolves_correctly(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        await t.record_compliance_attestation(
            ctx, confirmed=False, reason_or_example="Declined; cited family commitment",
        )
        result = captured["r"]
        assert result.compliance_confirmed is False
        assert result.compliance_reason_or_example == "Declined; cited family commitment"

    async def test_clarification_used_flag_propagates_to_result(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Simulate clarification having fired first.
        await t.request_compliance_clarification(ctx)
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        await t.record_compliance_attestation(
            ctx, confirmed=False, reason_or_example="Ambiguous response; did not confirm",
        )
        result = captured["r"]
        assert result.compliance_clarification_used is True

    async def test_knockout_flag_carries_through_when_disqualify_called_first(
        self, sample_question
    ) -> None:
        """The LLM is instructed to call disqualify_knockout BEFORE the
        terminal record_compliance_attestation when it's a hard 'no'. The
        knockout flag should persist into the TaskResult."""
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Simulate disqualify_knockout firing first (sets _partial.knockout).
        await t.disqualify_knockout(ctx, reason="Cannot work UK shift")
        captured: dict = {}
        t.complete = lambda r: captured.setdefault("r", r)  # type: ignore[method-assign]
        await t.record_compliance_attestation(
            ctx, confirmed=False, reason_or_example="No; UK shift not feasible",
        )
        result = captured["r"]
        assert result.compliance_confirmed is False
        assert result.knockout is True
        assert result.knockout_reason == "Cannot work UK shift"


class TestRequestComplianceClarification:
    async def test_first_call_returns_ask_once_instruction(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.request_compliance_clarification(ctx)
        assert "to confirm" in msg.lower() or "yes or no" in msg.lower()
        assert t._clarification_used is True

    async def test_second_call_returns_already_clarified(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        await t.request_compliance_clarification(ctx)
        msg = await t.request_compliance_clarification(ctx)
        assert "already clarified" in msg.lower() or "ambiguous" in msg.lower()
        # Counter doesn't double-fire.
        assert t._clarification_used is True


class TestForceComplete:
    def test_returns_forced_result_with_kind_compliance_binary(self, sample_question) -> None:
        t = _make_task(sample_question)
        r = t.force_complete(reason="task_timeout")
        assert isinstance(r, TaskResult)
        assert r.kind == "compliance_binary"
        assert r.forced is True
        assert r.forced_reason == "task_timeout"
        # Compliance fields default None when nothing was recorded.
        assert r.compliance_confirmed is None
        assert r.compliance_reason_or_example is None
        assert r.compliance_clarification_used is False
