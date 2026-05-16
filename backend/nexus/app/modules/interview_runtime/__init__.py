"""Phase 3C.2 — Interview engine runtime helpers.

Provides build_session_config and record_session_result — called
in-process by the merged interview_engine worker. The /api/internal/*
HTTP boundary and the engine dispatch JWT were retired in Phase 3 of
the modular-monolith uplift.
"""

from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    KnockoutFailure,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SessionResult,
    SignalMetadata,
    StageConfig,
    SteeringObservation,
    TranscriptEntry,
)
from app.modules.interview_runtime.service import (
    build_session_config,
    record_session_result,
)

__all__ = [
    "CandidateContext",
    "CompanyContext",
    "CompanyProfileMissingError",
    "KnockoutFailure",
    "QuestionBankNotReadyError",
    "QuestionConfig",
    "QuestionRubric",
    "SessionConfig",
    "SessionResult",
    "SignalMetadata",
    "StageConfig",
    "SteeringObservation",
    "TranscriptEntry",
    "build_session_config",
    "record_session_result",
]
