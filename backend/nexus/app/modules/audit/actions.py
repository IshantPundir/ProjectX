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

# Client actions
CLIENT_PROVISIONED = "client.provisioned"
CLIENT_ONBOARDING_COMPLETED = "client.onboarding_completed"
