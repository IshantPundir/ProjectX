"""Unit tests for BehavioralStarTask construction + @function_tool behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


@pytest.fixture
def sample_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-bhv-1",
        position=0,
        text="Tell me about a time you led a team through a tight deadline.",
        signal_values=["leadership"],
        estimated_minutes=4.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["delegation", "communication", "outcome"],
        red_flags=["solo_hero", "blame_team"],
        rubric=QuestionRubric(
            excellent="excellent rubric body",
            meets_bar="meets-bar rubric body",
            below_bar="below-bar rubric body",
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
        question_kind="behavioral_star",
    )


def _make_task(question, controller=None) -> BehavioralStarTask:
    return BehavioralStarTask(
        question_config=question,
        controller=controller,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal="<<INTERNAL_RUBRIC>>...stub...<<END_INTERNAL_RUBRIC>>",
    )


class TestConstruction:
    def test_kind_is_behavioral_star(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.kind == "behavioral_star"

    def test_max_probes_is_two(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.max_probes == 2

    def test_initial_partial_state_all_components_none(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t._partial.star_components == {
            "situation": None, "task": None, "action": None, "result": None,
        }

    def test_instructions_contains_question_text(self, sample_question) -> None:
        t = _make_task(sample_question)
        body = t.build_task_instructions()
        assert sample_question.text in body
        assert "<<INTERNAL_RUBRIC>>" in body


class TestRecordBehavioralAnswer:
    async def test_all_four_covered_returns_complete_instruction(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx,
            situation="Last year",
            task="Lead the migration",
            action="Broke into chunks",
            result="Shipped on time",
        )
        assert "Complete answer recorded" in msg
        assert "complete_question" in msg

    async def test_all_four_null_returns_non_answer_instruction(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx, situation=None, task=None, action=None, result=None
        )
        assert "Non-answer recorded" in msg
        assert "Do not probe" in msg

    async def test_partial_with_probes_remaining_lists_missing(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx, situation="Last year", task="Lead", action=None, result=None,
        )
        assert "Components missing" in msg
        assert "action" in msg
        assert "result" in msg
        assert "2 probe" in msg or "2 probes" in msg

    async def test_partial_with_no_probes_remaining_returns_exhausted(self, sample_question) -> None:
        t = _make_task(sample_question)
        t._probes_fired = 2  # exhausted
        ctx = MagicMock()
        msg = await t.record_behavioral_answer(
            ctx, situation="Last year", task=None, action=None, result=None,
        )
        assert "probe budget exhausted" in msg.lower()
        assert "complete_question" in msg

    async def test_cumulative_update_preserves_prior_fills(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        # First call fills situation+task only.
        await t.record_behavioral_answer(
            ctx, situation="Last year", task="Lead the migration",
            action=None, result=None,
        )
        # Second call (after a probe) fills action.
        await t.record_behavioral_answer(
            ctx, situation=None, task=None,
            action="Broke into chunks", result=None,
        )
        # Cumulative state should have situation+task+action filled.
        assert t._partial.star_components["situation"] == "Last year"
        assert t._partial.star_components["task"] == "Lead the migration"
        assert t._partial.star_components["action"] == "Broke into chunks"
        assert t._partial.star_components["result"] is None


class TestRequestStarProbe:
    async def test_increments_probe_counter(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Establish partial state first so the budget check has context.
        await t.record_behavioral_answer(
            ctx, situation="Last year", task=None, action=None, result=None,
        )
        msg = await t.request_star_probe(ctx, missing_component="task")
        assert t._probes_fired == 1
        assert "task" in msg.lower() or "follow-up" in msg.lower()

    async def test_refuses_probe_when_budget_exhausted(self, sample_question) -> None:
        t = _make_task(sample_question)
        t._probes_fired = 2
        ctx = MagicMock()
        msg = await t.request_star_probe(ctx, missing_component="action")
        assert "exhausted" in msg.lower()
        assert t._probes_fired == 2  # not incremented

    async def test_refuses_probe_on_non_answer_state(self, sample_question) -> None:
        """Q5 case B: probing a non-answer (all components null) is forbidden."""
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Record a non-answer state first.
        await t.record_behavioral_answer(
            ctx, situation=None, task=None, action=None, result=None,
        )
        msg = await t.request_star_probe(ctx, missing_component="situation")
        assert "non-answer" in msg.lower() or "cannot probe" in msg.lower()
        assert t._probes_fired == 0

    async def test_refuses_second_probe_when_no_progress(self, sample_question) -> None:
        """Q5 case C: probe-then-non-answer should not unlock a 2nd probe."""
        t = _make_task(sample_question)
        ctx = MagicMock()
        # Fill partial state.
        await t.record_behavioral_answer(
            ctx, situation="Last year", task=None, action=None, result=None,
        )
        # Fire probe 1 successfully.
        await t.request_star_probe(ctx, missing_component="action")
        assert t._probes_fired == 1
        # Candidate's follow-up was a non-answer — no new fields filled.
        await t.record_behavioral_answer(
            ctx, situation=None, task=None, action=None, result=None,
        )
        # Asking for probe 2 should be refused (no progress since last probe).
        msg = await t.request_star_probe(ctx, missing_component="result")
        assert ("no progress" in msg.lower() or "non-answer" in msg.lower()
                or "cannot probe" in msg.lower())
        assert t._probes_fired == 1


class TestCompleteQuestion:
    async def test_resolves_await_with_task_result(self, sample_question) -> None:
        t = _make_task(sample_question)
        ctx = MagicMock()
        await t.record_behavioral_answer(
            ctx, situation="Last year", task="Lead",
            action="Broke into chunks", result="Shipped on time",
        )
        # Capture the complete() call by stubbing it.
        captured: dict = {}

        def fake_complete(result):
            captured["result"] = result

        t.complete = fake_complete  # type: ignore[method-assign]
        await t.complete_question(ctx)
        result = captured["result"]
        assert isinstance(result, TaskResult)
        assert result.kind == "behavioral_star"
        assert result.star_components["situation"] == "Last year"
        assert result.star_components["result"] == "Shipped on time"
        assert result.forced is False
        assert result.probes_fired == 0


class TestForceComplete:
    def test_returns_forced_result_with_kind_behavioral_star(self, sample_question) -> None:
        t = _make_task(sample_question)
        r = t.force_complete(reason="task_timeout")
        assert isinstance(r, TaskResult)
        assert r.kind == "behavioral_star"
        assert r.forced is True
        assert r.forced_reason == "task_timeout"
        # Star components carry whatever partial state existed (initial = all None).
        assert r.star_components == {
            "situation": None, "task": None, "action": None, "result": None,
        }
