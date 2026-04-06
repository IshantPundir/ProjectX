"""UserContext — per-request authorization context.

Loaded via a single JOIN query across user_role_assignments, roles,
and organizational_units. Super admin check compares against
clients.super_admin_id.
"""

import uuid as uuid_mod
from dataclasses import dataclass, field

import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_db
from app.models import Client, OrganizationalUnit, Role, User, UserRoleAssignment

logger = structlog.get_logger()


@dataclass
class RoleAssignment:
    org_unit_id: uuid_mod.UUID
    org_unit_name: str
    role_id: uuid_mod.UUID
    role_name: str
    permissions: list[str]


@dataclass
class UserContext:
    user: User
    is_super_admin: bool
    workspace_mode: str = "enterprise"
    assignments: list[RoleAssignment] = field(default_factory=list)

    def has_role_in_unit(self, org_unit_id: uuid_mod.UUID, role_name: str) -> bool:
        return any(
            a.org_unit_id == org_unit_id and a.role_name == role_name for a in self.assignments
        )

    def has_permission_in_unit(self, org_unit_id: uuid_mod.UUID, permission: str) -> bool:
        for a in self.assignments:
            if a.org_unit_id == org_unit_id and permission in a.permissions:
                return True
        return False

    def permissions_in_unit(self, org_unit_id: uuid_mod.UUID) -> set[str]:
        perms: set[str] = set()
        for a in self.assignments:
            if a.org_unit_id == org_unit_id:
                perms.update(a.permissions)
        return perms

    def all_permissions(self) -> set[str]:
        perms: set[str] = set()
        for a in self.assignments:
            perms.update(a.permissions)
        return perms


async def get_current_user_roles(
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> UserContext:
    """FastAPI dependency — loads UserContext for the authenticated user.

    Single JOIN query for assignments. Raises 401/404 as appropriate.
    """
    token_payload = getattr(request.state, "token_payload", None)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_user_id = token_payload.sub

    # Load user + client in one query
    result = await db.execute(
        select(User, Client)
        .join(Client, User.tenant_id == Client.id)
        .where(User.auth_user_id == auth_user_id, User.is_active == True)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user, client = row
    is_super_admin = client.super_admin_id is not None and client.super_admin_id == user.id

    # Single JOIN: user_role_assignments + roles + organizational_units
    assignments_result = await db.execute(
        select(UserRoleAssignment, Role, OrganizationalUnit)
        .join(Role, UserRoleAssignment.role_id == Role.id)
        .join(OrganizationalUnit, UserRoleAssignment.org_unit_id == OrganizationalUnit.id)
        .where(UserRoleAssignment.user_id == user.id)
    )

    assignments = [
        RoleAssignment(
            org_unit_id=ura.org_unit_id,
            org_unit_name=ou.name,
            role_id=role.id,
            role_name=role.name,
            permissions=role.permissions or [],
        )
        for ura, role, ou in assignments_result.all()
    ]

    return UserContext(
        user=user,
        is_super_admin=is_super_admin,
        workspace_mode=client.workspace_mode,
        assignments=assignments,
    )


def require_super_admin():
    """FastAPI dependency factory — rejects non-super-admins."""

    async def _check(ctx: UserContext = Depends(get_current_user_roles)) -> UserContext:
        if not ctx.is_super_admin:
            raise HTTPException(status_code=403, detail="Super admin required")
        return ctx

    return Depends(_check)
