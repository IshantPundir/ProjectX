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
│   └── app/                   ← Next.js 14+ (App Router), TypeScript
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
| LLM async | Anthropic Claude API | — |
| LLM real-time | Claude Haiku | Per-token cost > GPU cost |
| TTS | Cartesia Sonic (pending benchmark) | — |
| Storage | AWS S3 | Already native, no migration |
| Email | Resend | Config swap only |
| SMS/OTP | Twilio | Config swap only |
| Observability | Sentry + Langfuse (self-hosted) | + CloudTrail |

**The rule:** Data model, RLS policies, auth contracts, and module boundaries must be correct from day 1. Everything else evolves on demand, triggered by a real client requirement — not in anticipation of one.

---

## Hard Rules (Apply Everywhere)

### Security — Non-Negotiable
- **NEVER commit `.env` files, API keys, secrets, or credentials to the repository.**
- Secrets belong in environment variables at MVP; AWS Secrets Manager at enterprise.
- Candidate JWT signing keys are treated with the same sensitivity as DB credentials.
- All S3 buckets are private. Pre-signed URL access only. No exceptions.
- RBAC is enforced at FastAPI middleware on every endpoint. Role is checked before any processing begins.
- Tenant isolation is enforced at the PostgreSQL RLS layer — never in application code alone.

### Auth Abstraction — Load-Bearing
- FastAPI must verify JWTs through a **provider-agnostic interface**. Never call the Supabase SDK directly in business logic.
- This is what makes a future Cognito swap a config change, not a rewrite.

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
-- FastAPI middleware sets before every transaction:
SET LOCAL app.current_tenant = '<uuid>';

-- RLS policy on every tenant-scoped table:
CREATE POLICY "tenant_isolation" ON <table>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```
`auth.jwt()` returns null in our context. Do not use it in RLS policies.

---

## Dev Commands

```bash
# Backend (from backend/nexus/)
docker compose up --build          # Start full backend stack
docker compose run nexus alembic upgrade head   # Run migrations
docker compose run nexus pytest    # Run tests

# Frontend (from frontend/app/)
npm run dev          # Start dev server (localhost:3000)
npm run build        # Production build
npm run lint         # ESLint check
npm run type-check   # TypeScript check
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