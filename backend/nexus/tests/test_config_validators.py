"""Tests for field_validators on app.config.Settings.

These are environment-sensitive security invariants — changing them should
surface loudly in review. See MED-1 (notifications dry-run guard) and the
existing _candidate_secret_required validator.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _make(**overrides):
    # Build a fresh Settings bypassing the .env file so the test is hermetic.
    defaults = dict(
        candidate_jwt_secret="test-secret-32-chars-long-0000000",
        environment="development",
        notifications_dry_run=True,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


def test_dry_run_allowed_in_development() -> None:
    s = _make(environment="development", notifications_dry_run=True)
    assert s.notifications_dry_run is True


def test_dry_run_allowed_in_test() -> None:
    s = _make(environment="test", notifications_dry_run=True)
    assert s.notifications_dry_run is True


def test_dry_run_rejected_in_production() -> None:
    with pytest.raises(ValidationError) as exc:
        _make(environment="production", notifications_dry_run=True)
    assert "NOTIFICATIONS_DRY_RUN" in str(exc.value)


def test_dry_run_rejected_in_staging() -> None:
    with pytest.raises(ValidationError) as exc:
        _make(environment="staging", notifications_dry_run=True)
    assert "NOTIFICATIONS_DRY_RUN" in str(exc.value)


def test_dry_run_false_always_allowed() -> None:
    for env in ("development", "test", "staging", "production"):
        s = _make(environment=env, notifications_dry_run=False)
        assert s.notifications_dry_run is False
