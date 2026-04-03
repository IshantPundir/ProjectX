from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
            structlog.dev.ConsoleRenderer() if settings.debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
    )
    logger.info("nexus.startup", environment=settings.environment)

    yield

    # Shutdown
    from app.database import engine

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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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

    # --- Health check ---
    @application.get("/health", tags=["infra"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
