"""Custom exceptions for the candidates module.

Each class maps to a specific HTTP status via the router's exception handler:
  404 — CandidateNotFoundError
  409 — DuplicateEmailError, AssignmentAlreadyExistsError, CandidateHasActiveSessionError
  422 — InvalidStageTransitionError, StageNotInPipelineError,
        ResumeNotFoundInS3Error, InvalidResumeContentTypeError
"""


class CandidateNotFoundError(Exception):
    """404 — candidate_id does not exist in the caller's tenant scope."""


class DuplicateEmailError(Exception):
    """409 — a candidate with this email already exists in this tenant."""

    def __init__(self, email: str) -> None:
        super().__init__(f"Candidate with email {email} already exists in this tenant")
        self.email = email


class AssignmentAlreadyExistsError(Exception):
    """409 — candidate is already assigned to this job_posting_id."""


class StageNotInPipelineError(Exception):
    """422 — target_stage_id is not a stage of the assignment's JD pipeline."""

    def __init__(self, stage_id: str) -> None:
        super().__init__(f"Stage {stage_id} is not part of this job's pipeline")
        self.stage_id = stage_id


class InvalidStageTransitionError(Exception):
    """422 — transition rejected (e.g. assignment is not in an active state)."""


class CandidateHasActiveSessionError(Exception):
    """409 — GDPR redaction blocked because an assignment has an active session."""


class ResumeNotFoundInS3Error(Exception):
    """422 — resume confirm step called but S3 HEAD returned 404."""


class InvalidResumeContentTypeError(Exception):
    """422 — S3 HEAD returned content-type other than application/pdf."""


class JobNotActiveError(Exception):
    """Raised when attempting to assign a candidate to a job that is not in 'active' status."""

    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__(
            f"Job is in '{current_status}' state; activation required to accept candidates"
        )
