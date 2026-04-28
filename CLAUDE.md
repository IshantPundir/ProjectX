# ProjectX — AI Video Interview Platform
## Claude Code Context (Root)

---

## What This Product Is

ProjectX is an enterprise-grade B2B SaaS platform that replaces recruiter phone screens with structured, AI-led video interviews. It integrates with existing ATS systems (Ceipal, Greenhouse, Workday), generates contextual question banks per role, conducts live proctored video interviews at scale, and produces detailed evaluation reports — all with configurable human oversight.

**The core wedge:** Replace the recruiter phone screen for Fortune 500 hiring teams running high-volume pipelines. Scale to 500+ simultaneous sessions without headcount.

---

## Who Uses This

- **Recruiting teams / HR admins** — configure interview pipelines, manage JDs, review reports
- **Hiring managers** — review evaluation reports, make advancement decisions
- **Human interviewers** — participate in Round 2 panel sessions with AI Copilot support
- **Candidates** — receive branded invite links, complete live video interviews (camera + mic required)

---

## Monorepo Structure

```
ProjectX/
├── CLAUDE.md                  ← you are here
├── frontend/
│   ├── app/                   ← Next.js 16 App Router — recruiter dashboard + candidate interview surface
│   └── admin/                 ← Next.js 16 App Router — internal ProjectX-operator console (provision tenants)
└── backend/
    └── nexus/                 ← FastAPI, Python 3.12, Docker, Modular Monolith
```

Each subdirectory has its own `CLAUDE.md` with context-specific rules. Always read the relevant subdirectory `CLAUDE.md` before working in that area.

---

## Two-Tier Architecture Philosophy

**MVP → Enterprise is a hosting swap, never a code rewrite.**

| Layer | MVP | Enterprise Trigger |
|---|---|---|
| Frontend | Railway | First VPC requirement |
| Auth + DB | Supabase Cloud Pro | Client requires data in own VPC |
| Backend | Railway | With DB migration |
| Task queue | Upstash Redis | With backend migration |
| Real-time | LiveKit Cloud | Client requires A/V in VPC |
| STT | Deepgram managed | Client requires audio isolation |
| LLM async | OpenAI API | — |
| LLM real-time | OpenAI API (GPT-5 mini class) | Per-token cost > GPU cost |
| TTS | Cartesia Sonic (pending benchmark) | — |
| Storage | AWS S3 | Already native, no migration |
| Email | Resend | Config swap only |
| SMS/OTP | Twilio | Config swap only |
| Observability | Sentry + Langfuse (self-hosted) | + CloudTrail |

**The rule:** Data model, RLS policies, auth contracts, and module boundaries must be correct from day 1. Everything else evolves on demand, triggered by a real client requirement — not in anticipation of one.

---

## Current Phase Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Auth, multi-tenancy + RLS, client provisioning, team invites, org units, roles, audit log, notifications | ✅ done |
| 2A | JD pipeline, signal schema v2 with provenance, `app/ai/` provider-agnostic layer, Langfuse tracing | ✅ done |
| 2B | Signal editing with snapshot versioning + row-locked save, company profile ancestry walk | ✅ done |
| 2C.1 | Pipeline templates + per-job instances + stages, drag-to-reorder, stage type v5 (6 values), participants | ✅ done |
| 2C.2 | Question bank generation (per-stage LLM, adaptive coverage, mandatory demotion, bundling, SSE) | ✅ done |
| 3B | Candidates module (CRUD, resume + S3, kanban, PII redaction gate) | ✅ done |
| 3C.1 | Scheduler invites + supersession chain; session pre-check / consent / OTP; **single-use token enforcement** atomic on `/start` | ✅ done |
| 3C.2 | LiveKit room + token provisioning (replaces the 501 stub on `session.start`) | 🟡 pending |
| 3D | Real-time `analysis` (scoring, probe selection) + `reporting` (post-session report) | 🟡 pending |
| ATS | Ceipal polling, Greenhouse/Workday adapters, outbound sync | 🟡 stubbed |

Subdirectory CLAUDE.md files are the source of truth for module-level detail. This table is the cross-cutting summary only.

---

## Hard Rules (Apply Everywhere)

### Security — Non-Negotiable
- **NEVER commit `.env` files, API keys, secrets, or credentials to the repository.**
- Secrets belong in environment variables at MVP; AWS Secrets Manager at enterprise.
- Candidate JWT signing keys are treated with the same sensitivity as DB credentials.
- All S3 buckets are private. Pre-signed URL access only. No exceptions.
- RBAC is enforced at FastAPI middleware on every endpoint. Role is checked before any processing begins.
- Tenant isolation is enforced at the PostgreSQL RLS layer — never in application code alone.
- **RLS is actually enforced at runtime** via a dedicated `nexus_app` role (`NOBYPASSRLS`, created by migration 0010). Every `get_tenant_db` / `get_bypass_db` session runs `SET LOCAL ROLE nexus_app` before any query. The default `postgres` Supabase role has `rolbypassrls=true` and would otherwise silently skip every policy. See `backend/nexus/CLAUDE.md` → "RLS runtime role" for the full model.
- The app verifies its RLS state at boot via a startup assertion (`_assert_rls_completeness` in `app/main.py`) that queries `pg_policies` and aborts on any missing policy.

### Auth Abstraction — Load-Bearing
- FastAPI must verify JWTs through a **provider-agnostic interface**. Never call the Supabase SDK directly in business logic.
- This is what makes a future Cognito swap a config change, not a rewrite.

### AI Provider — Load-Bearing
- AI provider is OpenAI for the entire system (Phase 2A onwards).
- All LLM calls go through the `app/ai/` module. Never call the OpenAI SDK (or `langfuse.openai`, or `instructor`) directly from business logic.
- `AIConfig` in `app/ai/config.py` is the single source of truth for model IDs and reasoning_effort — env-driven, never hardcoded in service files.
- Swapping a model for a task is a `.env` change, not a code change.

### Do NOT Use PostgREST
- Supabase's auto-generated REST/GraphQL API (PostgREST) is disabled. All data access goes through FastAPI.
- Supabase is used as a managed Postgres host and auth service only.

### Borderline Candidates — Human Review Always Required
- Candidates classified as "Borderline" can **never** be auto-advanced or auto-rejected.
- They must be held for human review. This is a product invariant, not a feature toggle.

### Candidate Consent
- A timestamped consent event must be logged to the session record before any recording begins.
- This is required for AIVIA compliance (applies to Illinois-based candidates regardless of ProjectX's incorporation state).

---

## Key Domain Concepts

| Concept | Meaning |
|---|---|
| `tenant_id` | Every table is scoped to a tenant. This is a company using ProjectX, not an end user. |
| Session | A live AI-led video interview. Has a state machine. Camera + mic required from candidate. |
| Question Bank | 8–10 questions generated from a 4-layer context stack (JD + company profile + candidate brief + optional project brief). Never generic templates. |
| AI Copilot Panel | Always-on panel visible to any human (non-candidate) in a session. Shows live transcript, signal cards, next bot probe, question coverage tracker. |
| Submission | In Ceipal, the correct primary entity is Submission (recruiter submits candidate to job) — not Applicant. |
| Borderline | AI score classification. Cannot be auto-resolved. Requires explicit human decision. |
| Correlation ID | Every session carries a correlation ID end-to-end through the entire pipeline (WebRTC → STT → LLM → scoring → report). Required for forensic debugging. |

---

## Critical Integration Details

### Ceipal ATS
- **Has no webhooks.** Polling every 15 minutes is the **primary and only** data pipeline — not a fallback.
- Delta detection: store `last_synced_at` per entity type per company.
- Auth: OAuth2 (access token + refresh token). Auto-refresh at 80% of token lifetime.
- Rate limits are undocumented — test on Day 1.

### RLS Pattern (asyncpg — NOT PostgREST)
```sql
-- Every request runs under `nexus_app` (NOBYPASSRLS) inside an explicit transaction.
-- `get_tenant_db` issues these two SETs at the top of each request transaction:
SET LOCAL ROLE nexus_app;
SET LOCAL app.current_tenant = '<uuid>';

-- Canonical RLS policy pair on every tenant-scoped table:
CREATE POLICY "tenant_isolation" ON <table>
  USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
CREATE POLICY "service_bypass" ON <table>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

Critical rules that are easy to get wrong:
- **`NULLIF(..., '')::uuid`** — raw `::uuid` crashes on empty string, which PostgreSQL restores to a custom GUC after `SET LOCAL` reverts at transaction end. Always wrap with `NULLIF`.
- **`tenant_isolation` must have both `USING` and `WITH CHECK`** with no `FOR SELECT` restriction. `FOR SELECT USING (...)` silently blocks all writes from tenant sessions.
- **`auth.jwt()` returns null** in our asyncpg context. Do not use it in RLS policies — it only works through PostgREST.

---

## Dev Commands

```bash
# Backend (from backend/nexus/)
docker compose up --build          # Start full backend stack (api + worker + redis)
docker compose run nexus alembic upgrade head   # Run migrations
docker compose run nexus pytest    # Run tests

# Recruiter app (from frontend/app/, port 3000)
npm run dev          # Start dev server (localhost:3000)
npm run build        # Production build
npm run lint         # ESLint check
npm run test         # Vitest

# Admin app (from frontend/admin/, port 3001)
npm run dev          # Start dev server (localhost:3001)
npm run build
npm run lint

# Local Supabase (Postgres on 54322, Studio on 54323, Inbucket on 54324)
supabase start
```

---

## CI/CD

- Source control: GitHub
- MVP: GitHub Actions → Railway (auto-deploy on merge to `main`)
- Enterprise: GitHub Actions → Amazon ECR → ECS rolling deployment
- Environments: `dev`, `staging`, `production` — identical Docker images, config-only differences
- Alembic migrations run as a pre-deployment step. A rollback script is required before any migration merges.

---

## Human-in-the-Loop Constraints

Require explicit human review before merging changes to:
- Authentication and JWT verification logic (`auth` module)
- RLS policies and tenant isolation (database migrations)
- Candidate scoring and classification thresholds
- Session state machine transitions
- Any billing or subscription logic (when implemented)

---

## Compliance Anchors

- **GDPR:** Supabase DPA + region selection at MVP. Candidate data deletion workflows required.
- **CCPA:** No data resale. Deletion on request.
- **EEOC / AI bias:** Scoring audit trail required. Human sign-off required on all final hiring decisions.
- **Illinois AIVIA:** Consent flow required pre-session. AI disclosure. Deletion on request. Applies to any Illinois-based candidate regardless of ProjectX's state of incorporation.
- **SOC 2:** MVP runs on Supabase Pro (SOC 2 Type II compliant infrastructure). Audit report access requires Team plan upgrade when a client requests it.

---

## Observability Standards

- Every session carries a **correlation ID** end-to-end.
- structlog for structured logging throughout the backend.
- OpenTelemetry for distributed tracing: WebRTC join → STT → LLM → scoring → report.
- Sentry for error tracking.
- Langfuse **self-hosted** for LLM observability. Do NOT use managed Langfuse cloud — it would make candidate evaluation data flow through a third-party sub-processor.

---