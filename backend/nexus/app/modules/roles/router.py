from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import Role
from app.modules.roles.schemas import RoleResponse

router = APIRouter(prefix="/api/roles", tags=["roles"])


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    db: AsyncSession = Depends(get_tenant_db),
) -> list[RoleResponse]:
    """List available roles — system + tenant custom (future)."""
    result = await db.execute(
        select(Role).order_by(Role.is_system.desc(), Role.name.asc())
    )
    return [
        RoleResponse(
            id=str(r.id),
            name=r.name,
            description=r.description or "",
            permissions=r.permissions or [],
            is_system=r.is_system,
        )
        for r in result.scalars().all()
    ]
