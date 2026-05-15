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
  4. ActivationPredicateFailure / ActivationPredicatesFailed — raised by
     activate_job() when one or more activation gate predicates fail.
     Mapped to HTTP 422 at the router layer with a structured predicates_failed
     array so the frontend can surface exactly which checks need attention.

Rich exception detail is still captured in structlog / Sentry — we only
sanitize what reaches the DB and the frontend."""

from dataclasses import dataclass
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
    """Raised by /enrich and /extract-signals when no ancestor of the target
    org unit has a completed company_profile. The profile is required by the
    enrichment + signal-extraction prompts (they read it via ancestry walk)
    so we surface a 422 before the LLM call, with org_unit_id in the body
    so the frontend can deep-link to the Company Profile tab."""

    def __init__(self, org_unit_id: UUID) -> None:
        self.org_unit_id = org_unit_id
        super().__init__(
            f"Org unit {org_unit_id} has no ancestor with a completed company profile"
        )


class EmptyRawJDError(Exception):
    """Raised by /enrich and /extract-signals when the job's description_raw
    is empty or whitespace-only. Mapped to HTTP 422 at the router layer.
    The recruiter is expected to add the JD via PATCH /api/jobs/{id} before
    triggering enrichment or extraction."""

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(
            f"Job {job_id} has no description_raw — add the JD before enriching or extracting"
        )


class JobNotEditableError(Exception):
    """Raised by PATCH /api/jobs/{id} when the job is not in 'draft' status.
    Editing basics or the raw JD after signal extraction would invalidate
    the snapshot. Mapped to HTTP 409 Conflict at the router layer."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"Job is in status '{status}'; only draft jobs can be edited"
        )


@dataclass
class ActivationPredicateFailure:
    """A single failed activation gate predicate.

    code — machine-readable string the frontend uses to render the right
    error chip (e.g. 'missing_interviewer').
    message — human-readable description shown in the UI.
    stage_id — set when the failure is tied to a specific stage; None
    for pipeline-level failures such as 'no_pipeline' or 'no_middle_stage'.
    """

    code: str
    message: str
    stage_id: UUID | None = None


class ActivationPredicatesFailed(Exception):
    """Raised by activate_job() when one or more activation gate predicates fail.

    Carries the full list of failures so the router can return all of them
    in a single 422 response rather than stopping at the first failure.
    """

    def __init__(self, failures: list[ActivationPredicateFailure]) -> None:
        self.failures = failures
        super().__init__(f"{len(failures)} activation predicate(s) failed")


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
    openai.PermissionDeniedError:
        "AI provider access denied — check model permissions. Contact support.",
    openai.NotFoundError:
        "AI model not found — check configuration. Contact support.",
    openai.InternalServerError:
        "The AI provider encountered an internal error. Please retry.",
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
