"""Tests for permission constants."""

from app.modules.auth.permissions import ALL_PERMISSIONS


def test_all_permissions_is_frozenset():
    assert isinstance(ALL_PERMISSIONS, frozenset)


def test_all_permissions_count():
    assert len(ALL_PERMISSIONS) == 18


def test_known_permissions_present():
    assert "jobs.create" in ALL_PERMISSIONS
    assert "candidates.view" in ALL_PERMISSIONS
    assert "interviews.conduct" in ALL_PERMISSIONS
    assert "reports.export" in ALL_PERMISSIONS
    assert "settings.client" in ALL_PERMISSIONS


def test_jobs_view_permission_exists():
    """Phase 2A adds jobs.view as a new canonical permission."""
    from app.modules.auth.permissions import ALL_PERMISSIONS
    assert "jobs.view" in ALL_PERMISSIONS


def test_candidates_permissions_registered():
    """Phase 3B: candidates.view + candidates.manage must be in ALL_PERMISSIONS."""
    from app.modules.auth.permissions import ALL_PERMISSIONS
    assert "candidates.view" in ALL_PERMISSIONS
    assert "candidates.manage" in ALL_PERMISSIONS
