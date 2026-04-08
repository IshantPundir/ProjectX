"""Tests for JD exceptions and status_error sanitization."""

import uuid

import openai
import pytest

from app.modules.jd.errors import (
    CompanyProfileIncompleteError,
    IllegalTransitionError,
    sanitize_error_for_user,
)


def test_illegal_transition_error_fields():
    exc = IllegalTransitionError("draft", "signals_extracted")
    assert exc.from_state == "draft"
    assert exc.to_state == "signals_extracted"
    assert "draft" in str(exc)
    assert "signals_extracted" in str(exc)


def test_company_profile_incomplete_carries_org_unit_id():
    unit_id = uuid.uuid4()
    exc = CompanyProfileIncompleteError(unit_id)
    assert exc.org_unit_id == unit_id


def test_sanitize_openai_rate_limit():
    """Rate limit messages should map to a fixed safe string regardless of
    sensitive content in the original exception."""
    class FakeResponse:
        request = None
        status_code = 429
        headers = {}
    exc = openai.RateLimitError(
        "rate limit hit with sensitive key sk-abc and url https://api.openai.com/v1/x",
        response=FakeResponse(),
        body=None,
    )
    msg = sanitize_error_for_user(exc)
    assert "rate-limiting" in msg.lower()
    assert "sk-abc" not in msg
    assert "sensitive" not in msg
    assert "api.openai.com" not in msg


def test_sanitize_unknown_exception_returns_default():
    """Exceptions without a mapping fall through to the safe default."""
    class WeirdError(Exception):
        pass
    msg = sanitize_error_for_user(WeirdError("internal path /app/secrets/key.pem"))
    assert "please retry" in msg.lower()
    assert "/app/secrets" not in msg
