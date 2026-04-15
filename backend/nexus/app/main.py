from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import sqlalchemy
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Dramatiq broker setup MUST be imported before any router that transitively
# imports a @dramatiq.actor module. Otherwise the decorator runs against
# Dramatiq's default broker (localhost:6379) and actor.send() calls from the
# API process fail with "Connection refused" because Redis is a sibling
# container, not on localhost inside the nexus container.
from app import brokers  # noqa: F401

from app.config import settings

logger = structlog.get_logger()


# Every tenant-scoped table MUST carry BOTH a `tenant_isolation` policy
# (with a non-NULL WITH CHECK — the full-command form) AND a
# `service_bypass` policy. A partial rollout that drops RLS on even one
# table silently turns that table into a cross-tenant leak.
#
# This list is kept in sync with app/models.py + the migration history.
# Update it whenever a new tenant-scoped table lands — the startup
# assertion below will fail loudly if the corresponding migration forgets
# to add the two policies.
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "clients",
    "users",
    "organizational_units",
    "user_role_assignments",
    "user_invites",
    "audit_log",
    "job_postings",
    "job_posting_signal_snapshots",
    "sessions",
    "pipeline_templates",
    "pipeline_template_stages",
    "job_pipeline_instances",
    "job_pipeline_stages",
    "stage_question_banks",
    "stage_questions",
)


async def _assert_rls_completeness() -> None:
    """Verify every tenant-scoped table has both tenant_isolation + service_bypass.

    Runs once at startup. If any table is missing either policy — or if
    tenant_isolation has a NULL WITH CHECK, which is the 'FOR SELECT
    trap' described in backend/CLAUDE.md — we log CRITICAL and raise
    RuntimeError so the deploy aborts instead of silently shipping with
    partially-applied RLS.

    Skips when:
      - settings.environment == 'test': the test suite uses
        Base.metadata.create_all, not real alembic migrations, so the
        policies don't exist at the test DB level.
      - settings.db_runtime_role is None: the role switch is disabled,
        which means every connection runs as postgres (BYPASSRLS). There
        is nothing to enforce, so checking the policies would be
        misleading. This is the bootstrap configuration before migration
        0010 has run.
    """
    if settings.environment == "test":
        return
    if not settings.db_runtime_role:
        return

    # Imported lazily so importing app.main at test-collection time does
    # not open a DB engine.
    from app.database import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            sqlalchemy.text(
                """
                SELECT tablename, policyname, with_check
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND policyname IN ('tenant_isolation', 'service_bypass')
                """
            )
        )
        rows = result.all()

    found_tenant_isolation: dict[str, object] = {}
    found_service_bypass: set[str] = set()
    for tablename, policyname, with_check in rows:
        if policyname == "tenant_isolation":
            found_tenant_isolation[tablename] = with_check
        elif policyname == "service_bypass":
            found_service_bypass.add(tablename)

    missing_isolation: list[str] = []
    missing_check: list[str] = []
    missing_bypass: list[str] = []

    for table in _TENANT_SCOPED_TABLES:
        if table not in found_tenant_isolation:
            missing_isolation.append(table)
        else:
            # with_check is None when the policy was written as
            # FOR SELECT / FOR INSERT / FOR UPDATE USING (...) with no
            # matching WITH CHECK — the 'silent trap' that blocks writes
            # from tenant sessions. See backend/CLAUDE.md.
            if found_tenant_isolation[table] is None:
                missing_check.append(table)
        if table not in found_service_bypass:
            missing_bypass.append(table)

    if missing_isolation or missing_check or missing_bypass:
        logger.critical(
            "rls.completeness_check_failed",
            missing_tenant_isolation=missing_isolation,
            missing_with_check=missing_check,
            missing_service_bypass=missing_bypass,
            tenant_scoped_tables=list(_TENANT_SCOPED_TABLES),
        )
        raise RuntimeError(
            "RLS completeness check failed — refusing to start. "
            f"missing tenant_isolation: {missing_isolation!r}; "
            f"tenant_isolation without WITH CHECK: {missing_check!r}; "
            f"missing service_bypass: {missing_bypass!r}. "
            "This means a migration shipped partially-applied RLS — fix "
            "the corresponding migration and redeploy."
        )

    logger.info(
        "rls.completeness_check_ok",
        tables_verified=len(_TENANT_SCOPED_TABLES),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.debug
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
    )
    logger.info("nexus.startup", environment=settings.environment)

    # Block startup if any tenant-scoped table is missing an RLS policy.
    # This is the last line of defence against a deploy that ships a
    # migration which forgot to enable RLS or forgot the WITH CHECK
    # clause.
    await _assert_rls_completeness()

    yield

    # Shutdown
    from app.ai.client import shutdown_langfuse
    from app.database import engine

    shutdown_langfuse()
    await engine.dispose()
    logger.info("nexus.shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # --- Middleware ---
    # Order matters: Starlette's `add_middleware` inserts at position 0,
    # so the LAST added is the OUTERMOST. We need CORSMiddleware to be the
    # outermost layer so that error responses returned by inner middleware
    # (e.g. AuthMiddleware short-circuiting with a 401) still pass through
    # CORS on the way out and pick up `Access-Control-Allow-Origin`.
    # Without this, browsers see the 401 as a CORS-blocked response and
    # surface it client-side as `TypeError: Failed to fetch` instead of an
    # ordinary HTTP error — making auth failures look like network
    # failures and breaking error handling in the dashboard.
    from app.middleware.auth import AuthMiddleware
    from app.middleware.tenant import TenantMiddleware

    application.add_middleware(TenantMiddleware)
    application.add_middleware(AuthMiddleware)

    # CORS goes LAST so it ends up as the outermost middleware. Always use
    # the explicit settings.cors_origins list. A wildcard
    # (`allow_origins=["*"]`) combined with `allow_credentials=True` is
    # rejected by all modern browsers, so the old "debug = wildcard"
    # shortcut never actually worked for credentialed requests — it only
    # masked configuration mistakes. Operators who need LAN access in
    # debug mode should add their LAN origin to CORS_ORIGINS.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-correlation-id"],
    )

    # --- Routers ---
    from app.modules.ats.router import router as ats_router
    from app.modules.jd.router import router as jd_router
    from app.modules.question_bank.router import router as question_bank_router
    from app.modules.scheduler.router import router as scheduler_router
    from app.modules.session.router import router as session_router, candidate_router
    from app.modules.analysis.router import router as analysis_router
    from app.modules.reporting.router import router as reporting_router
    from app.modules.notifications.router import router as notifications_router
    from app.modules.auth.router import router as auth_router
    from app.modules.admin.router import router as admin_router
    from app.modules.settings.router import router as settings_router, workspace_router
    from app.modules.org_units.router import router as org_units_router
    from app.modules.roles.router import router as roles_router
    from app.modules.pipelines.router import router as pipelines_router

    application.include_router(auth_router)
    application.include_router(ats_router)
    application.include_router(jd_router)
    application.include_router(question_bank_router)
    application.include_router(scheduler_router)
    application.include_router(session_router)
    application.include_router(analysis_router)
    application.include_router(reporting_router)
    application.include_router(notifications_router)
    application.include_router(candidate_router)
    application.include_router(admin_router)
    application.include_router(settings_router)
    application.include_router(workspace_router)
    application.include_router(org_units_router)
    application.include_router(roles_router)
    application.include_router(pipelines_router)

    # --- Exception handlers (Phase 2A — JD module) ---
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from app.modules.jd.errors import (
        CompanyProfileIncompleteError,
        IllegalTransitionError,
    )

    _ILLEGAL_TRANSITION_MESSAGES: dict[tuple[str, str], str] = {
        ("signals_extracting", "signals_extracting"):
            "Job is already being processed",
        ("signals_extracted", "signals_extracting"):
            "This job has already been extracted successfully — "
            "retry is only valid after an extraction failure",
        ("draft", "signals_extracted"):
            "Job cannot transition directly from draft to extracted",
        ("signals_confirmed", "signals_confirmed"):
            "Signals are already confirmed",
        ("signals_confirmed", "signals_extracting"):
            "Cannot re-extract a confirmed job — edit signals instead",
    }

    @application.exception_handler(IllegalTransitionError)
    async def illegal_transition_handler(
        request: Request, exc: IllegalTransitionError
    ) -> JSONResponse:
        key = (exc.from_state, exc.to_state)
        detail = _ILLEGAL_TRANSITION_MESSAGES.get(
            key,
            f"Cannot transition job from {exc.from_state} to {exc.to_state}",
        )
        return JSONResponse(status_code=409, content={"detail": detail})

    @application.exception_handler(CompanyProfileIncompleteError)
    async def company_profile_incomplete_handler(
        request: Request, exc: CompanyProfileIncompleteError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Company profile must be completed before creating a job description. "
                    "Visit Settings → Org Units → [your company] → Company Profile to finish setup."
                ),
                "org_unit_id": str(exc.org_unit_id),
            },
        )

    # --- Exception handlers (Phase 2C.2 — question_bank module) ---
    from app.modules.question_bank.errors import (
        BankAlreadyGeneratingError as QB_BankAlreadyGeneratingError,
        BankNotInReviewingError as QB_BankNotInReviewingError,
        IllegalTransitionError as QB_IllegalTransitionError,
        KnockoutUnprobedError as QB_KnockoutUnprobedError,
        MandatoryOverrunError as QB_MandatoryOverrunError,
        ReorderDuplicateError as QB_ReorderDuplicateError,
        ReorderMismatchError as QB_ReorderMismatchError,
    )

    @application.exception_handler(QB_BankAlreadyGeneratingError)
    async def qb_already_generating(
        request: Request, exc: QB_BankAlreadyGeneratingError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(QB_IllegalTransitionError)
    async def qb_illegal_transition(
        request: Request, exc: QB_IllegalTransitionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "from_state": exc.from_state,
                "to_state": exc.to_state,
            },
        )

    @application.exception_handler(QB_BankNotInReviewingError)
    async def qb_not_reviewing(
        request: Request, exc: QB_BankNotInReviewingError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(QB_KnockoutUnprobedError)
    async def qb_knockout_unprobed(
        request: Request, exc: QB_KnockoutUnprobedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "signal_value": exc.signal_value},
        )

    @application.exception_handler(QB_MandatoryOverrunError)
    async def qb_mandatory_overrun(
        request: Request, exc: QB_MandatoryOverrunError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(QB_ReorderMismatchError)
    async def qb_reorder_mismatch(
        request: Request, exc: QB_ReorderMismatchError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(QB_ReorderDuplicateError)
    async def qb_reorder_duplicate(
        request: Request, exc: QB_ReorderDuplicateError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # --- Health check ---
    @application.get("/health", tags=["infra"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
