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
from app import pubsub

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
#
# A small number of internal tables have NO `tenant_id` and only carry a
# `service_bypass` policy. Those go in `_BYPASS_ONLY_TABLES` below.
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
    "pipeline_stage_participants",
    "stage_question_banks",
    "stage_questions",
    # Phase 3B — candidates module
    "candidates",
    "candidate_job_assignments",
    "candidate_stage_progress",
    # Phase 3C — scheduler + session
    "candidate_session_tokens",
)


# Tables intentionally excluded from `_TENANT_SCOPED_TABLES` because they
# have NO `tenant_id` column and therefore NO `tenant_isolation` policy.
# They are RLS-enabled but service-bypass only — tenant scope is enforced
# at the application layer (e.g. via JWT claim) instead of in the policy.
# Each such table MUST still carry a `service_bypass` policy so the table
# is reachable from the bypass-RLS internal API; this assertion verifies
# that.
#
# DO NOT add a tenant-scoped table here, and DO NOT move a bypass-only table
# into `_TENANT_SCOPED_TABLES` — the migration intentionally omits the
# tenant_isolation policy and the assertion would fail.
_BYPASS_ONLY_TABLES: tuple[str, ...] = (
    # Phase 3 retired engine_token_uses (along with engine_dispatch_tokens)
    # — the engine no longer mints a JWT or reaches over HTTP. The list is
    # left as an empty tuple so future bypass-only tables can be added here.
)


async def _assert_rls_completeness() -> None:
    """Verify RLS completeness for all tracked tables at startup.

    Tenant-scoped tables (``_TENANT_SCOPED_TABLES``) must carry both a
    ``tenant_isolation`` policy (with a non-NULL WITH CHECK) and a
    ``service_bypass`` policy.

    Bypass-only tables (``_BYPASS_ONLY_TABLES``) have no ``tenant_id`` column
    and therefore no ``tenant_isolation`` policy; they must carry at least a
    ``service_bypass`` policy.

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
    missing_bypass_only: list[str] = []  # bypass-only tables missing service_bypass

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

    for table in _BYPASS_ONLY_TABLES:
        if table not in found_service_bypass:
            missing_bypass_only.append(table)

    if missing_isolation or missing_check or missing_bypass or missing_bypass_only:
        logger.critical(
            "rls.completeness_check_failed",
            missing_tenant_isolation=missing_isolation,
            missing_with_check=missing_check,
            missing_service_bypass=missing_bypass,
            missing_bypass_only_service_bypass=missing_bypass_only,
            tenant_scoped_tables=list(_TENANT_SCOPED_TABLES),
            bypass_only_tables=list(_BYPASS_ONLY_TABLES),
        )
        raise RuntimeError(
            "RLS completeness check failed — refusing to start. "
            f"missing tenant_isolation: {missing_isolation!r}; "
            f"tenant_isolation without WITH CHECK: {missing_check!r}; "
            f"missing service_bypass (tenant-scoped): {missing_bypass!r}; "
            f"missing service_bypass (bypass-only): {missing_bypass_only!r}. "
            "This means a migration shipped partially-applied RLS — fix "
            "the corresponding migration and redeploy."
        )

    logger.info(
        "rls.completeness_check_ok",
        tenant_scoped_tables_verified=len(_TENANT_SCOPED_TABLES),
        bypass_only_tables_verified=len(_BYPASS_ONLY_TABLES),
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

    # Force every per-module models.py to load so Base.registry sees every
    # mapper before configure() runs. Without these imports, a module whose
    # router never references its own ORM classes (rare but possible) would
    # not register its tables, and the first cross-module query would fail
    # at runtime with "Could not resolve string FK".
    #
    # Phase 4 of the modular-monolith refactor split app/models.py per
    # module. Every model module is imported here so configure() resolves
    # every string FK at boot, not at first request.
    import app.modules.auth.models  # noqa: F401
    import app.modules.audit.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401

    from app.database import Base
    Base.registry.configure()

    # OpenTelemetry bootstrap. Both exporters are off by default; setting
    # OTEL_DEV_CONSOLE_EXPORTER=true or OTEL_EXPORTER_OTLP_ENDPOINT=<url>
    # turns them on. See app/ai/otel.py for env-var contract.
    # Phase 3 dropped the OpenAI auto-instrumentor; LLM call sites use
    # explicit start_as_current_span blocks. set_llm_span_attributes still
    # works against those manual spans.
    from opentelemetry import trace
    from app.ai.otel import bootstrap_tracer_provider

    _otel_provider = bootstrap_tracer_provider()
    trace.set_tracer_provider(_otel_provider)

    # Block startup if any tenant-scoped table is missing an RLS policy.
    # This is the last line of defence against a deploy that ships a
    # migration which forgot to enable RLS or forgot the WITH CHECK
    # clause.
    await _assert_rls_completeness()

    # Pub/sub: verify connectivity before accepting traffic.
    # Runs after RLS assertion so a broken DB schema fails first.
    await pubsub.startup()

    yield

    # Shutdown — reverse order of startup.
    await pubsub.shutdown()

    from app.database import engine

    # OTel shutdown: flush + close any in-flight span batches before exit.
    _otel_provider.shutdown()
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
    from app.modules.question_bank.refine import router as question_bank_refine_router
    from app.modules.scheduler.router import scheduler_router
    from app.modules.session.router import candidate_session_router, session_router
    from app.modules.analysis.router import router as analysis_router
    from app.modules.reporting.router import router as reporting_router
    from app.modules.notifications.router import router as notifications_router
    from app.modules.auth.router import router as auth_router
    from app.modules.admin.router import router as admin_router
    from app.modules.settings.router import router as settings_router
    from app.modules.org_units.router import router as org_units_router
    from app.modules.roles.router import router as roles_router
    from app.modules.pipelines.router import router as pipelines_router
    from app.modules.candidates.router import (
        kanban_router as candidates_kanban_router,
        router as candidates_router,
    )

    application.include_router(auth_router)
    application.include_router(ats_router)
    application.include_router(jd_router)
    application.include_router(question_bank_router)
    application.include_router(question_bank_refine_router)
    application.include_router(analysis_router)
    application.include_router(reporting_router)
    application.include_router(notifications_router)
    application.include_router(admin_router)
    application.include_router(settings_router)
    application.include_router(org_units_router)
    application.include_router(roles_router)
    application.include_router(pipelines_router)
    application.include_router(candidates_router)
    application.include_router(candidates_kanban_router)
    # Phase 3C — scheduler + session
    application.include_router(scheduler_router)
    application.include_router(candidate_session_router)
    application.include_router(session_router)
    # Phase 3 retired the interview_runtime HTTP router — the engine now
    # calls build_session_config / record_session_result in-process.

    # --- Exception handlers (Phase 2A — JD module) ---
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from app.modules.auth.errors import AccountSuspendedError, suspended_response
    from app.modules.jd.errors import (
        CompanyProfileIncompleteError,
        IllegalTransitionError,
    )

    @application.exception_handler(AccountSuspendedError)
    async def _account_suspended(
        request: Request, exc: AccountSuspendedError
    ) -> JSONResponse:
        return suspended_response(exc.status)

    from app.modules.admin.service import ConfirmationMismatchError as _ConfirmationMismatchError

    @application.exception_handler(_ConfirmationMismatchError)
    async def _confirmation_mismatch(
        request: Request, exc: _ConfirmationMismatchError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Confirmation name does not match.",
                "code": "CONFIRMATION_MISMATCH",
            },
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

    # --- Exception handlers (Phase 3B — candidates module) ---
    from app.modules.candidates.errors import (
        AssignmentAlreadyExistsError,
        CandidateHasActiveSessionError,
        CandidateNotFoundError,
        DuplicateEmailError,
        InvalidResumeContentTypeError,
        InvalidStageTransitionError,
        ResumeNotFoundInS3Error,
        StageNotInPipelineError,
    )

    @application.exception_handler(CandidateNotFoundError)
    async def _candidate_not_found(
        request: Request, exc: CandidateNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Candidate not found"})

    @application.exception_handler(DuplicateEmailError)
    async def _duplicate_email(
        request: Request, exc: DuplicateEmailError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "code": "DUPLICATE_EMAIL"},
        )

    @application.exception_handler(AssignmentAlreadyExistsError)
    async def _assignment_exists(
        request: Request, exc: AssignmentAlreadyExistsError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Candidate already assigned to this job",
                "code": "ASSIGNMENT_ALREADY_EXISTS",
            },
        )

    @application.exception_handler(StageNotInPipelineError)
    async def _stage_not_in_pipeline(
        request: Request, exc: StageNotInPipelineError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "STAGE_NOT_IN_PIPELINE"},
        )

    @application.exception_handler(InvalidStageTransitionError)
    async def _invalid_stage_transition(
        request: Request, exc: InvalidStageTransitionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc) or "Invalid stage transition",
                "code": "INVALID_STAGE_TRANSITION",
            },
        )

    @application.exception_handler(CandidateHasActiveSessionError)
    async def _active_session(
        request: Request, exc: CandidateHasActiveSessionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Candidate has an active session — cannot redact PII",
                "code": "CANDIDATE_HAS_ACTIVE_SESSION",
            },
        )

    @application.exception_handler(ResumeNotFoundInS3Error)
    async def _resume_not_found(
        request: Request, exc: ResumeNotFoundInS3Error
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Resume upload not found in S3",
                "code": "RESUME_NOT_FOUND",
            },
        )

    @application.exception_handler(InvalidResumeContentTypeError)
    async def _invalid_resume_type(
        request: Request, exc: InvalidResumeContentTypeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Resume must be a PDF (content-type application/pdf)",
                "code": "INVALID_RESUME_CONTENT_TYPE",
            },
        )

    # --- Exception handlers (Phase 3C — scheduler + session) ---
    from app.modules.scheduler.errors import (
        AssignmentNotActiveError,
        InvalidStageTypeForInviteError,
        SessionAlreadyStartedError,
    )
    from app.modules.session.errors import (
        AgentDispatchFailedError,
        IllegalStartStateError,
        InvalidOtpError,
        InvalidSessionStateError,
        OtpExpiredError,
        OtpMaxAttemptsReachedError,
        OtpRateLimitedError,
        OtpRequiredError,
        SessionNotFoundError,
        SessionNotRejoinableError,
        TokenAlreadyUsedError,
        TokenSupersededError,
    )

    @application.exception_handler(InvalidStageTypeForInviteError)
    async def _invalid_stage_type_for_invite(
        request: Request, exc: InvalidStageTypeForInviteError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc),
                "code": "INVALID_STAGE_TYPE_FOR_INVITE",
                "stage_type": exc.stage_type,
            },
        )

    @application.exception_handler(AssignmentNotActiveError)
    async def _assignment_not_active(
        request: Request, exc: AssignmentNotActiveError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc) or "Assignment is not active",
                "code": "ASSIGNMENT_NOT_ACTIVE",
            },
        )

    @application.exception_handler(SessionAlreadyStartedError)
    async def _session_already_started(
        request: Request, exc: SessionAlreadyStartedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc) or "Session already started",
                "code": "SESSION_ALREADY_STARTED",
            },
        )

    @application.exception_handler(SessionNotFoundError)
    async def _session_not_found(
        request: Request, exc: SessionNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": "Session not found"},
        )

    @application.exception_handler(TokenSupersededError)
    async def _token_superseded(
        request: Request, exc: TokenSupersededError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "detail": str(exc) or "Token has been superseded",
                "code": "TOKEN_SUPERSEDED",
            },
        )

    @application.exception_handler(AgentDispatchFailedError)
    async def _agent_dispatch_failed(
        request: Request, exc: AgentDispatchFailedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "detail": exc.detail or "Agent dispatch failed",
                "code": "AGENT_DISPATCH_FAILED",
            },
        )

    @application.exception_handler(IllegalStartStateError)
    async def _illegal_start_state(
        request: Request, exc: IllegalStartStateError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc) or "Session is not in a startable state",
                "code": "INVALID_SESSION_STATE",
            },
        )

    @application.exception_handler(InvalidSessionStateError)
    async def _invalid_session_state(
        request: Request, exc: InvalidSessionStateError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc) or "Invalid session state for this action",
                "code": "INVALID_SESSION_STATE",
            },
        )

    @application.exception_handler(OtpRequiredError)
    async def _otp_required(
        request: Request, exc: OtpRequiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc) or "OTP verification required",
                "code": "OTP_REQUIRED",
            },
        )

    @application.exception_handler(OtpRateLimitedError)
    async def _otp_rate_limited(
        request: Request, exc: OtpRateLimitedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_seconds)},
            content={
                "detail": str(exc),
                "code": "OTP_RATE_LIMITED",
                "retry_after_seconds": exc.retry_after_seconds,
            },
        )

    @application.exception_handler(OtpExpiredError)
    async def _otp_expired(
        request: Request, exc: OtpExpiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc) or "OTP has expired",
                "code": "OTP_EXPIRED",
                "attempts_remaining": 0,
            },
        )

    @application.exception_handler(OtpMaxAttemptsReachedError)
    async def _otp_max_attempts(
        request: Request, exc: OtpMaxAttemptsReachedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc) or "Maximum OTP attempts reached",
                "code": "OTP_MAX_ATTEMPTS_REACHED",
                "attempts_remaining": 0,
            },
        )

    @application.exception_handler(InvalidOtpError)
    async def _invalid_otp(
        request: Request, exc: InvalidOtpError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc),
                "code": "INVALID_OTP",
                "attempts_remaining": exc.attempts_remaining,
            },
        )

    @application.exception_handler(TokenAlreadyUsedError)
    async def _token_already_used(
        request: Request, exc: TokenAlreadyUsedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc) or "Token has already been used",
                "code": "TOKEN_ALREADY_USED",
            },
        )

    @application.exception_handler(SessionNotRejoinableError)
    async def _session_not_rejoinable(
        request: Request, exc: SessionNotRejoinableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "code": "SESSION_NOT_REJOINABLE",
                "current_state": exc.current_state,
            },
        )

    # --- Health check ---
    @application.get("/health", tags=["infra"])
    async def health() -> dict:
        import asyncio

        result: dict = {"status": "ok", "checks": {}}

        # Pub/sub ping — 2s timeout.
        try:
            client = pubsub._get_client()
            await asyncio.wait_for(client.ping(), timeout=2.0)
            result["checks"]["pubsub"] = "ok"
        except Exception as exc:  # noqa: BLE001
            result["checks"]["pubsub"] = f"failed: {exc}"
            result["status"] = "degraded"

        return result

    return application


app = create_app()
