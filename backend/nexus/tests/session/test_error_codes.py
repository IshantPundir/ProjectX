"""Tests for app.modules.session.error_codes.classify_engine_exception."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
)
from app.modules.session.error_codes import classify_engine_exception


class _TinyModel(BaseModel):
    """Used to produce a real pydantic ValidationError for the test."""

    name: str = Field(min_length=5)


@pytest.mark.parametrize(
    ("exc_factory", "expected"),
    [
        (lambda: CompanyProfileMissingError("missing"), "engine_company_profile_missing"),
        (lambda: QuestionBankNotReadyError("not ready"), "engine_question_bank_not_ready"),
        # Real pydantic ValidationError — caught by module-path check, not by isinstance,
        # so classify_engine_exception doesn't have to import pydantic_core internals.
        (lambda: _force_validation_error(), "engine_session_config_invalid"),
        (lambda: RuntimeError("kaboom"), "engine_internal_error"),
        (lambda: ValueError("nope"), "engine_internal_error"),
    ],
)
def test_classify_engine_exception(exc_factory, expected):
    exc = exc_factory()
    assert classify_engine_exception(exc) == expected


def _force_validation_error():
    try:
        _TinyModel(name="x")
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("expected ValidationError to be raised")
