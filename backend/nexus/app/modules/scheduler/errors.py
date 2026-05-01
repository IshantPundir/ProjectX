"""Custom exceptions for the scheduler module.

HTTP mapping (via main.py handlers):
  409 — SessionAlreadyStartedError
  422 — InvalidStageTypeForInviteError, AssignmentNotActiveError
"""


class InvalidStageTypeForInviteError(Exception):
    """422 — assignment.current_stage.stage_type != 'ai_screening'."""
    def __init__(self, stage_type: str) -> None:
        super().__init__(f"Cannot send interview invite for stage_type={stage_type!r}")
        self.stage_type = stage_type


class AssignmentNotActiveError(Exception):
    """422 — invite dispatch attempted on archived/rejected/hired/withdrawn assignment."""


class SessionAlreadyStartedError(Exception):
    """409 — resend attempted on a session in active/completed/cancelled/error."""
