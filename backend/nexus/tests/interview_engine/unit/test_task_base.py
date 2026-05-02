"""Unit tests for QuestionTask base — TaskResult shape and force_complete."""

from __future__ import annotations

import pytest

from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)


class TestTaskResultDefaults:
    def test_defaults_for_required_fields(self) -> None:
        r = TaskResult(question_id="q-1", kind="technical_depth")
        assert r.signals_lacked == []
        assert r.evidence_keys == []
        assert r.knockout is False
        assert r.knockout_reason is None
        assert r.forced is False
        assert r.forced_reason is None
        assert r.probes_fired == 0

    def test_tier_optional(self) -> None:
        r = TaskResult(question_id="q-1", kind="technical_depth")
        assert r.tier is None


class TestTaskResultRoundtrip:
    def test_serialize_then_validate(self) -> None:
        r = TaskResult(
            question_id="q-1",
            kind="technical_depth",
            tier="strong",
            evidence_keys=["k1", "k2"],
            signals_lacked=["python"],
            knockout=False,
            probes_fired=1,
        )
        roundtripped = TaskResult.model_validate(r.model_dump())
        assert roundtripped == r


# ---- Force-complete behavior ----
# QuestionTask is abstract; we test force_complete via a minimal concrete
# subclass that records observations into self._observations.

class _StubTask(QuestionTask):
    kind = "technical_depth"
    max_probes = 1

    async def run(self) -> TaskResult:  # pragma: no cover — not exercised here
        raise NotImplementedError

    def build_task_instructions(self) -> str:
        return "stub instructions"


def _make_stub_task(question):
    return _StubTask(
        question_config=question,
        controller=None,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal="stub rubric",
    )


class TestForceComplete:
    def test_returns_task_result_with_forced_true(self, sample_question) -> None:
        task = _make_stub_task(sample_question)
        result = task.force_complete(reason="task_timeout")
        assert result.question_id == sample_question.id
        assert result.kind == "technical_depth"
        assert result.forced is True
        assert result.forced_reason == "task_timeout"

    def test_uses_partial_observation_state(self, sample_question) -> None:
        task = _make_stub_task(sample_question)
        # Simulate the LLM having recorded an observation before the watchdog fired.
        task._record_partial_assessment(
            tier="below_bar",
            evidence_keys=["k1"],
            signals_lacked=["python"],
            non_answer=False,
        )
        result = task.force_complete(reason="task_timeout")
        assert result.tier == "below_bar"
        assert result.evidence_keys == ["k1"]
        assert result.signals_lacked == ["python"]


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
