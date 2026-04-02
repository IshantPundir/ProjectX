from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Nexus"
    debug: bool = False
    environment: str = "development"  # development | staging | production

    # Database (asyncpg)
    database_url: str = "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth — provider-agnostic JWT verification
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = ""

    # Candidate JWT (separate signing key — treat as DB credential)
    candidate_jwt_secret: str = "change-me-candidate-secret"

    # Supabase Auth (used only as JWT issuer for dashboard users at MVP)
    supabase_url: str = ""
    supabase_jwt_secret: str = ""

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
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
