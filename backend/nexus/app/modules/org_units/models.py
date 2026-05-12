"""Tenant + org-unit ORM models.

`Client` is the tenant root. `OrganizationalUnit` is the hierarchical
container (company → division → region → team etc).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    """Tenant root."""
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, server_default="''")
    industry: Mapped[str] = mapped_column(Text, server_default="''")
    size: Mapped[str] = mapped_column(Text, server_default="''")
    logo_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String, nullable=False, server_default="trial")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    super_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", deferrable=True, initially="DEFERRED"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationalUnit(Base):
    __tablename__ = "organizational_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_type: Mapped[str] = mapped_column(String, nullable=False)
    is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    company_profile: Mapped[dict | None] = mapped_column(JSONB)
    company_profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_profile_completed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Pending → complete gate for auto-created org_units (e.g. ATS-imported
    # client_account units). Schema source-of-truth is migration 0031_ats_core;
    # declared here so Base.metadata.create_all builds the same shape in test
    # DBs that skip alembic.
    company_profile_completion_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'complete'"),
    )
    # Per-unit-type metadata (region offices, division description, team focus,
    # etc). Mapped to DB column "metadata" but exposed on the ORM as
    # `unit_metadata` because SQLAlchemy reserves `metadata` on Base for the
    # MetaData registry. API layer re-aliases to `metadata` for clients.
    unit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    deletable_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    admin_delete_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
