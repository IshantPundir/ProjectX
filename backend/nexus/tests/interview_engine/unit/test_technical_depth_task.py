"""Unit tests for TechnicalDepthTask construction + force_complete."""

from __future__ import annotations

import pytest

from app.modules.interview_engine.tasks.technical_depth import TechnicalDepthTask
from app.modules.interview_engine.tasks.base import TaskResult


def _make_task(question, controller=None):
    return TechnicalDepthTask(
        question_config=question,
        controller=controller,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal="<<INTERNAL_RUBRIC>>...stub...<<END_INTERNAL_RUBRIC>>",
    )


class TestConstruction:
    def test_kind_is_technical_depth(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.kind == "technical_depth"

    def test_max_probes_is_one(self, sample_question) -> None:
        t = _make_task(sample_question)
        assert t.max_probes == 1

    def test_instructions_contains_question_text(self, sample_question) -> None:
        t = _make_task(sample_question)
        # Instructions are passed up through AgentTask.__init__; we don't
        # poke into LiveKit internals. Instead, we test the assembly helper
        # directly: build_task_instructions() returns a string containing
        # the question's text.
        body = t.build_task_instructions()
        assert sample_question.text in body
        assert "<<INTERNAL_RUBRIC>>" in body


class TestForceCompleteIntegration:
    def test_returns_task_result_with_kind_filled(self, sample_question) -> None:
        t = _make_task(sample_question)
        r = t.force_complete(reason="task_timeout")
        assert isinstance(r, TaskResult)
        assert r.kind == "technical_depth"
        assert r.forced is True


class TestFactory:
    def test_build_task_for_returns_technical_depth_in_phase_2(self, sample_question) -> None:
        from app.modules.interview_engine.tasks import build_task_for
        t = build_task_for(
            sample_question,
            controller=None,  # type: ignore[arg-type]
            disqualified_signals=frozenset(),
        )
        assert isinstance(t, TechnicalDepthTask)


@pytest.fixture
def sample_question():
    from app.modules.interview_runtime.schemas import (
        QuestionConfig,
        QuestionRubric,
    )
    return QuestionConfig(
        id="q-1",
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
