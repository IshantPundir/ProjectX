"""Authorization guards for /api/ats/* routes.

ATS connection management is a high-privilege operation (credential storage,
sync trigger). Restricted to super_admin per spec; the require_super_admin
guard from auth/context handles the existing DB-backed check.

A future `ats_admin` permission can be added to roles/permissions.py to
allow Recruiting Operations to manage ATS without granting full super_admin.
For MVP, we delegate entirely to require_super_admin.
"""
from __future__ import annotations

from app.modules.auth.context import require_super_admin

# Re-export for /api/ats/* route handlers — single import site for
# the auth dependency this module needs.
require_ats_admin = require_super_admin
