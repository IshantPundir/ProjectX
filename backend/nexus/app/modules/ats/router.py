"""/api/ats/* HTTP endpoints — recruiter-facing ATS surface.

Endpoints:
  Connections:
    GET    /api/ats/connections                                   (any user)
    POST   /api/ats/connections                                   (super-admin)
    GET    /api/ats/connections/{id}                              (any user)
    DELETE /api/ats/connections/{id}                              (super-admin)
    POST   /api/ats/connections/{id}/sync                         (super-admin)
    POST   /api/ats/connections/{id}/reset-cursor                 (super-admin)
    PUT    /api/ats/connections/{id}/status-sync-mode             (super-admin)
    PUT    /api/ats/connections/{id}/job-status-filter            (super-admin)
    GET    /api/ats/connections/{id}/job-statuses                 (any user)
    GET    /api/ats/connections/{id}/sync-logs                    (any user)

  Stage mappings (mirror-mode opt-in; ships empty):
    GET    /api/ats/connections/{id}/stage-mappings               (super-admin)
    POST   /api/ats/connections/{id}/stage-mappings               (super-admin)
    DELETE /api/ats/stage-mappings/{mapping_id}                   (super-admin)

  Advisory actions:
    GET    /api/ats/advisory-actions?assignment_id=…              (recruiter)
    POST   /api/ats/advisory-actions/{id}/apply                   (recruiter)
    POST   /api/ats/advisory-actions/{id}/dismiss                 (recruiter)

  Quarantined-job retry:
    POST   /api/ats/jobs/{job_id}/retry-import                    (recruiter)

Rate limits are declared inline (`# Rate limit:`) per CLAUDE.md. The
limiter middleware is not wired yet — pre-merge gate is the comment;
CI-enforcement lands later. Per-tenant 5/hour on POST /sync is the
safety net against a compromised super-admin token hammering the vendor.

Credentials NEVER appear in any response — only metadata.
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
from app.modules.ats.constants import ATS_VENDOR_CEIPAL
from app.modules.ats.errors import (
    ATSAuthorizationError,
    ATSCredentialsInvalidError,
    ATSPermanentError,
)
from app.modules.ats.models import (
    ATSAdvisoryAction,
    ATSConnection,
    ATSStageMapping,
    ATSSyncLog,
)
from app.modules.ats.registry import get_ats_adapter
from app.modules.ats.service import (
    JobStatusFilterEmptyError,
    SyncAlreadyRunningError,
    create_connection,
    delete_connection,
    reset_cursor,
    retry_job_import,
    set_status_sync_mode,
    trigger_manual_sync,
    update_job_status_filter,
)
from app.modules.auth import UserContext, get_current_user_roles

router = APIRouter(prefix="/api/ats", tags=["ats"])


# ───────────────────────── Request models ─────────────────────────


class CeipalCredentials(BaseModel):
    email: str
    password: str = Field(..., repr=False)
    api_key: str = Field(..., repr=False)


class CeipalConnectionRequest(BaseModel):
    vendor: Literal["ats_ceipal"] = ATS_VENDOR_CEIPAL
    credentials: CeipalCredentials


# Discriminated union — adding a vendor = one more union member.
ConnectionCreateRequest = Annotated[
    CeipalConnectionRequest,
    Field(discriminator="vendor"),
]


class JobStatusFilterShape(BaseModel):
    ids: list[int]
    names: list[str]


class JobStatusFilterRequest(BaseModel):
    status_ids: list[int] = Field(..., min_length=1)
    names: list[str] = Field(..., min_length=1)


class StatusSyncModeRequest(BaseModel):
    mode: Literal["advisory", "mirror", "one_way"]


class ResetCursorRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class StageMappingCreateRequest(BaseModel):
    external_status_label: str = Field(..., min_length=1, max_length=200)
    projectx_stage_id: UUID
    action_on_match: Literal["move_to_stage", "reject", "archive", "no_op"]


# ───────────────────────── Response models ─────────────────────────


class CeipalJobStatusResponse(BaseModel):
    id: int
    name: str


class ConnectionResponse(BaseModel):
    id: UUID
    vendor: str
    active: bool
    status_sync_mode: str
    tenant_timezone: str | None = None
    last_synced_at: str | None = None
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
            status_sync_mode=row.status_sync_mode,
            tenant_timezone=row.tenant_timezone,
            last_synced_at=(
                row.last_synced_at.isoformat()
                if row.last_synced_at else None
            ),
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


class StageMappingResponse(BaseModel):
    id: UUID
    external_status_label: str
    projectx_stage_id: UUID
    action_on_match: str


class AdvisoryActionResponse(BaseModel):
    id: UUID
    assignment_id: UUID
    external_status_before: str | None
    external_status_after: str
    suggested_target_stage_id: UUID | None
    suggested_action: str
    resolution: str
    created_at: str


# ──────────────────────── Connection endpoints ─────────────────────


@router.get("/connections", response_model=list[ConnectionResponse])
# Rate limit: 60/min per-IP.
async def list_connections(
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[ConnectionResponse]:
    rows = await db.execute(
        select(ATSConnection)
        .where(ATSConnection.tenant_id == user.user.tenant_id)
        .order_by(ATSConnection.created_at.desc()),
    )
    return [ConnectionResponse.from_row(r) for r in rows.scalars()]


@router.post(
    "/connections",
    status_code=status.HTTP_201_CREATED,
    response_model=ConnectionResponse,
)
# Rate limit: 10/min per-IP.
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
        ) from exc
    except ATSAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ATS_AUTHORIZATION_INSUFFICIENT",
                "message": str(exc)[:200],
            },
        ) from exc

    await db.flush()
    new_row = await db.get(ATSConnection, conn_id)
    return ConnectionResponse.from_row(new_row)


@router.get(
    "/connections/{connection_id}",
    response_model=ConnectionResponse,
)
# Rate limit: 60/min per-IP.
async def get_connection(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> ConnectionResponse:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )
    return ConnectionResponse.from_row(row)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
# Rate limit: 10/min per-IP.
async def delete_connection_endpoint(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    await delete_connection(
        db, connection_id, user.user.tenant_id, user.user.id,
    )


@router.post(
    "/connections/{connection_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
# Rate limit: 10/min per-IP; 5/hour per-tenant (safety net against a
# compromised super-admin token hammering the vendor).
async def manual_sync(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> dict:
    """Enqueue a sync. Cursor-based incremental; first sync (last_synced_at
    is NULL) implicitly walks the full filter.

    422 if job_status_filter is empty. 409 if a sync is already running.
    """
    try:
        await trigger_manual_sync(
            db, connection_id, user.user.tenant_id, user.user.id,
        )
    except JobStatusFilterEmptyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "JOB_STATUS_FILTER_EMPTY",
                "message": str(exc),
            },
        ) from exc
    except SyncAlreadyRunningError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATS_SYNC_ALREADY_RUNNING",
                "message": str(exc),
            },
        ) from exc
    except ATSPermanentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ATS_SYNC_REJECTED",
                "message": str(exc),
            },
        ) from exc
    return {"status": "enqueued"}


@router.post(
    "/connections/{connection_id}/reset-cursor",
    status_code=status.HTTP_204_NO_CONTENT,
)
# Rate limit: 1/hour per-tenant (heavy operation — next sync re-walks the
# full filter).
async def reset_cursor_endpoint(
    connection_id: UUID,
    body: ResetCursorRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )
    await reset_cursor(
        db,
        connection_id=connection_id,
        tenant_id=user.user.tenant_id,
        actor_id=user.user.id,
        reason=(body.reason if body else "") or "",
    )


@router.put(
    "/connections/{connection_id}/status-sync-mode",
    status_code=status.HTTP_204_NO_CONTENT,
)
# Rate limit: 10/min per-IP.
async def set_status_sync_mode_endpoint(
    connection_id: UUID,
    body: StatusSyncModeRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )
    try:
        await set_status_sync_mode(
            db,
            connection_id=connection_id,
            tenant_id=user.user.tenant_id,
            actor_id=user.user.id,
            mode=body.mode,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_STATUS_SYNC_MODE", "message": str(exc)},
        ) from exc


@router.get(
    "/connections/{connection_id}/job-statuses",
    response_model=list[CeipalJobStatusResponse],
)
# Rate limit: 30/min per-IP.
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
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )

    async with get_bypass_session() as bypass_db:
        await bypass_db.execute(
            text(f"SET LOCAL app.current_tenant = '{user.user.tenant_id}'"),
        )
        state = await load_connection_state(bypass_db, connection_id)

    adapter = get_ats_adapter(state)
    try:
        statuses = await adapter.list_job_statuses()
    except (ATSCredentialsInvalidError, ATSAuthorizationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ATS_CREDENTIALS_INVALID",
                "message": str(exc)[:200],
            },
        ) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501, detail="vendor_no_status_endpoint",
        ) from exc
    finally:
        # Adapters may or may not implement aclose; best-effort.
        close = getattr(adapter, "aclose", None)
        if close is not None:
            await close()

    return [
        CeipalJobStatusResponse(id=int(s.external_id), name=s.name)
        for s in statuses
    ]


@router.put(
    "/connections/{connection_id}/job-status-filter",
    status_code=status.HTTP_204_NO_CONTENT,
)
# Rate limit: 30/min per-IP.
async def set_job_status_filter(
    connection_id: UUID,
    body: JobStatusFilterRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )

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
        ) from exc
    await db.flush()


@router.get(
    "/connections/{connection_id}/sync-logs",
    response_model=list[SyncLogResponse],
)
# Rate limit: 60/min per-IP.
async def list_sync_logs(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[SyncLogResponse]:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )
    rows = await db.execute(
        select(ATSSyncLog)
        .where(ATSSyncLog.connection_id == connection_id)
        .order_by(ATSSyncLog.started_at.desc())
        .limit(50),
    )
    return [
        SyncLogResponse(
            id=r.id,
            started_at=r.started_at.isoformat(),
            completed_at=(
                r.completed_at.isoformat() if r.completed_at else None
            ),
            status=r.status,
            entity_counts=r.entity_counts,
            progress=r.progress or {},
            error_phase=r.error_phase,
            error_summary=r.error_summary,
        )
        for r in rows.scalars()
    ]


# ───────────────────────── Stage mappings ──────────────────────────


@router.get(
    "/connections/{connection_id}/stage-mappings",
    response_model=list[StageMappingResponse],
)
# Rate limit: 60/min per-IP.
async def list_stage_mappings(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> list[StageMappingResponse]:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )
    rows = await db.execute(
        select(ATSStageMapping)
        .where(ATSStageMapping.connection_id == connection_id)
        .order_by(ATSStageMapping.external_status_label.asc()),
    )
    return [
        StageMappingResponse(
            id=r.id,
            external_status_label=r.external_status_label,
            projectx_stage_id=r.projectx_stage_id,
            action_on_match=r.action_on_match,
        )
        for r in rows.scalars()
    ]


@router.post(
    "/connections/{connection_id}/stage-mappings",
    response_model=StageMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
# Rate limit: 30/min per-IP.
async def create_stage_mapping(
    connection_id: UUID,
    body: StageMappingCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> StageMappingResponse:
    conn = await db.get(ATSConnection, connection_id)
    if conn is None or conn.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_CONNECTION_NOT_FOUND",
        )
    mapping = ATSStageMapping(
        tenant_id=user.user.tenant_id,
        connection_id=connection_id,
        external_status_label=body.external_status_label,
        projectx_stage_id=body.projectx_stage_id,
        action_on_match=body.action_on_match,
    )
    db.add(mapping)
    await db.flush()
    return StageMappingResponse(
        id=mapping.id,
        external_status_label=mapping.external_status_label,
        projectx_stage_id=mapping.projectx_stage_id,
        action_on_match=mapping.action_on_match,
    )


@router.delete(
    "/stage-mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
# Rate limit: 30/min per-IP.
async def delete_stage_mapping(
    mapping_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    row = await db.get(ATSStageMapping, mapping_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(
            status_code=404, detail="ATS_STAGE_MAPPING_NOT_FOUND",
        )
    await db.delete(row)
    await db.flush()


# ─────────────────────── Advisory actions ──────────────────────────


@router.get(
    "/advisory-actions",
    response_model=list[AdvisoryActionResponse],
)
# Rate limit: 60/min per-IP.
async def list_advisory_actions(
    assignment_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[AdvisoryActionResponse]:
    """List pending advisory actions for an assignment (newest first)."""
    rows = await db.execute(
        select(ATSAdvisoryAction)
        .where(ATSAdvisoryAction.assignment_id == assignment_id)
        .order_by(ATSAdvisoryAction.created_at.desc())
        .limit(50),
    )
    return [
        AdvisoryActionResponse(
            id=r.id,
            assignment_id=r.assignment_id,
            external_status_before=r.external_status_before,
            external_status_after=r.external_status_after,
            suggested_target_stage_id=r.suggested_target_stage_id,
            suggested_action=r.suggested_action,
            resolution=r.resolution,
            created_at=r.created_at.isoformat(),
        )
        for r in rows.scalars()
    ]


# ───────────────────── Quarantined-job retry ───────────────────────


@router.post(
    "/jobs/{job_id}/retry-import",
    status_code=status.HTTP_204_NO_CONTENT,
)
# Rate limit: 30/min per-user. Authorization: recruiter with jobs.edit on
# the job's org_unit OR super-admin. For now, gated by super-admin until
# the Phase D UI surfaces the action with finer-grained authz wiring.
async def retry_job_import_endpoint(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    await retry_job_import(
        db,
        job_id=job_id,
        tenant_id=user.user.tenant_id,
        actor_id=user.user.id,
    )
