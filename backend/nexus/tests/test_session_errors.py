from app.modules.session.errors import (
    IllegalStartStateError,
    InvalidOtpError,
    InvalidSessionStateError,
    OtpExpiredError,
    OtpMaxAttemptsReachedError,
    OtpRateLimitedError,
    OtpRequiredError,
    SessionNotFoundError,
    TokenAlreadyUsedError,
    TokenSupersededError,
)


def test_invalid_otp_error_carries_attempts_remaining():
    e = InvalidOtpError(attempts_remaining=2)
    assert e.attempts_remaining == 2


def test_otp_rate_limited_error_carries_retry_after():
    e = OtpRateLimitedError(retry_after_seconds=42)
    assert e.retry_after_seconds == 42


def test_plain_errors_instantiate():
    for cls in [
        IllegalStartStateError, InvalidSessionStateError, OtpRequiredError,
        OtpExpiredError, OtpMaxAttemptsReachedError, SessionNotFoundError,
        TokenAlreadyUsedError, TokenSupersededError,
    ]:
        err = cls()
        assert isinstance(err, Exception)
