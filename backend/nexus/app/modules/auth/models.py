"""Auth-owned ORM models.

Tables: users, user_role_assignments, user_invites.

FK references to other modules' tables (e.g. clients, organizational_units, roles)
are string-based — no Python-side cross-imports — so cross-module model files do
not need to be loaded for SQLAlchemy mapper configuration to succeed.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """Dashboard user — identity only. Roles live on user_role_assignments.

    Provenance columns (`source`, `external_id`, `external_source_metadata`)
    unify native users and ATS-imported users into the same table. See
    `docs/superpowers/specs/2026-05-14-job-scoped-ats-sync-design.md`.

    `auth_user_id` is nullable: an ATS-imported user has no Supabase auth
    account until someone invites them through the dashboard.
    """
    __tablename__ = "users"
    __table_args__ = (
        # Partial unique index: enforces auth_user_id uniqueness only among
        # non-soft-deleted rows. Lets a re-invitation of the same Supabase
        # Auth identity to a fresh tenant succeed after the prior tenant was
        # soft-deleted (and its users were cascade-soft-deleted). Multiple
        # NULLs are allowed by partial unique indexes — ATS-imported rows
        # with auth_user_id IS NULL coexist freely. See migration 0022 for
        # the rationale on the partial-unique form and 0036 for the
        # nullability change.
        Index(
            "users_auth_user_id_active_uniq",
            "auth_user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # ATS-sourced rows MUST carry an external_id; native rows may have
        # one (set via case-2 email-collision link) but it isn't required.
        CheckConstraint(
            "(source = 'native') OR (source LIKE 'ats_%' AND external_id IS NOT NULL)",
            name="users_source_external_id_check",
        ),
        # Identity uniqueness for ATS-imported rows. Partial — only enforced
        # where external_id IS NOT NULL.
        Index(
            "users_external_identity_uniq",
            "tenant_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    auth_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Provenance: 'native' for natively-invited users; 'ats_<vendor>' for
    # users imported from an ATS sync (e.g. 'ats_ceipal').
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="native")
    external_id: Mapped[str | None] = mapped_column(Text)
    external_source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRoleAssignment(Base):
    """Junction: user assigned to org unit with a specific role."""
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "org_unit_id", "role_id", name="unique_user_unit_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class UserInvite(Base):
    """Invite to join a tenant — no role info, just email + token."""
    __tablename__ = "user_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    projectx_admin_id: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user_invites.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW() + INTERVAL '72 hours'"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
