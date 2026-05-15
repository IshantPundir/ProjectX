"""Canonical audit action string constants.

Convention: resource.verb — lowercase, dot-separated.
All constants are plain strings (not an enum).
"""

# User actions
USER_INVITED = "user.invited"
USER_INVITE_RESENT = "user.invite_resent"
USER_INVITE_REVOKED = "user.invite_revoked"
USER_INVITE_CLAIMED = "user.invite_claimed"
USER_DEACTIVATED = "user.deactivated"

# Org unit actions
ORG_UNIT_CREATED = "org_unit.created"
ORG_UNIT_UPDATED = "org_unit.updated"
ORG_UNIT_DELETED = "org_unit.deleted"
ORG_UNIT_MEMBER_ADDED = "org_unit.member_added"
ORG_UNIT_MEMBER_REMOVED = "org_unit.member_removed"
ORG_UNIT_ROLE_REMOVED = "org_unit.role_removed"

# Job posting actions (lifecycle transitions are written by jd.state_machine
# under action="job_posting.status_changed"; this constant covers draft-edit
# PATCH updates that don't change status)
JOB_POSTING_UPDATED = "job_posting.updated"

# Client actions
CLIENT_PROVISIONED = "client.provisioned"
CLIENT_ONBOARDING_COMPLETED = "client.onboarding_completed"
CLIENT_BLOCKED = "client.blocked"
CLIENT_UNBLOCKED = "client.unblocked"
CLIENT_DELETED = "client.deleted"
CLIENT_HARD_DELETED = "client.hard_deleted"
CLIENT_HARD_DELETE_AUTH_PARTIAL = "client.hard_delete_auth_partial"

# ────────────────────────────── ATS events ─────────────────────────────
# Per spec docs/superpowers/specs/2026-05-14-job-scoped-ats-sync-design.md
# § "Change detection & events". Every event flows through `audit.log_event`
# with `correlation_id` propagated from `ats_sync_logs.correlation_id` and
# `action_source` ∈ {'manual', 'scheduled', 'system'}.

# Connection lifecycle
ATS_CONNECTION_CREATED = "ats.connection.created"
ATS_CONNECTION_DELETED = "ats.connection.deleted"
ATS_CONNECTION_DISABLED = "ats.connection.disabled"
ATS_CONNECTION_CURSOR_RESET = "ats.connection.cursor_reset"
ATS_CONNECTION_JOB_STATUS_FILTER_UPDATED = (
    "ats.connection.job_status_filter_updated"
)
ATS_CONNECTION_STATUS_SYNC_MODE_CHANGED = (
    "ats.connection.status_sync_mode_changed"
)

# Sync run
ATS_SYNC_STARTED = "ats.sync.started"
ATS_SYNC_COMPLETED = "ats.sync.completed"
ATS_SYNC_PARTIAL = "ats.sync.partial"
ATS_SYNC_FAILED = "ats.sync.failed"
ATS_SYNC_MANUALLY_TRIGGERED = "ats.sync.manually_triggered"

# Org units
ATS_ORG_UNIT_IMPORTED = "ats.org_unit.imported"
ATS_ORG_UNIT_METADATA_REFRESHED = "ats.org_unit.metadata_refreshed"
ATS_ORG_UNIT_ORPHAN_CLIENT = "ats.org_unit.orphan_client"

# Users
ATS_USER_IMPORTED = "ats.user.imported"
ATS_USER_LINKED_TO_NATIVE = "ats.user.linked_to_native"
ATS_USER_METADATA_REFRESHED = "ats.user.metadata_refreshed"
ATS_USER_COLLISION_SKIPPED = "ats.user.collision_skipped"

# Jobs
ATS_JOB_IMPORTED = "ats.job.imported"
ATS_JOB_FIELDS_UPDATED = "ats.job.fields_updated"
ATS_JOB_STATUS_CHANGED = "ats.job.status_changed"
ATS_JOB_ARCHIVED = "ats.job.archived"
ATS_JOB_RECRUITER_ASSIGNMENTS_CHANGED = (
    "ats.job.recruiter_assignments_changed"
)
ATS_JOB_ORPHAN_CLIENT = "ats.job.orphan_client"
ATS_JOB_IMPORT_ERRORED = "ats.job.import_errored"
ATS_JOB_IMPORT_FAILED_NO_ROW = "ats.job.import_failed_no_row"
ATS_JOB_IMPORT_QUARANTINED = "ats.job.import_quarantined"
ATS_JOB_IMPORT_QUARANTINE_CLEARED = "ats.job.import_quarantine_cleared"

# Submissions
ATS_SUBMISSION_CREATED = "ats.submission.created"
ATS_SUBMISSION_STATUS_CHANGED = "ats.submission.status_changed"
ATS_SUBMISSION_FIELDS_UPDATED = "ats.submission.fields_updated"
ATS_SUBMISSION_REMOVED = "ats.submission.removed"
ATS_SUBMISSION_SKIPPED_NO_PIPELINE = "ats.submission.skipped_no_pipeline"
ATS_SUBMISSION_BORDERLINE_MIRROR_BLOCKED = (
    "ats.submission.borderline_mirror_blocked"
)

# Candidate (existing actions reused; ATS-driven calls audit through
# candidates.service.import_candidate which writes candidate.imported /
# candidate.linked_to_external)
ATS_CANDIDATE_FETCH_FAILED = "ats.candidate.fetch_failed"

# Advisory action lifecycle
ATS_ADVISORY_ACTION_APPLIED = "ats.advisory_action.applied"
ATS_ADVISORY_ACTION_DISMISSED = "ats.advisory_action.dismissed"
ATS_ADVISORY_ACTION_SUPERSEDED_BY_MANUAL_MOVE = (
    "ats.advisory_action.superseded_by_manual_move"
)
