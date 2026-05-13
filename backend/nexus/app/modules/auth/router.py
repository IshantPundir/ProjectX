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
    from app.modules.auth.admin.base import SessionTokens

from app.database import get_bypass_db
from app.modules.audit import actions as audit_actions, log_event
from app.modules.auth.admin import (
    AuthProvider,
    AuthProviderError,
    InvalidCredentialsError,
    SessionTokens,
    UserAlreadyExistsError,
    UserIdentity,
    UserNotFoundError,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.errors import AccountSuspendedError
from app.modules.auth.models import User, UserInvite
from app.modules.auth.schemas import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RoleAssignmentResponse,
    VerifyInviteResponse,
)
from app.modules.auth.service import verify_access_token
from app.modules.org_units import Client, OrganizationalUnit

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

    # Tenant-lifecycle gate. A blocked or deleted tenant must not have
    # outstanding invites accepted into it.
    if client.deleted_at is not None or client.blocked_at is not None:
        suspension_status = "deleted" if client.deleted_at is not None else "blocked"
        raise AccountSuspendedError(status=suspension_status)

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
    from app.modules.auth.admin import get_auth_provider

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

    # Tenant-lifecycle gate, BEFORE we touch the auth provider. We must
    # not provision/reuse a Supabase Auth user for a tenant that has been
    # suspended in the interim — the user would end up with a working
    # auth identity that immediately can't reach the dashboard.
    if _client_row.deleted_at is not None or _client_row.blocked_at is not None:
        suspension_status = "deleted" if _client_row.deleted_at is not None else "blocked"
        raise AccountSuspendedError(status=suspension_status)

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

        # Auto-link any ATS user mappings that match this email. Closes the
        # loop on the team-page invite flow: an invite sent for an ATS-imported
        # user now wires up internal_user_id without a separate mapping step,
        # so assigned-recruiter resolution on imported jobs starts working
        # immediately. Match is case-insensitive (vendors normalize).
        await db.execute(
            sqlalchemy.text("""
                UPDATE public.ats_user_mappings
                   SET internal_user_id = :user_id,
                       mapped_at        = NOW(),
                       mapped_by        = :user_id,
                       updated_at       = NOW()
                 WHERE tenant_id        = :tenant_id
                   AND LOWER(external_user_email) = LOWER(:email)
                   AND internal_user_id IS NULL
            """),
            {
                "user_id": str(user.id),
                "tenant_id": str(claimed_row.tenant_id),
                "email": email,
            },
        )

        is_super_admin = claimed_row.projectx_admin_id is not None
        root_unit_id = ""
        if is_super_admin:
            client_row = (
                await db.execute(
                    sqlalchemy.text(
                        "UPDATE public.clients SET super_admin_id = :user_id "
                        "WHERE id = :tenant_id "
                        "RETURNING name, domain"
                    ),
                    {
                        "user_id": str(user.id),
                        "tenant_id": str(claimed_row.tenant_id),
                    },
                )
            ).first()
            # The Client row is guaranteed to exist — the User row we just
            # inserted above has a tenant_id FK to it.
            client_name = client_row.name  # type: ignore[union-attr]
            client_domain = client_row.domain  # type: ignore[union-attr]

            # Seed the org unit's editable metadata from admin-provisioned
            # fields so the user doesn't see empty inputs for values they've
            # already supplied. `clients.domain` is the same shape as the
            # form's "Website" field (`metadata.website`).
            root_metadata = {"website": client_domain} if client_domain else None

            from app.modules.org_units import create_org_unit as _create_root_unit

            root_unit = await _create_root_unit(
                db=db,
                client_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
                name=client_name,
                unit_type="company",
                parent_unit_id=None,
                created_by=user.id,
                actor_email=email,
                company_profile=None,
                metadata=root_metadata,
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


@router.post("/login", response_model=LoginResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_bypass_db),
) -> LoginResponse:
    """Backend-owned login.

    Moves the last `supabase.auth.signInWithPassword` call behind the
    provider-agnostic AuthProvider boundary so a future Cognito/Keycloak
    swap is a config change, not a code rewrite.

    Error contract:
    - 401 for invalid credentials / unknown user. Generic message,
      no user enumeration.
    - 403 for accounts missing tenant_id (ProjectX-admin-only).
    - 403 for deactivated accounts (users.is_active = false).
    - 422 for Pydantic validation failures (handled by FastAPI).

    Public endpoint — no bearer token required (see middleware
    _PUBLIC_PREFIXES).

    RLS bypass justification:
      Uses get_bypass_db because login is pre-tenant-context: we have
      no verified tenant_id until after the provider signs the user in
      and we decode their fresh access token. The handler only reads
      the caller's own user + client row (lookup-by-email then
      lookup-by-id) — no cross-tenant surface.
    """
    from app.modules.auth.admin import get_auth_provider

    provider = get_auth_provider()

    # Phase 1: sign in. Generic 401 on any credential/user-not-found path
    # so the handler never discloses whether an email is registered.
    #
    # Note: this branch raises BEFORE tokens are minted by the provider,
    # so there is nothing to revoke here. _revoke_quietly is only used
    # below, where sign_in_with_password has already returned tokens.
    try:
        tokens = await provider.sign_in_with_password(data.email, data.password)
    except (InvalidCredentialsError, UserNotFoundError):
        logger.info(
            "auth.login.rejected",
            email=data.email,
            reason="invalid_credentials",
        )
        raise HTTPException(
            status_code=401, detail="Invalid email or password."
        )

    # Phase 2: decode the access token to pull tenant_id. A token that
    # fails verification at this point means the provider returned a
    # token we cannot trust — treat as an auth failure.
    payload = verify_access_token(tokens.access_token)
    if payload is None:
        logger.error("auth.login.token_verify_failed", email=data.email)
        await _revoke_quietly(provider, tokens)
        raise HTTPException(
            status_code=401, detail="Invalid email or password."
        )

    tenant_id = payload.tenant_id or ""
    if not tenant_id:
        logger.info("auth.login.no_tenant", email=data.email)
        await _revoke_quietly(provider, tokens)
        raise HTTPException(
            status_code=403,
            detail=(
                "This account does not have access to the client dashboard."
            ),
        )

    # Phase 3: app user lookup. Reject deactivated accounts.
    # Filter on is_active=TRUE so a soft-deleted historical row from a
    # prior (now-deleted) tenant does not collide with the current active
    # row when the same email has been re-onboarded — scalar_one_or_none()
    # would otherwise raise on the multi-row result.
    user_row = await db.execute(
        select(User).where(User.email == data.email, User.is_active == True)
    )
    user = user_row.scalar_one_or_none()
    if user is None:
        logger.error("auth.login.no_app_user", email=data.email)
        await _revoke_quietly(provider, tokens)
        raise HTTPException(
            status_code=403,
            detail=(
                "This account does not have access to the client dashboard."
            ),
        )
    if not user.is_active:
        logger.info("auth.login.deactivated", user_id=str(user.id))
        await _revoke_quietly(provider, tokens)
        raise HTTPException(
            status_code=403,
            detail="This account has been deactivated.",
        )

    # Phase 4: compute redirect_to.
    client_row = await db.execute(
        select(Client).where(Client.id == user.tenant_id)
    )
    client = client_row.scalar_one()

    # Lifecycle gate — refuse login for suspended tenants. We've already
    # minted Supabase tokens at this point, so revoke them before raising
    # so the now-rejected user doesn't carry a usable session.
    if client.deleted_at is not None or client.blocked_at is not None:
        suspension_status = "deleted" if client.deleted_at is not None else "blocked"
        logger.info(
            "auth.login.suspended",
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            status=suspension_status,
        )
        await _revoke_quietly(provider, tokens)
        raise AccountSuspendedError(status=suspension_status)

    is_super_admin = client.super_admin_id == user.id
    redirect_to = (
        "/onboarding"
        if is_super_admin and not client.onboarding_complete
        else "/"
    )

    logger.info(
        "auth.login.success",
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        redirect_to=redirect_to,
    )

    return LoginResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        redirect_to=redirect_to,
    )


async def _revoke_quietly(
    provider: "AuthProvider", tokens: "SessionTokens"
) -> None:
    """Revoke a session, swallowing transport errors.

    Used by the login handler when an auth failure happens AFTER the
    provider has minted tokens. The user is already being told their
    auth attempt failed; a revocation failure must not change that
    response, but it MUST be surfaced in structured logs at error level
    for ops.
    """
    try:
        await provider.sign_out(tokens)
    except AuthProviderError:
        logger.exception("auth.login.sign_out_failed")


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
