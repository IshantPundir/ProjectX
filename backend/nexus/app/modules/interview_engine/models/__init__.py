"""Pydantic models for the structured interview engine.

This package re-exports every model class so callers can import from one place:

    from app.modules.interview_engine.models import JudgeOutput, Observation, LedgerEntry, ...

The Judge-emitted ClaimEntry shape (no captured_at_*) is exposed as `JudgeClaimEntry`
to avoid clashing with the canonical `ClaimEntry` from claims.py.
"""
from app.modules.interview_engine.models.judge import (
    NextAction,
    CoverageTransition,
    Observation,
    ClaimEntry as JudgeClaimEntry,
    TurnMetadata,
    AdvancePayload,
    ProbePayload,
    ClarifyPayload,
    RepeatPayload,
    RedirectPayload,
    AcknowledgeNoExperiencePayload,
    PoliteClosePayload,
    EndSessionPayload,
    NextActionPayload,
    JudgeOutput,
)
from app.modules.interview_engine.models.speaker import (
    InstructionKind,
    SpeakerInput,
)
from app.modules.interview_engine.models.ledger import (
    CoverageState,
    LedgerEntry,
    SignalSnapshot,
    SignalLedgerSnapshot,
)
from app.modules.interview_engine.models.queue import (
    QuestionStatus,
    QuestionState,
    QuestionQueueSnapshot,
)
from app.modules.interview_engine.models.claims import (
    ClaimEntry,
    ClaimsPoolSnapshot,
)


__all__ = [
    # judge
    "NextAction", "CoverageTransition",
    "Observation", "JudgeClaimEntry", "TurnMetadata",
    "AdvancePayload", "ProbePayload", "ClarifyPayload", "RepeatPayload",
    "RedirectPayload", "AcknowledgeNoExperiencePayload",
    "PoliteClosePayload", "EndSessionPayload",
    "NextActionPayload", "JudgeOutput",
    # speaker
    "InstructionKind", "SpeakerInput",
    # ledger
    "CoverageState", "LedgerEntry", "SignalSnapshot", "SignalLedgerSnapshot",
    # queue
    "QuestionStatus", "QuestionState", "QuestionQueueSnapshot",
    # claims
    "ClaimEntry", "ClaimsPoolSnapshot",
]
