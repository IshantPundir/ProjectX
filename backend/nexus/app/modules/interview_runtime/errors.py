"""Typed errors for interview_runtime — mapped to HTTP codes by app/main.py."""


class EngineTokenInvalidError(Exception):
    """401 — JWT signature/algorithm/claim/expiry/replay failure."""


class StageNotAiDrivenError(Exception):
    """422 — stage_type not in (ai_screening, phone_screen)."""

    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        super().__init__(f"stage_type={stage_type} does not run an AI agent")


class QuestionBankNotReadyError(Exception):
    """409 — bank.status != 'ready' or is_stale."""


class SessionNotActiveError(Exception):
    """409 — record_session_result called against a non-active session."""


class CompanyProfileMissingError(Exception):
    """422 — org-unit ancestry walk found no company profile."""
