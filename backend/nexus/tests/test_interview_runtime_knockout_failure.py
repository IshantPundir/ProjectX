"""Pure-unit tests for KnockoutFailure + _scrub_pii.

Defense-in-depth: the LLM prompt instructs the agent never to include
PII in `knockout_reason`. The Pydantic field validator on
`KnockoutFailure.reason` runs `_scrub_pii` on every construction path
(including model_validate from a DB read), unconditionally. Together
with prompt + RLS, these are 3 layers of defense.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.interview_runtime import KnockoutFailure


# --- _scrub_pii: emails ---


def test_scrub_email_simple() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate said reach me at john@acme.com after work.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "[redacted]" in f.reason
    assert "john@acme.com" not in f.reason


def test_scrub_email_with_plus_addressing() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Send confirmation to j.smith+work@example.io please.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "j.smith+work@example.io" not in f.reason


# --- _scrub_pii: phones ---


def test_scrub_phone_us_dashed() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate's number is +1 555-123-4567 for follow-up.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "555-123-4567" not in f.reason


def test_scrub_phone_us_parens() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Reachable on (555) 123-4567 anytime.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "(555) 123-4567" not in f.reason


def test_scrub_phone_dotted() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Best line is 555.123.4567 weekdays.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert "555.123.4567" not in f.reason


# --- _scrub_pii: passes plain text ---


def test_plain_text_passes_through() -> None:
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate stated they cannot work UK shift hours.",
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    )
    assert f.reason == "Candidate stated they cannot work UK shift hours."


def test_short_numbers_not_scrubbed() -> None:
    """Phone regex requires 8+ digits — short numeric runs should pass."""
    f = KnockoutFailure(
        question_id="q1",
        reason="Candidate has 5 years of experience.",
        signal_values=["years_exp"],
        occurred_at_ms=1000,
    )
    assert "5 years" in f.reason


# --- _scrub_pii: idempotent ---


def test_scrub_idempotent() -> None:
    text = "Contact me at john@acme.com or +1 555-123-4567."
    once = KnockoutFailure(
        question_id="q1",
        reason=text,
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    ).reason
    twice = KnockoutFailure(
        question_id="q1",
        reason=once,
        signal_values=["uk_shift"],
        occurred_at_ms=1000,
    ).reason
    assert once == twice


# --- field constraints ---


def test_question_id_min_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="",
            reason="Cannot work UK shift hours.",
            signal_values=["uk_shift"],
            occurred_at_ms=1000,
        )


def test_reason_min_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="",
            signal_values=["uk_shift"],
            occurred_at_ms=1000,
        )


def test_reason_max_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="x" * 501,  # 500 is the cap
            signal_values=["uk_shift"],
            occurred_at_ms=1000,
        )


def test_signal_values_min_length() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="Cannot work UK shift hours.",
            signal_values=[],
            occurred_at_ms=1000,
        )


def test_occurred_at_ms_non_negative() -> None:
    with pytest.raises(ValidationError):
        KnockoutFailure(
            question_id="q1",
            reason="Cannot work UK shift hours.",
            signal_values=["uk_shift"],
            occurred_at_ms=-1,
        )


# --- model_validate (DB read) path runs the scrub too ---


def test_model_validate_runs_scrub() -> None:
    """Defense-in-depth: a row inserted before scrub was active must
    still be scrubbed when read back via model_validate."""
    raw = {
        "question_id": "q1",
        "reason": "My number is +1 555-123-4567.",
        "signal_values": ["uk_shift"],
        "occurred_at_ms": 1000,
    }
    f = KnockoutFailure.model_validate(raw)
    assert "555-123-4567" not in f.reason
