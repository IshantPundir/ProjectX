from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


NoiseCancellationMode = Literal[
    "ai_coustics_quail",
    "ai_coustics_quail_vf",
]


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
    # `livekit_url` — used by the backend for server-to-server calls (token
    # mint, agent dispatch, room delete). In Docker dev this is the
    # host-internal hostname (e.g. ws://host.docker.internal:7880).
    # `livekit_public_url` — what the candidate browser receives in /start
    # and /rejoin responses. In Docker dev this is what the browser on the
    # host can resolve (e.g. ws://localhost:7880). In Cloud or single-host
    # deploys, set both to the same value.
    livekit_url: str = ""
    livekit_public_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # How long an empty LiveKit room lingers before LiveKit deletes it.
    # LiveKit's default is 5 minutes. We pre-create the room with a
    # shorter window so the dashboard "Active" state and the agent
    # worker process die quickly after the agent + candidate both
    # disconnect — but not so quickly that LiveKit Cloud's Agent
    # Insights ingest pipeline loses the trailing batch flush.
    # 30 s is a safe middle ground; tunable per deployment.
    livekit_room_empty_timeout_seconds: int = 30

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

    # STT / TTS — Sarvam.ai (Indian-language realtime, default for both STT and TTS)
    sarvam_api_key: str = ""

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
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
    ]

    # --- Interview engine (in-process, Phase 3 merged) ---
    # The engine no longer runs as a separate Docker image with its own
    # config. These fields are read directly by app/modules/interview_engine.

    # Default agent name. The pre-overhaul "Dakota-1785" reads as a
    # robotic identifier; "Sam" is gender-neutral, short (one syllable for
    # TTS efficiency), and unambiguous. Tenants can override per-session
    # via tenant_settings.engine_agent_name.
    engine_agent_name: str = "Sam"
    # Turn detection / endpointing (forwarded to AgentSession).
    #
    # Tuned for technical-interview UX where candidates think
    # mid-sentence. The framework's MultilingualModel turn detector
    # decides when within [min_delay, max_delay] to fire EOU based on
    # context (does the user's text look like they're done?). Minimum
    # is the floor — even with a high "user is done" probability, the
    # session waits at least min_delay after speech stops.
    #
    # min_delay 1.0s (was 0.3): extra patience for thinking pauses.
    #   Pre-fix the candidate's "Hey. So I would like map business
    #   systems to..." was cut after a 0.3s pause at "...to" because
    #   STT fired is_final=True and the orchestrator advanced. The
    #   real fix is consuming `on_user_turn_completed` (turn-detector
    #   EOU, not STT finals) — but a longer min_delay also gives the
    #   turn detector more signal before deciding.
    # max_delay 6.0s (Phase 2 P2.2, 2026-05-08, was 2.5): session
    #   09e8fc33 showed candidate thinking pauses up to 22s and EOU
    #   delays p95 of 5.5s on non-trivial questions; the 2.5s cap was
    #   firing turn-end mid-thought. 6.0s gives the turn detector room
    #   to wait out a real pause; the snappiness loss on simple-answer
    #   turns is acceptable vs cutting candidates off mid-sentence.
    engine_endpointing_min_delay: float = 1.0
    engine_endpointing_max_delay: float = 6.0
    # Phase 3D — audio pipeline tuning (LK Cloud locked, 2026-05-06)
    # Architecture is locked to LK Cloud + ai-coustics exclusively.
    # "off" and "krisp_nc" are no longer valid values.
    # Set ai_coustics_quail (default, background noise suppression) or
    # ai_coustics_quail_vf (voice isolation — kills other voices in the room).
    interview_noise_cancellation: NoiseCancellationMode = "ai_coustics_quail"
    # Passed as `enhancement_level` to the ai_coustics plugin (0.0 = off, 1.0 = max).
    interview_nc_enhancement_level: float = 0.5

    # Observability
    engine_log_audio_events: bool = True
    engine_log_user_transcripts: bool = False

    # Phase 2 (engine redesign — controller cutover) — idle-nudge timing.
    # 30/30/30 chosen to balance against thinking pauses on hard technical
    # questions while still detecting a candidate who walked away within
    # ~90s. Tunable per-deploy without redeploy.
    engine_idle_first_nudge_seconds: float = 30.0
    engine_idle_second_nudge_seconds: float = 30.0
    engine_idle_give_up_seconds: float = 30.0

    # Phase 2 — task watchdog overhead. Padding on `estimated_minutes * 60`
    # so a clean task on the wire (one that fires its terminal tool right
    # at the budget boundary) doesn't trip the timer mid-tool-call.
    engine_task_budget_overhead_seconds: float = 5.0

    # Phase 2 — closing TTS drain timeout. Bounds how long the controller
    # waits for the closing line to play before forcing shutdown. Avoids
    # deadlocking teardown on a stuck TTS pipeline.
    engine_closing_drain_timeout_seconds: float = 8.0

    # Phase 3D (structured agent) — judge + speaker model selection and tuning.
    # Judge: async LLM that decides next action each turn.
    # Speaker: streaming Responses API LLM that generates candidate-facing text.
    engine_judge_model: str = "gpt-5.4-mini-2026-03-17"
    engine_speaker_model: str = "gpt-5.4-nano-2026-03-17"
    # Total wall-clock budget (ms) the judge is allowed before fallback kicks in.
    engine_judge_total_budget_ms: int = 10000
    # Wait between judge retry attempts (ms).
    engine_judge_retry_wait_ms: int = 250
    # Max tokens the speaker may emit in a single turn.
    engine_speaker_max_output_tokens: int = 200
    # Checkpoint cadence — persist state every N turns or every M seconds,
    # whichever fires first.
    engine_checkpoint_turns: int = 10
    engine_checkpoint_seconds: int = 30
    # Maximum number of candidate utterance claims to keep in the pool.
    engine_claims_pool_max: int = 50
    # Prompt version tags — controls which versioned prompt file is loaded.
    engine_judge_prompt_version: str = "v1"
    engine_speaker_prompt_version: str = "v1"

    # Continuation coalescing (Lever 3 for noisy-office multi-segment answers,
    # 2026-05-11). When a new turn arrives within the window of the prior
    # turn's TURN_COMPLETED AND the prior turn's Speaker did not deliver its
    # body, the new turn's candidate text is prepended with the prior turn's
    # text before the Judge call. State mutations from the prior turn are
    # NOT reverted — only the user-utterance text is merged.
    engine_coalesce_enabled: bool = True
    # Generous safety net; the primary gate is whether the prior turn's
    # Speaker delivered its body. Range [1, 30000] enforced at startup.
    engine_coalesce_window_ms: int = 5000

    @field_validator("engine_coalesce_window_ms")
    @classmethod
    def _coalesce_window_range(cls, v: int) -> int:
        if not 1 <= v <= 30000:
            raise ValueError(
                f"engine_coalesce_window_ms must be in [1, 30000]; got {v}"
            )
        return v

    # Canned terminal message played to the candidate after the session
    # lifecycle has entered 'closing' or 'closed' (e.g. polite_close was
    # already delivered but the candidate keeps talking). The
    # ``{candidate_name}`` placeholder interpolates to the candidate's
    # first name; falls back gracefully if the name is empty.
    engine_session_ended_message: str = (
        "Thanks for your time, {candidate_name}. This session has ended; "
        "the recruitment team will be in contact with you."
    )

    # Phase 1 (engine redesign) — event log sink config. The engine writes a
    # per-session JSON envelope at session close; the sink chosen here decides
    # where it lands. Production runs `metadata` redaction (no PII content);
    # `full` is consent-gated audit replay only and must never be the default.
    # `none` disables the writer entirely (smoke tests, ephemeral envs).
    engine_event_log_sink: Literal["local", "s3", "none"] = "local"
    engine_event_log_dir: str = "/tmp/engine-events"
    engine_event_log_redaction: Literal["metadata", "full"] = "metadata"
    aws_s3_bucket_engine_events: str = ""

    @field_validator("aws_s3_bucket_engine_events")
    @classmethod
    def _s3_bucket_required_when_sink_is_s3(cls, v: str, info) -> str:
        sink = info.data.get("engine_event_log_sink", "local")
        if sink == "s3" and not v:
            raise ValueError(
                "AWS_S3_BUCKET_ENGINE_EVENTS is required when "
                "ENGINE_EVENT_LOG_SINK=s3. Provide an S3 bucket name."
            )
        return v

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

    # ───────── STT (realtime) — provider-switchable ─────────
    # Default ``sarvam`` (saaras:v3, Indian-language tuned). Set
    # INTERVIEW_STT_PROVIDER=deepgram to roll back to the legacy nova-3 path.
    # The ``model`` / ``language`` fields are interpreted by the chosen provider's
    # plugin factory (see app/ai/realtime.py); incompatible values are caught at
    # plugin construction.
    interview_stt_provider: Literal["sarvam", "deepgram"] = "sarvam"
    interview_stt_model: str = "saaras:v3"
    interview_stt_language: str = "en-IN"
    # Sarvam-only knob; ignored when interview_stt_provider="deepgram".
    # Allowed values for ``saaras:v3``: transcribe, translate, verbatim, translit, codemix.
    # Default ``transcribe`` matches the plugin default.
    interview_stt_mode: str = "transcribe"

    # ───────── TTS (realtime) — provider-switchable ─────────
    # Default ``sarvam`` (bulbul:v3, speaker shubh, en-IN). Switch to
    # ``openai`` (gpt-4o-mini-tts) by setting INTERVIEW_TTS_PROVIDER=openai
    # AND INTERVIEW_TTS_MODEL=gpt-4o-mini-tts AND INTERVIEW_TTS_VOICE=ash.
    # Switch to ``cartesia`` (sonic-2) by setting INTERVIEW_TTS_PROVIDER=cartesia
    # AND INTERVIEW_TTS_MODEL=sonic-2 AND INTERVIEW_TTS_VOICE=<cartesia-voice-uuid>.
    # The model / voice / language fields below are interpreted by the chosen
    # provider's plugin factory (build_tts_plugin in app/ai/realtime.py);
    # incompatible values are caught at plugin construction, not at config-load time.
    interview_tts_provider: Literal["sarvam", "openai", "cartesia"] = "sarvam"
    interview_tts_model: str = "bulbul:v3"
    # Sarvam uses speaker names (e.g. shubh, anushka). OpenAI voice presets:
    # alloy / ash / ballad / coral / echo / fable / nova / onyx / sage /
    # shimmer. Cartesia uses voice UUIDs.
    interview_tts_voice: str = "shubh"
    interview_tts_language: str = "en-IN"
    # Sarvam-only TTS knobs; ignored when interview_tts_provider in {openai, cartesia}.
    # pace: 0.5–2.0; temperature: 0.01–1.0 (only used by bulbul:v3 / bulbul:v3-beta).
    interview_tts_pace: float = 1.0
    interview_tts_temperature: float = 0.6

    # ───────── TTS prewarm concurrency (provider-agnostic) ─────────
    # Caps in-flight TTS synthesis calls during the per-worker-process
    # opener-cache build (76 calls fired by build_opener_cache) and the
    # per-session intro line (synth_one in agent.py). A single
    # asyncio.Semaphore sized to this value is shared process-wide across
    # all concurrent sessions, so a fleet of sessions starting on the
    # same worker cannot collectively exceed the cap.
    #
    # Why: Sarvam's free / starter tier enforces a tight per-second rate
    # limit (HTTP 429 from sarvam.ai/text-to-speech). Firing the cache
    # build's 76-call gather() in one wave exceeds it deterministically;
    # the framework's own 3× retry-on-429 just re-fires the same wave
    # 2s later and fails again, leaving variants with audio_frames=None
    # and forcing fallback to live per-turn TTS that ALSO trips the
    # limit. Bounded concurrency turns the burst into ~ceil(76 / N)
    # serial waves of N calls, which fits inside the rate-limit window.
    #
    # Default 4 is conservative; raise on production tiers with higher
    # limits to reduce first-session warmup latency. Range [1, 16] is
    # enforced — below 1 stalls forever, above 16 has no realistic
    # provider where it pays off.
    interview_tts_prewarm_concurrency: int = 4

    @field_validator("interview_tts_prewarm_concurrency")
    @classmethod
    def _prewarm_concurrency_range(cls, v: int) -> int:
        if not 1 <= v <= 16:
            raise ValueError(
                f"interview_tts_prewarm_concurrency must be in [1, 16]; got {v}"
            )
        return v

    # End-of-utterance confidence floor for the multilingual turn-detector
    # plugin. None lets the plugin's per-language tuned defaults (~0.3-0.5)
    # apply. Phase 2 P2.2 (2026-05-08) dropped the explicit 0.15 override:
    # 0.15 was *more eager* than the language-tuned defaults, which made
    # the agent commit turn-end on lower-confidence EOU signals — the
    # opposite of what we want for candidates who pause mid-thought.
    # Letting the plugin choose is both more patient AND more accurate.
    # Set explicitly only when you have a tuning reason. Range: 0.0 – 1.0.
    interview_turn_detector_unlikely_threshold: float | None = None

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

    # Candidate session base URL — used to build interview invite links in
    # scheduler emails. Kept SEPARATE from frontend_base_url so the two
    # surfaces (recruiter dashboard vs. candidate session app) can be
    # deployed at different origins. Every environment must set
    # CANDIDATE_SESSION_BASE_URL explicitly. Defaulting both to the same
    # host masked the split during Phase 1; we are not repeating that mistake.
    candidate_session_base_url: str = "http://localhost:3002"

    @field_validator("candidate_session_base_url")
    @classmethod
    def _strip_candidate_session_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()
