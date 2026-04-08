"""JD module exceptions + user-facing error sanitization.

Three responsibilities co-located in one file:
  1. IllegalTransitionError — raised by state_machine.transition(),
     mapped to HTTP 409 Conflict at the router layer.
  2. CompanyProfileIncompleteError — raised by create_job_posting() when
     no ancestor has a completed company profile. Mapped to HTTP 422 at
     the router layer, with org_unit_id in the body so the frontend can
     deep-link to the Company Profile tab.
  3. sanitize_error_for_user() — maps third-party exception TYPES to
     fixed safe user-facing strings. The raw str(exc) from an OpenAI or
     instructor failure may leak API URLs, keys, request IDs, file paths,
     or prompt payloads — none of which should reach job_posting.status_error
     or the frontend.

Rich exception detail is still captured in structlog / Sentry — we only
sanitize what reaches the DB and the frontend."""

from typing import Final
from uuid import UUID

import openai

# Day-1 Task 5 verification: instructor 1.12.0 deprecates instructor.exceptions
# in favor of instructor.core. Both expose the same class object, but the new
# path avoids a startup DeprecationWarning that would pollute production logs.
from instructor.core import InstructorRetryException

# --- Exception classes ----------------------------------------------------

class IllegalTransitionError(Exception):
    """Raised when code attempts an illegal job_posting.status transition.
    Mapped to HTTP 409 Conflict at the router layer with a state-specific
    message (see app/main.py exception handler)."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Illegal transition: {from_state} → {to_state}")


class CompanyProfileIncompleteError(Exception):
    """Raised by create_job_posting() when no ancestor of the target org unit
    has a completed company_profile. Mapped to HTTP 422 Unprocessable Entity
    at the router layer, with org_unit_id in the body."""

    def __init__(self, org_unit_id: UUID) -> None:
        self.org_unit_id = org_unit_id
        super().__init__(
            f"Org unit {org_unit_id} has no ancestor with a completed company profile"
        )


# --- Error sanitization for job_posting.status_error ---------------------

# Day-1 Task 5 verified: instructor 1.12.0 raises InstructorRetryException
# from instructor.core when max_retries is exceeded. Use the canonical path.

_SAFE_MESSAGES: Final[dict[type[Exception], str]] = {
    openai.RateLimitError:
        "Our AI provider is rate-limiting us. Please retry in a minute.",
    openai.APITimeoutError:
        "The AI provider timed out. Please retry.",
    openai.APIConnectionError:
        "Could not reach the AI provider. Please retry.",
    openai.AuthenticationError:
        "AI provider authentication failed. Contact support.",
    openai.BadRequestError:
        "The job description could not be processed. Please check the input and retry.",
    InstructorRetryException:
        "The AI response did not match the expected format after retries. Please retry.",
}

_DEFAULT_MESSAGE: Final[str] = (
    "Extraction failed — please retry. Contact support if this persists."
)


def sanitize_error_for_user(exc: Exception) -> str:
    """Return a safe user-facing message for the given exception.

    NEVER returns str(exc) or any fragment of the exception's args —
    only fixed strings from _SAFE_MESSAGES or the default."""
    for exc_type, message in _SAFE_MESSAGES.items():
        if isinstance(exc, exc_type):
            return message
    return _DEFAULT_MESSAGE
