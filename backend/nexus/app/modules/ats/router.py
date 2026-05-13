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
  GET    /api/ats/connections/{id}/job-statuses
  PUT    /api/ats/connections/{id}/job-status-filter

Write endpoints require super_admin (via require_ats_admin). Credentials
NEVER appear in any response — only metadata.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_session, get_tenant_db
from app.modules.ats.authz import require_ats_admin
from app.modules.ats.connection import load_connection_state
from app.modules.ats.errors import (
    ATSAuthorizationError,
    ATSCredentialsInvalidError,
)
from app.modules.ats.models import (
    ATSConnection,
    ATSSyncLog,
    ATSUserMapping,
)
from app.modules.ats.registry import get_ats_adapter
from app.modules.ats.service import (
    create_connection,
    delete_connection,
    map_ats_user_to_internal,
    trigger_manual_sync,
    update_job_status_filter,
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


class CeipalJobStatusResponse(BaseModel):
    id: int
    name: str


class JobStatusFilterShape(BaseModel):
    ids: list[int]
    names: list[str]


class JobStatusFilterRequest(BaseModel):
    status_ids: list[int] = Field(..., min_length=1)
    names: list[str] = Field(..., min_length=1)


class ConnectionResponse(BaseModel):
    id: UUID
    vendor: str
    active: bool
    last_synced_at: str | None = None
    next_poll_at: str | None = None
    last_poll_error: str | None = None
    disabled_reason: str | None = None
    created_at: str
    job_status_filter: JobStatusFilterShape | None = None

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
            job_status_filter=(
                JobStatusFilterShape(**row.job_status_filter)
                if row.job_status_filter else None
            ),
        )


class SyncLogResponse(BaseModel):
    id: UUID
    started_at: str
    completed_at: str | None = None
    status: str
    entity_counts: dict
    progress: dict = Field(default_factory=dict)
    error_phase: str | None = None
    error_summary: str | None = None


class UnmappedUserResponse(BaseModel):
    external_user_id: str
    external_user_email: str
    external_user_display_name: str
    external_user_role: str | None = None


class MapUserRequest(BaseModel):
    internal_user_id: UUID


# The five-phase enum is the closed set the importer knows about. Anything
# else is a 422 from Pydantic before the handler runs.
_PHASE_NAMES = Literal[
    "clients", "users", "jobs", "applicants", "submissions",
]


class ManualSyncRequest(BaseModel):
    """Optional body for POST /sync. ``phases=None`` (no body, or null) runs
    all five phases — equivalent to the "Sync all" button. Pass a subset
    to scope a run to specific phases (dev-mode manual control)."""
    phases: list[_PHASE_NAMES] | None = None


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
    # NOTE: no automatic sync trigger here. Per the dev-mode manual-sync
    # design, sync is recruiter-triggered per phase via POST /sync. The
    # connection row gets created, credentials get verified by the adapter's
    # ensure_authenticated() call inside create_connection, and that's the
    # complete responsibility of this endpoint.
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
    body: ManualSyncRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> dict:
    """Enqueue a sync. ``body.phases=None`` (or omitted) runs all five
    phases; otherwise restricts to the subset.
    """
    phase_filter = body.phases if (body and body.phases) else None
    await trigger_manual_sync(
        db, connection_id, user.user.tenant_id, user.user.id,
        phase_filter=phase_filter,
    )
    return {"status": "enqueued", "phases": phase_filter}


@router.get(
    "/connections/{connection_id}/job-statuses",
    response_model=list[CeipalJobStatusResponse],
)
async def list_connection_job_statuses(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[CeipalJobStatusResponse]:
    """Live fetch from the vendor. Not cached server-side; the modal calls
    this on every open. Read-only — no state change, so super_admin is not
    required."""
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")

    async with get_bypass_session() as bypass_db:
        await bypass_db.execute(
            text(f"SET LOCAL app.current_tenant = '{user.user.tenant_id}'")
        )
        state = await load_connection_state(bypass_db, connection_id)

    adapter = get_ats_adapter(state)
    try:
        raw = await adapter.list_job_statuses()
    except ATSCredentialsInvalidError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "ATS_CREDENTIALS_INVALID", "message": str(exc)[:200]},
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="vendor_no_status_endpoint")
    finally:
        await adapter.aclose()

    return [
        CeipalJobStatusResponse(id=int(s["id"]), name=str(s["name"]))
        for s in raw
    ]


@router.put(
    "/connections/{connection_id}/job-status-filter",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_job_status_filter(
    connection_id: UUID,
    body: JobStatusFilterRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")

    try:
        await update_job_status_filter(
            db,
            connection_id=connection_id,
            tenant_id=user.user.tenant_id,
            actor_id=user.user.id,
            status_ids=body.status_ids,
            names=body.names,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "JOB_STATUS_FILTER_INVALID", "message": str(exc)},
        )
    await db.flush()
    # NOTE: no automatic sync trigger. Recruiter clicks the per-phase
    # "Sync Jobs" / "Sync all" button on the detail page when ready.


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
            progress=r.progress or {},
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
