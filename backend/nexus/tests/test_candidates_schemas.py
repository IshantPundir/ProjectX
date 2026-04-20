import uuid

import pytest
from pydantic import ValidationError

from app.modules.candidates.schemas import (
    AssignmentCreateRequest,
    AssignmentStatus,
    CandidateCreateRequest,
    CandidateSource,
    CandidateUpdateRequest,
    RedactPIIRequest,
    StageTransitionRequest,
)


def test_candidate_create_request_accepts_valid():
    req = CandidateCreateRequest(name="Alice", email="alice@example.com", phone="+1234567890")
    assert req.name == "Alice"
    assert req.source == CandidateSource.MANUAL


def test_candidate_create_request_rejects_empty_name():
    with pytest.raises(ValidationError):
        CandidateCreateRequest(name="", email="a@b.com")


def test_candidate_create_request_rejects_invalid_email():
    with pytest.raises(ValidationError):
        CandidateCreateRequest(name="Alice", email="not-an-email")


def test_candidate_create_request_forbids_extras():
    with pytest.raises(ValidationError):
        CandidateCreateRequest(name="Alice", email="a@b.com", unknown_field="x")


def test_candidate_update_request_accepts_partial():
    req = CandidateUpdateRequest(phone="+1555")
    assert req.phone == "+1555"
    assert req.name is None


def test_stage_transition_request_reason_optional():
    stage_id = uuid.uuid4()
    req = StageTransitionRequest(target_stage_id=stage_id)
    assert req.reason is None
    assert req.override is False


def test_assignment_create_request_target_stage_optional():
    req = AssignmentCreateRequest(job_posting_id=uuid.uuid4())
    assert req.target_stage_id is None


def test_assignment_status_values():
    assert set(AssignmentStatus) == {
        AssignmentStatus.ACTIVE,
        AssignmentStatus.ARCHIVED,
        AssignmentStatus.HIRED,
        AssignmentStatus.REJECTED,
        AssignmentStatus.WITHDRAWN,
    }


def test_redact_pii_requires_exact_confirmation_phrase():
    with pytest.raises(ValidationError):
        RedactPIIRequest(confirmation="yes")
    req = RedactPIIRequest(confirmation="I understand this permanently removes PII")
    assert req.confirmation == "I understand this permanently removes PII"
