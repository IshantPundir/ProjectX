"""/api/ats/* HTTP endpoints — recruiter-facing connection management.

Endpoints:
  GET    /api/ats/connections
  POST   /api/ats/connections
  GET    /api/ats/connections/{id}
  DELETE /api/ats/connections/{id}
  POST   /api/ats/connections/{id}/sync
  GET    /api/ats/connections/{id}/sync-logs
  GET    /api/ats/connections/{id}/unmapped-users
  POST   /api/ats/connections/{id}/users/{external_user_id}/map

Write endpoints require super_admin (via require_ats_admin). Credentials
NEVER appear in any response — only metadata.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.modules.ats.authz import require_ats_admin
from app.modules.ats.errors import (
    ATSAuthorizationError,
    ATSCredentialsInvalidError,
)
from app.modules.ats.models import (
    ATSConnection,
    ATSSyncLog,
    ATSUserMapping,
)
from app.modules.ats.service import (
    create_connection,
    delete_connection,
    map_ats_user_to_internal,
    trigger_manual_sync,
)
from app.modules.auth import UserContext, get_current_user_roles

router = APIRouter(prefix="/api/ats", tags=["ats"])


# ---------- Request/response models ----------


class CeipalCredentials(BaseModel):
    email: str
    password: str = Field(..., repr=False)  # never appears in repr/logs
    api_key: str = Field(..., repr=False)


class CeipalConnectionRequest(BaseModel):
    vendor: Literal["ceipal"] = "ceipal"
    credentials: CeipalCredentials


# Discriminated union — adding a vendor = one more union member.
ConnectionCreateRequest = Annotated[
    CeipalConnectionRequest,
    Field(discriminator="vendor"),
]


class ConnectionResponse(BaseModel):
    id: UUID
    vendor: str
    active: bool
    last_synced_at: str | None = None
    next_poll_at: str | None = None
    last_poll_error: str | None = None
    disabled_reason: str | None = None
    created_at: str

    @classmethod
    def from_row(cls, row: ATSConnection) -> ConnectionResponse:
        return cls(
            id=row.id,
            vendor=row.vendor,
            active=row.active,
            last_synced_at=row.last_poll_completed_at.isoformat()
            if row.last_poll_completed_at
            else None,
            next_poll_at=row.next_poll_at.isoformat() if row.next_poll_at else None,
            last_poll_error=row.last_poll_error,
            disabled_reason=row.disabled_reason,
            created_at=row.created_at.isoformat(),
        )


class SyncLogResponse(BaseModel):
    id: UUID
    started_at: str
    completed_at: str | None = None
    status: str
    entity_counts: dict
    error_phase: str | None = None
    error_summary: str | None = None


class UnmappedUserResponse(BaseModel):
    external_user_id: str
    external_user_email: str
    external_user_display_name: str
    external_user_role: str | None = None


class MapUserRequest(BaseModel):
    internal_user_id: UUID


# ---------- Endpoints ----------


@router.get("/connections", response_model=list[ConnectionResponse])
async def list_connections(
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[ConnectionResponse]:
    rows = await db.execute(
        select(ATSConnection)
        .where(ATSConnection.tenant_id == user.user.tenant_id)
        .order_by(ATSConnection.created_at.desc())
    )
    return [ConnectionResponse.from_row(r) for r in rows.scalars()]


@router.post(
    "/connections",
    status_code=status.HTTP_201_CREATED,
    response_model=ConnectionResponse,
)
async def create_connection_endpoint(
    body: ConnectionCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> ConnectionResponse:
    try:
        conn_id = await create_connection(
            db,
            tenant_id=user.user.tenant_id,
            vendor=body.vendor,
            credentials=body.credentials.model_dump(),
            created_by=user.user.id,
        )
    except ATSCredentialsInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ATS_CREDENTIALS_INVALID",
                "message": str(exc)[:200],
            },
        )
    except ATSAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ATS_AUTHORIZATION_INSUFFICIENT",
                "message": str(exc)[:200],
            },
        )
    # Fire-and-forget initial sync. We can't call db.commit() here — the
    # get_tenant_db dependency wraps the session in `async with session.begin()`
    # which owns the transaction lifecycle. Calling commit closes that
    # transaction and any subsequent db.* op raises InvalidRequestError.
    # Flush so the audit row + connection row are visible to db.get below,
    # and let the dependency commit on context exit.
    await db.flush()
    await trigger_manual_sync(db, conn_id, user.user.tenant_id, user.user.id)
    await db.flush()

    new_row = await db.get(ATSConnection, conn_id)
    return ConnectionResponse.from_row(new_row)


@router.get("/connections/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> ConnectionResponse:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")
    return ConnectionResponse.from_row(row)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connection_endpoint(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    await delete_connection(db, connection_id, user.user.tenant_id, user.user.id)


@router.post(
    "/connections/{connection_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
async def manual_sync(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> dict:
    await trigger_manual_sync(db, connection_id, user.user.tenant_id, user.user.id)
    return {"status": "enqueued"}


@router.get(
    "/connections/{connection_id}/sync-logs",
    response_model=list[SyncLogResponse],
)
async def list_sync_logs(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[SyncLogResponse]:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")
    rows = await db.execute(
        select(ATSSyncLog)
        .where(ATSSyncLog.connection_id == connection_id)
        .order_by(ATSSyncLog.started_at.desc())
        .limit(50)
    )
    return [
        SyncLogResponse(
            id=r.id,
            started_at=r.started_at.isoformat(),
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            status=r.status,
            entity_counts=r.entity_counts,
            error_phase=r.error_phase,
            error_summary=r.error_summary,
        )
        for r in rows.scalars()
    ]


@router.get(
    "/connections/{connection_id}/unmapped-users",
    response_model=list[UnmappedUserResponse],
)
async def list_unmapped_users(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[UnmappedUserResponse]:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")
    rows = await db.execute(
        select(ATSUserMapping).where(
            ATSUserMapping.tenant_id == user.user.tenant_id,
            ATSUserMapping.ats_vendor == conn.vendor,
            ATSUserMapping.internal_user_id.is_(None),
        )
    )
    return [
        UnmappedUserResponse(
            external_user_id=r.external_user_id,
            external_user_email=r.external_user_email,
            external_user_display_name=r.external_user_display_name,
            external_user_role=r.external_user_role,
        )
        for r in rows.scalars()
    ]


@router.post(
    "/connections/{connection_id}/users/{external_user_id}/map",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def map_user(
    connection_id: UUID,
    external_user_id: str,
    body: MapUserRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    await map_ats_user_to_internal(
        db,
        connection_id=connection_id,
        external_user_id=external_user_id,
        internal_user_id=body.internal_user_id,
        tenant_id=user.user.tenant_id,
        actor_id=user.user.id,
    )
