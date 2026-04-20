from app.modules.scheduler.errors import (
    AssignmentNotActiveError,
    InvalidStageTypeForInviteError,
    SessionAlreadyStartedError,
)


def test_invalid_stage_type_carries_type():
    e = InvalidStageTypeForInviteError(stage_type="manual_review")
    assert "manual_review" in str(e)


def test_other_errors_instantiate():
    assert isinstance(AssignmentNotActiveError(), Exception)
    assert isinstance(SessionAlreadyStartedError(), Exception)
