"""Typed exception hierarchy for ATS adapter operations.

The Dramatiq actor (app/modules/ats/actors.py) catches these to decide:
  - ATSRateLimitedError  → advance next_poll_at, return cleanly (no retry)
  - ATSPermanentError    → disable connection, raise (lands in DLQ)
  - ATSTransientError    → re-raise so Dramatiq retries with exp backoff
  - any other Exception  → unexpected, treat as transient (Dramatiq retries)
"""
from __future__ import annotations


class ATSError(Exception):
    """Base class for all ATS adapter errors."""


# ----- Permanent (orchestrator disables connection, surfaces in UI) -----

class ATSPermanentError(ATSError):
    """Non-retryable. Caller must take action."""


class ATSCredentialsInvalidError(ATSPermanentError):
    """Auth failed even after refresh attempt. Recruiter must reconnect."""


class ATSAuthorizationError(ATSPermanentError):
    """API key has insufficient scope. Recruiter must regenerate."""


class ATSVendorContractError(ATSPermanentError):
    """Vendor returned a response we cannot parse — schema drift.
    Logged with full raw payload; engineering action required."""


class ATSUnknownVendorError(ATSPermanentError):
    """No adapter registered for the connection's vendor."""


class ATSConnectionNotFoundError(ATSPermanentError):
    """The connection row referenced by the actor no longer exists."""


# ----- Transient (Dramatiq retries) -----

class ATSTransientError(ATSError):
    """Retryable."""


class ATSNetworkError(ATSTransientError):
    """Network failure, 5xx response, connection timeout."""


class ATSRateLimitedError(ATSTransientError):
    """Vendor said 'wait N seconds'. Actor sets next_poll_at = now() + N
    and exits cleanly (no Dramatiq retry; next tick resumes naturally)."""

    def __init__(self, retry_after_seconds: int, message: str = "") -> None:
        super().__init__(
            message or f"Rate limited; retry after {retry_after_seconds}s"
        )
        self.retry_after_seconds = retry_after_seconds
