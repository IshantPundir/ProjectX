"""Orchestrator — pure state-machine + ledger code for the structured
AI Screening Agent. No I/O, no LLMs, no DB.

Public API:

* ``SignalLedger`` / ``SignalState`` / ``EvidenceQuote`` /
  ``CoverageStatus`` — per-signal coverage tracking with append-only
  evidence and forward-only normal coverage transitions.
* ``InterviewState`` / ``QuestionState`` / ``InterviewPhase`` /
  ``ExitMode`` — the orchestrator's state-machine record with
  legal-transition enforcement.
* ``LedgerInvariantError`` / ``InterviewPhaseError`` — typed errors
  raised when an attempted mutation would violate an invariant.

Persistence (Redis fire-and-forget) lives in ``persistence.py`` (A.4).
The structured agent class itself lands in ``structured_agent.py`` (B).
"""
from app.modules.interview_engine.orchestrator.flow import (
    evaluate_exit_condition,
    pick_next_question,
)
from app.modules.interview_engine.orchestrator.ledger import (
    CoverageStatus,
    EvidenceQuote,
    EvidenceStrength,
    LedgerInvariantError,
    SignalLedger,
    SignalState,
)
from app.modules.interview_engine.orchestrator.persistence import (
    DEFAULT_TTL_SECONDS,
    LedgerPersistence,
)
from app.modules.interview_engine.orchestrator.state import (
    AskedMode,
    ExitMode,
    InterviewPhase,
    InterviewPhaseError,
    InterviewState,
    QuestionState,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "AskedMode",
    "CoverageStatus",
    "EvidenceQuote",
    "EvidenceStrength",
    "ExitMode",
    "InterviewPhase",
    "InterviewPhaseError",
    "InterviewState",
    "LedgerInvariantError",
    "LedgerPersistence",
    "QuestionState",
    "SignalLedger",
    "SignalState",
    "evaluate_exit_condition",
    "pick_next_question",
]
