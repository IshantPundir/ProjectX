from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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

    # --- CORS ---
    # Always use the explicit settings.cors_origins list. A wildcard
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

    # --- Middleware ---
    from app.middleware.auth import AuthMiddleware
    from app.middleware.tenant import TenantMiddleware

    application.add_middleware(TenantMiddleware)
    application.add_middleware(AuthMiddleware)

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
