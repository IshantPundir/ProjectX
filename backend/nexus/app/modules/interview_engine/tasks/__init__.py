"""Per-question task subclasses for the InterviewController.

Phase 2 ships QuestionTask (abstract) + TechnicalDepthTask (concrete).
Phase 3 adds BehavioralStarTask + ComplianceBinaryTask and extends
build_task_for to route on question_kind.
"""

from __future__ import annotations

from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)
from app.modules.interview_engine.tasks.technical_depth import (
    TechnicalDepthTask,
)

__all__ = [
    "QuestionTask",
    "TaskResult",
    "TechnicalDepthTask",
    "build_task_for",
]


def build_task_for(question, *, controller, disqualified_signals):
    """Factory: route a QuestionConfig to the right task subclass.

    Phase 2: always TechnicalDepthTask. Phase 3 adds routing on
    question.question_kind once the field exists.
    """
    return TechnicalDepthTask(
        question_config=question,
        controller=controller,
        disqualified_signals=disqualified_signals,
        rubric_internal=_build_rubric_block(question),
    )


def _build_rubric_block(question) -> str:
    """Assemble the <<INTERNAL_RUBRIC>> string for the task prompt."""
    return (
        "<<INTERNAL_RUBRIC>>\n"
        f"Question: {question.text}\n"
        f"Signals: {', '.join(question.signal_values)}\n"
        f"Positive evidence: {'; '.join(question.positive_evidence)}\n"
        f"Red flags: {'; '.join(question.red_flags)}\n"
        f"Excellent: {question.rubric.excellent}\n"
        f"Meets bar: {question.rubric.meets_bar}\n"
        f"Below bar: {question.rubric.below_bar}\n"
        f"Evaluation hint: {question.evaluation_hint}\n"
        "<<END_INTERNAL_RUBRIC>>"
    )
