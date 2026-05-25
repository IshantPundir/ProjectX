from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # ATS integration — encrypts per-tenant credentials and OAuth tokens at rest.
    # First key in the list encrypts; all keys are tried for decrypt (MultiFernet).
    # Rotation = prepend a new key, backfill ciphertexts, drop the old key.
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    #
    # `NoDecode` opts this field out of pydantic-settings' default JSON
    # decoding of complex types from env vars. Without it an empty
    # `ATS_CREDENTIALS_ENCRYPTION_KEYS=` in .env would crash with a
    # JSONDecodeError before the required-field validator below ran, and
    # the documented `key1,key2,key3` env-var syntax would need to be
    # written as the JSON literal `["key1","key2","key3"]`. The
    # mode="before" validator below splits the raw env string on commas.
    ats_credentials_encryption_keys: Annotated[list[str], NoDecode] = []

    # Default backoff (seconds) when an adapter raises ATSRateLimitedError
    # without a Retry-After hint. Per-connection rate_limit_qps can override.
    ats_default_retry_after_seconds: int = 60

    # Minimum seconds between consecutive HTTP requests to a single ATS
    # vendor on a given connection. Per-connection
    # ``ats_connections.rate_limit_qps`` overrides this: when set,
    # pacing = 1 / rate_limit_qps.
    #
    # Ceipal team confirmed (2026-05-14) a hard ceiling of 60 calls/min
    # = 1.0 req/s. We pace at 1.1s (≈54 req/min) to leave a 10% safety
    # margin for clock skew, burst retries, and any side-channel calls
    # (token refresh, count probes) that share the request budget.
    ats_default_request_pacing_seconds: float = 1.1

    @field_validator("ats_credentials_encryption_keys", mode="before")
    @classmethod
    def _split_ats_encryption_keys(cls, v):
        # pydantic-settings JSON-decodes complex types from env vars by
        # default; that means an empty `ATS_CREDENTIALS_ENCRYPTION_KEYS=` in
        # .env crashes with a JSONDecodeError before the required-field
        # validator below ever runs, and `key1,key2` would need to be
        # quoted as `["key1","key2"]`. Coerce a comma-separated string into
        # a list here so the env-var contract documented in .env.example
        # (`ATS_CREDENTIALS_ENCRYPTION_KEYS=key1,key2,key3`) actually works
        # and so the friendly "required" message wins over JSONDecodeError.
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("ats_credentials_encryption_keys")
    @classmethod
    def _ats_encryption_keys_required(cls, v: list[str], info) -> list[str]:
        env = info.data.get("environment", "development")
        if not v and env != "test":
            raise ValueError(
                "ATS_CREDENTIALS_ENCRYPTION_KEYS is required "
                "(comma-separated; first key is active). Generate a new key with: "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`"
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
    # Fast/cheap model for the per-bank STT keyterm extraction LLM call (one
    # call per bank generation; result cached on stage_question_banks.extracted_keyterms).
    # See docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md.
    openai_question_bank_keyterm_model: str = "gpt-5.4-nano-2026-03-17"
    # Bank-gen prompts: spoken-question rewrite lives in prompts/v2 (engine-v2 M2).
    question_bank_prompt_version: str = "v2"

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
    # max_delay 3.0s (Phase 5, 2026-05-12, was 6.0): matches LiveKit's
    #   documented default. Earlier tuning (unlikely_threshold→0.5,
    #   min_duration→1.0s) has stablized EOU detection; long-pause
    #   tolerance built into the orchestrator coalescing logic
    #   (docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md).
    # Endpointing tuning (2026-05-17 conversational-continuation design).
    #
    # mode "dynamic" (Python-only): the framework computes an EMA over the
    #   session's pause statistics and adapts the effective delay within
    #   [min_delay, max_delay]. Fast talker → tighter; thinker → looser.
    #   Requires MultilingualModel or VAD-based turn detection.
    #
    # min_delay 0.8s (was 1.0): slight tightening for the snappy-end of the
    #   dynamic range.
    # max_delay 4.5s (was 3.0): headroom for thinking pauses. The canonical
    #   failure session (engine-events/2115a63a-…json) had a 3.32s pause
    #   that crossed the 3.0s ceiling and caused an orphan fragment turn.
    #   4.5s swallows that pause without firing EOU.
    engine_endpointing_mode: Literal["fixed", "dynamic"] = "dynamic"
    engine_endpointing_min_delay: float = 0.8
    engine_endpointing_max_delay: float = 4.5

    # --- Interview engine v2 (two-plane) — EOU / turn-taking knobs ---
    # Isolated from the v1 engine_endpointing_* / interview_turn_detector_*
    # knobs so retuning v2 on talk-tests never changes v1 behavior (master §3
    # "M3 must not break v1").
    #
    # unlikely_threshold = None => use the MultilingualModel's DOCUMENTED DEFAULT
    # (the docs say the detector "has no configuration"; English true-positive
    # rate 99.3% — it commits a complete answer fast and waits on an unfinished
    # one). Talk-test 2026-05-22 (R1): v1's Phase-5 override of 0.5 ("more patient
    # EOU") made the detector treat COMPLETE answers as unfinished — it waited the
    # full max_delay on every turn, which both lagged complete answers AND fired a
    # spurious hold-space cue on them. Reverting v2 to the model default restores
    # discrimination so the hold-space cue lands only on genuinely-unfinished turns.
    engine_v2_turn_detector_unlikely_threshold: float | None = None
    engine_v2_endpointing_mode: Literal["fixed", "dynamic"] = "dynamic"
    engine_v2_endpointing_min_delay: float = 0.8
    # max_delay 10.0 (was v1's 4.5): with the model-default detector restoring
    # discrimination, complete answers commit fast (~1-2s, well under the ceiling),
    # so a high ceiling only buys patience for genuine mid-answer think-pauses.
    # Talk-test 2026-05-22 honored real 7-8.5s think-pauses under this ceiling that
    # 4.5s was cutting off. (quality-before-latency; EOU is off the perceived-latency
    # critical path per CMI-3.)
    engine_v2_endpointing_max_delay: float = 10.0

    # Mid-pause patience cue: a warm "take your time" on a genuine mid-thought
    # pause (R1, the #1 UX risk for Indian candidates). RE-ENABLED in M5
    # (decision E) after M4 disabled the blind timer. Now GATED on
    # incompleteness (agent.py): fires only while the turn is still open and
    # the turn-detector is holding it as incomplete — never on a complete
    # answer's trailing pause.
    #
    # Gating mechanism (R3): the LiveKit turn-detector (MultilingualModel
    # 1.5.9) does NOT expose a mid-pause "incomplete/extending" signal or EOU
    # probability. We use the delay-above-commit-latency proxy: this delay is
    # set ABOVE the worst-case complete-answer commit latency observed on
    # talk-tests (~1-2s), so a complete answer always commits first
    # (on_user_turn_completed fires → state["responding"]=True mutes the
    # timer); only a detector-held-open incomplete pause survives to fire.
    # Honest v1 caveat: text-only detection makes "never on a complete answer"
    # best-effort; perfect needs the v2 audio-prosody model. Tune on Task 10
    # talk-test.
    engine_v2_hold_space_enabled: bool = True
    # Delay before the cue. Keep ABOVE the worst-case complete-answer commit
    # latency (talk-test) so a complete answer always commits first; only a
    # held-open incomplete pause reaches the cue.
    engine_v2_hold_space_delay_s: float = 3.0
    engine_v2_hold_space_message: str = "Take your time."

    # Unresponsive ladder: candidate not responding to a posed question.
    # ~7s -> gentle nudge; ~15s -> "still there?"; after N no-responses ->
    # close as candidate_unresponsive (doc 08 "resolved": ~6-8s / ~15s / 2).
    engine_v2_unresponsive_prompt_1_s: float = 7.0
    engine_v2_unresponsive_prompt_2_s: float = 15.0
    engine_v2_unresponsive_max_no_responses: int = 2
    engine_v2_unresponsive_message_1: str = "Whenever you're ready."
    engine_v2_unresponsive_message_2: str = "Are you still there?"

    # Backchannel gate: an utterance with fewer than this many words, OR made
    # entirely of backchannel tokens, is treated as engagement (AI keeps the
    # floor), not a turn grab. Mirrors the LiveKit interruption min_words=2.
    engine_v2_backchannel_min_words: int = 2

    # M4 directive-injection talk-test scenario. "" = the default canned flow
    # (INTRO -> ASK -> ACK_ADVANCE per turn -> CLOSE). "supersession" stages a
    # speculative PROBE then a superseding ACK_ADVANCE for the CMI-4 live test.
    engine_v2_mouth_scenario: str = ""

    # M5 ack-mask (D3): a short, content-free, persona-voiced acknowledgment played the instant
    # the candidate finishes so the brain's ~3-7s reasoning runs MASKED (never a silent wait). The
    # mouth pre-renders Arjun-voiced variants at session start; this canned list is the seed +
    # fallback. Content-free by design — it commits to nothing, so it is never wrong ahead of any
    # brain move (advance, probe, even a redirect). No questions.
    # Canned content-free acks: the triage fallback filler + the M5 reflex pre-render seed. Keep
    # them neutral acknowledgments of the CANDIDATE — never agent-stalling phrases like
    # "Let me think on that." (that made the agent sound confused in fe3a5434).
    engine_v2_ack_messages: list[str] = [
        "Mm, okay.",
        "Right.",
        "Got it.",
        "Mm-hmm.",
    ]

    # Conversational continuation — pre-Speaker cancellation watcher.
    # See docs/superpowers/specs/2026-05-17-conversational-continuation-design.md
    #
    # The watcher subscribes to ``user_input_transcribed`` during an
    # in-flight turn. When STT delivers a final transcript with at least
    # ``engine_continuation_min_word_count`` words AND the agent has not
    # yet started speaking (commit_event not set), the in-flight Judge
    # task is cancelled, the State Engine is rolled back to the pre-turn
    # snapshot, and the candidate's text is buffered for stitching into
    # the next turn.
    #
    # Trigger switched from VAD-based (sustained user_state speaking) to
    # STT-based (is_final=True + word-count gate) on 2026-05-17 after
    # session 7970e91c showed VAD-triggered aborts on non-speech sounds
    # and brief filler interjections.
    #
    # ``engine_continuation_enabled`` is the kill switch. Set to False to
    # disable the entire mechanism (orchestrator behaves identically to
    # the pre-2026-05-17 code path) — useful for emergency rollback
    # without a code deploy.
    engine_continuation_enabled: bool = True
    engine_continuation_min_word_count: int = 2
    engine_continuation_consecutive_abort_cap: int = 3
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

    # Stuck-session reaper. Sweeps state='active' sessions whose
    # state_changed_at is older than reaper_stuck_threshold_seconds and
    # transitions them to state='error' with error_code='engine_unresponsive'.
    # Disabled by setting reaper_enabled=false (tests do this).
    reaper_enabled: bool = True
    reaper_interval_seconds: int = 300   # how often the scheduler ticks
    reaper_stuck_threshold_seconds: int = 900  # 15 min — typical AI screen is 30 min

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
    # Default v2 (shipped 2026-05-17). Rollback: set to v1 and restart
    # the engine container — v1 files remain in repo for one sprint.
    engine_judge_prompt_version: str = "v2"
    engine_speaker_prompt_version: str = "v2"

    # --- Interview engine v2 (two-plane) — selection + model map ---
    # Selection: per-job override (job_postings.interview_engine_version) falls
    # back to this global default. 'v1' keeps every session on the legacy engine.
    interview_engine_default_version: Literal["v1", "v2"] = "v1"

    # Brain (Control Plane) — FAST model + reasoning-FIRST FIELD for coherence, NO
    # extended-thinking penalty. gpt-5 (a reasoning model) paid ~8-10s thinking
    # latency even at low effort and timed out the 6s budget EVERY turn (live
    # talk-test 2026-05-24), forcing the deterministic fallback all interview =>
    # zero signal coverage. Switched to the same fast model the mouth uses
    # (gpt-5.4-mini), PROVEN to work with instructor TOOLS_STRICT + NO
    # reasoning_effort (the mouth reflex pre-render returns 200 with it).
    # `engine_brain_effort=""` => `_call_brain` gates `if
    # ai_config.engine_brain_effort:` so NO reasoning_effort is sent => fast, and it
    # sidesteps the reasoning-model + tools incompatibility entirely. Coherence
    # comes from the reasoning-first field in BrainDecision (the model writes
    # `reasoning` before the decision fields), not from extended thinking.
    engine_brain_model: str = "gpt-5.4-mini-2026-03-17"
    engine_brain_effort: str = ""

    # Mouth (Conversation Plane) — GPT-5.4 Mini, latency-first, no reasoning effort.
    engine_mouth_model: str = "gpt-5.4-mini-2026-03-17"
    engine_mouth_effort: str = ""

    # New v3 engine prompt family (rewritten from scratch — brain + per-act mouth).
    engine_brain_prompt_version: str = "v3"
    engine_mouth_prompt_version: str = "v3"

    # Brain total wall-clock budget (ms) before the deterministic fallback directive
    # kicks in. The brain runs async/parallel, MASKED by the mouth's acknowledgment
    # (M5 D3, off the CMI-3 perceived-latency gate) — but it's still bounded so a stuck
    # call can't strand the turn behind the ack. GPT-5.4 low-effort is ~3-7s; the old
    # 6000ms budget timed out a LEGITIMATE ~6s decision in session 046f21e3 (fallback_advance
    # then skipped a candidate clarification), so it is 8000ms — covers the real 3-7s range
    # with margin; the fallback still backstops a truly-stuck (>8s) call (masked by the filler).
    engine_brain_total_budget_ms: int = 8000

    # Triage tier (the fast classify-and-speak first call; design 2026-05-24). MINI model:
    # measured comparably fast to nano warm (~1.1-1.5s) but classifies materially better
    # (nano mislabeled clarifications as job_question/repeat in fe3a5434, breaking the convo);
    # reasoning-FIRST field (no reasoning_effort). Budget gates the immediate voice but must cover
    # the cold-start/variance tail (nano hit 2.5s -> fallback to a canned ack in fe3a5434), so it
    # is 3500ms. On timeout/error -> canned ack + route=to_brain (never skip the brain).
    engine_triage_model: str = "gpt-5.4-mini-2026-03-17"
    engine_triage_effort: str = ""
    engine_triage_prompt_version: str = "v3"
    engine_triage_total_budget_ms: int = 3500  # mini ~1.1-1.5s warm; covers cold-start/variance
    # §9 reconciliation: after any reflex/triage cue, suppress the OTHER cue path for this long so
    # the acoustic hold-space pacer and triage's "still-pending" continuation cue never double-fire.
    engine_v2_cue_cooldown_s: float = 4.0
    # Dev-only (design §7): on a HANDLED turn, let the (otherwise-cancelled) brain finish ONLY
    # to log a triage↔brain disagreement — never to change what is spoken. OFF in prod.
    engine_v2_triage_brain_disagreement_log: bool = False
    # After this many consecutive "still pending" holds on one answer, force the brain to evaluate.
    engine_triage_hold_cap: int = 2

    # Explicit OpenAI prompt_cache_key per surface for stable cache routing
    # (design §11: stable-prefix -> dynamic-suffix). Bump the suffix on a
    # prompt change to avoid cross-version cache pollution.
    engine_brain_prompt_cache_key: str = "brain:v1"
    engine_mouth_prompt_cache_key: str = "mouth:v1"

    # v2 mouth persona display name. The design persona is "Arjun"; kept a
    # dedicated v2 knob so v1's shared engine_agent_name ("Sam") is untouched.
    # Rendered once per session into the (otherwise byte-stable) persona preamble.
    # Blank -> the mouth falls back to engine_agent_name.
    engine_mouth_persona_name: str = "Arjun"

    # Canned terminal message override. None = use PersonaSpec.fallback_session_ended
    # (Arjun-voiced default with {comma_name} that omits the comma when no name is
    # present). Set to a literal string to override for a specific tenant / env.
    engine_session_ended_message: str | None = None

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
    # Default ``deepgram`` (nova-3, en-IN) with per-session keyterm
    # prompting — keyterms are LLM-extracted at bank-generation time and
    # cached on stage_question_banks.extracted_keyterms (see spec
    # docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md).
    # Switch to Sarvam (alternate) by setting INTERVIEW_STT_PROVIDER=sarvam,
    # INTERVIEW_STT_MODEL=saaras:v3, INTERVIEW_STT_LANGUAGE=en-IN — Sarvam
    # is Indian-language tuned (en-IN, hi-IN, code-mix) but its STT
    # mistranscribes tech vocabulary; only keep it for code-mix candidates.
    # The ``model`` / ``language`` fields are interpreted by the chosen
    # provider's plugin factory (see app/ai/realtime.py); incompatible
    # values are caught at plugin construction.
    interview_stt_provider: Literal["sarvam", "deepgram"] = "deepgram"
    interview_stt_model: str = "nova-3"
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
    # Aligned to PersonaSpec.tts_*_recommended for Arjun (P4.3).
    interview_tts_pace: float = 0.95
    interview_tts_temperature: float = 0.6

    # End-of-utterance confidence floor for the multilingual turn-detector
    # plugin. None lets the plugin's per-language tuned defaults (~0.3-0.5)
    # apply. Phase 2 P2.2 (2026-05-08) dropped the explicit 0.15 override:
    # 0.15 was *more eager* than the language-tuned defaults, which made
    # the agent commit turn-end on lower-confidence EOU signals — the
    # opposite of what we want for candidates who pause mid-thought.
    # Letting the plugin choose is both more patient AND more accurate.
    # Set explicitly only when you have a tuning reason. Range: 0.0 – 1.0.
    # Phase 5 (2026-05-12): bumped from None -> 0.5 for the Sarvam +
    # MultilingualModel path. The product's first candidates are
    # Indian-English speakers who tend to pause mid-thought; a more
    # conservative EOU floor (only fire end-of-turn when the model is
    # confidently sure) reduces premature turn closures. Tune empirically
    # from real session audio.tuning_summary data.
    interview_turn_detector_unlikely_threshold: float | None = 0.5

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

    # --- Reporting — offline report scorer (Phase 3D+ post-session) ---
    # The report scorer is an async LLM judge that runs after a session
    # completes and produces the per-candidate evaluation report.
    # All knobs are env-driven following the same convention as the
    # extraction / question-bank / engine models above.
    #
    # ``openai_report_scorer_model`` — strong reasoning model to evaluate
    # full transcripts and emit structured rubric grades. gpt-5.4 is the
    # default (full reasoning model, supports reasoning_effort). Switch to a
    # cheaper model by setting OPENAI_REPORT_SCORER_MODEL=gpt-5.4-mini.
    openai_report_scorer_model: str = "gpt-5.4"

    # ``openai_report_scorer_effort`` — reasoning_effort forwarded to the
    # OpenAI API only when non-empty (effort-gating contract: callers gate on
    # `if ai_config.report_scorer_effort:` before forwarding the param).
    # "medium" is a good default for transcript-length reasoning; set "" to
    # disable reasoning_effort entirely (required for non-reasoning models).
    openai_report_scorer_effort: str = "medium"

    # ``openai_report_scorer_verbosity`` — controls how verbose the judge's
    # chain-of-thought / explanations are in structured output. "low" keeps
    # the response compact and reduces token cost; increase to "high" for
    # debugging or audit-trail depth.
    openai_report_scorer_verbosity: str = "low"

    # ``openai_report_scorer_n_samples`` — number of independent LLM samples
    # to draw per report and then aggregate (majority-vote / mean). Higher
    # values improve consistency at the cost of token spend. 3 is a sensible
    # default for production; set to 1 for fast dev/test cycles.
    openai_report_scorer_n_samples: int = 3

    # ``report_scorer_prompt_version`` — controls which versioned prompt
    # directory PromptLoader reads from (prompts/v{N}/). v3 is the current
    # active prompt family (engine-v2 prompts live there).
    report_scorer_prompt_version: str = "v3"

    # ``report_scorer_prompt_cache_key_prefix`` — PREFIX only (not a verbatim
    # key). The report scorer concatenates this with dynamic parts to form keys
    # like ``judge:{prompt_version}:{question_id}:{model}``. This differs from
    # ``engine_brain_prompt_cache_key``, which IS used verbatim. Bump this
    # prefix on a prompt-family change to avoid cross-version cache pollution.
    report_scorer_prompt_cache_key_prefix: str = "judge"


settings = Settings()
