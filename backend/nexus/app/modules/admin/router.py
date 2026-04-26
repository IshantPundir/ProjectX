import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.modules.admin.schemas import (
    ClientListItem,
    ClientStatusResponse,
    ProvisionClientRequest,
    ProvisionClientResponse,
)
from app.modules.admin.service import (
    ClientNotFoundError,
    InvalidClientStateError,
    _client_status,
    block_client,
    delete_client,
    list_clients,
    provision_client,
    unblock_client,
)
from app.modules.auth.service import require_projectx_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post(
    "/provision-client",
    response_model=ProvisionClientResponse,
    dependencies=[require_projectx_admin()],
)
async def provision_client_endpoint(
    data: ProvisionClientRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> ProvisionClientResponse:
    """Provision a new enterprise client and send invite to their Company Admin."""
    admin_email = request.state.token_payload.email

    client, invite, invite_url = await provision_client(
        db=db,
        client_name=data.client_name,
        admin_email=data.admin_email,
        domain=data.domain,
        industry=data.industry,
        plan=data.plan,
        admin_identity=admin_email,
        ip_address=request.client.host if request.client else None,
    )

    return ProvisionClientResponse(
        client_id=str(client.id),
        invite_id=str(invite.id),
        admin_email=data.admin_email,
        invite_url=invite_url,
    )


@router.get(
    "/clients",
    response_model=list[ClientListItem],
    dependencies=[require_projectx_admin()],
)
async def list_clients_endpoint(
    db: AsyncSession = Depends(get_bypass_db),
) -> list[ClientListItem]:
    """List all provisioned companies and their invite + lifecycle status."""
    clients = await list_clients(db)
    return [ClientListItem(**c) for c in clients]


def _parse_client_id(client_id: str) -> uuid_mod.UUID:
    try:
        return uuid_mod.UUID(client_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Client not found")


def _to_status_response(client) -> ClientStatusResponse:
    return ClientStatusResponse(
        client_id=str(client.id),
        status=_client_status(client),
        blocked_at=client.blocked_at.isoformat() if client.blocked_at else None,
        deleted_at=client.deleted_at.isoformat() if client.deleted_at else None,
    )


@router.post(
    "/clients/{client_id}/block",
    response_model=ClientStatusResponse,
    dependencies=[require_projectx_admin()],
)
async def block_client_endpoint(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> ClientStatusResponse:
    cid = _parse_client_id(client_id)
    try:
        client = await block_client(
            db=db,
            client_id=cid,
            admin_identity=request.state.token_payload.email,
            ip_address=request.client.host if request.client else None,
        )
    except ClientNotFoundError:
        raise HTTPException(status_code=404, detail="Client not found")
    except InvalidClientStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_status_response(client)


@router.post(
    "/clients/{client_id}/unblock",
    response_model=ClientStatusResponse,
    dependencies=[require_projectx_admin()],
)
async def unblock_client_endpoint(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> ClientStatusResponse:
    cid = _parse_client_id(client_id)
    try:
        client = await unblock_client(
            db=db,
            client_id=cid,
            admin_identity=request.state.token_payload.email,
            ip_address=request.client.host if request.client else None,
        )
    except ClientNotFoundError:
        raise HTTPException(status_code=404, detail="Client not found")
    except InvalidClientStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_status_response(client)


@router.delete(
    "/clients/{client_id}",
    response_model=ClientStatusResponse,
    dependencies=[require_projectx_admin()],
)
async def delete_client_endpoint(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> ClientStatusResponse:
    """Soft-delete the client. Restoring is DB-only on purpose."""
    cid = _parse_client_id(client_id)
    try:
        client = await delete_client(
            db=db,
            client_id=cid,
            admin_identity=request.state.token_payload.email,
            ip_address=request.client.host if request.client else None,
        )
    except ClientNotFoundError:
        raise HTTPException(status_code=404, detail="Client not found")
    return _to_status_response(client)
