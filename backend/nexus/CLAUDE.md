# ProjectX — Backend (Nexus)
## Claude Code Context (Backend)

> Read the root `CLAUDE.md` first. This file contains backend-specific rules that extend it.

---

## What Nexus Is

**Nexus** is the FastAPI backend — a single deployable Docker container with clean internal module boundaries. It is a **modular monolith**, not microservices. No module is extracted into a standalone service unless it demonstrably requires independent scaling and a real client requirement triggers it.

- **Language:** Python 3.12
- **Framework:** FastAPI (async throughout)
- **DB driver:** asyncpg (direct PostgreSQL connection — NOT PostgREST)
- **ORM:** SQLAlchemy async + Alembic migrations
- **Task queue:** Dramatiq + Redis
- **Containerisation:** Docker + Docker Compose
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate (same Docker image, different target)

---

## Module Structure

```
backend/nexus/
├── app/
│   ├── main.py                  ← FastAPI app factory, middleware registration
│   ├── config.py                ← Settings via pydantic-settings (env vars only)
│   ├── database.py              ← asyncpg connection pool, SQLAlchemy engine
│   ├── middleware/
│   │   ├── tenant.py            ← Sets app.current_tenant before every query
│   │   └── auth.py              ← JWT verification, RBAC enforcement
│   └── modules/
│       ├── ats/                 ← Per-ATS adapters, polling, outbound sync
│       ├── jd/                  ← JD parsing, project brief, interview config
│       ├── question_bank/       ← AI generation pipeline, versioning, admin editing
│       ├── scheduler/           ← Session provisioning, invite dispatch, OTP
│       ├── session/             ← LiveKit orchestration, session state machine
│       ├── analysis/            ← Real-time scoring, probe decision logic
│       ├── reporting/           ← Report compilation, score aggregation
│       ├── notifications/       ← Provider-agnostic email/SMS dispatch
│       └── auth/                ← Supabase JWT verification, RBAC middleware
├── migrations/                  ← Alembic migration files
│   └── versions/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── CLAUDE.md                    ← you are here
```

---

## Absolute Rules

### Database Access
- **ALL data access goes through FastAPI.** Never expose or use PostgREST/Supabase auto-generated REST endpoints.
- Use `asyncpg` driver directly via SQLAlchemy async. No sync ORM calls anywhere.
- Every query runs inside a transaction that has first called `SET LOCAL app.current_tenant = '<uuid>'`.

### RLS Pattern — Always Applied
Every tenant-scoped table MUST have this RLS policy:
```sql
-- Tenant isolation policy
CREATE POLICY "tenant_isolation" ON <table_name>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- Service role bypass (for internal admin ops)
CREATE POLICY "service_role_bypass" ON <table_name>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Do NOT use `auth.jwt()` in RLS policies.** It returns null when connecting via direct asyncpg — it only works through PostgREST. Using it will silently block all queries or fail to isolate tenants.

Add a migration lint check to catch tables missing RLS policies before they reach production.

### Auth Abstraction — Load-Bearing Constraint
JWT verification must go through a **provider-agnostic interface** in `app/modules/auth/`. Business logic must never call the Supabase Python SDK directly. This is what makes a future Cognito swap a config change, not a rewrite.

```python
# CORRECT — provider-agnostic interface
from app.modules.auth import verify_token, get_current_tenant

# WRONG — hard couples to Supabase
from supabase import Client
```

### RBAC Enforcement
- Role is checked in FastAPI middleware **before any processing begins**.
- Roles: `Company Admin`, `Recruiter`, `Hiring Manager`, `Interviewer`, `Observer`
- Candidates are **not Supabase Auth users**. They access sessions via signed single-use JWTs generated and verified entirely by the backend.

### Candidate JWT Rules
- Single-use JWT. Marked used on first verification — no replay possible.
- JWT signing key stored in env vars (MVP) / AWS Secrets Manager (Enterprise).
- Treat with the same sensitivity as a DB credential.
- OTP (6-digit code via email/SMS) is a configurable layer on top — set per JD, not globally.

### Secrets
- Load ALL configuration through `app/config.py` using `pydantic-settings`.
- Config reads from environment variables only.
- **Never hardcode secrets, API keys, or credentials anywhere in the codebase.**
- `.env` files are for local development only and must be in `.gitignore`.

---

## Module Responsibilities

| Module | What It Owns |
|---|---|
| `ats` | Per-ATS adapter interface, Ceipal polling cron, Greenhouse/Workday webhooks, outbound sync after session completion |
| `jd` | JD parsing and enrichment (AI), project brief upload, interview configuration, session settings |
| `question_bank` | 4-layer context stack question generation, versioning, admin editing, mandatory question marking |
| `scheduler` | Session provisioning, JWT invite generation, scheduling link, OTP dispatch, calendar invites |
| `session` | LiveKit room creation/teardown, session state machine, Redis state management, copilot pipeline |
| `analysis` | Real-time answer scoring, probe selection logic, signal detection (depth, specificity, evidence quality) |
| `reporting` | Post-session report compilation, per-question scorecards, transcript assembly, score aggregation, PDF generation |
| `notifications` | Provider-agnostic email (Resend→SES) and SMS (Twilio→SNS) dispatch interface |
| `auth` | JWT verification (provider-agnostic), RBAC middleware, tenant context injection |

---

## ATS Adapter Pattern

New ATS integrations only require a new adapter implementing the `ATSAdapter` interface. The core pipeline never changes.

```python
# Every ATS adapter must implement this interface
class ATSAdapter(Protocol):
    async def fetch_new_jobs(self, tenant: Tenant) -> list[Job]: ...
    async def fetch_new_candidates(self, tenant: Tenant, job_id: str) -> list[Candidate]: ...
    async def push_interview_outcome(self, tenant: Tenant, outcome: InterviewOutcome) -> None: ...
```

### Ceipal — Critical Details
- **Has NO webhooks.** Polling is the **sole** data pipeline — not a fallback.
- Poll every 15 minutes per entity type per connected company.
- Delta detection: store `last_synced_at` per entity type per company. Compare `ceipal_job_id` / `ceipal_submission_id` against DB on every poll.
- Auth: OAuth2. Access token + refresh token. Auto-refresh at 80% of token lifetime.
- Primary entity is **Submission** (recruiter submits candidate to job), not Applicant.
- Rate limits are undocumented. Test on first integration — run consecutive list calls and inspect response headers.

---

## Session State Machine

Live session state is stored in Redis with TTL. A crashed session agent recovers from the last Redis checkpoint.

State stored per session:
- Conversation history
- Current question index
- Detected signals (depth, specificity, evidence quality)
- Running per-question scores
- AI Copilot buffer (transcript, signal cards, next probe)

**Session invariants:**
- AI Copilot pipeline runs for every session regardless of whether a human is present — data is stored in Redis and surfaces in the post-session report.
- Camera + mic are required from candidates throughout.
- Candidate consent timestamped event must be logged to the session record before recording begins.

---

## Task Queue (Dramatiq)

Dramatiq is chosen over Celery: no worker memory leaks, cleaner async process model, better retry and DLQ configuration.

Tasks that must be async (never block the request cycle):
- Question bank generation (LLM call, 10–30s)
- Post-session report compilation
- Candidate notification dispatch
- ATS outbound status sync after session completion

---

## AI Pipeline Rules

### Async (Claude API — report quality, no latency pressure)
Used for: question bank generation, post-session reports, signal analysis, candidate summaries.
These are batch tasks. API latency is not a constraint.

### Real-time (Claude Haiku — 1,200ms P50 budget end-to-end)
Used for: live answer scoring, probe selection, dynamic question generation during sessions.

Real-time latency budget:
| Step | Target |
|---|---|
| WebRTC transport in | 20–50ms |
| Voice activity detection | 100–150ms |
| Deepgram Nova-3 STT | 200–300ms |
| Claude Haiku inference | 200–400ms |
| TTS first audio byte | 80–300ms |
| WebRTC transport out | 20–50ms |
| **Total P50** | **~620–1,250ms** |

TTS TTFB is the highest-leverage variable. Benchmark Cartesia Sonic vs ElevenLabs under realistic concurrent load before building the session engine.

### Langfuse — Self-Hosted Only
Langfuse traces every LLM call including candidate response text. This is sensitive candidate evaluation data. **Never use managed Langfuse cloud.** Self-host using the official Docker Compose setup. Set `LANGFUSE_HOST` in environment config.

---

## Notifications Abstraction

All email/SMS dispatch must go through the provider-agnostic `notifications` module interface. Never call Resend, Twilio, AWS SES, or AWS SNS directly from business logic.

```python
# CORRECT
from app.modules.notifications import send_email, send_sms

# WRONG — hard-couples to provider
import resend
```

---

## Database Migrations (Alembic)

- Every new table must include `tenant_id UUID NOT NULL` and have the RLS policy applied.
- Alembic runs as a pre-deployment step.
- A rollback script is required before any migration merges.
- Migration lint: fail the CI pipeline if any table is missing RLS policies.

---

## Code Standards

- **Python 3.12** — use modern syntax (match/case, `X | Y` unions, etc.)
- **Type hints required everywhere** — no untyped function signatures
- **Async throughout** — no sync blocking calls in async context. Use `asyncio.to_thread()` if a library is sync-only.
- **Pydantic v2** for all request/response schemas and config
- **Structured logging** via structlog with correlation IDs on every log record
- **OpenTelemetry** instrumentation on all module boundaries
- Follow existing module structure. New features go inside the correct module, not at root level.
- One responsibility per module. Cross-module imports go through defined interfaces, not internal paths.

---

## Dev Commands

```bash
# Start full stack (FastAPI + Redis + Postgres via Supabase local)
docker compose up --build

# Run migrations
docker compose run nexus alembic upgrade head

# Generate new migration
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

---

## Human Review Required For

- Any change to `app/modules/auth/` (JWT verification, RBAC logic)
- Any new Alembic migration touching RLS policies or tenant isolation
- Any change to the session state machine
- Any change to the candidate scoring or classification thresholds
- The Ceipal polling adapter (primary data pipeline — no fallback exists)

---