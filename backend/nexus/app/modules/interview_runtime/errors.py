"""Typed errors for interview_runtime — mapped to HTTP codes by app/main.py."""


class StageNotAiDrivenError(Exception):
    """422 — stage_type not in (ai_screening, phone_screen)."""

    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        super().__init__(f"stage_type={stage_type} does not run an AI agent")


class QuestionBankNotReadyError(Exception):
    """409 — bank.status != 'confirmed' or is_stale.

    'confirmed' is the recruiter-final terminal state in the question_bank
    state machine (draft → generating → reviewing → confirmed). The engine
    refuses to run an interview against a bank the recruiter hasn't
    explicitly signed off on.
    """


class SessionNotActiveError(Exception):
    """409 — record_session_result called against a non-active session."""


class CompanyProfileMissingError(Exception):
    """422 — org-unit ancestry walk found no company profile."""


class EmptySignalMetadataError(Exception):
    """422 — projected SessionConfig.signal_metadata is empty.

    The structured AI Screening Agent's Orchestrator + SignalLedger
    require at least one tracked signal to make question-selection,
    coverage, and knockout decisions. Upstream invariants make this
    case unreachable in production: ``ExtractedSignals.signals`` enforces
    ``min_length=5`` (`app/ai/schemas.py`), so a confirmed snapshot
    always has ≥ 5 valid signals; ``_project_signal_metadata`` only drops
    rows that fail strict shape validation, which a confirmed snapshot
    should never contain.

    If this fires, it means a confirmed snapshot somehow carries either
    zero signals or only off-spec rows — a data-integrity bug worth
    failing loud at the engine boundary rather than degrading silently
    into an orchestrator that has no signals to track.
    """
