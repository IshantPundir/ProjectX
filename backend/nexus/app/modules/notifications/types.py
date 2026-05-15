"""Canonical notification type IDs.

These are string identifiers that the future in-app notification dispatcher
keys on (per spec docs/superpowers/specs/2026-05-14-job-scoped-ats-sync-design.md
§ "Notifications wiring"). The current notifications module only exposes
email/SMS primitives — when the in-app notification panel lands (Phase D),
these IDs will be looked up against a per-type template + recipient
resolver pair.

Convention: snake_case, prefixed by the originating domain.
All constants are plain strings (not an enum).
"""

# ────────────────────────────── ATS notifications ─────────────────────

# Sync-run outcomes (super-admin recipients)
ATS_SYNC_PARTIAL = "ats_sync_partial"
ATS_SYNC_FAILED = "ats_sync_failed"
ATS_CONNECTION_DISABLED = "ats_connection_disabled"

# Job lifecycle (recruiter recipients — assigned + primary on the job)
ATS_JOB_IMPORTED = "ats_job_imported"
ATS_JOB_STATUS_CHANGED = "ats_job_status_changed"
ATS_JOB_FIELDS_UPDATED = "ats_job_fields_updated"
ATS_JOB_RECRUITER_ASSIGNMENTS_CHANGED = "ats_job_recruiter_assignments_changed"
ATS_JOB_ORPHAN_CLIENT = "ats_job_orphan_client"
ATS_JOB_QUARANTINED = "ats_job_quarantined"

# Submission lifecycle (recruiter + Hiring Manager recipients)
ATS_SUBMISSION_CREATED = "ats_submission_created"
ATS_SUBMISSION_STATUS_CHANGED = "ats_submission_status_changed"

# Data quality (super-admin recipients)
ATS_USER_LINKED_TO_NATIVE = "ats_user_linked_to_native"
ATS_USER_COLLISION_SKIPPED = "ats_user_collision_skipped"

# Mirror-mode safety (recruiter recipients on the affected job)
ATS_BORDERLINE_BLOCKED = "ats_borderline_blocked"
