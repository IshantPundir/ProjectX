# ProjectX — Backend (Nexus)
## Claude Code Context (Backend)

> Read the root `CLAUDE.md` first. This file contains backend-specific rules that extend it.

@AGENTS.md
---

## What Nexus Is

**Nexus** is the FastAPI backend — a single deployable Docker container with clean internal module boundaries. It is a **modular monolith**, not microservices. No module is extracted into a standalone service unless it demonstrably requires independent scaling and a real client requirement triggers it.

- **Language:** Python 3.13
- **Framework:** FastAPI (async throughout)
- **DB driver:** asyncpg (direct PostgreSQL connection — NOT PostgREST)
- **ORM:** SQLAlchemy async (asyncpg driver)
- **Schema management:** Supabase SQL for the initial cut + Supabase-managed objects (auth hook, `supabase_auth_admin` grants); Alembic for every incremental change after that. `migrations/versions/` currently has 28 revisions; head is `0028_audio_tuning_summary`.
- **Task queue:** Dramatiq + Redis. Used for JD extraction/re-enrichment and question-bank generation. Notification dispatch still runs via FastAPI `BackgroundTasks` (short, non-retryable).
- **Containerisation:** Docker + Docker Compose
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate (same Docker image, different target)

---

## Current State

- **Phase 1** — done: auth (Supabase + provider-agnostic interface), multi-tenancy with RLS, client provisioning, team invites, org units (hierarchical tree with typed nesting rules), roles & permissions, audit log, notification abstraction (Resend + dry-run).
- **Phase 2A** — done: JD pipeline (create → extract → confirm → re-enrich), signal schema v2 with provenance, Dramatiq worker, `app/ai/` provider-agnostic AI layer, OpenAI instructor + Langfuse (self-hosted) tracing.
- **Phase 2B** — done: signal editing with snapshot versioning + row-locked version conflict detection, company profile ancestry walk.
- **Phase 2C.1** — done: pipeline builder (templates + per-job instances + stages), drag-to-reorder, tenant-scoped template library, starter pipelines. Stage type collapsed to v5 (6 values) in migration 0016. Pipeline versioning + stage pause + stale-bank tracking added in 0018.
- **Phase 2C.2** — done: question bank generation (adaptive coverage, mandatory demotion, per-stage LLM call, bundling discipline, SSE progress stream).
- **Phase 3B** — done: candidates module (`candidates`, `candidate_job_assignments`, `candidate_stage_progress` tables; resume upload + S3; PII redaction gate; kanban board; created in migration 0013).
- **Phase 3C.1** — done: scheduler invite/resend/revoke flow; candidate JWT mint + supersession chain; OTP code (CSPRNG + HMAC-SHA256 hash + constant-time verify); session pre-check / consent / OTP / start endpoints; **single-use token enforcement** via atomic `UPDATE … WHERE used_at IS NULL RETURNING` (`session/service.py:412–426`).
- **Phase 3C.2** — done: `/start` provisions a LiveKit room + mints candidate access token + dispatches the engine agent worker (`session/livekit.py`). The `interview_runtime` module exposes `build_session_config` + `record_session_result` — both called in-process by the engine entrypoint (single-image compose service from the same codebase). Defense layer is RLS-only: every query in `interview_runtime.service` filters by an explicit `tenant_id` argument under a bypass-RLS session.
- **Phase 3D.engine** — `app/modules/interview_engine/` is a thin LiveKit `AgentSession` harness: `agent.py` runs an STT (Deepgram) → LLM (OpenAI) → TTS (Cartesia) loop with a system prompt built from the JD + question bank. No structured per-question scoring, no in-session meta tools, no per-kind task dispatch. `event_log/` writes a per-session audit envelope to `engine-events/<session_id>.json`. `event_kinds.py` is the registry of audit-event kind strings.

  Schema surface that exists but is **not consumed by the current engine** (kept available for a future structured agent — populating it costs no migration):
  - `stage_questions.question_kind` column (migration `0026`) — bank-generator emits the field on every question.
  - `tenant_settings` table (migration `0027`) — `engine_agent_name` is read at session start; `engine_knockout_policy` is unread.
  - `sessions.knockout_failures` JSONB (migration `0027`) + `KnockoutFailure` Pydantic model in `interview_runtime.schemas` — `SessionResult.knockout_failures` is the wire contract; the engine currently writes `[]`.
- **Phase 3D.audio-pipeline (2026-05-06)** — done. LK Cloud cutover with adaptive interruption + ai-coustics QUAIL_L noise cancellation + ai-coustics built-in VAD adapter. The architecture is locked to LK Cloud. New JSONB column `sessions.audio_tuning_summary` (migration `0028`) persists per-session pause/interruption/latency percentiles for empirical tuning. `preemptive_tts: True` in `TurnHandlingOptions`. Frontend reads server-authoritative audio constraints via the `audio_processing_hints` field on the `/start` response (`noiseSuppression: false`, `echoCancellation: true`, `autoGainControl: true`). Adaptive interruption uses `min_words=2` to gate single-word backchannel. See spec `docs/superpowers/specs/2026-05-06-audio-pipeline-design.md`.
- **Phase 3D.simplification (2026-05-12)** — done. Stripped the orchestrator's five layered race-condition mitigations (continuation coalescing, stale-turn drop-and-drain, post-Judge resumption gate, must-deliver whitelist, and supporting timestamp tracking). STT stays Sarvam (`saaras:v3`, `en-IN`) — Indian-customer focus rules out Deepgram Flux (English-only). Turn detection stays `MultilingualModel` with `unlikely_threshold` raised None → 0.5 for more patient EOU. Hardened the Judge fallback path: the `push_back+concrete` cross-field check no longer raises (the State Engine's new `inverse_quality_gate` downgrades to `probe`), and `validation_error` synthesizes `clarify` instead of force-advancing the queue. Added `state.snapshot` and `speaker.input` audit events; populated `judge.call.input_summary` with the full `JudgeInputPayload` (was hardcoded empty). `engine_endpointing_max_delay` lowered 6.0s → 3.0s (LK default); adaptive interruption `min_duration` raised 0.5s → 1.0s. Supersedes `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md`. Spec: `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`.
- **Phase 3D** — pending: real-time `analysis` (scoring, probe selection) and `reporting` (post-session report compilation), plus a structured agent layered on top of `agent.py` if/when the dormant schema surface above is wired up.

Stubbed modules (routers registered, no business logic yet): `ats`, `analysis`, `reporting`.

---

## Module Structure

```
backend/nexus/
├── app/
│   ├── main.py                  ← FastAPI app factory, middleware + router registration, _assert_rls_completeness startup check
│   ├── config.py                ← Settings via pydantic-settings (env vars only)
│   ├── database.py              ← SQLAlchemy async engine + Base; 3 session types (tenant/bypass/raw); SET LOCAL ROLE nexus_app on every request
│   │                              (ORM classes live in per-module models.py — see "Module public API" below; Base.registry.configure() runs at lifespan startup)
│   ├── worker.py                ← Dramatiq worker entrypoint — imports all actor modules to register with broker
│   ├── middleware/
│   │   ├── auth.py              ← JWT extraction via JWKS, attaches token_payload to request.state
│   │   └── tenant.py            ← Binds tenant_id to structlog context
│   ├── ai/                      ← Provider-agnostic AI layer (Phase 2A)
│   │   ├── config.py            ← AIConfig — env-driven model IDs and reasoning_effort
│   │   ├── client.py            ← get_openai_client() — instructor.AsyncInstructor + plain openai.AsyncOpenAI
│   │   ├── otel.py              ← TracerProvider bootstrap, OpenAI auto-instrumentor
│   │   ├── tracing.py           ← set_llm_span_attributes() — adds prompt metadata to active OTel span
│   │   ├── prompts.py           ← PromptLoader — versioned prompt file reader, in-memory cache
│   │   └── schemas.py           ← EnrichmentOutput, SignalExtractionOutput, ReEnrichmentOutput — structured output schemas with provenance validators
│   └── modules/
│       ├── auth/                ← JWT verification, UserContext, RBAC guards, /me endpoint, invite claiming
│       │   ├── service.py       ← verify_access_token(), verify_candidate_token(), require_projectx_admin()
│       │   ├── context.py       ← UserContext dataclass, get_current_user_roles(), require_super_admin()
│       │   ├── schemas.py       ← TokenPayload, MeResponse, CandidateTokenPayload
│       │   ├── permissions.py   ← 16 canonical permission constants (frozenset)
│       │   └── router.py        ← /api/auth/* (verify-invite, accept-invite, me, onboarding/complete)
│       ├── admin/               ← ProjectX internal ops: provision-client, list clients
│       │   ├── service.py       ← provision_client() — creates tenant + sends Company Admin invite
│       │   ├── schemas.py
│       │   └── router.py        ← /api/admin/* (requires is_projectx_admin)
│       ├── settings/            ← Team management: invite, resend, revoke, deactivate
│       │   ├── service.py       ← create_team_invite(), _delete_auth_user() via Supabase Admin API
│       │   ├── schemas.py
│       │   └── router.py        ← /api/settings/team/* (requires super_admin for writes)
│       ├── org_units/           ← Org unit CRUD, member/role assignment, company profile ancestry
│       │   ├── service.py       ← create/list/update/delete org units, assign/remove roles, company profile hooks
│       │   ├── schemas.py       ← CompanyProfile strict schema, org unit request/response models
│       │   └── router.py        ← /api/org-units/*
│       ├── roles/               ← Role listing (system + tenant custom)
│       │   ├── schemas.py
│       │   └── router.py        ← /api/roles
│       ├── notifications/       ← Provider-agnostic email dispatch (Resend or dry-run)
│       │   ├── service.py       ← send_email(), DryRunProvider, ResendProvider, render_template()
│       │   ├── schemas.py
│       │   ├── router.py
│       │   └── templates/       ← HTML email templates (company_admin_invite, team_invite)
│       ├── audit/               ← Audit log writer — tenant-scoped append-only trail (Batch A fixed its RLS INSERT gap in migration 0008)
│       ├── ats/                 ← [Stub] Per-ATS adapters, polling, outbound sync
│       ├── jd/                  ← JD pipeline (Phase 2A + 2B)
│       │   ├── router.py        ← /api/jd/* (create, get, list, stream status, re-enrich, confirm signals)
│       │   ├── service.py       ← create_job_posting, confirm_signals, save_signals (FOR UPDATE row lock + version conflict detection)
│       │   ├── schemas.py       ← JobPosting request/response models
│       │   ├── errors.py        ← IllegalTransitionError (409), CompanyProfileIncompleteError (422)
│       │   ├── state_machine.py ← JD state machine with audit trail
│       │   ├── authz.py         ← require_job_access() — ancestry-walking authorization
│       │   ├── actors.py        ← Dramatiq actors: extract_and_enhance_jd (Call 1), reenrich_jd (Call 2)
│       │   └── sse.py           ← SSE status stream — routes every poll through get_tenant_session (Batch F)
│       ├── pipelines/            ← Phase 2C.1 — template library, per-job instance, stage CRUD, drag-to-reorder
│       │   ├── router.py         ← /api/pipelines/* templates + /api/jobs/{id}/pipeline instance
│       │   ├── service.py        ← auto_apply_pipeline_on_confirmation, PipelineAlreadyExistsError
│       │   ├── authz.py          ← require_template_access — ancestry-walking template authz
│       │   └── errors.py
│       ├── question_bank/        ← Phase 2C.2 — AI generation, adaptive coverage, mandatory demotion, bundling
│       │   ├── router.py         ← /api/jobs/{id}/banks/* — list (idempotent GET), generate, regenerate, update, stream
│       │   ├── service.py        ← get_banks_for_pipeline (bulk 4-query load), ensure_bank_exists
│       │   ├── refine.py         ← Per-question draft + refine LLM calls (uses get_openai_client)
│       │   ├── context.py        ← Context-stack builder for per-stage prompts
│       │   ├── authz.py          ← require_bank_access_by_stage, require_question_access
│       │   ├── state_machine.py  ← IllegalTransitionError, ReorderMismatchError
│       │   ├── actors.py         ← Dramatiq actors: generate_question_bank_stage (per-stage LLM call)
│       │   └── sse.py            ← SSE status stream — routes every poll through get_tenant_session (Batch F)
│       ├── candidates/           ← Phase 3B — candidate CRUD, resume upload, kanban board, assignments
│       │   ├── router.py         ← /api/candidates/* + /api/candidates/kanban
│       │   ├── service.py        ← create/get/list/update/redact_pii, kanban aggregation, stage transitions
│       │   ├── resume_service.py ← S3 upload + content extraction
│       │   ├── sources.py        ← Source typing (manual / ats / referral)
│       │   ├── authz.py          ← require_candidate_access (ancestry-walking)
│       │   ├── schemas.py        ← CandidateCreate / CandidateResponse / KanbanBoardResponse
│       │   └── errors.py         ← DuplicateEmailError, CandidateHasActiveSessionError
│       ├── scheduler/            ← Phase 3C.1 — invite send/resend/revoke, supersession chain
│       │   ├── router.py         ← /api/scheduler/*
│       │   ├── service.py        ← send_invite, resend_invite, revoke_invite (mints candidate JWT + token row)
│       │   ├── authz.py          ← require_scheduler_access
│       │   └── errors.py
│       ├── session/              ← Phase 3C — candidate session state machine + atomic single-use enforcement
│       │   ├── router.py         ← candidate_session_router (/api/candidate-session/{token}/*) + session_router (/api/sessions/*)
│       │   ├── service.py        ← pre_check, consent, request/verify OTP, start (LiveKit room + dispatch), supersession + replay gates
│       │   ├── livekit.py        ← Phase 3C.2 — mint_candidate_lk_token, mint_engine_dispatch_jwt, dispatch_agent, cancel_room
│       │   ├── state_machine.py  ← created → pre_check → consented → active → completed | cancelled | error
│       │   ├── otp.py            ← CSPRNG generate_code + HMAC-SHA256 hash + constant-time verify
│       │   ├── schemas.py
│       │   └── errors.py         ← TokenAlreadyUsedError, TokenSupersededError, OtpInvalidError, AgentDispatchFailedError
│       ├── interview_runtime/    ← Helpers the in-process engine calls (post-Phase-3 merge)
│       │   ├── service.py        ← build_session_config, record_session_result (called directly by agent.py)
│       │   ├── schemas.py        ← SessionConfig, SessionResult, QuestionConfig, KnockoutFailure (the wire contract)
│       │   └── errors.py         ← CompanyProfileMissingError, EmptySignalMetadataError, QuestionBankNotReadyError, …
│       ├── tenant_settings/      ← Phase 5 — per-tenant engine configuration
│       │   ├── models.py         ← TenantSettingsModel (PK = tenant_id, FK clients.id ON DELETE CASCADE)
│       │   ├── schemas.py        ← TenantSettings (engine_knockout_policy Literal, engine_agent_name)
│       │   └── service.py        ← get_tenant_settings — lazy-default read (no row → schema defaults)
│       ├── ats/                  ← [Stub] Per-ATS adapters, polling, outbound sync
│       ├── analysis/             ← [Stub] Real-time scoring, probe decision logic
│       └── reporting/            ← [Stub] Report compilation, score aggregation
├── prompts/
│   └── v1/
│       ├── jd_enrichment.txt        ← Phase 1 — JD enrichment (rewrite raw JD)
│       ├── jd_signal_extraction.txt ← Phase 2 — signal extraction from (enriched or raw) JD
│       ├── jd_reenrichment.txt      ← Call 2 — re-enrichment after signal edits
│       ├── question_bank_common.txt ← Shared system prompt for question bank calls
│       └── question_bank_<stage_type>.txt ← Per-stage-type system prompts
├── migrations/                  ← Alembic — 28 revisions; head is `0028_audio_tuning_summary`
│   └── versions/
├── tests/
│   └── conftest.py              ← AsyncClient fixture
├── Dockerfile
├── docker-compose.yml           ← nexus + nexus-worker + redis services
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
- Available to every tenant. The previous `workspace_mode='agency'` gate was removed in migration 0020 — every tenant can model itself as a staffing agency, an in-house employer, or a hybrid.
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

### RLS runtime role — Load-Bearing

The Supabase local `postgres` role has `rolbypassrls=true`. A role with that attribute **ignores every RLS policy on every table, regardless of policy contents**. Connecting as `postgres` alone gives you zero tenant isolation at the database layer — only the application's WHERE clauses would keep tenants apart, with no backstop.

The fix: every `get_tenant_db` and `get_bypass_db` session runs `SET LOCAL ROLE nexus_app` at the top of its transaction. `nexus_app` is created by alembic migration `0010_create_nexus_app_role` with `NOBYPASSRLS`, so its queries are subject to the full policy pair:

- `tenant_isolation` grants row access when `tenant_id = current_setting('app.current_tenant')` (set by `get_tenant_db`)
- `service_bypass` grants row access when `current_setting('app.bypass_rls') = 'true'` (set by `get_bypass_db`)

The switch is controlled by `DB_RUNTIME_ROLE` in `.env` (default `nexus_app`). Leaving it empty disables the switch — **only safe in tests and in the first bootstrap of a fresh cluster before migration 0010 has run**. Never ship production config with it empty.

Alembic continues to run as `postgres` because only that role has CREATE privileges; migrations are the only place that should ever touch schema.

### RLS Pattern — Always Applied
Every tenant-scoped table MUST have this RLS policy pair (the canonical full-command form — NO `FOR SELECT`):
```sql
-- Tenant isolation — applies to SELECT/INSERT/UPDATE/DELETE
CREATE POLICY "tenant_isolation" ON <table_name>
  USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- Service role bypass (for internal admin ops)
CREATE POLICY "service_bypass" ON <table_name>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Two traps to avoid:**

1. **`FOR SELECT USING (...)` without a matching `WITH CHECK`** silently blocks INSERT/UPDATE/DELETE from tenant-scoped sessions because the implicit CHECK falls through to `service_bypass`, which is false when `app.bypass_rls` is unset. Phase 1 tables (clients, users, organizational_units, user_role_assignments, user_invites, audit_log) were originally written with this broken pattern; migrations 0008 and 0009 corrected them.

2. **Raw `::uuid` cast without `NULLIF`.** `SET LOCAL app.current_tenant = '<uuid>'` reverts at transaction end, but for a custom GUC that was never declared at session boot, PostgreSQL restores it to empty string `""` — **not** NULL. The next pooled request that evaluates `current_setting('app.current_tenant', true)::uuid` crashes with `invalid input syntax for type uuid: ""`. Migration 0011 wraps every tenant-filter policy in `NULLIF(current_setting(...), '')::uuid`. Any new policy must do the same.

**Do NOT use `auth.jwt()` in RLS policies.** It returns null when connecting via direct asyncpg — it only works through PostgREST. Using it will silently block all queries or fail to isolate tenants.

**Runtime verification**: `app/main.py` runs a startup assertion (`_assert_rls_completeness`) that queries `pg_policies` for every tenant-scoped table and aborts startup with a CRITICAL log if any table is missing `tenant_isolation` (with non-NULL `WITH CHECK`) or `service_bypass`. Skipped under `ENVIRONMENT=test` or when `DB_RUNTIME_ROLE` is unset. When adding a new tenant-scoped table, add it to the enumerated list in that helper.

### Auth Abstraction — Load-Bearing Constraint
JWT verification goes through a **provider-agnostic interface** in `app/modules/auth/`. Business logic never calls the Supabase Python SDK directly. This is what makes a future Cognito swap a config change, not a rewrite.

```python
# CORRECT — provider-agnostic interface
from app.modules.auth.service import verify_access_token
from app.modules.auth.context import get_current_user_roles, UserContext

# WRONG — hard couples to Supabase
from supabase import Client
```

`verify_access_token()` enforces:
- **Algorithm pinning** — `algorithms=["ES256"]` only (Supabase's auth hook signs ES256; `RS256` was removed in Batch G to close an algorithm-confusion surface).
- **Audience** — `aud = "authenticated"` (Supabase's default role claim).
- **Issuer** — when `settings.supabase_url` is set, `iss` must equal `{supabase_url}/auth/v1`. This prevents a token minted by a different Supabase project that shares the JWKS path from authenticating to this backend.
- **JWKS caching** — fetched once, refreshed on key rotation.

Candidate JWTs (single-use session tokens, HS256, signed with `CANDIDATE_JWT_SECRET`) are the separate `verify_candidate_token()` path. Algorithm is hardcoded to `["HS256"]`; there is no algorithm allowlist confusion path.

**Single-use enforcement** (Phase 3C.1, **shipped**):
1. **Middleware gate** (`middleware/auth.py`) — verifies signature + expiry, then queries `candidate_session_tokens` by `jti`. Rejects with 401 if the JTI is unknown (`TOKEN_UNKNOWN`) or `superseded_at IS NOT NULL` (`TOKEN_SUPERSEDED`). The middleware does **not** consume `used_at` — that is the sole responsibility of the `/start` endpoint, by design (so a candidate can re-fetch pre-check / consent endpoints without burning their token).
2. **Atomic consume on `/start`** (`session/service.py:412–426`) — `UPDATE candidate_session_tokens SET used_at = now() WHERE jti = ? AND used_at IS NULL RETURNING jti`. If 0 rows, raises `TokenAlreadyUsedError` (409). Replay coverage in `tests/test_middleware_candidate_single_use.py`.
3. **Supersession chain** — when an invite is resent, `scheduler/service.py` mints a new token row and stamps `superseded_at = now()` + `superseded_by = <new_jti>` on the prior row. The middleware gate above rejects superseded tokens before they reach any handler.

The only direct Supabase HTTP call is in `settings/service.py::_delete_auth_user()` for user deactivation (hitting the Admin API with the service role key). This is isolated and would be the single swap point.

## AI Provider & Prompt Management — Load-Bearing

Phase 2A introduces `app/ai/` as the provider-agnostic AI layer.

- **AIConfig** (`app/ai/config.py`) — env-driven model IDs and `reasoning_effort`. Never hardcode. Swapping a model for a task is a single `.env` change.
- **PromptLoader** (`app/ai/prompts.py`) — reads versioned prompts from `prompts/v{N}/<name>.txt`, cached in memory. Prompt updates are file changes, not code deploys.
- **OpenAI client factory** (`app/ai/client.py`) — returns an `instructor.AsyncInstructor` wrapped around `openai.AsyncOpenAI`. OpenTelemetry tracing is wired separately at app startup via `app/ai/otel.py` — the OpenAI auto-instrumentor captures every `chat.completions.create` call as a span. Both exporters (Console for dev, OTLP for production) are off by default — see `.env.example` for the contract.
- **EnrichmentOutput** and **SignalExtractionOutput** schemas (`app/ai/schemas.py`) — strict Pydantic models for the two-phase JD pipeline. Phase 1 produces an enriched JD; Phase 2 produces signals + seniority + role_summary with provenance metadata. `source=ai_inferred` requires `inference_basis`; `ai_extracted` requires it to be null.

Business logic imports `get_openai_client()` and `prompt_loader` from `app.ai.*` — never openai/instructor directly. This is the single swap point for a future provider change.

```python
# CORRECT — provider-agnostic interface
from app.ai.client import get_openai_client
from app.ai.prompts import prompt_loader
from app.ai.config import ai_config

# WRONG — hard couples to OpenAI
from openai import AsyncOpenAI
```

**Documented carve-outs (allowed):**
- `app/modules/jd/errors.py` and `app/modules/jd/actors.py` import `openai` and `instructor.core.InstructorRetryException` *as types*, exclusively for retry/permanent-error classification (`_PERMANENT_EXCEPTIONS`) and user-safe error message mapping (`_SAFE_MESSAGES`). They never call the SDK. If the provider changes, this exception map moves with the new SDK; nothing else changes.
- `app/ai/realtime.py` is the second blessed import site for vendor SDKs. It owns LiveKit plugin instantiation (`livekit.plugins.openai`, `deepgram`, `cartesia`, `sarvam`, `ai_coustics`, `turn_detector`) so the interview-engine entrypoint never touches them directly. Reads model IDs / voices / effort from `AIConfig` — never from env or settings. (`interview_agent_name` is the only LiveKit fleet-wide routing label that lives in `settings`; AIConfig is for AI provider config only.) Lazy imports inside each factory keep the FastAPI nexus process free of the realtime plugin packages even though they're installed in the same image — the engine compose service is the only one that loads them. Current factories: `build_noise_cancellation()` (ai_coustics QUAIL_L or QUAIL_VF, env-selected), `build_interruption_options()` (`mode="adaptive"` with `min_words=2`), `build_vad()` (returns `ai_coustics.VAD()`), `build_stt_plugin()` (provider dispatcher: `sarvam` default, `deepgram` rollback), `build_tts_plugin()` (provider dispatcher: `sarvam` default, `openai` / `cartesia` alternates).
- A future cleanup is to lift the JD exception map into `app/ai/errors.py` and re-export typed sentinels so module code never references vendor exception classes by name. Tracked as tech-debt; not blocking.
- The engine runs as the `nexus-engine` compose service from the **same Docker image** as `nexus` and `nexus-worker`, just with command `python -m app.modules.interview_engine`. Single venv. The two-venv layout that existed pre-Phase-3 was retired when the engine source tree merged into nexus.

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

### Phase 2A — Implemented

| Module | What It Owns |
|---|---|
| `ai` | Provider-agnostic AI layer. `AIConfig` (env-driven model/effort), `PromptLoader` (versioned prompts, in-memory cache), `get_openai_client()` (instructor + plain openai.AsyncOpenAI), `tracing.py` + `otel.py` (OpenTelemetry auto-instrumentation + prompt-attribute helper), `EnrichmentOutput` + `SignalExtractionOutput` schemas (split in the JD creation flow refinement) with provenance validators. (The original combined `ExtractionOutput` landed in Phase 2A; subsequently split into the two-phase form on 2026-04-28 — see docs/superpowers/specs/2026-04-28-jd-creation-flow-refinement-design.md.) |
| `jd` | JD pipeline full implementation. `create_job_posting` with company profile ancestry gate, `extract_and_enhance_jd` + `reenrich_jd` Dramatiq actors (Call 1 + Call 2), state machine with audit trail, `require_job_access` ancestry-walking authz, SSE status stream via `get_tenant_session` (Batch F RLS fix), `x-correlation-id` header validation (Batch D), router with create/get/list/stream/re-enrich/confirm-signals endpoints. |
| `org_units` (extended) | `CompanyProfile` strict Pydantic schema, `find_company_profile_in_ancestry()` helper, `_validate_and_normalize_company_profile()` hook in create/update, `company_profile_completed_at/by` tracking stamps. |
| `audit` | Append-only audit log with tenant-scoped RLS (migration 0008 added the missing `FOR INSERT WITH CHECK` policy that was silently dropping tenant-scoped writes). |

**`enrichment_status` flow note:** The initial extraction actor (`extract_and_enhance_jd`) drives `enrichment_status` through `idle → streaming → completed` (or `failed`) as part of its two-phase run; `streaming` is written as a pre-mark before the phase-1 LLM call so the frontend can render a loading indicator. The legacy re-enrichment actor (`reenrich_jd`, Call 2, triggered after signal edits) also writes `streaming`/`completed`/`failed` but operates as an independent flow against an already-confirmed job. The `streaming` literal is therefore shared between both flows; retiring it is contingent on moving `reenrich_jd` to the same two-phase pattern as the initial extraction actor.

### Phase 2B — Implemented

| Module | What It Owns |
|---|---|
| `jd` (extended) | `save_signals` uses `SELECT … FOR UPDATE` on the parent job row to serialize concurrent writers, then inserts a new snapshot at `MAX(version) + 1`. A unique constraint on `(job_posting_id, version)` backstops the lock. There is no optimistic concurrency check — clients don't send an `expected_version` and the server doesn't raise a conflict error. `confirm_signals` treats `PipelineAlreadyExistsError` as an idempotent no-op; other errors continue through the error + audit path. |

### Phase 2C.1 — Implemented

| Module | What It Owns |
|---|---|
| `pipelines` | Pipeline template library scoped by org unit, per-job pipeline instance, stage CRUD (name/type/duration/difficulty/signal filter/pass criteria/advance behavior), drag-to-reorder, template swap, reset-to-source. Auto-apply on signal confirmation via `auto_apply_pipeline_on_confirmation`. Ancestry-walking authz via `require_template_access`. **Stage type v5 (migration 0016):** types collapsed from 9 → 6 (`intake`, `phone_screen`, `ai_screening`, `human_interview`, `debrief`, `take_home`); `recruiter`, `panel_interview`, `offer`, `ai_interview` hard-removed with no aliases. Instance stages carry participants (interviewers / observers / reviewers) via `pipeline_stage_participants`, gated by system-role lookup at the job's org unit ancestry; `pipeline_stage_participants` is in `_TENANT_SCOPED_TABLES` and covered by the startup RLS check. Templates remain staffing-agnostic (no participants). |

### Phase 2C.2 — Implemented

| Module | What It Owns |
|---|---|
| `question_bank` | Per-stage question bank generation via Dramatiq actor. Adaptive coverage (mandatory-fits-session validation, mandatory demotion auto-correction, duration as session time limit not generation budget), bundling discipline, per-stage-type prompts, coverage notes persistence for audit trail. `list_banks` GET is read-idempotent (Batch G — returns placeholder entries for stages without banks, does NOT create drafts on poll). State machine raises typed exceptions (`IllegalTransitionError`, `ReorderMismatchError`). Bulk `get_banks_for_pipeline` uses 4 constant queries instead of 1+2N. `refine.py` handles per-question draft + refine LLM calls. `set_llm_span_attributes()` calls on actors wire OTel span metadata (prompt name+version, bank id, stage id, tenant id). Bank staleness is tracked via `pipeline_version_at_generation` + `is_stale` (added in 0018) so pipeline edits invalidate banks deterministically. |

### Phase 3B — Implemented

| Module | What It Owns |
|---|---|
| `candidates` | Candidate CRUD with PII fields, partial-unique index on `(tenant_id, email) WHERE pii_redacted_at IS NULL` (duplicates raise `DuplicateEmailError`), resume upload via `resume_service.py` (S3 pre-signed URL pattern), source typing in `sources.py` (manual / ATS / referral). Kanban board aggregation (`/api/candidates/kanban`) and assignment-based stage transitions. PII redaction is a separate gate guarded by `CandidateHasActiveSessionError` — a candidate with any non-terminal session cannot be redacted. Ancestry-walking authz via `require_candidate_access`. |

### Phase 3C — Implemented

| Module | What It Owns |
|---|---|
| `scheduler` | Invite send / resend / revoke. `send_invite` mints a candidate JWT (HS256) and inserts the matching `candidate_session_tokens` row (jti, tenant, session_id, expires_at). Resend creates a new token row and stamps `superseded_at + superseded_by` on the prior row, building a per-session supersession chain. Notification dispatch via the provider-agnostic notifications module — notification dispatch reads settings.candidate_session_base_url (NOT frontend_base_url). |
| `session` | Two routers: `candidate_session_router` (candidate-facing, JWT in path) and `session_router` (recruiter-facing, read-only). State machine: `created → pre_check → consented → active → completed \| cancelled \| error`. OTP gate (CSPRNG 6-digit code, HMAC-SHA256 hash, 10-minute lifetime, max 3 attempts, 60s rate limit, constant-time compare). **Single-use token enforcement** is atomic on `/start` (`UPDATE … WHERE used_at IS NULL RETURNING`). Phase 3C.2 wired the LiveKit room + token provisioning: `/start` mints a candidate `room_join` token, mints + records an engine dispatch JWT, dispatches the agent, then atomically consumes the candidate token and transitions to `active` (502 `AGENT_DISPATCH_FAILED` if dispatch fails — token is NOT consumed in that case so the candidate can retry). LiveKit helpers live at `session/livekit.py`. |
| `interview_runtime` | In-process helpers the engine calls directly (post-Phase-3 merge). `build_session_config` walks session → assignment → candidate → job → stage → bank → snapshot → questions → ancestry-walked company profile to build the engine's `SessionConfig`. `record_session_result` atomically updates the session row gated on `state='active'`, idempotent on retry, writes an audit row. Both run on a bypass-RLS session with explicit `tenant_id` filter on every query (RLS-only defense layer; no HTTP router, no engine-dispatch JWT — those were retired in migration `0025`). The wire-format Pydantic models (`SessionConfig`, `SessionResult`, `KnockoutFailure`) live here for the engine to import. |
| `tenant_settings` | Phase 5 — per-tenant engine configuration. ORM `TenantSettingsModel` (PK = `tenant_id`, FK `clients.id` ON DELETE CASCADE); Pydantic `TenantSettings` with `engine_knockout_policy: Literal['record_only','close_polite']` and `engine_agent_name: str \| None`. `get_tenant_settings(db, tenant_id)` is the single read path with lazy-default semantics (no row → schema defaults). No router; recruiter-side editing UI is post-arc per overview Decision #19. |

### Future Phases — Stubbed

| Module | What It Will Own |
|---|---|
| `ats` | Per-ATS adapter interface, Ceipal polling cron, Greenhouse/Workday webhooks, outbound sync after session completion |
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

### Actor Discipline

- **Idempotency is mandatory.** Every actor must be safe to re-run. Use a database row-state check (e.g. `enrichment_status`, `bank_status`) at the top of the actor and short-circuit if the work is already done or in progress.
- **Retry policy is explicit.** Permanent errors (validation failures, missing rows, API 4xx that won't change on retry) raise a typed exception listed in `_PERMANENT_EXCEPTIONS`; Dramatiq does not retry those. Transient errors (network, 5xx, rate limits) retry with exponential backoff up to the actor's declared `max_retries`.
- **Dead-letter queue (DLQ).** Tasks that exhaust retries land in the DLQ — not the main broker. The DLQ is monitored; SEV3 fires if it fills above 0 for >24h.
- **Tracing.** Each actor's LLM call is auto-captured as an OpenTelemetry span by the OpenAI instrumentor. Actors call `set_llm_span_attributes()` from `app/ai/tracing.py` to add prompt name+version, tenant id, and correlation id. The correlation ID also flows through structured logs at every hop, so log-grep and trace-search produce the same picture.
- **No PII in actor arguments.** Pass IDs (job_id, candidate_id, session_id), not bodies. The actor reloads from Postgres under the right RLS context.
- **Bypass-RLS sessions in actors.** Worker tasks run outside a request — they don't have `app.current_tenant` set. Use `get_bypass_db()` and re-establish the tenant scope explicitly via `SET LOCAL app.current_tenant` if the actor needs RLS, or stay bypass-only for cross-tenant batch work.

---

## AI Pipeline Rules

### Async (OpenAI API — report quality, no latency pressure)
Used for: question bank generation, post-session reports, signal analysis, candidate summaries.
These are batch tasks. API latency is not a constraint.

### Real-time (OpenAI API (GPT-5 mini) — 1,200ms P50 budget end-to-end)
Used for: live answer scoring, probe selection, dynamic question generation during sessions.

Real-time latency budget:
| Step | Target |
|---|---|
| WebRTC transport in | 20–50ms |
| Voice activity detection | 100–150ms |
| Deepgram Nova-3 STT | 200–300ms |
| OpenAI API (GPT-5 mini) inference | 200–400ms |
| TTS first audio byte | 80–300ms |
| WebRTC transport out | 20–50ms |
| **Total P50** | **~620–1,250ms** |

TTS TTFB is the highest-leverage variable. Benchmark Cartesia Sonic vs ElevenLabs under realistic concurrent load before building the session engine.

### OpenTelemetry — Vendor-Neutral by Design
LLM traces flow through OpenTelemetry instrumentation, not a vendor-specific SDK. The `opentelemetry-instrumentation-openai-v2` auto-instrumentor captures every `chat.completions.create` call as a span; `app/ai/tracing.set_llm_span_attributes()` adds prompt metadata. Two opt-in exporters (`OTEL_DEV_CONSOLE_EXPORTER` for stdout, `OTEL_EXPORTER_OTLP_ENDPOINT` for production); both off by default. Spans contain candidate evaluation data, so the OTLP endpoint MUST point at a sink the operator controls — never a third-party-hosted backend without a signed sub-processor agreement.

---

## Notifications Abstraction

All email/SMS dispatch must go through the provider-agnostic `notifications` module interface. Never call Resend, Twilio, AWS SES, or AWS SNS directly from business logic.

```python
# CORRECT
from app.modules.notifications import send_email, send_sms

# WRONG — hard-couples to provider
import resend
```

### Invite / confirmation link URLs

Outbound emails build links pointing at one of two frontends. There are two distinct settings — pick the right one per email type:

| Email type | Setting | Frontend app |
|---|---|---|
| Company admin invite, team invite, team-invite resend | `settings.frontend_base_url` | `frontend/app` (recruiter dashboard) |
| Candidate interview invite, interview resend | `settings.candidate_session_base_url` | `frontend/session` (candidate interview surface) |

Pointing a candidate-bound email at `frontend_base_url` would land the candidate on the recruiter login page — the wrong surface entirely. The historical pattern `f"{settings.frontend_base_url}/interview/{token_str}"` was correct only because both surfaces lived in the same app; after the 2026-05-01 split, that pattern is a bug.

**Anti-regression grep**: `grep -rn "frontend_base_url.*interview\|frontend_base_url.*candidate-session" app/` must return zero matches. Pre-merge gate today; CI gate when CI lands.

Every environment must set BOTH settings explicitly. The localhost defaults are local-dev convenience only.

---

## Database Migrations

### Current State
The initial schema (6 tables, first-cut RLS policies, system role seeds, and the Supabase auth hook) lives in `backend/supabase/migrations/20260405000000_initial_schema.sql`. Every incremental change since Phase 2A has been an Alembic migration in `migrations/versions/`. Current head: `0028_audio_tuning_summary`.

Migrations so far:
- `0001_phase_2b_columns` — signal editing + version columns
- `0002_add_updated_by_to_job_postings` — track the last editor for audit
- `0003_signal_schema_v2` — provenance-aware signal model
- `0004_pipeline_builder` — Phase 2C.1 tables
- `0005_simplify_signal_filter` — flatten legacy include/exclude nesting
- `0006_question_banks` — Phase 2C.2 tables
- `0007_add_coverage_notes` — audit trail for coverage decisions
- `0008_audit_log_tenant_insert` — **RLS hardening**: fixed `audit_log` silently dropping tenant writes
- `0009_phase1_rls_full_command` — **RLS hardening**: Phase 1 tables get full-command `tenant_isolation` (USING + WITH CHECK)
- `0010_create_nexus_app_role` — **RLS hardening**: dedicated `nexus_app` role with `NOBYPASSRLS` + grants; without this every policy is a runtime no-op because `postgres` has `rolbypassrls=true`
- `0011_rls_null_safe_current_tenant` — **RLS hardening**: wraps `current_setting('app.current_tenant', true)::uuid` in `NULLIF(..., '')::uuid` everywhere. Fixes a PG quirk where `SET LOCAL` reverts a custom GUC to empty-string rather than NULL on transaction end, making the cast crash on the next pooled request.
- `0012_rename_service_role_bypass` — **cleanup**: rename `service_role_bypass` → `service_bypass` on Phase 2A/2C tables for consistency with the canonical name used elsewhere.
- `0013_candidates_core` — **Phase 3B**: `candidates`, `candidate_job_assignments`, `candidate_stage_progress` tables; canonical RLS pair (with NULLIF) on each; permission seed for candidate_view / candidate_edit / candidate_delete.
- `0014_sessions_scheduler_core` — **Phase 3C.1**: reshape of `sessions` table; new `candidate_session_tokens` table (jti PK, tenant, session_id, expires_at, used_at, superseded_at, superseded_by); `job_pipeline_stages.otp_required_default BOOLEAN`.
- `0015_pipeline_stage_v4` — pipeline stage type allowlist broadened to 9 values (intermediate state — superseded by 0016).
- `0016_stage_v5_participants` — stage type enum collapsed from 9 → 6 values (`intake`, `phone_screen`, `ai_screening`, `human_interview`, `debrief`, `take_home`); legacy rows renamed (`recruiter`/`panel_interview` → `human_interview`, `ai_interview` → `ai_screening`); `offer` rows deleted. Adds `pipeline_stage_participants` (instance-only staffing table) with canonical RLS pair. Registered in `_TENANT_SCOPED_TABLES`.
- `0017_stage_questions_updated_at_trigger` — BEFORE UPDATE trigger using `clock_timestamp()` (not `NOW()`) so updated_at advances even within a single transaction.
- `0018_pipeline_versioning_and_pause` — adds `job_pipeline_instances.pipeline_version` (monotonic per-instance counter), `job_pipeline_stages.paused_at`, `stage_question_banks.{pipeline_version_at_generation, stage_config_snapshot, is_stale}`, `candidate_job_assignments.entered_at_pipeline_version`. Broadens `job_postings.status` CHECK to include `pipeline_built`, `active`, `archived`.
- `0019_relax_io_stage_columns` — relaxes NOT NULL on stage columns that are FORBIDDEN for `intake` / `debrief` stage types (duration_minutes, difficulty, signal_filter, advance_behavior). Pydantic field-rules validator is the source of truth.
- `0020_drop_workspace_mode` — removes `clients.workspace_mode`. The agency/enterprise distinction collapsed: every tenant can now nest `client_account` units regardless of how it identified during onboarding.
- `0021_add_clients_blocked_at` — adds `clients.blocked_at TIMESTAMP NULL`. Pairs with `deleted_at` to define three tenant states (active / blocked / deleted) enforced at `/api/auth/login` and `get_current_user_roles`.
- `0022_users_partial_unique_auth` — replaces the plain UNIQUE on `users.auth_user_id` with a partial unique index `WHERE deleted_at IS NULL`. Required so a re-invite after tenant soft-delete can re-bind the same Supabase Auth identity.
- `0023_tenant_hard_delete_cascade` — drops `audit_log_tenant_id_fkey` + `audit_log_actor_id_fkey` (audit history outlives the rows it references); converts every other `tenant_id` FK to `ON DELETE CASCADE` so `DELETE FROM clients WHERE id = ?` propagates cleanly.
- `0024_engine_integration` — Adds `engine_dispatch_tokens` + `engine_token_uses` tables (tenant-scoped + service-bypass) and 7 result columns to `sessions` (`livekit_room_name`, `agent_started_at`, `agent_completed_at`, `transcript`, `questions_asked`, `questions_skipped`, `total_probes_fired`). The two new tables were dropped in `0025`; the `sessions` columns remain.
- `0025_drop_engine_dispatch_tables` — Drops `engine_dispatch_tokens` + `engine_token_uses` (the engine now calls `interview_runtime.service` in-process, no HTTP boundary).
- `0026_question_kind_column` — Adds `stage_questions.question_kind` (TEXT NOT NULL DEFAULT `'technical_depth'`, CHECK in `('technical_depth','behavioral_star','compliance_binary','open_culture')`). Bank-generator emits the field on every question.
- `0027_tenant_settings` — Adds `tenant_settings` table (PK = `tenant_id`, FK→`clients` ON DELETE CASCADE, two columns: `engine_knockout_policy` enum (`'record_only' | 'close_polite'`, default `'record_only'`) + `engine_agent_name` nullable TEXT) and `sessions.knockout_failures JSONB NOT NULL DEFAULT '[]'`. Lazy-default read pattern: `get_tenant_settings` returns schema defaults when the tenant has no row.
- `0028_audio_tuning_summary` — Adds `sessions.audio_tuning_summary JSONB NULL`. Per-session snapshot of NC model + enhancement level + interruption mode + browser-side getUserMedia hints, used for empirical tuning and audit.

### Going Forward
- Future schema changes should use Alembic migrations in `migrations/versions/`.
- Every new tenant-scoped table must include `tenant_id UUID NOT NULL` AND the canonical RLS policy pair (`tenant_isolation` with USING + WITH CHECK, `service_bypass` with USING). Use `NULLIF(current_setting('app.current_tenant', true), '')::uuid` — not the raw cast.
- The **startup RLS completeness check** in `app/main.py` (added in Batch G) queries `pg_policies` on app boot and aborts startup with a structured CRITICAL log if any tenant-scoped table is missing `tenant_isolation` (with non-NULL WITH CHECK) or `service_bypass`. The check is skipped when `ENVIRONMENT=test` or `DB_RUNTIME_ROLE` is unset. Add your new table to the enumerated list in that helper.
- Alembic runs as a pre-deployment step.
- A rollback script is required before any migration merges.
- If adding new auth-hook-dependent tables, update both the Supabase migration (for `supabase_auth_admin` grants) and Alembic (for the table DDL).

---

## Code Standards

> Cross-cutting enterprise standards (rate limiting, supply chain, secrets rotation, logging/PII, audit, code review, incident response, threat model) are defined **once in the root `CLAUDE.md` → Enterprise Operating Standards**. The rules below are backend-specific implementation details on top of those.

- **Python 3.13** — use modern syntax (match/case, `X | Y` unions, etc.)
- **Type hints required everywhere** — no untyped function signatures
- **Async throughout** — no sync blocking calls in async context. Use `asyncio.to_thread()` if a library is sync-only.
- **Pydantic v2** for all request/response schemas and config
- **Structured logging** via structlog with correlation IDs on every log record
- **OpenTelemetry** instrumentation on all module boundaries
- Follow existing module structure. New features go inside the correct module, not at root level.
- One responsibility per module. Cross-module imports go through defined interfaces, not internal paths.

### Logging & PII Discipline

- **No raw PII in `structlog` events.** Forbidden values: candidate emails (post-redaction phase or otherwise), resume contents, transcripts, OTP codes, full JWT bearer values, full token payloads, signing keys.
- Use redactors: log `candidate_id` not email; log `session_id` + `jti_prefix=<first 8>` not the full JWT; log lengths and hashes, not bodies.
- Errors raised from validation must scrub user input from the message before reaching the log handler. The `errors.py` per-module file is the single place to map raw exceptions to user-safe messages.
- `correlation_id` is required on every log record on every request path. Middleware injects it; never overwrite or strip it inside a handler.
- The candidate JWT (`token` path param in `/api/candidate-session/{token}/*`) MUST NOT appear in any structured log field, error message, or audit row. Use `jti_prefix=<first 8 chars of jti>` from the verified token's payload, never the raw bearer string.

### Rate Limiting

- Every public router declares a rate limit at the route level. Limits are in the root CLAUDE.md → "Rate Limiting & Abuse Posture" table. Adding an endpoint without a declared limit blocks merge.
- `/api/sessions/candidate/*` endpoints are the highest-risk surface — keep per-IP and per-session caps tight.
- `/api/admin/*` is operator-facing but still rate-limited (a compromised admin token must be capped).

### Database & Connection Pool

- One asyncpg pool per process. Pool size = `min(2 * cpu_count, 32)` for the API; workers run their own pool sized to `--processes * --threads`.
- `statement_cache_size=0` for the `nexus_app` role (RLS GUC values change per request — cached prepared statements would bind to the wrong tenant context). Confirm in `app/database.py` whenever pool config changes.
- Every tenant-scoped query goes through `get_tenant_db(request)`. Every admin/internal query goes through `get_bypass_db()`. Never construct a session manually outside these helpers.
- `SELECT … FOR UPDATE` on the parent row is the chosen concurrency primitive (see `jd/service.py::save_signals`). Optimistic concurrency is opt-in, not default.

### Tenant Hard-Delete Path

- The cascade configured in migration 0023 is the contract. New tenant-scoped tables must declare `ON DELETE CASCADE` on `tenant_id` (audit_log is the documented exception).
- Deletion order matters: app code soft-deletes first (`deleted_at`), and only the admin endpoint hard-deletes via PG cascade. Never delete tenants from a request handler.

---

## Module public API

Phase 4 of the modular-monolith refactor (umbrella spec at `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md`) split the monolithic `app/models.py` per module and introduced public-API discipline at the module boundary. The previous shim is gone; every domain module under `app/modules/<m>/` declares its public surface via `__init__.py` + `__all__`.

**Cross-module callers MUST import through the destination module's public API**, never through internal files:

```python
# CORRECT — public API
from app.modules.org_units import OrganizationalUnit, get_org_unit_ancestry
from app.modules.audit import log_event, actions
from app.modules.jd import require_job_access, transition

# WRONG — cross-module deep import
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.audit.service import log_event
```

**Rule scope:**
- Applies to imports CROSSING module boundaries (`from app.modules.<other> import X`).
- INTRA-module deep imports are fine (`from app.modules.jd.service import X` inside any file under `app/modules/jd/`).
- Routers and Dramatiq actors are deep-imported by `app/main.py` and `app/worker.py` — those callers live outside `app/modules/` and do not trip the rule.

**Documented exceptions (each rationalized inline at the call site):**

1. **Cross-module `from app.modules.<m>.models import X`** is permitted for ORM data-class imports — the AST lint test (`tests/test_module_boundaries.py`) explicitly allows the `models` submodule cross-module. This carve-out exists because (a) data-class imports do not introduce business-logic dependencies, and (b) some legitimate cycle-breaking patterns (`auth.context` ↔ `org_units.service` ↔ `roles.models`) cannot route through the module's `__init__.py` without re-entering a partially initialized package. See the inline NOTE comments in `app/modules/auth/context.py` and `app/modules/org_units/service.py`.

2. **`auth → org_units` invite-acceptance edge.** `auth/router.py` imports `create_org_unit` from `org_units` (via the public API: `from app.modules.org_units import create_org_unit`) to seed the root company unit during invite acceptance. The foundational-module trio (auth/audit/notifications) nominally should not reach upward into a domain module, but this single site is preserved because (a) it's acyclic, (b) the alternative (post-invite-accept hook registry) is disproportionate to the boundary purity gain. If the call-site list ever grows beyond invite acceptance, extract an orchestrator module (e.g. `onboarding/`) that depends on both auth and org_units.

3. **Lazy intra-module imports** (function-body, intentional): `auth/admin/__init__.py:39` and `auth/admin/_factory.py:14` are lazy by design (singleton factory + circular config-import avoidance). `auth/router.py` keeps a small number of intra-module function-body imports for test mockability. All have inline comments explaining the rationale.

**Enforcement:** `tests/test_module_boundaries.py` walks the AST of every `app/modules/<m>/*.py` file and asserts no cross-module deep import remains (other than the `models` allowance). Adding a new domain module? Add its name to `KNOWN_DOMAIN_MODULES` in that file. Adding a new exception is NOT permitted — extend the destination module's `__all__` instead.

**Why:** every module ends up depending on a small set of well-named re-exports (`from app.modules.org_units import OrganizationalUnit`), so refactoring inside a module (renaming a service file, splitting a schemas file) doesn't ripple across the codebase. The rule also catches accidentally-deep imports introduced during PR review — they're a flashing-red asymmetric trip wire, not a "clean it up later" item.

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

# Run tests with coverage — see "Coverage in Docker" below for the
# pytest-cov + Python 3.13 + livekit segfault workaround.
docker compose run nexus pytest --cov=app --cov-report=term-missing

# Lint
docker compose run nexus ruff check .

# Type check
docker compose run nexus mypy app/
```

### Dramatiq worker (Phase 2A)

```bash
# Start worker alongside API + Redis
docker compose up nexus-worker

# Run directly (no container)
dramatiq app.worker --processes 2 --threads 4

# Worker logs
docker compose logs -f nexus-worker
```

The worker reads actor definitions from `app/worker.py`, which imports every actor module so they register with the Redis broker at startup.

### Coverage in Docker — pytest-cov + Python 3.13 segfault workaround

`pytest --cov=...` segfaults intermittently in the nexus container. Root cause: PyO3-backed native extensions in the LiveKit dependency tree (and possibly others) are compiled for an older CPython ABI than `coverage.py`'s tracer assumes; under Python 3.13 the interpreter aborts with `ImportError: PyO3 modules compiled for CPython 3.8 or older may only be initialized once per interpreter process` mid-collection. Reproduces consistently when the test run touches `livekit.agents` modules (the entire `tests/interview_engine/` subtree).

**Workaround** — run `coverage` directly inside the long-running container instead of through the pytest-cov plugin:

```bash
docker compose up -d nexus
docker compose exec nexus python -m coverage run --branch \
    --source=app/modules/interview_engine/tasks \
    -m pytest tests/interview_engine -m "not prompt_quality" -q
docker compose exec nexus python -m coverage report --show-missing
```

Adjust `--source=...` to the package(s) under test. The `--branch` flag matches the CLAUDE.md "100% branch coverage" gate. Coverage data is identical to a successful pytest-cov run; only the invocation path differs.

Why not pin `coverage` / `pytest-cov` to a pre-segfault version? The segfault is in the PyO3 ↔ Python-3.13 interaction inside livekit's deps, not in coverage.py itself — pinning would only mask the issue temporarily and could break the rest of the dev toolchain. The workaround is the supported path until the upstream native deps are rebuilt against 3.13.

---

## Human Review Required For

- Any change to `app/modules/auth/` (JWT verification, RBAC logic)
- Any new Alembic migration touching RLS policies or tenant isolation
- Any change to the session state machine
- Any change to the candidate scoring or classification thresholds
- The Ceipal polling adapter (primary data pipeline — no fallback exists)

---