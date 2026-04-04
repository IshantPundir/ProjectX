"""Tests for permission constants."""

from app.modules.auth.permissions import ALL_PERMISSIONS


def test_all_permissions_is_frozenset():
    assert isinstance(ALL_PERMISSIONS, frozenset)


def test_all_permissions_count():
    assert len(ALL_PERMISSIONS) == 16


def test_known_permissions_present():
    assert "jobs.create" in ALL_PERMISSIONS
    assert "candidates.view" in ALL_PERMISSIONS
    assert "interviews.conduct" in ALL_PERMISSIONS
    assert "reports.export" in ALL_PERMISSIONS
    assert "settings.client" in ALL_PERMISSIONS
