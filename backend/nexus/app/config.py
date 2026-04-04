from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Nexus"
    debug: bool = False
    environment: str = "development"  # development | staging | production

    # Database (asyncpg)
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth — ES256 JWKS verification (no shared secret)
    supabase_jwks_url: str = ""  # e.g. http://127.0.0.1:54321/auth/v1/.well-known/jwks.json

    # Supabase Admin API — used ONLY for auth user lifecycle (delete on deactivate).
    # NOT for data access — all data goes through asyncpg directly.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Candidate JWT (separate signing key — treat as DB credential)
    candidate_jwt_secret: str = "change-me-candidate-secret"
    candidate_jwt_algorithm: str = "HS256"

    # Notifications
    notifications_dry_run: bool = True  # True = log emails to stdout, False = send via Resend

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # AI — Anthropic
    anthropic_api_key: str = ""

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

    # Observability
    sentry_dsn: str = ""
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"]


settings = Settings()
