# Nexus — ProjectX Backend

FastAPI backend for the ProjectX AI video interview platform. Modular monolith architecture — single deployable container with clean internal module boundaries.

## Tech Stack

- **Python 3.12** / **FastAPI** (async throughout)
- **PostgreSQL 16** via SQLAlchemy async + asyncpg
- **Redis 7** (session state, task queue broker)
- **Dramatiq** (async task queue)
- **Alembic** (database migrations)
- **Pydantic v2** (request/response schemas, settings)
- **structlog** + **OpenTelemetry** + **Sentry** (observability)

## Prerequisites

- Docker & Docker Compose

## Quick Start

```bash
# Copy environment config
cp .env.example .env

# Start all services (FastAPI + Postgres + Redis)
docker compose up --build

# Verify
curl http://localhost:8000/health
# → {"status": "ok"}
```

The API server runs at **http://localhost:8000** with hot-reload enabled via volume mount.

## Services

| Service  | Port | Description                      |
|----------|------|----------------------------------|
| nexus    | 8000 | FastAPI application server       |
| postgres | 5432 | PostgreSQL 16 (user: `projectx`) |
| redis    | 6379 | Redis 7 (session state + tasks)  |

## Project Structure

```
backend/nexus/
├── app/
│   ├── main.py              # App factory, middleware, router registration
│   ├── config.py            # Settings via pydantic-settings (env vars)
│   ├── database.py          # Async engine, tenant-aware session
│   ├── middleware/
│   │   ├── auth.py          # JWT verification, RBAC enforcement
│   │   └── tenant.py        # Tenant context for structured logging
│   └── modules/
│       ├── auth/            # Provider-agnostic JWT, roles, token schemas
│       ├── ats/             # ATS adapter protocol, polling, sync
│       ├── jd/              # Job description parsing, interview config
│       ├── question_bank/   # AI question generation, versioning
│       ├── scheduler/       # Session provisioning, invite dispatch, OTP
│       ├── session/         # LiveKit orchestration, session state machine
│       ├── analysis/        # Real-time scoring, signal detection
│       ├── reporting/       # Report compilation, score aggregation
│       └── notifications/   # Provider-agnostic email/SMS dispatch
├── migrations/              # Alembic (async SQLAlchemy)
├── tests/
├── Dockerfile               # Multi-stage build, non-root user
├── docker-compose.yml       # Full local stack
└── pyproject.toml           # Dependencies & tool config
```

## Common Commands

```bash
# Run migrations
docker compose run nexus alembic upgrade head

# Generate a new migration
docker compose run nexus alembic revision --autogenerate -m "description"

# Run tests
docker compose run nexus pytest

# Run tests with coverage
docker compose run nexus pytest --cov=app --cov-report=term-missing

# Lint
docker compose run nexus ruff check .

# Type check
docker compose run nexus mypy app/
```

## Environment Variables

Copy `.env.example` to `.env` for local development. Key variables:

| Variable              | Description                          |
|-----------------------|--------------------------------------|
| `DATABASE_URL`        | PostgreSQL async connection string   |
| `REDIS_URL`           | Redis connection string              |
| `JWT_SECRET`          | Dashboard user JWT signing key       |
| `CANDIDATE_JWT_SECRET`| Candidate session JWT signing key    |
| `SUPABASE_JWT_SECRET` | Supabase Auth JWT verification key   |
| `ANTHROPIC_API_KEY`   | Claude API for AI pipelines          |
| `DEEPGRAM_API_KEY`    | Speech-to-text                       |
| `LIVEKIT_*`           | LiveKit server URL, API key & secret |
| `RESEND_API_KEY`      | Email dispatch (MVP provider)        |
| `TWILIO_*`            | SMS/OTP dispatch (MVP provider)      |
| `SENTRY_DSN`          | Error tracking                       |
| `LANGFUSE_*`          | LLM observability (self-hosted only) |

See `.env.example` for the full list.

## Architecture Notes

- **Tenant isolation** is enforced at the PostgreSQL RLS layer via `SET LOCAL app.current_tenant` per transaction.
- **Auth is provider-agnostic** — JWT verification goes through `app/modules/auth/`, never the Supabase SDK directly. This makes a future Cognito swap a config change.
- **Candidates are not Supabase Auth users.** They access sessions via signed single-use JWTs verified entirely by Nexus.
- **All data access goes through FastAPI.** Supabase PostgREST is disabled.
- **Notifications** use a provider-agnostic interface — business logic never imports Resend/Twilio directly.

## Deployment

The same Docker image is used across all environments:

| Target     | Platform                |
|------------|-------------------------|
| MVP        | Railway (auto-deploy)   |
| Enterprise | AWS ECS Fargate         |
