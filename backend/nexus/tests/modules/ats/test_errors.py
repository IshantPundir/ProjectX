"""Hierarchy + behavioral tests for the ATS exception classes.

The actor logic (later phase) classifies catches by whether the exception is
ATSPermanentError or ATSTransientError. These tests pin that classification.
"""
from __future__ import annotations

import pytest

from app.modules.ats.errors import (
    ATSError,
    ATSPermanentError, ATSCredentialsInvalidError, ATSAuthorizationError,
    ATSVendorContractError, ATSUnknownVendorError, ATSConnectionNotFoundError,
    ATSTransientError, ATSNetworkError, ATSRateLimitedError,
)


def test_permanent_subclasses():
    for cls in (ATSCredentialsInvalidError, ATSAuthorizationError,
                ATSVendorContractError, ATSUnknownVendorError,
                ATSConnectionNotFoundError):
        assert issubclass(cls, ATSPermanentError)
        assert issubclass(cls, ATSError)


def test_transient_subclasses():
    assert issubclass(ATSNetworkError, ATSTransientError)
    assert issubclass(ATSRateLimitedError, ATSTransientError)


def test_permanent_and_transient_are_disjoint():
    for cls in (ATSNetworkError, ATSRateLimitedError):
        assert not issubclass(cls, ATSPermanentError)
    for cls in (ATSCredentialsInvalidError, ATSAuthorizationError,
                ATSVendorContractError):
        assert not issubclass(cls, ATSTransientError)


def test_rate_limited_carries_retry_after():
    exc = ATSRateLimitedError(retry_after_seconds=42, message="429 from vendor")
    assert exc.retry_after_seconds == 42
    assert "42" in str(exc)


def test_rate_limited_default_message():
    exc = ATSRateLimitedError(retry_after_seconds=60)
    assert "60" in str(exc)
