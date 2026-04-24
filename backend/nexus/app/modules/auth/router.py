"""Auth endpoints — invite verification, invite claim, and user profile."""

import hashlib
import uuid as uuid_mod
from typing import TYPE_CHECKING

import sqlalchemy
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.modules.auth.admin import AuthProvider

from app.database import get_bypass_db
from app.models import Client, OrganizationalUnit, User, UserInvite
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    MeResponse,
    RoleAssignmentResponse,
    VerifyInviteResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/verify-invite", response_model=VerifyInviteResponse)
async def verify_invite(
    token: str = Query(..., description="Raw invite token from email URL"),
    db: AsyncSession = Depends(get_bypass_db),
) -> VerifyInviteResponse:
    """Verify an invite token. Public endpoint — no auth required."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(UserInvite, Client)
        .join(Client, UserInvite.tenant_id == Client.id)
        .where(
            UserInvite.token_hash == token_hash,
            UserInvite.status == "pending",
            UserInvite.expires_at > sqlalchemy.func.now(),
        )
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired invite")

    invite, client = row
    return VerifyInviteResponse(email=invite.email, client_name=client.name)


@router.post("/accept-invite", response_model=AcceptInviteResponse)
async def accept_invite(
    data: AcceptInviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> AcceptInviteResponse:
    """Backend-owned invite acceptance.

    Replaces the legacy 2-call frontend flow (frontend signUp + backend claim).
    Public endpoint: the raw invite token is proof of possession; no
    bearer JWT required (see middleware _PUBLIC_PREFIXES).

    Compensation on DB failure: any auth user created inside this
    handler is deleted if the subsequent DB writes fail.
    """
    from app.modules.auth.admin import (
        AuthProviderError,
        InvalidCredentialsError,
        UserAlreadyExistsError,
        get_auth_provider,
    )

    provider = get_auth_provider()

    # Phase 1: verify the invite (read-only). We re-check inside the
    # atomic-claim UPDATE below, but failing fast here gives a crisp
    # 401 without touching the auth provider at all.
    token_hash = hashlib.sha256(data.raw_token.encode()).hexdigest()
    verify_result = await db.execute(
        select(UserInvite, Client)
        .join(Client, UserInvite.tenant_id == Client.id)
        .where(
            UserInvite.token_hash == token_hash,
            UserInvite.status == "pending",
            UserInvite.expires_at > sqlalchemy.func.now(),
        )
    )
    verify_row = verify_result.first()
    if not verify_row:
        raise HTTPException(status_code=401, detail="Invalid or expired invite")
    invite_row, _client_row = verify_row
    email = invite_row.email

    # Phase 2: provision the auth user.
    auth_user_created_here = False
    try:
        identity = await provider.create_user(email, data.password)
        auth_user_created_here = True
    except UserAlreadyExistsError:
        existing = await provider.find_user_by_email(email)
        if existing is None:
            logger.error(
                "auth.accept_invite.provider_inconsistent",
                email=email,
            )
            raise HTTPException(
                status_code=500,
                detail="Auth provider is in an inconsistent state",
            )
        await provider.update_user_password(existing.id, data.password)
        identity = existing
        logger.info(
            "auth.accept_invite.reused_existing_user",
            auth_user_id=existing.id,
            email=email,
        )
    except AuthProviderError as err:
        logger.error("auth.accept_invite.provision_failed", error=str(err))
        raise HTTPException(
            status_code=502, detail="Could not create account"
        )

    auth_user_id = uuid_mod.UUID(identity.id)

    # Phase 3: sign in (external). Password was just set, so failure here
    # is a provider issue — compensate by deleting the just-created user.
    try:
        tokens = await provider.sign_in_with_password(email, data.password)
    except (InvalidCredentialsError, AuthProviderError) as err:
        logger.error("auth.accept_invite.sign_in_failed", error=str(err))
        await _safe_delete_auth_user(provider, identity.id, created_by_this_request=auth_user_created_here)
        raise HTTPException(
            status_code=502,
            detail="Account created but sign-in failed; please try logging in.",
        )

    # Phase 4: DB writes. On any exception, compensate by deleting the
    # auth user (the DB transaction rolls back automatically via the
    # get_bypass_db context manager).
    try:
        claimed_result = await db.execute(
            sqlalchemy.text("""
                UPDATE public.user_invites
                   SET status = 'accepted', accepted_at = NOW()
                 WHERE token_hash  = :token_hash
                   AND status      = 'pending'
                   AND expires_at  > NOW()
                RETURNING id, tenant_id, email, invited_by, projectx_admin_id
            """),
            {"token_hash": token_hash},
        )
        claimed_row = claimed_result.first()
        if not claimed_row:
            # Race — between the verify read and this claim, another
            # process accepted the same invite.
            raise HTTPException(
                status_code=409,
                detail="Invite has already been accepted",
            )

        user = User(
            auth_user_id=auth_user_id,
            tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
            email=email,
        )
        db.add(user)
        await db.flush()

        is_super_admin = claimed_row.projectx_admin_id is not None
        root_unit_id = ""
        if is_super_admin:
            await db.execute(
                sqlalchemy.text(
                    "UPDATE public.clients SET super_admin_id = :user_id "
                    "WHERE id = :tenant_id"
                ),
                {
                    "user_id": str(user.id),
                    "tenant_id": str(claimed_row.tenant_id),
                },
            )
            from app.modules.org_units.service import (
                create_org_unit as _create_root_unit,
            )

            root_unit = await _create_root_unit(
                db=db,
                client_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
                name="Company",
                unit_type="company",
                parent_unit_id=None,
                created_by=user.id,
                actor_email=email,
                workspace_mode="enterprise",
                company_profile=None,
            )
            root_unit_id = str(root_unit.id)

        redirect_to = "/onboarding" if is_super_admin else "/"

        await log_event(
            db,
            tenant_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
            actor_id=user.id,
            actor_email=email,
            action=audit_actions.USER_INVITE_CLAIMED,
            resource="user",
            resource_id=user.id,
            payload={
                "email": email,
                "is_super_admin": is_super_admin,
                "root_unit_id": root_unit_id,
            },
            ip_address=request.client.host if request.client else None,
        )

        logger.info(
            "auth.invite_accepted",
            user_id=str(user.id),
            tenant_id=str(claimed_row.tenant_id),
            is_super_admin=is_super_admin,
        )
    except HTTPException:
        await _safe_delete_auth_user(provider, identity.id, created_by_this_request=auth_user_created_here)
        raise
    except Exception:
        logger.exception("auth.accept_invite.db_write_failed")
        await _safe_delete_auth_user(provider, identity.id, created_by_this_request=auth_user_created_here)
        raise HTTPException(
            status_code=500,
            detail="Could not finalize account creation",
        )

    return AcceptInviteResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        redirect_to=redirect_to,
    )


async def _safe_delete_auth_user(
    provider: "AuthProvider",
    auth_user_id: str,
    *,
    created_by_this_request: bool,
) -> None:
    """Best-effort compensation. Only deletes when the auth user was
    created by THIS request — critical for the race-claim path, where a
    losing request reuses a pre-existing user via `find_user_by_email`
    and must NOT delete it (that would break the winning request's
    session).

    Never raises — logs on failure and leaves the auth user orphaned
    (next invite retry self-heals via the already-exists fallback).
    """
    if not created_by_this_request:
        logger.warning(
            "auth.accept_invite.compensation_skipped",
            auth_user_id=auth_user_id,
            reason="auth user was pre-existing; skipping deletion to avoid disrupting its owner",
        )
        return
    try:
        await provider.delete_user(auth_user_id)
    except Exception as comp_err:
        logger.error(
            "auth.accept_invite.compensation_failed",
            auth_user_id=auth_user_id,
            error=str(comp_err),
        )


@router.get("/me", response_model=MeResponse)
async def get_current_user(
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_bypass_db),
) -> MeResponse:
    """Return the current user's profile, assignments, and company info.

    RLS bypass justification (B2):
      Uses get_bypass_db rather than get_tenant_db because:
      1. get_current_user_roles already runs under a bypass session — it
         resolves the user row by auth_user_id, which is needed to discover
         tenant_id in the first place.
      2. For newly-signed-up users whose JWT was issued before the auth
         hook could fill in tenant_id (edge case: user row not yet in DB
         when the token was minted), payload.tenant_id may be "". That
         would trip _coerce_tenant_id and 401 the user on every dashboard
         load, including the onboarding redirect path.
      3. /me only SELECTs the caller's own Client and counts their own
         OrganizationalUnit rows — both are scoped by user.tenant_id from
         the authenticated DB user row, not from the JWT. There is no
         cross-tenant read surface even without RLS enforcement.
      When tenant propagation in the JWT is rock-solid we can revisit.
    """
    user = ctx.user

    # Get client name
    result = await db.execute(select(Client).where(Client.id == user.tenant_id))
    client = result.scalar_one()

    # Check if tenant has any org units (separate query — not per-row)
    org_exists_result = await db.execute(
        select(func.count())
        .select_from(OrganizationalUnit)
        .where(OrganizationalUnit.client_id == user.tenant_id)
    )
    has_org_units = (org_exists_result.scalar() or 0) > 0

    return MeResponse(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        tenant_id=str(user.tenant_id),
        client_name=client.name,
        is_super_admin=ctx.is_super_admin,
        onboarding_complete=client.onboarding_complete,
        has_org_units=has_org_units,
        workspace_mode=ctx.workspace_mode,
        assignments=[
            RoleAssignmentResponse(
                org_unit_id=str(a.org_unit_id),
                org_unit_name=a.org_unit_name,
                role_name=a.role_name,
                permissions=a.permissions,
            )
            for a in ctx.assignments
        ],
    )


@router.post("/onboarding/complete")
async def complete_onboarding(
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_bypass_db),
) -> dict[str, str]:
    """Mark onboarding as complete. Super admin only.

    Validates: caller is super admin AND at least one org unit exists.

    RLS bypass justification (B2):
      Uses get_bypass_db because this endpoint UPDATEs `clients.onboarding_complete`.
      The `clients` table has only a `tenant_read` SELECT policy plus
      `service_bypass` for all commands — there is no UPDATE policy under
      tenant isolation, so writes must run under bypass_rls. Swapping to
      get_tenant_db would silently 0-row the UPDATE under RLS.
      Authorization is enforced by require_super_admin (checked explicitly
      above via ctx.is_super_admin) before any mutation happens.
    """
    if not ctx.is_super_admin:
        raise HTTPException(status_code=403, detail="Only the super admin can complete onboarding")

    # Check at least one org unit exists
    org_count = await db.execute(
        select(func.count())
        .select_from(OrganizationalUnit)
        .where(OrganizationalUnit.client_id == ctx.user.tenant_id)
    )
    if (org_count.scalar() or 0) == 0:
        raise HTTPException(
            status_code=400,
            detail="Create at least one organizational unit before completing onboarding",
        )

    result = await db.execute(select(Client).where(Client.id == ctx.user.tenant_id))
    client = result.scalar_one()
    client.onboarding_complete = True

    await log_event(
        db,
        tenant_id=ctx.user.tenant_id,
        actor_id=ctx.user.id,
        actor_email=ctx.user.email,
        action=audit_actions.CLIENT_ONBOARDING_COMPLETED,
        resource="client",
        resource_id=ctx.user.tenant_id,
        payload={},
        ip_address=request.client.host if request.client else None,
    )

    return {"status": "completed"}
