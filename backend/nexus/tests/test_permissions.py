"""Tests for permission validation logic."""

import pytest
from app.modules.auth.permissions import (
    ALL_PERMISSIONS,
    SUPER_ADMIN_PERMISSIONS,
    validate_permissions,
    require_permission,
)


def test_super_admin_has_all_permissions():
    assert set(SUPER_ADMIN_PERMISSIONS) == ALL_PERMISSIONS


def test_validate_subset_passes():
    parent = ["jobs.create", "jobs.manage", "candidates.view"]
    child = ["jobs.create", "candidates.view"]
    validate_permissions(child, parent)  # should not raise


def test_validate_exceeds_parent_fails():
    parent = ["jobs.create"]
    child = ["jobs.create", "jobs.manage"]
    with pytest.raises(ValueError, match="exceed"):
        validate_permissions(child, parent)


def test_validate_unknown_permission_fails():
    with pytest.raises(ValueError, match="Unknown"):
        validate_permissions(["fake.permission"], list(ALL_PERMISSIONS))


def test_require_permission_passes():
    require_permission(["jobs.create", "jobs.manage"], "jobs.create")


def test_require_permission_fails():
    with pytest.raises(ValueError, match="do not have"):
        require_permission(["jobs.create"], "jobs.manage")
