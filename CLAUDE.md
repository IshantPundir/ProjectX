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

## Enterprise Operating Standards

These apply to **every** subdirectory and surface (backend, recruiter app, admin app). A leaf CLAUDE.md may be stricter; it must never be looser. "Internal" surfaces (e.g. the admin app) are not exempt — operator surfaces have the highest blast radius.

### Reliability Targets

| Surface | Availability | P50 | P95 |
|---|---|---|---|
| Recruiter dashboard API | 99.9% | < 200ms | < 600ms |
| Candidate session API | 99.95% | < 200ms | < 500ms |
| Real-time AI response (end-to-end) | per-session | ≤ 1,200ms | ≤ 1,500ms |
| Async LLM tasks (Dramatiq) | per-task | ≤ 30s | ≤ 60s |

Error budget: 0.1% / 30 days for the dashboard, 0.05% / 30 days for the candidate session path. If burned, ship-freeze on non-critical changes until the next window. LLM-bound endpoints are excluded from API latency targets.

### Backup, Restore & Disaster Recovery

- Postgres: managed daily backups + PITR. **RPO ≤ 15 min. RTO ≤ 4 h.**
- Restore drill required quarterly — restore into a scratch DB, run `_assert_rls_completeness` + `pytest`, then drop. Log results under `docs/dr/`.
- Redis (Dramatiq broker, session checkpoints) is ephemeral by design. Anything that must survive a flush goes to Postgres.
- S3: versioning ON for the resume bucket and the recording bucket. MFA-delete ON for the recording bucket.

### Rate Limiting & Abuse Posture

Mandatory on every public endpoint. Limits cap **per-IP and per-token/per-tenant** — the per-token cap is the safety net for stolen credentials.

| Endpoint class | Per-IP | Per-token / per-tenant |
|---|---|---|
| `/api/auth/*` (login, invite) | 10/min | — |
| `/api/sessions/candidate/*/otp/request` | 3/hour | 5/day per session |
| `/api/sessions/candidate/*/otp/verify` | 3 attempts (in code) | 60s window (in code) |
| `/api/admin/*` | 30/min | — |
| All other authenticated | 600/min | 10k/min per tenant |

Declare the rate limit in the router when adding an endpoint, not after — un-rate-limited new endpoints block merge.

### Supply Chain Security

- Lockfiles are committed and authoritative (`uv.lock` / `poetry.lock`, `package-lock.json`). Never run `npm install` or `poetry add` "to see what happens" on CI.
- CI runs `pip-audit` (backend) and `npm audit --omit=dev` (frontend) on every PR. Critical CVE → block merge. High CVE → block unless waived in the PR description with a CVE exception note.
- Dependabot opens PRs; humans approve. No auto-merge of dep updates.
- SBOM (CycloneDX) generated at release time, retained for compliance audits.

### Secrets & Rotation

- Local: gitignored `.env`. MVP: Railway env vars scoped per environment. Enterprise: AWS Secrets Manager + KMS, scoped to the ECS task role.
- Rotate **candidate JWT signing key** and **OTP HMAC key** every 90 days, on personnel change, or on incident — whichever comes first.
- Service-role keys (Supabase admin, S3, Resend, Twilio, Deepgram, Cartesia, LiveKit) rotate on personnel change or incident.
- Rotation runbooks are the gate, not the rotation itself: a key cannot be rotated until its runbook is current. Runbooks live under `docs/security/`.

### Logging, PII & Audit

- **No raw PII in logs.** Forbidden values: candidate emails (post-redaction), resumes, transcripts, OTP codes, JWT bearer values, full token payloads, signing keys, raw resume contents.
- Use redactors: log `candidate_id` not email; log `session_id` + `jti_prefix=<first 8>` not the full JWT; log `<n> chars` not the body.
- The audit log is mandatory for: tenant provision/block/unblock/delete, candidate invite send/resend/revoke, role assignment changes, signal confirmation, PII redaction, **any action taken by a ProjectX-internal admin**.
- Every audit record carries `actor_id`, `tenant_id`, `action`, `resource_type`, `resource_id`, `correlation_id`. This applies to admin-app operator actions too — admin operations are not exempt.

### Test Coverage Gates

PRs touching these paths without test deltas are rejected:

- `app/modules/auth/` — JWT verification (algorithm pinning, audience, issuer, JWKS), candidate token verification, RBAC guards.
- `app/middleware/` — tenant binding, candidate single-use enforcement.
- `app/database.py` — RLS context binding helpers (`get_tenant_db`, `get_bypass_db`).
- `app/modules/session/{otp.py,service.py}` — CSPRNG, HMAC compare, attempt cap, atomic single-use consume, supersession chain.
- Any new tenant-scoped table — cross-tenant read must return 0 rows.
- `frontend/app/proxy.ts` and `frontend/admin/proxy.ts` — auth gating + redirect allowlist.

Project-wide line coverage target: 80%. Auth, RLS, candidate-session, and admin-app paths target **100% branch**.

### Code Review

- Every non-trivial PR requires at least one human reviewer. Trivial = typo fixes, Dependabot lockfile bumps, generated Alembic migrations with no policy changes.
- Subdirectory "Human Review Required For" lists are stricter (senior reviewer + explicit sign-off in the PR description), never weaker.
- AI-authored PRs follow the same rules. The merging human is the author of record; the AI is co-author (`Co-Authored-By:` line).

### Incident Response

- **SEV1**: data exposure, auth bypass, candidate-data leak, full outage. **SEV2**: degraded for >5% of tenants, partial auth failure, scheduler/notification breakage. **SEV3**: single-tenant or single-feature degradation.
- SEV1/SEV2 require a written, **blameless** post-mortem within 5 business days, stored under `docs/incidents/YYYY-MM-DD-<slug>.md`. Template: root cause, contributing factors, detection, mitigation, action items.
- Action items from a post-mortem are first-class work — they cannot be silently dropped.
- SEV1 communications: status-page post within 30 min, affected Super Admins notified within 1 h.

### Threat Model

- A current threat model lives at `docs/security/threat-model.md` (STRIDE per trust boundary).
- Update whenever: a new external service joins the data path (LiveKit, Cartesia, Deepgram, …); a new auth surface is added; tenant-isolation boundaries change; the candidate-facing surface changes.
- PRs touching auth, RLS, or session must reference the relevant section.

### Documentation Anchors

Operating runbooks and standards live under `docs/`:

- `docs/dr/` — backup/restore drill logs and DR runbook.
- `docs/incidents/` — post-mortems.
- `docs/security/` — threat model, key rotation runbooks, security review notes.
- `docs/onboarding/` — new engineer ramp.

If any of these directories are missing when an enterprise standard above demands one, that gap is itself the action item — create the directory and the first runbook in the same PR that introduces the dependency.

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