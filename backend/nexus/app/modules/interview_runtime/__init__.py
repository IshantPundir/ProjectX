"""Phase 3C.2 — Interview engine runtime helpers.

Provides build_session_config and record_session_result — called
in-process by the interview_engine worker. The /api/internal/*
HTTP boundary and the engine dispatch JWT were retired in Phase 3 of
the modular-monolith uplift.
"""

from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
)
from app.modules.interview_runtime.results import (
    ClaimEntry,
    ClaimsPoolSnapshot,
    CoverageState,
    LedgerEntry,
    QuestionQueueSnapshot,
    QuestionState,
    QuestionStatus,
    SignalLedgerSnapshot,
    SignalSnapshot,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SessionResult,
    SignalMetadata,
    StageConfig,
    SteeringObservation,
    TranscriptEntry,
    WordTiming,
)
from app.modules.interview_runtime.service import (
    _project_signal_metadata as project_signal_metadata,
)
from app.modules.interview_runtime.service import (
    build_session_config,
    record_engine_heartbeat,
    record_session_evidence,
    record_session_result,
)
from app.modules.interview_runtime.transcript_timing import (
    asked_at_ms_by_question_evidence,
    relative_words,
)

__all__ = [
    "CandidateContext",
    "ClaimEntry",
    "ClaimsPoolSnapshot",
    "CompanyContext",
    "CompanyProfileMissingError",
    "CoverageState",
    "LedgerEntry",
    "QuestionBankNotReadyError",
    "QuestionConfig",
    "QuestionQueueSnapshot",
    "QuestionRubric",
    "QuestionState",
    "QuestionStatus",
    "SessionConfig",
    "SessionResult",
    "SignalLedgerSnapshot",
    "SignalMetadata",
    "SignalSnapshot",
    "StageConfig",
    "SteeringObservation",
    "TranscriptEntry",
    "WordTiming",
    "asked_at_ms_by_question_evidence",
    "build_session_config",
    "project_signal_metadata",
    "record_engine_heartbeat",
    "record_session_evidence",
    "record_session_result",
    "relative_words",
]
