"""Phase 3C.2 — Interview engine runtime helpers.

Provides build_session_config and record_session_result — called
in-process by the merged interview_engine worker. The /api/internal/*
HTTP boundary and the engine dispatch JWT were retired in Phase 3 of
the modular-monolith uplift.
"""

from app.modules.interview_runtime.schemas import (
    KnockoutFailure,
    QuestionConfig,
    QuestionResult,
    SessionConfig,
    SessionResult,
    SteeringObservation,
    TranscriptEntry,
)
from app.modules.interview_runtime.service import (
    build_session_config,
    record_session_result,
)

__all__ = [
    "KnockoutFailure",
    "QuestionConfig",
    "QuestionResult",
    "SessionConfig",
    "SessionResult",
    "SteeringObservation",
    "TranscriptEntry",
    "build_session_config",
    "record_session_result",
]
