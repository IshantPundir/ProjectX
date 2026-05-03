"""Per-question task factory.

Phase 3 extracts the inline factory that lived in tasks/__init__.py during
Phase 2. Routes a QuestionConfig to its task subclass via _ROUTING_TABLE,
falling back to TechnicalDepthTask for any unknown question_kind.

`effective_budget_seconds_for` consults the routed class's optional
`budget_seconds_hard_cap` attribute to compute the controller's per-task
watchdog timeout. Compliance tasks have a 60s hard cap; other kinds use
the standard estimated_minutes-based budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings
from app.modules.interview_engine.tasks.base import QuestionTask
from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_engine.tasks.compliance_binary import ComplianceBinaryTask
from app.modules.interview_engine.tasks.technical_depth import TechnicalDepthTask

if TYPE_CHECKING:
    from app.modules.interview_engine.controller import InterviewController
    from app.modules.interview_runtime import QuestionConfig


_ROUTING_TABLE: dict[str, type[QuestionTask]] = {
    "technical_depth": TechnicalDepthTask,
    "behavioral_star": BehavioralStarTask,
    "compliance_binary": ComplianceBinaryTask,
    "open_culture": TechnicalDepthTask,  # deferred — see overview spec §1.2
}


def build_task_for(
    question: "QuestionConfig",
    *,
    controller: "InterviewController",
    disqualified_signals: frozenset[str],
) -> QuestionTask:
    """Route a QuestionConfig to its task subclass.

    Falls back to TechnicalDepthTask for any unrecognized question_kind.
    Phase 4 lights this up by adding the question_kind DB column and
    updating the bank-generator to emit non-default values.
    """
    cls = _ROUTING_TABLE.get(question.question_kind, TechnicalDepthTask)
    return cls(
        question_config=question,
        controller=controller,
        disqualified_signals=disqualified_signals,
        rubric_internal=_build_rubric_block(question),
    )


def effective_budget_seconds_for(question: "QuestionConfig") -> float:
    """Return the watchdog timeout for this question's per-task dispatch.

    Returns min(estimated_minutes * 60 + overhead, per-kind hard cap if any).
    The only kind setting `budget_seconds_hard_cap` in Phase 3 is
    ComplianceBinaryTask (60s). Other kinds use the unconstrained budget.
    """
    cls = _ROUTING_TABLE.get(question.question_kind, TechnicalDepthTask)
    base = question.estimated_minutes * 60.0 + settings.engine_task_budget_overhead_seconds
    cap = getattr(cls, "budget_seconds_hard_cap", None)
    return min(base, cap) if cap is not None else base


def _build_rubric_block(question: "QuestionConfig") -> str:
    """Assemble the <<INTERNAL_RUBRIC>> string for the task prompt.

    Moved verbatim from the Phase 2 inline implementation in tasks/__init__.py.
    """
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
