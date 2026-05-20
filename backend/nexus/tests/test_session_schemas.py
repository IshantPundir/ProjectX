"""Pydantic schemas for candidate-facing session endpoints."""
import uuid

import pytest
from pydantic import ValidationError

from app.modules.session.schemas import (
    ConsentRequest,
    PreCheckResponse,
    SessionState,
    VerifyOtpRequest,
)


def test_consent_request_requires_consented_true():
    with pytest.raises(ValidationError):
        ConsentRequest(consented=False, user_agent="Mozilla/5.0")
    ok = ConsentRequest(consented=True, user_agent="Mozilla/5.0")
    assert ok.consented is True


def test_consent_request_forbids_extras():
    with pytest.raises(ValidationError):
        ConsentRequest(consented=True, user_agent="UA", extra="x")


def test_verify_otp_rejects_non_6_digit_codes():
    with pytest.raises(ValidationError):
        VerifyOtpRequest(code="12345")     # too short
    with pytest.raises(ValidationError):
        VerifyOtpRequest(code="1234567")   # too long
    with pytest.raises(ValidationError):
        VerifyOtpRequest(code="abcdef")    # non-numeric
    ok = VerifyOtpRequest(code="123456")
    assert ok.code == "123456"


def test_session_state_enum_values():
    assert set(SessionState) == {
        SessionState.CREATED, SessionState.PRE_CHECK, SessionState.CONSENTED,
        SessionState.ACTIVE, SessionState.COMPLETED,
        SessionState.CANCELLED, SessionState.ERROR, SessionState.TERMINATED,
    }


def test_pre_check_response_round_trips():
    resp = PreCheckResponse(
        session_id=uuid.uuid4(),
        company_name="Acme",
        job_title="Engineer",
        stage_name="AI Interview",
        duration_minutes=30,
        consent_text="I consent…",
        state=SessionState.PRE_CHECK,
        otp_required=True,
        otp_verified_at=None,
        otp_issued_at=None,
        proctoring_enabled=True,
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["otp_required"] is True
    assert dumped["state"] == "pre_check"
    assert dumped["proctoring_enabled"] is True
