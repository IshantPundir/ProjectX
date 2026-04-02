# Tech Stack

**Platform:** ProjectX — AI Video Interview Platform

---

# Guiding principle

Two explicit tiers. **MVP optimises for shipping speed.** **Enterprise swaps hosting — not application code.**

The rule: data model, RLS policies, auth contracts, and module boundaries must be correct from day 1. Everything else evolves on demand, triggered by a real client requirement — not in anticipation of one.

![image.png](../assets/Tech%20Stack/image.png)

---

# Stack at a glance

| Layer | MVP | Enterprise | Trigger |
| --- | --- | --- | --- |
| Frontend | Next.js · Railway | Next.js · AWS ECS + CloudFront | First VPC requirement |
| Auth + DB | Supabase Cloud (Pro) | Self-hosted Supabase on AWS or plain RDS + Cognito | Client requires data in own VPC |
| Backend | FastAPI · Railway | FastAPI · AWS ECS Fargate | With DB migration |
| Task queue | Dramatiq · Upstash Redis | Dramatiq · AWS ElastiCache | With backend migration |
| Real-time sessions | LiveKit Cloud | LiveKit self-hosted · AWS EC2 | Client requires audio/video in VPC |
| STT | Deepgram Nova-3 managed | Deepgram self-hosted · AWS | Client requires audio data isolation |
| LLM — async | Anthropic Claude API | Same | — |
| LLM — real-time | Claude Haiku API | Llama/Mistral · AWS GPU (g4dn) | Per-token cost exceeds GPU infra cost |
| TTS | Cartesia Sonic or ElevenLabs ⚠️ | Same | — |
| Storage | AWS S3 | AWS S3 | Already AWS-native — no migration |
| Email | Resend | AWS SES | Config swap only |
| SMS / OTP | Twilio | AWS SNS | Config swap only |
| Observability | Sentry · Langfuse | Same + AWS CloudTrail | — |

---

# 1. Frontend

**Next.js (App Router) · TypeScript**

Two surfaces: recruiter/admin dashboard and the candidate video interview UI.

- **MVP →** Railway. Zero DevOps. Same `Dockerfile` as local dev.
- **Enterprise →** AWS ECS Fargate + CloudFront. Full VPC placement.

Migration is a container redeploy. No code changes.

---

# 2. Auth + database

Both handled by Supabase. GoTrue auth integrates natively with PostgreSQL RLS — tenant isolation enforced at the DB engine, not the app layer.

## Auth — Supabase GoTrue

| Feature | Availability |
| --- | --- |
| Email, magic link, Google/Microsoft OAuth | All plans |
| SAML SSO (Okta, Azure AD, Google Workspace) | Pro+ on managed cloud · **Free on self-hosted** |
| MFA (TOTP, passkeys) | All plans |

> ⚠️ **Known gap — SCIM provisioning.** Supabase does not have native SCIM support. Fortune 500 IT teams managing users in Okta or Azure AD expect automated provisioning and deprovisioning (SCIM 2.0) as a baseline requirement — adding a new employee should automatically create their ProjectX account; offboarding should revoke it instantly. Supabase cannot do this natively. When a client raises SCIM as a requirement, the options are: (1) build a custom SCIM bridge that translates SCIM 2.0 calls into Supabase Admin API calls, or (2) trigger the auth migration to AWS Cognito via the auth abstraction layer. Neither blocks MVP or early enterprise clients. Document it in the contract stage as a "roadmap item" with a delivery SLA.
> 

**Candidate access does not use Supabase Auth.** Candidates get a signed single-use JWT generated and verified entirely by the FastAPI backend. Marked used on first verification — no replay possible. The JWT signing key is stored in environment variables at MVP tier and rotated into AWS Secrets Manager at enterprise tier. Treat it with the same sensitivity as a database credential — compromise enables arbitrary session link forgery against any candidate.

**OTP** is a configurable layer on top of the JWT (set per JD, not globally). A 6-digit code sent via email/SMS before the session starts. Closes the candidate impersonation gap. Can be disabled per client to reduce drop-off.

## Database — Supabase PostgreSQL

**Row-Level Security is implemented from day 1.** Every table has a `tenant_id` column. FastAPI middleware sets a session variable before every query. RLS reads that variable and enforces isolation at the engine level — a buggy query cannot cross tenant boundaries.

> 🚨 **Developer note — `auth.jwt()` does not work with direct Postgres connections.** `auth.jwt()` is a Supabase function that only has a value when the request flows through PostgREST or Edge Functions. Since we explicitly bypass PostgREST and connect via `asyncpg`, `auth.jwt()` returns null in our context — using it in an RLS policy silently blocks all queries or fails to isolate tenants.
> 

> 
> 

> The correct pattern for FastAPI + direct asyncpg: FastAPI middleware calls `SET LOCAL app.current_tenant = '<uuid>'` at the start of every transaction. RLS policies read it via `current_setting()`:
> 

```sql
-- FastAPI middleware — run this before every query in a transaction:
-- await conn.execute("SET LOCAL app.current_tenant = $1", tenant_id)

-- RLS policy on every tenant-scoped table:
CREATE POLICY "tenant_isolation" ON sessions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- Also add a fallback for service-role operations:
CREATE POLICY "service_role_bypass" ON sessions
  USING (current_setting('app.bypass_rls', true) = 'true');
```

Every new table added to the schema must have this policy applied. Add a migration lint check to catch tables missing RLS policies before they reach production.

**ORM:** SQLAlchemy async (`asyncpg` driver) + Alembic for versioned migrations.

> 🚨 **Developer note — do not use PostgREST.** Supabase's auto-generated REST/GraphQL API (PostgREST) creates a second direct path into the database that bypasses FastAPI entirely. All data access must go through FastAPI. Use Supabase as a managed Postgres host and auth service only.
> 

**Hosting:**

- **MVP →** Supabase Cloud Pro ($25/month). SOC 2 Type II covered by Supabase's audit.
- **Enterprise →** Self-hosted Supabase on AWS, or migrate to AWS RDS + Cognito.

Both migrations are config-only. Same PostgreSQL schema and RLS policies throughout. Self-hosted Supabase unlocks SAML SSO at no additional cost.

---

# 3. Backend

**FastAPI · Python 3.12 · Docker · Modular Monolith**

Single deployable unit with clean internal module boundaries. No microservices until a module demonstrably needs independent scaling.

| Module | Responsibility |
| --- | --- |
| `ats` | Per-ATS adapter, webhook ingestion, outbound sync |
| `jd` | JD parsing, project brief, interview configuration |
| `question_bank` | AI generation pipeline, versioning, admin editing |
| `scheduler` | Session provisioning, invite dispatch, OTP |
| `session` | LiveKit orchestration, session state machine |
| `analysis` | Real-time scoring, probe decision logic |
| `reporting` | Report compilation, score aggregation |
| `notifications` | Email/SMS dispatch via provider-agnostic interface |
| `auth` | Supabase JWT verification, RBAC middleware |

**Session state during live sessions** is stored in Redis with a TTL. Conversation history, current question index, detected signals, and running scores survive process restarts. A crashed session agent recovers from the last Redis checkpoint.

> 🚨 **Ceipal has no webhook API.** The polling cron is the primary and only data pipeline — not a fallback. There is no inbound event push from Ceipal. All job and candidate data flows into ProjectX via scheduled REST API polls.
> 

**Ceipal ATS integration specifics:**

| Property | Detail |
| --- | --- |
| Auth | OAuth2 — access token + refresh token. Token exchange on first connect; auto-refresh at 80% of token lifetime. |
| API type | REST, read-heavy. No webhooks, no event subscriptions, no push mechanism. |
| Primary entities | Job Postings, Submissions, Applicant Details, Interview records, Master Data (statuses, types) |
| Key write endpoints | Create Applicant, Apply Without Registration |
| Staffing firm note | **Submissions** (recruiter submits a candidate to a job) is the correct primary entity — not Applicants. Confirm with your firm before Week 2. |
| Polling cadence | Every 15 minutes per entity type per connected company |
| Delta detection | Store `last_synced_at` per entity type per company. Compare `ceipal_job_id` / `ceipal_submission_id` against DB on every poll. |
| Admin config UI | API key field + base URL field + Test Connection button. No webhook URL to generate. |
| Rate limits | Undocumented. Test on Day 1 Week 1 — run consecutive list calls and inspect response headers. |
| Outbound sync | Interview outcome can be pushed back into Ceipal via the Interviews write API after session completion. |

New ATS adapters only require a new adapter module implementing the `ATSAdapter` interface — core pipeline never changes.

- **MVP →** Railway
- **Enterprise →** AWS ECS Fargate. Same Docker image, different deployment target.

---

# 4. Task queue

**Dramatiq + Redis**

Chosen over Celery: no worker memory leaks, cleaner async process model, better retry and dead-letter queue configuration out of the box.

Tasks handled asynchronously:

- Question bank generation (LLM call, 10–30s)
- Post-session report compilation
- Candidate notification dispatch
- ATS outbound status sync after session completion

**Redis hosting:**

- **MVP →** Upstash (serverless, zero ops, pay per request)
- **Enterprise →** AWS ElastiCache. Broker URL is the only config change.

---

# 5. Real-time interview sessions

**LiveKit — WebRTC infrastructure**

SFU architecture for 500+ concurrent sessions. LiveKit Agents framework handles the real-time audio pipeline between STT, LLM, and TTS — including turn detection, interruption handling, and barge-in natively.

**Session format:** Video interview. Camera + microphone required from candidates. Bot appears as an avatar tile (no camera).

**AI Copilot panel:** Renders automatically for any human (non-candidate) who joins the session — supervisors, recruiters, or hiring managers. Always-on, never optional. Shows: live transcript, real-time signal cards, bot’s next planned probe (before it fires), and question coverage tracker. The copilot pipeline runs for every session regardless of whether a human is present — data is stored in Redis and surfaces in the post-session report when no human is watching live.

**Human participants:** Any human (supervisor, recruiter, HM) joins as a full video participant visible to the candidate. They see the Copilot panel but cannot redirect the bot mid-session.

**Session duration:** 10–15 minutes · 8–10 questions.

**Recordings:** LiveKit Egress writes directly to AWS S3. SSE-KMS encryption at rest. Pre-signed URL access only.

- **MVP →** LiveKit Cloud. Zero ops. Same SDK as self-hosted.
- **Enterprise →** Self-hosted on AWS EC2 (c6i autoscaling group). SDK config change only — zero code changes.

> ⚠️ **TURN/STUN infrastructure is required for self-hosted LiveKit.** LiveKit Cloud includes managed TURN/STUN relay servers that handle WebRTC connections from candidates sitting behind corporate firewalls, strict NAT, or enterprise proxy configurations. When self-hosting LiveKit, we must also deploy and operate TURN/STUN servers on AWS — without them, candidates in precisely the enterprise environments that triggered the migration will fail to connect. Plan TURN/STUN as a required component of the self-hosted deployment, not an afterthought. LiveKit's open-source `livekit/turn` or the `coturn` server both run on a single EC2 `t3.medium` at moderate session volumes. Expose UDP 3478 and TCP 5349 publicly; everything else stays private.
> 

---

# 6. AI pipeline

## Real-time response latency budget

Target P50 end-to-end: **under 1,200ms**

| Step | Target |
| --- | --- |
| WebRTC transport (in) | 20–50ms |
| Voice activity detection | 100–150ms |
| Deepgram Nova-3 STT (streaming) | 200–300ms |
| Claude Haiku inference (streaming start) | 200–400ms |
| TTS first audio byte (TTFB) | 80–300ms |
| WebRTC transport (out) | 20–50ms |
| **Total P50** | **~620–1,250ms** |

TTS TTFB is the highest-leverage variable. Benchmark before committing.

## STT — Deepgram Nova-3

Best-in-class for accented speech, natural language, and technical vocabulary. Streaming transcription with word-level output. SOC 2 Type II, GDPR compliant, DPAs available.

- **MVP →** Managed Deepgram API
- **Enterprise →** Deepgram self-hosted on AWS. Audio data never leaves the VPC.

## LLM — async workloads (Claude API)

Used for: question bank generation, post-session reports, signal analysis, candidate summaries.

These are batch tasks. API latency is not a constraint. Claude's reasoning quality is the right fit for nuanced evaluation work (evidence quality, values-alignment scoring).

## LLM — real-time (Claude Haiku → self-hosted)

Used for: live answer scoring, probe selection, dynamic question generation during sessions.

| Phase | Model | Hosting |
| --- | --- | --- |
| v1 | Claude Haiku | Anthropic API |
| v2+ | Llama 3 / Mistral | AWS g4dn GPU instances |

Switch trigger: when per-token API cost at session volume exceeds self-hosted GPU cost, or a client requires real-time LLM data to stay in-VPC.

## TTS — decision pending

| Option | TTFB | Trade-off |
| --- | --- | --- |
| Cartesia Sonic | ~80ms | Lower latency, slightly less natural |
| ElevenLabs | ~300ms | Higher quality, ~220ms slower to first audio |

Run benchmark under realistic load (concurrent sessions, varied sentence lengths) before building the session engine. Both hold enterprise DPAs.

## Avatar

**Not in scope for this phase.** Will be a self-hosted custom AI model in a future phase. No third-party avatar vendor.

---

# 7. Storage

**AWS S3 — both tiers, from day 1**

Stores: session recordings (video + audio), transcripts, generated PDF reports, question bank exports, branding assets.

All buckets: SSE-KMS encryption at rest · versioning enabled · no public access · pre-signed URL access only · lifecycle policies per client's configured retention period.

S3 is AWS-native from the start. No migration ever needed.

---

# 8. Observability

| Tool | Purpose |
| --- | --- |
| Sentry | Error tracking, exception monitoring |
| Langfuse (self-hostable) | LLM observability — token usage, cost, latency, prompt version tracking per session |
| structlog | Structured logging with correlation IDs across the full pipeline |
| OpenTelemetry | Distributed tracing: WebRTC join → STT → LLM → scoring → report |

Every session carries a correlation ID end-to-end. Debugging a bad session is forensically possible without replaying the recording.

AWS CloudTrail added at enterprise tier for infrastructure-level audit logging.

> 🚨 **Developer note — self-host Langfuse from day 1.** Langfuse traces every LLM call end-to-end, including the candidate’s response text passed to Claude for scoring, the probe decision logic, and the real-time signal classifications per session. That is sensitive candidate evaluation data. Managed Langfuse cloud requires a DPA before any real candidate data flows through it, and introduces a third-party sub-processor that enterprise procurement teams must approve.
> 

> 
> 

> Self-host Langfuse using their official Docker Compose setup — it takes under an hour, runs on a single `t3.small` for MVP volumes, and is fully featured. Point `LANGFUSE_HOST` in your environment to your self-hosted instance. There is no reason to use managed Langfuse cloud for this product.
> 

---

# 9. Security

Applied at both tiers from day 1:

- All data encrypted at rest (AES-256) and in transit (TLS 1.3)
- Tenant isolation via PostgreSQL RLS — not application code
- RBAC enforced at FastAPI middleware on every endpoint — role checked before any processing
- Secrets in env vars (MVP) → AWS Secrets Manager (Enterprise)
- All S3 buckets private; pre-signed URL access only, time-limited
- Candidate consent: timestamped event logged per session record before recording begins
- API key rotation: 90-day maximum

---

# 10. Compliance

| Requirement | MVP | Enterprise |
| --- | --- | --- |
| SOC 2 Type II | Hosted on SOC 2 Type II compliant infrastructure via Supabase Cloud. **The actual audit report is only accessible on Team plan ($599/mo)** — on Pro ($25/mo) you are on compliant infrastructure but cannot hand a client the audit document. If an early enterprise client requests the SOC 2 report before you have reached Fortune 500 scale, either upgrade to Team plan or negotiate a shared-responsibility explanation in writing. Do not represent yourself as independently SOC 2 certified. | Must operate own infrastructure controls and commission own annual third-party SOC 2 Type II audit. Engage auditor 6 months before first Fortune 500 deal close — the audit takes 12–18 months and cannot be rushed. Budget $30–60k. Timeline 12–18 months. |
| GDPR | Supabase DPA + region selection | Self-hosted — full data control |
| CCPA | Deletion workflows, no data resale | Same |
| EEOC / AI bias | Scoring audit trail, human sign-off required on all final decisions | Same + third-party bias audit |
| Illinois AIVIA | Consent flow pre-session, AI disclosure, deletion on request. Applies to any company using ProjectX for Illinois-based candidates regardless of where ProjectX is incorporated. | Same |
| Data encryption | AES-256 at rest, TLS 1.3 in transit | Same + customer-managed AWS KMS |
| SAML SSO | Supabase Pro ($25/mo) | **Free on self-hosted Supabase** |

---

# 11. CI/CD

- **Source control:** GitHub
- **MVP →** GitHub Actions → Railway (automatic deploy on merge to `main`)
- **Enterprise →** GitHub Actions → Amazon ECR → ECS rolling deployment (zero-downtime)
- **Environments:** `dev`, `staging`, `production` — identical Docker images, different config only
- **Migrations:** Alembic runs as a pre-deployment step. Rollback script required before any migration merges.

---

# 12. Migration playbook

Nothing migrates until a real client requirement triggers it. Every swap is config-only — same Docker image, same codebase, same schema.

| Component | Trigger | What changes |
| --- | --- | --- |
| Frontend hosting | First VPC requirement | Redeploy to AWS ECS. Same container. |
| Auth + DB | Client requires data in own VPC | Self-host Supabase on AWS, or swap to RDS + Cognito via auth abstraction layer |
| Backend + queue | With DB migration | Redeploy to ECS; swap Upstash → ElastiCache. Broker URL only. |
| LiveKit | Client requires A/V in own VPC | Point SDK config at self-hosted EC2 cluster. Zero code changes. |
| Deepgram | Client requires audio isolation | Deploy Deepgram self-hosted on AWS. Config change only. |
| LLM real-time | Per-token cost > GPU instance cost | Deploy Llama/Mistral on AWS g4dn. Update inference endpoint in session config. |
| Email / SMS | AWS migration or procurement | Resend → SES, Twilio → SNS. Notification abstraction handles this. |
| SOC 2 audit | First Fortune 500 deal in pipeline | Engage auditor 6 months before deal close. |

> 💡 **Auth abstraction is load-bearing.** FastAPI must verify JWTs through a provider-agnostic interface — never call Supabase's SDK directly in business logic. This is what makes the Cognito swap a config change, not a rewrite.
> 