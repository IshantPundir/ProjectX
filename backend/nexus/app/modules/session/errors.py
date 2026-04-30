"""Custom exceptions for the session module.

HTTP mapping (applied by main.py exception handlers):
  404 — SessionNotFoundError
  401 — TokenSupersededError
  409 — IllegalStartStateError, InvalidSessionStateError, TokenAlreadyUsedError
  422 — OtpRequiredError, OtpExpiredError, OtpMaxAttemptsReachedError, InvalidOtpError
  429 — OtpRateLimitedError
  501 — LIVEKIT_INTEGRATION_PENDING (returned by service, not raised as exception)
  502 — AgentDispatchFailedError
"""


class SessionNotFoundError(Exception):
    """404 — session_id not found in tenant scope."""


class TokenSupersededError(Exception):
    """401 — JWT valid but its DB row has been superseded or the candidate_session_tokens row is missing."""


class IllegalStartStateError(Exception):
    """409 — POST /start called when state != 'consented'."""


class InvalidSessionStateError(Exception):
    """409 — any candidate endpoint called from a state that forbids it."""


class OtpRequiredError(Exception):
    """422 — /start called with otp_required=true but otp_verified_at is None."""


class OtpRateLimitedError(Exception):
    """429 — request-otp called within 60s of last issuance."""
    def __init__(self, retry_after_seconds: int = 60) -> None:
        super().__init__(f"Retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class OtpExpiredError(Exception):
    """422 — verify-otp called > 10 minutes after otp_issued_at."""


class OtpMaxAttemptsReachedError(Exception):
    """422 — 3rd failed verify-otp; hash wiped, must request a new code."""


class InvalidOtpError(Exception):
    """422 — verify-otp code mismatch (attempts remaining > 0)."""
    def __init__(self, attempts_remaining: int) -> None:
        super().__init__(f"Invalid OTP; {attempts_remaining} attempts remaining")
        self.attempts_remaining = attempts_remaining


class TokenAlreadyUsedError(Exception):
    """409 — POST /start on a token already consumed by a prior /start."""


class AgentDispatchFailedError(Exception):
    """502 — LiveKit dispatch raised; token NOT consumed; candidate can retry."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
