# Nexus — ProjectX Backend

FastAPI backend for the ProjectX AI video interview platform. Modular monolith architecture — single deployable container with clean internal module boundaries.

## Tech Stack

- **Python 3.13** / **FastAPI** (async throughout)
- **PostgreSQL** (Supabase-hosted; local dev via `supabase start`) over SQLAlchemy async + asyncpg — NOT PostgREST
- **Redis** (Dramatiq broker; ephemeral)
- **Dramatiq** (async task queue — default + `report_scoring` / `ats_poll` / `reel` / `vision` queues)
- **Alembic** (database migrations)
- **Pydantic v2** (request/response schemas, settings)
- **OpenAI** for all LLM work (via the `app/ai/` provider-agnostic layer)
- **LiveKit** (real-time interview) + **Deepgram** STT + **Sarvam** TTS + **Silero** VAD (no server-side NC; browser does light NS)
- **structlog** + **OpenTelemetry** (LLM/distributed traces) + **Sentry** (errors). No Langfuse (removed 2026-05-01).

## Prerequisites

- Docker & Docker Compose

## Quick Start

```bash
# Copy environment config
cp .env.example .env

# Start the stack (FastAPI + workers + Redis; Postgres is Supabase-hosted)
docker compose up --build

# Verify
curl http://localhost:8000/health
# → {"status": "ok"}
```

The API server runs at **http://localhost:8000** with hot-reload enabled via volume mount.

## Services

| Service               | Port | Description                                              |
|-----------------------|------|---------------------------------------------------------|
| nexus                 | 8000 | FastAPI application server                               |
| nexus-worker          | —    | Dramatiq worker (default + report_scoring + ats_poll)   |
| nexus-engine          | —    | Live interview engine worker (`python -m app.modules.interview_engine`) |
| nexus-vision-worker   | —    | Dedicated worker for `reel` + `vision` queues (ffmpeg/ONNX, GPU-first) |
| redis                 | 6379 | Dramatiq broker                                         |

> Postgres is **not** a compose service — Nexus connects to a Supabase-hosted
> database via `DATABASE_URL` (local dev: `supabase start`, Postgres on 54322).

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
│   ├── storage/            # Provider-agnostic object storage (AWS S3 resumes + Cloudflare R2 recordings/reels)
│   └── modules/
│       ├── auth/            # Provider-agnostic JWT, roles, RBAC, /me
│       ├── admin/          # ProjectX-internal tenant provisioning
│       ├── settings/       # Team management (invite/resend/revoke)
│       ├── org_units/      # Org-unit tree, roles, company profile
│       ├── jd/             # JD pipeline (extract → enrich → signals)
│       ├── pipelines/      # Pipeline templates + per-job stages
│       ├── question_bank/  # AI question generation, versioning
│       ├── candidates/     # Candidate CRUD, resume (S3), kanban, PII redaction
│       ├── scheduler/      # Invite dispatch + supersession chain
│       ├── session/        # LiveKit orchestration, single-use token, OTP, consent, proctoring events
│       ├── interview_runtime/ # In-process wire contract the engine calls
│       ├── interview_engine/  # The three-tier live interview engine (triage ∥ brain → mouth)
│       ├── reporting/      # Post-session report (3-layer hybrid scorer + narrative)
│       ├── reel/           # Candidate Reel highlight video (Director EDL + ffmpeg render)
│       ├── vision/         # Server-plane gaze proctoring (POC, ONNX)
│       ├── tenant_settings/ # Per-tenant engine + proctoring config
│       ├── ats/            # Ceipal sync (manual-trigger)
│       ├── analysis/       # [Dead stub] absorbed by interview_engine
│       └── notifications/  # Provider-agnostic email/SMS dispatch
├── migrations/              # Alembic (async SQLAlchemy) — head 0053
├── vision_worker.py         # Dedicated Dramatiq worker for the reel + vision queues (ffmpeg/ONNX)
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

| Variable                  | Description                                              |
|---------------------------|---------------------------------------------------------|
| `DATABASE_URL`            | PostgreSQL async connection string                      |
| `DB_RUNTIME_ROLE`         | RLS runtime role (default `nexus_app`; empty only in tests/bootstrap) |
| `REDIS_URL`               | Redis connection string (Dramatiq broker)               |
| `SUPABASE_URL` / `SUPABASE_*` | Supabase project URL + admin/service keys (JWKS, ES256 verification) |
| `CANDIDATE_JWT_SECRET`    | Candidate session JWT signing key (HS256)               |
| `OTP_HMAC_KEY`            | OTP HMAC key                                            |
| `OPENAI_API_KEY`          | OpenAI — all LLM work (async + real-time)               |
| `DEEPGRAM_API_KEY`        | Speech-to-text (nova-3, en-IN)                          |
| `SARVAM_API_KEY`          | TTS (bulbul:v3) — and optional STT alternate            |
| `LIVEKIT_*`               | LiveKit server URL, API key & secret                    |
| `RECORDING_STORAGE_*`     | Cloudflare R2 endpoint/bucket/keys (recordings, reels, thumbnails) |
| `AWS_*` / S3 bucket vars  | AWS S3 (candidate resumes)                              |
| `RESEND_API_KEY`          | Email dispatch (MVP provider)                           |
| `TWILIO_*`                | SMS/OTP dispatch (MVP provider)                         |
| `SENTRY_DSN`              | Error tracking                                          |
| `OTEL_*`                  | OpenTelemetry exporters (off by default; OTLP → operator sink) |

> Auth verification is JWKS/ES256 (Supabase auth hook) — there is no static
> `JWT_SECRET`/`SUPABASE_JWT_SECRET` for dashboard users.

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
