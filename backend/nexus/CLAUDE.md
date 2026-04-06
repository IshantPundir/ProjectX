# ProjectX — Backend (Nexus)
## Claude Code Context (Backend)

> Read the root `CLAUDE.md` first. This file contains backend-specific rules that extend it.

---

## What Nexus Is

**Nexus** is the FastAPI backend — a single deployable Docker container with clean internal module boundaries. It is a **modular monolith**, not microservices. No module is extracted into a standalone service unless it demonstrably requires independent scaling and a real client requirement triggers it.

- **Language:** Python 3.12
- **Framework:** FastAPI (async throughout)
- **DB driver:** asyncpg (direct PostgreSQL connection — NOT PostgREST)
- **ORM:** SQLAlchemy async (asyncpg driver)
- **Schema management:** Supabase migrations (initial schema); Alembic configured for future incremental changes
- **Task queue:** Dramatiq + Redis (not yet used — Phase 1 uses `BackgroundTasks` for email only)
- **Containerisation:** Docker + Docker Compose
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate (same Docker image, different target)

---

## Current State (Phase 1)

Phase 1 implements: auth, multi-tenancy, client provisioning, team invites, org units, roles & permissions, and the notification abstraction (email only). All interview-related modules (ATS, JD, question bank, scheduler, session, analysis, reporting) are registered as routers but contain no business logic yet.

See `docs/phase-1-implementation.md` for detailed developer documentation of the current implementation.

---

## Module Structure

```
backend/nexus/
├── app/
│   ├── main.py                  ← FastAPI app factory, middleware + router registration
│   ├── config.py                ← Settings via pydantic-settings (env vars only)
│   ├── database.py              ← SQLAlchemy async engine, 3 session types (tenant/bypass/raw)
│   ├── models.py                ← All ORM models (6 tables: clients, users, org units, roles, assignments, invites)
│   ├── middleware/
│   │   ├── auth.py              ← JWT extraction via JWKS, attaches token_payload to request.state
│   │   └── tenant.py            ← Binds tenant_id to structlog context
│   └── modules/
│       ├── auth/                ← JWT verification, UserContext, RBAC guards, /me endpoint, invite claiming
│       │   ├── service.py       ← verify_access_token(), verify_candidate_token(), require_projectx_admin()
│       │   ├── context.py       ← UserContext dataclass, get_current_user_roles(), require_super_admin()
│       │   ├── schemas.py       ← TokenPayload, MeResponse, CandidateTokenPayload
│       │   ├── permissions.py   ← 16 canonical permission constants (frozenset)
│       │   └── router.py        ← /api/auth/* (verify-invite, complete-invite, me, onboarding/complete)
│       ├── admin/               ← ProjectX internal ops: provision-client, list clients
│       │   ├── service.py       ← provision_client() — creates tenant + sends Company Admin invite
│       │   ├── schemas.py
│       │   └── router.py        ← /api/admin/* (requires is_projectx_admin)
│       ├── settings/            ← Team management: invite, resend, revoke, deactivate
│       │   ├── service.py       ← create_team_invite(), _delete_auth_user() via Supabase Admin API
│       │   ├── schemas.py
│       │   └── router.py        ← /api/settings/team/* (requires super_admin for writes)
│       ├── org_units/           ← Org unit CRUD, member/role assignment
│       │   ├── service.py       ← create/list/update/delete org units, assign/remove roles
│       │   ├── schemas.py
│       │   └── router.py        ← /api/org-units/*
│       ├── roles/               ← Role listing (system + tenant custom)
│       │   ├── schemas.py
│       │   └── router.py        ← /api/roles
│       ├── notifications/       ← Provider-agnostic email dispatch (Resend or dry-run)
│       │   ├── service.py       ← send_email(), DryRunProvider, ResendProvider, render_template()
│       │   ├── schemas.py
│       │   ├── router.py
│       │   └── templates/       ← HTML email templates (company_admin_invite, team_invite)
│       ├── ats/                 ← [Stub] Per-ATS adapters, polling, outbound sync
│       ├── jd/                  ← [Stub] JD parsing, project brief, interview config
│       ├── question_bank/       ← [Stub] AI generation pipeline, versioning, admin editing
│       ├── scheduler/           ← [Stub] Session provisioning, invite dispatch, OTP
│       ├── session/             ← [Stub] LiveKit orchestration, session state machine
│       ├── analysis/            ← [Stub] Real-time scoring, probe decision logic
│       └── reporting/           ← [Stub] Report compilation, score aggregation
├── migrations/                  ← Alembic (configured, versions/ empty — initial schema is in Supabase migration)
│   └── versions/
├── tests/
│   └── conftest.py              ← AsyncClient fixture
├── Dockerfile
├── docker-compose.yml           ← nexus + redis services
├── pyproject.toml
├── .env.example                 ← All required env vars documented
└── CLAUDE.md                    ← you are here
```

---

## Organizational Unit Types

Valid types (as of Phase 2): `company`, `division`, `client_account`, `region`, `team`

### Rules

**`company`**
- Auto-created during onboarding. Never manually created by recruiters.
- Non-deletable (`is_root = true`).
- Exactly one per tenant. `parent_unit_id` must be NULL.
- `unit_type` is immutable once set to `'company'`.
- `company_profile` (JSONB) is required — cannot be null.

**`client_account`**
- Only available when `clients.workspace_mode = 'agency'`.
- `company_profile` (JSONB) is required — cannot be null.
- Cannot be nested under another `client_account` or under a `team`.
- Can be nested under `company`, `division`, or `region`.

**`division`**
- No special data requirements. General intermediate grouping.
- Cannot be nested under a `team`.

**`region`**
- No special data requirements. Geographic grouping.
- Cannot be nested under a `team`.

**`team`**
- Leaf node. No child units of any type allowed under a team.
- Enforced on the parent side at `create_org_unit` time.

### Nesting Rule Enforcement

All nesting rules are enforced in `create_org_unit` in `app/modules/org_units/service.py`.
The single check `parent.unit_type == 'team' → reject` covers all child types.
The extra check `unit_type == 'client_account' and parent.unit_type == 'client_account' → reject` handles the client_account-under-client_account case.

---

## Absolute Rules

### Database Access
- **ALL data access goes through FastAPI.** Never expose or use PostgREST/Supabase auto-generated REST endpoints.
- Use `asyncpg` driver directly via SQLAlchemy async. No sync ORM calls anywhere.
- Every tenant-scoped query uses `get_tenant_db(request)` dependency which runs `SET LOCAL app.current_tenant = '<uuid>'`.
- Admin/internal operations use `get_bypass_db()` which runs `SET LOCAL app.bypass_rls = 'true'`.
- All three session types are defined in `app/database.py`.

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
JWT verification goes through a **provider-agnostic interface** in `app/modules/auth/`. Business logic never calls the Supabase Python SDK directly. This is what makes a future Cognito swap a config change, not a rewrite.

```python
# CORRECT — provider-agnostic interface
from app.modules.auth.service import verify_access_token
from app.modules.auth.context import get_current_user_roles, UserContext

# WRONG — hard couples to Supabase
from supabase import Client
```

The only direct Supabase HTTP call is in `settings/service.py::_delete_auth_user()` for user deactivation (hitting the Admin API with the service role key). This is isolated and would be the single swap point.

### RBAC Enforcement
- Auth middleware extracts JWT and attaches `token_payload` to `request.state` before any route handler runs.
- Roles: `Admin`, `Recruiter`, `Hiring Manager`, `Interviewer`, `Observer` (system roles, seeded at migration)
- Super Admin is determined by `client.super_admin_id == user.id`, not a role assignment
- `UserContext` (loaded via `get_current_user_roles` dependency) provides `has_role_in_unit()`, `has_permission_in_unit()`, `all_permissions()` methods
- Authorization guards: `require_projectx_admin()` (JWT claim), `require_super_admin()` (DB check)
- Permissions are NOT in the JWT — they are loaded from the DB per-request via role assignments
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

### Phase 1 — Implemented

| Module | What It Owns |
|---|---|
| `auth` | JWKS-based JWT verification (provider-agnostic), `UserContext` RBAC, `require_projectx_admin()` / `require_super_admin()` guards, `/me` endpoint, invite claiming, onboarding completion |
| `admin` | ProjectX internal operations: `provision_client()` creates tenant + sends Company Admin invite. `list_clients()` for admin dashboard. Requires `is_projectx_admin` JWT claim. |
| `settings` | Team management: send invite, resend (supersedes old), revoke, deactivate user (including Supabase auth account deletion via Admin API) |
| `org_units` | Org unit CRUD (hierarchical tree), member/role assignment, visibility filtering (super admin sees all, others see assigned + ancestors) |
| `roles` | Role listing endpoint — returns system roles (visible to all) + tenant custom roles |
| `notifications` | Provider-agnostic email dispatch. `DryRunProvider` (logs to stdout) and `ResendProvider`. HTML template rendering for invite emails. |

### Future Phases — Stubbed

| Module | What It Will Own |
|---|---|
| `ats` | Per-ATS adapter interface, Ceipal polling cron, Greenhouse/Workday webhooks, outbound sync after session completion |
| `jd` | JD parsing and enrichment (AI), project brief upload, interview configuration, session settings |
| `question_bank` | 4-layer context stack question generation, versioning, admin editing, mandatory question marking |
| `scheduler` | Session provisioning, JWT invite generation, scheduling link, OTP dispatch, calendar invites |
| `session` | LiveKit room creation/teardown, session state machine, Redis state management, copilot pipeline |
| `analysis` | Real-time answer scoring, probe selection logic, signal detection (depth, specificity, evidence quality) |
| `reporting` | Post-session report compilation, per-question scorecards, transcript assembly, score aggregation, PDF generation |

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

## Database Migrations

### Current State
The initial schema (6 tables, all RLS policies, system role seeds, and the auth hook) lives in `backend/supabase/migrations/20260405000000_initial_schema.sql`. This is the single source of truth for the current schema. Alembic is configured (`migrations/env.py`) but `versions/` is empty.

### Going Forward
- Future schema changes should use Alembic migrations in `migrations/versions/`.
- Every new table must include `tenant_id UUID NOT NULL` and have the RLS policy applied.
- Alembic runs as a pre-deployment step.
- A rollback script is required before any migration merges.
- Migration lint: fail the CI pipeline if any table is missing RLS policies.
- If adding new auth-hook-dependent tables, update both the Supabase migration (for grants) and Alembic (for the table DDL).

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