"""ORM model for the tenant_settings table.

PK = tenant_id (one row per tenant). FK clients.id ON DELETE CASCADE
follows the migration 0023 hard-delete discipline. CHECK constraint
mirrors the DB-level CHECK in migration 0027 so create_all-based test
DBs exercise the same behavior.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TenantSettingsModel(Base):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        CheckConstraint(
            "engine_knockout_policy IN ('record_only', 'close_polite')",
            name="ck_tenant_settings_engine_knockout_policy",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    engine_knockout_policy: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'close_polite'")
    )
    engine_agent_name: Mapped[str | None] = mapped_column(nullable=True)
    proctoring_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    proctoring_soft_violation_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3")
    )
    proctoring_fullscreen_grace_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
