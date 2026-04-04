"""Tests for UserContext helper methods."""

import uuid

from app.modules.auth.context import RoleAssignment, UserContext


def _make_ctx(assignments: list[RoleAssignment], is_super_admin: bool = False) -> UserContext:
    """Create a UserContext with a mock user for testing."""
    from unittest.mock import MagicMock
    user = MagicMock()
    user.id = uuid.uuid4()
    return UserContext(user=user, is_super_admin=is_super_admin, assignments=assignments)


def test_has_role_in_unit_true():
    unit_id = uuid.uuid4()
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Admin", permissions=[]),
    ])
    assert ctx.has_role_in_unit(unit_id, "Admin") is True


def test_has_role_in_unit_false():
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=uuid.uuid4(), org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=[]),
    ])
    assert ctx.has_role_in_unit(uuid.uuid4(), "Admin") is False


def test_has_permission_in_unit():
    unit_id = uuid.uuid4()
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=["jobs.create", "jobs.manage"]),
    ])
    assert ctx.has_permission_in_unit(unit_id, "jobs.create") is True
    assert ctx.has_permission_in_unit(unit_id, "interviews.conduct") is False


def test_permissions_in_unit_union():
    unit_id = uuid.uuid4()
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=["jobs.create"]),
        RoleAssignment(org_unit_id=unit_id, org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Interviewer", permissions=["interviews.conduct"]),
    ])
    perms = ctx.permissions_in_unit(unit_id)
    assert perms == {"jobs.create", "interviews.conduct"}


def test_all_permissions_across_units():
    ctx = _make_ctx([
        RoleAssignment(org_unit_id=uuid.uuid4(), org_unit_name="Eng", role_id=uuid.uuid4(), role_name="Recruiter", permissions=["jobs.create"]),
        RoleAssignment(org_unit_id=uuid.uuid4(), org_unit_name="Sales", role_id=uuid.uuid4(), role_name="Observer", permissions=["candidates.view"]),
    ])
    assert ctx.all_permissions() == {"jobs.create", "candidates.view"}


def test_super_admin_flag():
    ctx = _make_ctx([], is_super_admin=True)
    assert ctx.is_super_admin is True
