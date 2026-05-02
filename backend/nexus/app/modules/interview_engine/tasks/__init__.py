"""Per-question task subclasses for the InterviewController.

Phase 2 shipped QuestionTask (abstract) + TechnicalDepthTask (concrete) +
the inline factory. Phase 3 extracted the factory to factory.py and adds
BehavioralStarTask + ComplianceBinaryTask + question_kind-based routing.
"""

from __future__ import annotations

from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)
from app.modules.interview_engine.tasks.behavioral import (
    BehavioralStarTask,
)
from app.modules.interview_engine.tasks.compliance_binary import (
    ComplianceBinaryTask,
)
from app.modules.interview_engine.tasks.factory import (
    build_task_for,
    effective_budget_seconds_for,
)
from app.modules.interview_engine.tasks.technical_depth import (
    TechnicalDepthTask,
)

__all__ = [
    "BehavioralStarTask",
    "ComplianceBinaryTask",
    "QuestionTask",
    "TaskResult",
    "TechnicalDepthTask",
    "build_task_for",
    "effective_budget_seconds_for",
]
