from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.modules.admin.schemas import (
    ClientListItem,
    ProvisionClientRequest,
    ProvisionClientResponse,
)
from app.modules.admin.service import list_clients, provision_client
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
    """List all provisioned companies and their invite statuses."""
    clients = await list_clients(db)
    return [ClientListItem(**c) for c in clients]
