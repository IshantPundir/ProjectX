"""Permission constants.

Permissions are derived from roles — never stored per-user.
This module defines the canonical permission set for validation.
"""

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
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
    }
)
