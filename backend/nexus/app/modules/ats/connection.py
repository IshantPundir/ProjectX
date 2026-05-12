"""ATSConnectionState — in-memory working copy of an ats_connections row.

Distinct from the ORM model `ATSConnection` (app/modules/ats/models.py):
  - ORM row: persisted; credentials + tokens encrypted.
  - State:   in-memory; decrypted; mutable; adapter writes through it.

Lifecycle: load -> decrypt -> state -> adapter mutates -> encrypt -> persist.
The adapter never touches the ORM directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ats.crypto import (
    decrypt_credentials_blob,
    decrypt_secret,
    encrypt_secret,
)
from app.modules.ats.errors import ATSConnectionNotFoundError
from app.modules.ats.models import ATSConnection


@dataclass
class ATSConnectionState:
    id: UUID
    tenant_id: UUID
    vendor: str
    credentials: dict[str, Any]
    access_token: str | None = None
    refresh_token: str | None = None
    access_token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    last_synced_cursors: dict[str, str] = field(default_factory=dict)
    poll_interval_seconds: int = 900
    # Optional per-connection request-rate cap. asyncpg returns the DB
    # NUMERIC column as Decimal; the adapter coerces to float when
    # computing the inter-request gap (1 / qps). None → fall back to
    # settings.ats_default_request_pacing_seconds.
    rate_limit_qps: Any = None


async def load_connection_state(
    db: AsyncSession, connection_id: UUID,
) -> ATSConnectionState:
    """Hydrate the in-memory state from a persisted ATSConnection row.

    Caller is responsible for tenant-scope binding (this typically runs inside
    a bypass-RLS session with SET LOCAL app.current_tenant already issued, or
    after an explicit tenant_id filter at the application layer).
    """
    row = await db.get(ATSConnection, connection_id)
    if row is None:
        raise ATSConnectionNotFoundError(str(connection_id))

    return ATSConnectionState(
        id=row.id,
        tenant_id=row.tenant_id,
        vendor=row.vendor,
        credentials=decrypt_credentials_blob(row.credentials_ciphertext),
        access_token=(
            decrypt_secret(row.access_token_ciphertext)
            if row.access_token_ciphertext else None
        ),
        refresh_token=(
            decrypt_secret(row.refresh_token_ciphertext)
            if row.refresh_token_ciphertext else None
        ),
        access_token_expires_at=row.access_token_expires_at,
        refresh_token_expires_at=row.refresh_token_expires_at,
        last_synced_cursors=dict(row.last_synced_cursors or {}),
        poll_interval_seconds=row.poll_interval_seconds,
        rate_limit_qps=row.rate_limit_qps,
    )


async def persist_connection_state(
    db: AsyncSession, state: ATSConnectionState,
) -> None:
    """Write back the mutated token + cursor fields. credentials_ciphertext
    is NOT rewritten here (credentials don't change during a sync; the
    /connections POST handler is the only place that writes credentials).
    """
    row = await db.get(ATSConnection, state.id)
    if row is None:
        raise ATSConnectionNotFoundError(str(state.id))

    row.access_token_ciphertext = (
        encrypt_secret(state.access_token) if state.access_token else None
    )
    row.refresh_token_ciphertext = (
        encrypt_secret(state.refresh_token) if state.refresh_token else None
    )
    row.access_token_expires_at = state.access_token_expires_at
    row.refresh_token_expires_at = state.refresh_token_expires_at
    row.last_synced_cursors = state.last_synced_cursors
