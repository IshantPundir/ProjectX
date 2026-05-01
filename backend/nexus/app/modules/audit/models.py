"""Audit-log ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    """Append-only audit trail for tenant-scoped mutations.

    NOTE: `tenant_id` and `actor_id` are intentionally PLAIN UUID
    columns, not ForeignKey references, so audit rows survive
    tenant/user hard-delete. See migration
    0023_tenant_hard_delete_cascade. Re-adding either FK would break
    `DELETE FROM clients` for any tenant with audit history (the
    hard-delete cascade would be blocked) and would also break
    user-deletion paths whose actor_id points at the row being
    removed. `actor_email` is denormalized so attribution queries
    keep working after the user row is gone.
    """
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    # Intentionally NOT a ForeignKey — see class docstring + migration 0023.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Intentionally NOT a ForeignKey — see class docstring + migration 0023.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
