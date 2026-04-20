from app.modules.candidates.errors import (
    AssignmentAlreadyExistsError,
    CandidateHasActiveSessionError,
    CandidateNotFoundError,
    DuplicateEmailError,
    InvalidResumeContentTypeError,
    InvalidStageTransitionError,
    ResumeNotFoundInS3Error,
    StageNotInPipelineError,
)


def test_duplicate_email_error_carries_email_in_message():
    e = DuplicateEmailError("alice@example.com")
    assert "alice@example.com" in str(e)
    assert e.email == "alice@example.com"


def test_stage_not_in_pipeline_error_carries_stage_id():
    import uuid
    stage_id = str(uuid.uuid4())
    e = StageNotInPipelineError(stage_id)
    assert stage_id in str(e)
    assert e.stage_id == stage_id


def test_simple_error_classes_instantiate():
    """Others are marker exceptions — the router maps them to HTTP codes."""
    for cls in [
        CandidateNotFoundError,
        AssignmentAlreadyExistsError,
        InvalidStageTransitionError,
        CandidateHasActiveSessionError,
        ResumeNotFoundInS3Error,
        InvalidResumeContentTypeError,
    ]:
        err = cls()
        assert isinstance(err, Exception)
