from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Nexus"
    debug: bool = False
    environment: str = "development"  # development | staging | production | test

    # Database (asyncpg)
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"

    # PostgreSQL role that per-request sessions run under via `SET LOCAL ROLE`.
    # This is how RLS is actually enforced — the `postgres` role in Supabase
    # has rolbypassrls=true, so without switching role every query bypasses
    # all tenant_isolation / service_bypass policies. nexus_app is created
    # by migration 0010 with NOBYPASSRLS + least-privilege grants.
    #
    # Leave empty (None) to skip the role switch. Tests do this because the
    # test DB uses SQLAlchemy Base.metadata.create_all rather than real
    # migrations, so nexus_app isn't created there.
    #
    # Dev/staging/prod should set DB_RUNTIME_ROLE=nexus_app in their .env
    # AFTER running `alembic upgrade head` at least once.
    db_runtime_role: str | None = None

    @field_validator("db_runtime_role")
    @classmethod
    def _validate_db_runtime_role(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        # Guard against SQL injection — this value is interpolated into a
        # `SET LOCAL ROLE <name>` statement because asyncpg can't parameterise
        # DDL-like commands. Accept only PG identifier characters.
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(
                f"DB_RUNTIME_ROLE must be a PostgreSQL identifier "
                f"([a-zA-Z_][a-zA-Z0-9_]*), got: {v!r}"
            )
        return v

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth — ES256 JWKS verification (no shared secret)
    supabase_jwks_url: str = ""  # e.g. http://127.0.0.1:54321/auth/v1/.well-known/jwks.json

    # Supabase Admin API — used ONLY for auth user lifecycle (delete on deactivate).
    # NOT for data access — all data goes through asyncpg directly.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Auth provider selector — controls which concrete AuthProvider
    # implementation `get_auth_provider()` returns. Defaults to Supabase;
    # swap to "cognito"/"keycloak"/etc. when adding a new provider class.
    auth_provider: str = "supabase"

    # JWT issuer string the backend expects in dashboard tokens. Defaults to
    # `{supabase_url}/auth/v1` which matches Supabase Cloud, where the
    # backend's network-reachable URL and the issuer Supabase advertises in
    # tokens are the same string. In Supabase local under Docker they
    # diverge: the container reaches Supabase via `host.docker.internal`
    # but Supabase advertises itself as `127.0.0.1` (its own self-view), so
    # the derived issuer never matches the actual `iss` claim. Set this
    # explicitly in any environment where SUPABASE_URL doesn't match the
    # real `iss`. Empty string disables the issuer check entirely (not
    # recommended).
    supabase_jwt_issuer: str = ""

    # Candidate JWT (separate signing key — treat as DB credential).
    # REQUIRED in any non-test environment. No default — empty at import-time
    # is caught by the field_validator below. Signing algorithm is hardcoded
    # to HS256 in app/modules/auth/service.py; it is NOT a deployment setting.
    candidate_jwt_secret: str = ""

    # TTL for minted candidate session JWTs (hours). 72h default covers a
    # standard 3-day invite window. Consumed by
    # app/modules/auth/service.py::create_candidate_token().
    candidate_jwt_ttl_hours: int = 72

    @field_validator("candidate_jwt_secret")
    @classmethod
    def _candidate_secret_required(cls, v: str, info) -> str:
        # info.data contains already-validated fields, including `environment`.
        env = info.data.get("environment", "development")
        if not v and env != "test":
            raise ValueError(
                "CANDIDATE_JWT_SECRET is required (generate with: "
                "`openssl rand -hex 32`). This signs candidate session JWTs "
                "and must never be empty in dev/staging/prod. Set "
                "ENVIRONMENT=test to skip this check in the test suite."
            )
        return v

    # Notifications
    notifications_dry_run: bool = True  # True = log emails to stdout, False = send via Resend

    @field_validator("notifications_dry_run")
    @classmethod
    def _dry_run_forbidden_in_prod(cls, v: bool, info) -> bool:
        # DryRunProvider logs the full invite URL (which contains the signed
        # single-use candidate JWT) and the OTP code to stdout as first-class
        # structured-log fields. That's intentional for local development —
        # engineers grab the values from the terminal to test the candidate
        # flow without Resend credentials. It is NEVER safe outside
        # development, where logs typically flow into Sentry, log aggregators,
        # or shared observability infra and would leak session-bypass
        # credentials to anyone with log-reader access.
        env = info.data.get("environment", "development")
        if v and env in {"production", "staging"}:
            raise ValueError(
                f"NOTIFICATIONS_DRY_RUN=true is unsafe in environment={env!r}: "
                "the dry-run provider logs candidate JWT invite URLs and OTP "
                "codes to stdout. Set NOTIFICATIONS_DRY_RUN=false and configure "
                "RESEND_API_KEY for this environment."
            )
        return v

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # --- AI — OpenAI (Phase 2A) ---
    openai_api_key: str = ""

    # Model selection — env-driven, swappable without code changes.
    # Consumed by app/ai/config.py::AIConfig in Task 14. Default placeholders;
    # real values come from .env or deployment config.
    openai_extraction_model: str = "gpt-5.2"
    openai_extraction_effort: str = "medium"
    openai_reenrichment_model: str = "gpt-5.2"
    openai_reenrichment_effort: str = "medium"
    openai_question_bank_model: str = "gpt-5"
    openai_question_bank_effort: str = "medium"

    # OpenAI request tuning
    openai_request_timeout_seconds: float = 240.0
    openai_max_retries: int = 2  # instructor-level schema retries; actor-level retries are separate

    # STT — Deepgram
    deepgram_api_key: str = ""

    # TTS — Cartesia
    cartesia_api_key: str = ""

    # Notifications — Email (Resend at MVP)
    resend_api_key: str = ""
    email_from: str = "noreply@projectx.com"

    # Notifications — SMS (Twilio at MVP)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # AWS — S3 (candidate resumes at MVP; future Egress recordings at enterprise)
    aws_s3_bucket_candidate_resumes: str = ""
    aws_region: str = "us-east-1"
    resume_upload_url_ttl_seconds: int = 300

    # Observability
    sentry_dsn: str = ""

    # OpenTelemetry — vendor-neutral tracing.
    # Both exporters default to OFF. Set OTEL_DEV_CONSOLE_EXPORTER=true to dump
    # spans to stdout for local dev visibility. Set OTEL_EXPORTER_OTLP_ENDPOINT
    # to ship to a collector or backend (Sentry, Jaeger, Tempo, custom).
    # When both unset: spans are created and finished but discarded silently
    # (production-safe — no accidental data leak).
    otel_exporter_otlp_endpoint: str = ""
    otel_dev_console_exporter: bool = False
    otel_service_name: str = "nexus"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"]

    # --- Interview engine (in-process, Phase 3 merged) ---
    # The engine no longer runs as a separate Docker image with its own
    # config. These fields are read directly by app/modules/interview_engine.
    # `interview_engine_jwt_secret` is retained for now because Task 9 of
    # Phase 3 deletes its only consumer (mint_engine_dispatch_jwt). When that
    # task lands, this field can be removed.
    interview_engine_jwt_secret: str = ""

    engine_agent_name: str = "Dakota-1785"
    # State machine
    engine_max_probes_per_question: int = 3
    engine_time_warning_threshold: float = 0.8
    # Turn detection / endpointing (forwarded to AgentSession)
    engine_endpointing_min_delay: float = 0.3
    engine_endpointing_max_delay: float = 2.5
    # Silero VAD prewarm
    engine_silero_activation_threshold: float = 0.3
    engine_silero_min_speech_duration: float = 0.05
    engine_silero_min_silence_duration: float = 0.55
    # Observability
    engine_log_audio_events: bool = True
    engine_log_user_transcripts: bool = False

    # Realtime model selection — env-driven, mirrors the JD/question-bank
    # convention. Consumed by AIConfig (in app/ai/config.py) and the
    # plugin factories in app/ai/realtime.py.
    #
    # ``interview_reasoning_effort`` is forwarded to ``openai.LLM`` only
    # when non-empty (see ``app/ai/realtime.py::build_llm_plugin``). Per
    # OpenAI's API docs, ``reasoning_effort`` is **not supported for
    # non-reasoning chat models** — sending it to ``*-chat-latest`` returns
    # HTTP 400. Default is empty so the param is omitted, which is the
    # correct contract for the default chat model below.
    #
    # When switching to a reasoning model (e.g. ``gpt-5.1``, ``o3``,
    # ``o4-mini``, ``gpt-5-pro``), set ``INTERVIEW_REASONING_EFFORT`` to
    # one of the model's documented values (``none|minimal|low|medium|
    # high|xhigh`` — each model's allowed subset is in OpenAI's docs).
    # Lower effort = lower first-token latency; ``low`` is a good default
    # for the realtime conversational pipeline since the InterviewStateMachine
    # — not the LLM — drives probe selection / signal detection / mandatory
    # coverage. The LLM's job is to be a fluent conversationalist.
    interview_llm_model: str = "gpt-5.3-chat-latest"
    interview_reasoning_effort: str = ""

    # STT — Deepgram realtime
    interview_stt_model: str = "nova-3"
    interview_stt_language: str = "en"

    # TTS — Cartesia realtime
    interview_tts_model: str = "sonic-2"
    interview_tts_voice: str = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
    interview_tts_language: str = "en"

    # End-of-utterance confidence floor for the multilingual turn-detector
    # plugin. None (default) lets the plugin choose. Raising this above the
    # plugin default (~0.15 in current versions) makes the agent wait
    # longer before deciding the candidate has finished speaking — useful
    # in noisy environments where stray sound bursts can prematurely
    # trigger end-of-turn. Don't set blindly; tune from real session
    # latency data. Range: 0.0 – 1.0.
    interview_turn_detector_unlikely_threshold: float | None = None

    # Noise cancellation — ai_coustics. Default is QUAIL_VF_L (Voice Focus
    # Large, single-speaker isolation). Per LiveKit's published WER table,
    # QUAIL_VF_L gives the best STT accuracy for agent pipelines (11.8%
    # vs Krisp BVC's 23.5%). Other ai_coustics models: QUAIL_S (small,
    # lightweight), QUAIL_L (background-noise suppression, less aggressive
    # than VF_L), QUAIL_BV (broadband voice).
    #
    # ``interview_noise_cancellation_level`` (0.0–1.0) controls how
    # aggressively the model processes audio. None = plugin built-in
    # default. Lower = less aggressive (safer for soft-spoken candidates
    # and quiet environments where over-suppression can attenuate real
    # voice frames). LiveKit's docs use 0.8 in their published samples.
    # 0.7 is a reasonable balance for office environments with HVAC noise
    # without eating quieter speech.
    interview_noise_cancellation_model: str = "QUAIL_VF_L"
    interview_noise_cancellation_level: float | None = 0.7

    @field_validator("interview_engine_jwt_secret")
    @classmethod
    def _engine_secret_required(cls, v: str, info) -> str:
        env = info.data.get("environment", "development")
        if not v and env != "test":
            raise ValueError(
                "INTERVIEW_ENGINE_JWT_SECRET is required (generate with: "
                "`openssl rand -hex 32`). This signs the engine dispatch JWT "
                "embedded in LiveKit dispatch metadata and authenticates the "
                "interview-engine worker against /api/internal/sessions/*. "
                "Set ENVIRONMENT=test to skip this check in the test suite."
            )
        return v

    # Frontend base URL — used to build invite/confirmation links in emails.
    # Previously hardcoded with a `debug ? localhost : app.projectx.com`
    # ternary, which meant a staging deploy with DEBUG=false would mint
    # invite links that point at production. Now every environment must
    # set FRONTEND_BASE_URL explicitly.
    frontend_base_url: str = "http://localhost:3000"

    @field_validator("frontend_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()
