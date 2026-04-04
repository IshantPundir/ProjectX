"""Permission constants and validation for the hierarchical permission system.

Permissions are stored as a JSONB array of strings on public.users.permissions.
They are NOT in the JWT — fetch them via GET /api/auth/me.

Rule: a user can only grant a subset of their own permissions.
"""

ALL_PERMISSIONS: frozenset[str] = frozenset({
    "users.invite_admins",
    "users.invite_users",
    "users.deactivate",
    "org_units.create",
    "org_units.manage",
    "jobs.create",
    "jobs.manage",
    "candidates.view",
    "candidates.evaluate",
    "candidates.advance",
    "interviews.schedule",
    "interviews.conduct",
    "reports.view",
    "reports.export",
    "settings.client",
    "settings.integrations",
})

SUPER_ADMIN_PERMISSIONS: list[str] = sorted(ALL_PERMISSIONS)


def validate_permissions(
    new_permissions: list[str],
    parent_permissions: list[str],
) -> None:
    """Raise ValueError if new_permissions exceeds parent_permissions."""
    new_set = set(new_permissions)
    unknown = new_set - ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"Unknown permissions: {sorted(unknown)}")
    parent_set = set(parent_permissions)
    excess = new_set - parent_set
    if excess:
        raise ValueError(
            f"Cannot grant permissions that exceed your own: {sorted(excess)}"
        )


def require_permission(user_permissions: list[str], permission: str) -> None:
    """Raise ValueError if user does not hold the given permission."""
    if permission not in user_permissions:
        raise ValueError(f"You do not have the '{permission}' permission")
