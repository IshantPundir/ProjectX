import uuid

import pytest
from pydantic import ValidationError

from app.modules.scheduler.schemas import InviteCreateRequest, InviteResponse


def test_invite_create_request_minimum():
    req = InviteCreateRequest(assignment_id=uuid.uuid4())
    assert req.otp_required is None


def test_invite_create_request_forbids_extras():
    with pytest.raises(ValidationError):
        InviteCreateRequest(assignment_id=uuid.uuid4(), stage_id=uuid.uuid4())


def test_invite_create_request_rejects_missing_assignment():
    with pytest.raises(ValidationError):
        InviteCreateRequest()


def test_invite_response_round_trip():
    from datetime import datetime, UTC
    resp = InviteResponse(
        session_id=uuid.uuid4(),
        token_expires_at=datetime.now(UTC),
    )
    assert resp.session_id is not None
