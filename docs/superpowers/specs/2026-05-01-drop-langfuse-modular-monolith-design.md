# Drop Langfuse + True Modular Monolith — Design

**Date:** 2026-05-01
**Status:** Approved (brainstorming complete; pending implementation plan)
**Author:** Brainstorming session between user and assistant
**Scope:** Backend (`backend/nexus/`, `backend/interview_engine/`), Frontend (`frontend/app/`, `frontend/admin/`)

---

## TL;DR

Five sequential, separately-mergeable phases:

1. **Observability swap** — drop `langfuse` entirely; replace with vendor-neutral OpenTelemetry instrumentation. Two opt-in exporters (console for dev, OTLP for future); both off by default.
2. **Dependency uplift** — `openai` 1.x → 2.x, `instructor` 1.x → 2.x, Python 3.12 → 3.13.
3. **Engine merge** — `backend/interview_engine/` becomes `app/modules/interview_engine/`. Single Docker image runs three commands. HTTP boundary `/api/internal/sessions/{id}/{config,results}` and the engine dispatch JWT are deleted; engine talks to Postgres directly via existing helpers.
4. **Modular monolith refactor** — split `app/models.py` per module; hoist late imports; fix the inverted `auth → interview_runtime` edge; introduce `__init__.py` public-API exports backed by an AST-walking lint test.
5. **Package upgrade sweep** — context7-verified patch/minor bumps across backend `pyproject.toml` and both frontend `package.json` files. No speculative majors.

**Hard contract freeze**: every public URL, response shape, error code, header, SSE event, and LiveKit attribute the frontend currently consumes is immutable through this work. Frontend consumes only patch/minor dep bumps in Phase 5.

**Estimated work-days:** ~13. **Calendar time with soak:** ~5 weeks if Phase 3 soaks ≥1 week before Phase 4 ships.

---

## Decisions Locked

| # | Decision | Rationale |
|---|---|---|
| Q1 | **OpenTelemetry-native LLM tracing.** No Langfuse SDK in any form; `opentelemetry-instrumentation-openai-v2` auto-captures OpenAI calls into vendor-neutral spans. | Existing OTel infra already pinned (unused). Vendor-neutral is the architectural property the user is paying for. |
| Q1.1 | **Defer the trace sink.** No Sentry wiring, no managed collector, no infra commitment now. Two opt-in env-var exporters: `OTEL_DEV_CONSOLE_EXPORTER=true` (ConsoleSpanExporter to stdout, dev) and `OTEL_EXPORTER_OTLP_ENDPOINT=<url>` (OTLPSpanExporter, future). Both unset → silent. | Maximum vendor flexibility; future custom admin-dashboard observability is feasible without code changes. Conscious trade: no live LLM dashboard until a sink is wired. |
| Q2 | **Engine merges into nexus** as `app/modules/interview_engine/`. Single image, three command-variants from one Dockerfile. Python bumped to 3.13. | Once openai 1.x cap lifts (Q4), the two-venv hack stops being load-bearing. "Modular monolith" requires logical boundaries to be the source of truth, not deployment topology. |
| Q2.1 | **Engine dispatch JWT retired entirely.** Both `engine_dispatch_tokens` and `engine_token_uses` tables dropped via Alembic 0025. `mint_engine_dispatch_jwt` and `verify_engine_token` deleted. RLS becomes the primary defense layer. | Both ends are us; the JWT is dead weight. Residual threat (LiveKit-creds leak + forged dispatch) is documented in `docs/security/threat-model.md` as a known trade. Mitigation if the threat ever materializes: re-add a signed dispatch envelope as a follow-up. |
| Q3 | **Strict modular monolith on four axes:** (1) split `app/models.py` per module; (2) hoist all late imports to module top-level; (3) fix the inverted `auth → interview_runtime` edge; (4) introduce module-public-API discipline (`__init__.py` exports + lint test). | Brings the codebase to a defensible "true" modular monolith. Each module owns its data + import surface. |
| Q3.1 | **Skip per-module Alembic branching.** Migrations stay globally numbered. | Most Python monoliths don't branch Alembic; the operational pain is greater than the boundary-purity gain. |
| Q4 | **Forced + patch/minor + OTel current; frontend included on the same discipline.** No speculative major bumps (pydantic 2 → 3, sqlalchemy 2 → 3, next 16 → 17 — all out of scope until those releases stabilize). | Each pin verified via context7 against current PyPI/npm; no library left behind. |
| Q5 | **Hard contract freeze.** Every public URL, request/response shape, header, error code, SSE event, LiveKit participant attribute, and ProgressBanner attribute the frontend currently consumes is byte-compatible through Phases 1–4. Phase 5 touches frontend only via dep bumps. | Modular monolith ≠ rename public APIs. URL hygiene is its own future scoped PR. |
| Sequencing | **Approach B — phased, each shippable.** Five separate PRs. Each phase ships green CI and is independently rollback-able. | Smaller blame surface on regressions; bisectable; matches the codebase's blameless-post-mortem culture. |

---

## Target End-State Architecture

```
backend/
└── nexus/
    ├── Dockerfile                    ← single image, single venv, Python 3.13
    ├── docker-compose.yml            ← 4 services from this image:
    │                                    nexus, nexus-worker, nexus-engine, redis
    ├── pyproject.toml                ← langfuse out, openai>=2, instructor>=2,
    │                                    livekit-agents in, OTel-OpenAI in
    ├── livekit.toml                  ← was backend/interview_engine/livekit.toml
    ├── app/
    │   ├── main.py                   ← FastAPI factory, _assert_rls_completeness,
    │   │                                Base.registry.configure(), OTel TracerProvider
    │   │                                bootstrap, OpenAIInstrumentor().instrument()
    │   ├── config.py                 ← LANGFUSE_* removed; OTEL_*, engine_* added
    │   ├── database.py               ← Base lives here; engine module uses
    │   │                                get_bypass_db() directly (no HTTP wire)
    │   ├── worker.py                 ← dramatiq entrypoint
    │   ├── ai/
    │   │   ├── client.py             ← `instructor.from_openai(AsyncOpenAI())`
    │   │   ├── tracing.py            ← NEW: set_llm_span_attributes() helper
    │   │   ├── prompts.py
    │   │   ├── realtime.py
    │   │   ├── config.py
    │   │   └── schemas.py
    │   ├── middleware/
    │   │   ├── auth.py
    │   │   └── tenant.py
    │   └── modules/
    │       ├── auth/                 ← models.py: User, UserRoleAssignment, UserInvite
    │       ├── admin/
    │       ├── settings/
    │       ├── org_units/            ← models.py: Client, OrganizationalUnit
    │       ├── roles/                ← models.py: Role
    │       ├── notifications/
    │       ├── audit/                ← models.py: AuditLog
    │       ├── jd/                   ← models.py: JobPosting, JobPostingSignalSnapshot
    │       ├── pipelines/            ← models.py: PipelineTemplate,
    │       │                              PipelineTemplateStage,
    │       │                              JobPipelineInstance, JobPipelineStage,
    │       │                              PipelineStageParticipant
    │       ├── question_bank/        ← models.py: StageQuestionBank, StageQuestion
    │       ├── candidates/           ← models.py: Candidate, CandidateJobAssignment,
    │       │                              CandidateStageProgress
    │       ├── scheduler/
    │       ├── session/              ← models.py: Session, CandidateSessionToken
    │       │                          livekit.py: dispatch_agent simplified
    │       │                              (no JWT issued)
    │       ├── interview_runtime/    ← router.py REMOVED in Phase 3.
    │       │                          service.py: build_session_config,
    │       │                              record_session_result are pure Python.
    │       │                          schemas.py: kept (in-process contract).
    │       │                          errors.py: EngineTokenInvalidError removed.
    │       ├── interview_engine/     ← NEW (was backend/interview_engine/)
    │       │   ├── __init__.py       ← public API
    │       │   ├── __main__.py       ← LiveKit cli.run_app(server) entrypoint
    │       │   ├── agent.py          ← was interview_engine/agent.py
    │       │   ├── interviewer.py    ← was agents/interviewer.py
    │       │   ├── state_machine.py
    │       │   └── prompt_builder.py
    │       ├── ats/                  ← stub
    │       ├── analysis/             ← stub
    │       └── reporting/            ← stub
    ├── prompts/v1/                   ← unchanged location
    ├── migrations/versions/          ← +0025_drop_engine_dispatch_tables
    └── tests/
        ├── interview_engine/         ← was backend/interview_engine/tests/
        ├── test_module_boundaries.py ← AST-walking public-API enforcement test
        ├── test_startup_integrity.py ← Base.registry.configure() smoke + OTel
        ├── test_ai_tracing.py        ← OTel InMemorySpanExporter assertions
        └── (existing tests, mostly unchanged)
```

### What's gone (after all 5 phases)

- `backend/interview_engine/` directory (entire — moved into nexus).
- `app/models.py` (split per-module; transitional shim retired in Phase 4d).
- `langfuse` dep + every `@observe`, `langfuse_context.update_current_trace`, `langfuse_enabled()`, `flush_langfuse()`, `shutdown_langfuse()` reference.
- `LANGFUSE_*` env vars in `.env.example`, `config.py`, `docker-compose.yml`.
- The two-venv PYTHONPATH layering in the engine Dockerfile.
- HTTP boundary `/api/internal/sessions/{id}/{config,results}`.
- Engine dispatch JWT (`mint_engine_dispatch_jwt`, `verify_engine_token`, `EngineTokenInvalidError`).
- `engine_dispatch_tokens` and `engine_token_uses` tables.
- `nexus_client.py` (engine's httpx wrapper).
- `INTERVIEW_ENGINE_JWT_SECRET`, `NEXUS_INTERNAL_BASE_URL` env vars.
- The auth → interview_runtime inverted import edge (resolved automatically by deletion).
- Late imports inside function bodies in `jd/service.py`, `jd/state_machine.py`, etc.

### What's new

- OpenTelemetry instrumentation for OpenAI calls via `opentelemetry-instrumentation-openai-v2`.
- `app/ai/tracing.py` — sets `gen_ai.prompt.name`, `gen_ai.prompt.version`, `tenant.id`, `app.correlation_id` attributes on the active OTel span.
- Per-module `models.py` files (one per domain module that owns tables).
- Per-module `__init__.py` public-API exports, `__all__`-discipline.
- `tests/test_module_boundaries.py` — AST-walking lint test enforcing public-API discipline.
- `app/modules/interview_engine/__main__.py` — LiveKit Agents CLI entrypoint.
- `nexus-engine` compose service (one-line config; same image as nexus + nexus-worker).
- Alembic migration `0025_drop_engine_dispatch_tables`.

### What stays exactly the same (frozen contract)

- All `/api/...` URLs, request/response shapes, error codes, headers, SSE event names.
- LiveKit room name format, candidate token TTL, room join semantics.
- `session_outcome` LiveKit participant attribute behavior.
- Progress attributes (`current_question_index`, `total_questions`, `time_remaining_seconds`).
- Candidate JWT scheme, supersession chain, single-use semantics.
- Prompt file format and location (`prompts/v1/<name>.txt`).
- The `app/ai/*` import surface that business code uses (`get_openai_client()`, `prompt_loader`).
- Sentry pinning (left as future work; no `sentry_sdk.init()` added in this work).

---

## Out of Scope

- Sentry SDK initialization (deferred per Q1.1).
- New observability features (custom admin dashboard, metric panels). The OTel foundation makes a future custom dashboard feasible — building it is its own multi-week project.
- Phase 3D (analysis + reporting modules).
- Frontend major-version bumps (Next.js 16 → 17, React 19 → 20, etc.).
- Public API rename / URL hygiene improvements.
- LiveKit Egress recording pipeline.
- ATS integrations beyond what's stubbed today.
- Per-module Alembic branching (decided no in Q3.1).
- Switching `instructor.from_openai()` to `instructor.from_provider()` — the latter is more idiomatic in instructor 2.x but is an API choice, not a forced migration. Stay with `from_openai()` to keep the diff small.

---

## Phase 1 — Observability Swap

### Goal
Drop every Langfuse code path. Replace with vendor-neutral OpenTelemetry instrumentation. Preserve the *capability* (per-LLM-call tracing with prompt-version metadata) without the vendor.

### Approach: side-by-side then drop (two commits, one PR)

1. **Commit 1**: Add OTel instrumentation alongside existing Langfuse. Both run; verify OTel emits expected spans in dev (Console exporter on).
2. **Commit 2**: Remove all Langfuse code paths, deps, env vars, comments.

This trades a slightly larger transient diff for live verification before deleting the safety net.

### Changes

**`pyproject.toml`** (nexus)
- Remove `langfuse>=2.56,<3`.
- Add `opentelemetry-instrumentation-openai-v2` (verify exact version via context7 at implementation time; current is 2.x line).
- Bump `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi` to current minor.
- Add `opentelemetry-exporter-otlp` explicit pin.

**`app/ai/client.py`** — rewrite
- Delete: `_resolve_langfuse_url`, `langfuse_enabled`, `flush_langfuse`, `shutdown_langfuse`, `_langfuse_configured`, `langfuse_context.configure()`, `from langfuse.openai import AsyncOpenAI`.
- Replace: `from openai import AsyncOpenAI; client = instructor.from_openai(AsyncOpenAI())`.
- Singleton pattern preserved.

**`app/ai/tracing.py`** — NEW (~50 lines)
- `set_llm_span_attributes(prompt_name: str, prompt_version: str, **extra)` — adds `gen_ai.prompt.name`, `gen_ai.prompt.version`, `tenant.id`, `app.correlation_id` to the active span via `trace.get_current_span()`.
- Naming follows OpenTelemetry GenAI semantic conventions.

**`app/main.py`**
- Delete langfuse shutdown handler.
- Bootstrap OTel TracerProvider: `OTLPSpanExporter` if `OTEL_EXPORTER_OTLP_ENDPOINT` set, else no-op. `ConsoleSpanExporter` if `OTEL_DEV_CONSOLE_EXPORTER=true`. Both unset → spans created and discarded silently.
- Register `OpenAIInstrumentor().instrument()` once at startup.
- Add `tracer_provider.shutdown()` to shutdown handlers.

**`app/worker.py`**
- Delete `from app.ai.client import shutdown_langfuse` + `atexit.register(shutdown_langfuse)`.
- Workers run their own TracerProvider — same setup as the API process.

**`app/modules/jd/actors.py`**
- Delete `from langfuse.decorators import langfuse_context, observe` and the `@observe(...)` decorators on `_jd_enrichment_phase`, `_jd_signal_extraction_phase`, `reenrich_jd`. The OpenAI auto-instrumentor produces a span automatically; explicit decorators are no longer required.
- Replace `langfuse_context.update_current_trace(metadata=...)` with `set_llm_span_attributes(...)` from `app.ai.tracing`.
- Delete `if langfuse_enabled(): await asyncio.to_thread(flush_langfuse)` blocks.

**`app/modules/question_bank/actors.py`** — same pattern as `jd/actors.py`.

**`app/modules/jd/router.py`** — comment cleanup only ("Langfuse tags" → "OTel attributes"). No code change.

**`app/config.py`**
- Delete: `langfuse_host`, `langfuse_base_url`, `langfuse_public_key`, `langfuse_secret_key`.
- Add: `otel_exporter_otlp_endpoint: str = ""`, `otel_dev_console_exporter: bool = False`, `otel_service_name: str = "nexus"`.

**`.env.example`**
- Delete the LANGFUSE block.
- Add an OTEL block with comments explaining: empty → silent; set `OTEL_DEV_CONSOLE_EXPORTER=true` for local dev visibility; set `OTEL_EXPORTER_OTLP_ENDPOINT` to ship to a collector or backend.

**`docker-compose.yml`** (nexus)
- Remove all `LANGFUSE_*` env passthroughs.
- Add `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_DEV_CONSOLE_EXPORTER` (commented-out by default).

**Tests**
- Migrate any test mocking at the langfuse boundary (e.g. patches `langfuse.openai.AsyncOpenAI`) to mock at `openai.AsyncOpenAI`.
- New: `tests/test_ai_tracing.py` — uses `opentelemetry.sdk.trace.export.in_memory_span_exporter.InMemorySpanExporter` to assert that `_jd_enrichment_phase()` produces spans with `gen_ai.prompt.name="jd_enrichment"`, `tenant.id=<uuid>`, `app.correlation_id=<id>`.

### What does NOT change
- Prompt files in `prompts/v1/*.txt` — same format, same loader.
- Public API surface — frontend has no langfuse coupling.
- Sentry — entirely separate; left untouched (unwired today; remains unwired).
- The `app/ai/*` import contract that business code uses.

### Risk + rollback
- **Risk:** *low.* Langfuse is observability-only; outage of traces does not break the candidate flow.
- **Trade:** No live LLM-trace dashboard in production until a sink is wired later. structlog with correlation_id remains the audit trail. Console exporter gives dev visibility.
- **Rollback:** revert the PR; pure code revert with no data migration.

### Verification gates
1. JD enrichment end-to-end produces an OTel trace with `gen_ai.prompt.name="jd_enrichment"`, observable in Console exporter output (or any OTLP backend wired during dev).
2. Question-bank generation produces ≥1 trace per stage with `gen_ai.prompt.name="question_bank_<stage_type>"`.
3. `pytest` full suite green.
4. `pip-audit` clean.
5. `grep -r langfuse backend/nexus` returns zero results outside `migrations/` history comments.

---

## Phase 2 — OpenAI v2 + Instructor v2 + Python 3.13

### Goal
Lift the openai/instructor pins so the engine merge in Phase 3 lands on a single venv. Bump Python so livekit-agents (engine code in Phase 3) and nexus run the same interpreter.

### Changes

**`pyproject.toml`**
- `openai>=1.60,<2` → **`openai>=2.10,<3`** (context7 verified — v2.11.0 is current; pin exact at implementation time).
- `instructor>=1.7,<2` → **`instructor>=2.0,<3`** (context7-verify exact at implementation time).
- `python>=3.12` → **`python>=3.13`** in `requires-python`.

**`Dockerfile` (nexus)**
- `FROM python:3.12-slim` → **`FROM python:3.13-slim`**.
- Verify `build-essential libpq-dev` still satisfies asyncpg's wheel install on 3.13.

**`app/ai/client.py`**
- Pattern remains `instructor.from_openai(openai.AsyncOpenAI())`. The factory signature is unchanged between instructor 1.7 and 2.x. Likely a 1-line migration if any.

**`app/modules/jd/errors.py` + `app/modules/jd/actors.py`**
- These import openai exception types for retry classification. The relevant 2.x exceptions:
  - `openai.RateLimitError`, `openai.APIError`, `openai.APIConnectionError`, `openai.APITimeoutError`, `openai.AuthenticationError`, `openai.BadRequestError` — same names, same module path. **No-op.**
  - `instructor.exceptions.InstructorRetryException` — verify path in 2.x; update if relocated.
- Re-validate `_PERMANENT_EXCEPTIONS` list against the 2.x exception class hierarchy.

**`app/ai/schemas.py`**
- Pydantic 2.x models with custom validators. Untouched by openai/instructor bumps — verify only.

**`app/ai/realtime.py`**
- Lazy imports of `livekit.plugins.*` stay. They prevent FastAPI worker boot from loading those packages, which is a perf win regardless of venv consolidation. Documented as "keep, follow-up to revisit" in CLAUDE.md.

**Tests**
- Re-run full pytest. Most failures will be at the openai-mock seam.
- New: smoke test asserting `instructor.from_openai(AsyncOpenAI())` round-trips with `response_model=` correctly.

**`docker-compose.yml`** — no change (Python version is in the image).

### What does NOT change
- Public API.
- Frontend.
- Database schema.
- Prompt files.
- The `app/ai/*` import surface.
- The `interview_engine` Dockerfile (deleted entirely in Phase 3 — no point updating here).

### Risk + rollback
- **Risk:** *medium.* OpenAI 2.x preserves the chat-completions surface; instructor 2.x preserves `from_openai()`. Most call sites unchanged. The risk is exception-class relocation in instructor.
- **Mitigation:** context7-verify both libraries at implementation time; integration test on retry classification; pytest matrix on a feature branch before merge.
- **Rollback:** pure pyproject + Dockerfile revert. Single git revert, rebuild image.

### Verification gates
1. JD enrichment + question-bank generation succeed end-to-end against real OpenAI.
2. Manually trigger `OPENAI_API_KEY=invalid` and verify the actor short-circuits → `failed` (retry classification works).
3. `docker compose up --build` completes on Python 3.13 without wheel errors.
4. Full pytest green.
5. `pip-audit` clean.

---

## Phase 3 — Engine Merge into Nexus

### Goal
Move `backend/interview_engine/` into `backend/nexus/app/modules/interview_engine/`. Drop the two-venv hack, the HTTP boundary, the dispatch JWT, the engine token tables. Ship one Docker image; run three command-variants from it.

### File moves

| From | To |
|---|---|
| `backend/interview_engine/agent.py` | `backend/nexus/app/modules/interview_engine/agent.py` |
| `backend/interview_engine/agents/interviewer.py` | `backend/nexus/app/modules/interview_engine/interviewer.py` |
| `backend/interview_engine/state_machine.py` | `backend/nexus/app/modules/interview_engine/state_machine.py` |
| `backend/interview_engine/prompt_builder.py` | `backend/nexus/app/modules/interview_engine/prompt_builder.py` |
| `backend/interview_engine/config.py` | merged into `backend/nexus/app/config.py` (Settings) |
| `backend/interview_engine/tests/` | `backend/nexus/tests/interview_engine/` |
| `backend/interview_engine/livekit.toml` | `backend/nexus/livekit.toml` |
| `backend/interview_engine/nexus_client.py` | **deleted** |
| `backend/interview_engine/pyproject.toml` | **deleted** (deps absorbed into nexus) |
| `backend/interview_engine/Dockerfile` | **deleted** |
| `backend/interview_engine/AGENTS.md` | content merged into `nexus/CLAUDE.md`; original deleted |

A new `app/modules/interview_engine/__main__.py`:

```python
"""LiveKit Agent worker entrypoint. Run: python -m app.modules.interview_engine"""
from livekit.agents import cli
from app.modules.interview_engine.agent import server

if __name__ == "__main__":
    cli.run_app(server)
```

### Dependency consolidation (`pyproject.toml`)

Add to nexus:
```toml
"livekit-agents[silero,turn-detector]>=1.5.4",
"livekit-plugins-ai-coustics>=0.2.7",
"livekit-plugins-noise-cancellation~=0.2",
"livekit-plugins-deepgram>=1.5.4",
"livekit-plugins-openai>=1.5.4",
"livekit-plugins-cartesia>=1.5.4",
```

### docker-compose.yml — three services from one image

```yaml
nexus:
  build: { context: ., dockerfile: Dockerfile }
  command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

nexus-worker:
  build: { context: ., dockerfile: Dockerfile }
  command: dramatiq app.worker --processes 2 --threads 4

nexus-engine:                                            # NEW
  build: { context: ., dockerfile: Dockerfile }
  command: python -m app.modules.interview_engine
  env_file: .env
  environment:
    DATABASE_URL: postgresql+asyncpg://...
    REDIS_URL: redis://redis:6379/0
    # NEXUS_INTERNAL_BASE_URL — REMOVED. No HTTP boundary.
    # INTERVIEW_ENGINE_JWT_SECRET — REMOVED. No JWT.
  volumes:
    - hf-cache:/app/.cache/huggingface
  depends_on:
    - redis
    # No `nexus` dependency — engine talks to Postgres directly.
```

**Dev caveat:** uvicorn's `--reload` watches `nexus`'s source tree but won't restart `nexus-engine` on code change. After editing `app/modules/interview_engine/`, run `docker compose restart nexus-engine` manually. Document this in `nexus/README.md` Phase 3.

### Dispatch flow rewrite

**Before** (HTTP wire + JWT):
```
/start → mint LK candidate token + mint engine JWT + INSERT engine_dispatch_tokens
       → dispatch agent with metadata={session_id, engine_jwt, ...}
Engine → join room → HTTP GET /api/internal/sessions/{id}/config (Bearer JWT)
       → conduct interview
       → on close: HTTP POST /api/internal/sessions/{id}/results (same JWT)
```

**After** (in-process):
```
/start → mint LK candidate token only
       → dispatch agent with metadata={session_id, tenant_id, correlation_id}
       → consume candidate token, transition session → active
Engine → join room
       → from app.modules.interview_runtime.service import build_session_config
         async with get_bypass_db() as db:
             await db.execute(text("SET LOCAL app.current_tenant = :t"),
                              {"t": tenant_id})
             config = await build_session_config(db, session_id)
       → conduct interview
       → on close: same in-process pattern with record_session_result
```

The engine sets `app.current_tenant` itself using the tenant_id from dispatch metadata. RLS is the new defense layer — replacing what JWT-based authn was providing.

### Code changes

**`app/modules/session/livekit.py`**
- Delete `mint_engine_dispatch_jwt()` (~40 lines).
- `dispatch_agent()` signature simplifies — no `engine_jwt` parameter:
  ```python
  async def dispatch_agent(
      *, room_name: str, session_id: UUID, tenant_id: UUID, correlation_id: str
  ) -> None:
  ```
- Metadata payload: `{"session_id": ..., "tenant_id": ..., "correlation_id": ...}`.

**`app/modules/session/service.py`**
- `start_session()`: remove the `mint_engine_dispatch_jwt(...)` call. Remove the `engine_jwt` parameter from the `dispatch_agent()` invocation. All other start-session logic (atomic candidate token consume, room create, state transition) is unchanged.

**`app/modules/interview_runtime/router.py`** — **delete entire file.**
- Remove its registration from `app/main.py`.
- Remove the auth middleware exemption for `/api/internal/`.

**`app/modules/interview_runtime/service.py`**
- Delete `verify_engine_token()`.
- Keep `build_session_config(db, session_id)` and `record_session_result(db, session_id, result)` — these become the in-process API. Add `db: AsyncSession` as the first param explicitly.

**`app/modules/interview_runtime/errors.py`**
- Delete `EngineTokenInvalidError`.

**`app/modules/auth/service.py`**
- Delete `from app.modules.interview_runtime.errors import EngineTokenInvalidError` (the inverted edge — fixed automatically by deletion).

**`app/modules/interview_engine/agent.py`** (was `backend/interview_engine/agent.py`)
- Replace `from nexus_client import fetch_session_config` with:
  ```python
  from app.database import get_bypass_db
  from app.modules.interview_runtime.service import (
      build_session_config, record_session_result,
  )
  ```
- Replace HTTP calls with the in-process `async with get_bypass_db()` blocks above.

**`app/config.py`**
- Add: every field that was in `InterviewEngineConfig` (silero thresholds, agent_name, results_fallback_dir, etc.) — they all become part of nexus's `Settings`.
- Remove: `interview_engine_jwt_secret`, `nexus_internal_base_url`. `interview_agent_name` renamed to `engine_agent_name` for consistency with the new `nexus-engine` service.
- The shared `AIConfig` already lives in `app/ai/config.py` — unchanged.

**`app/database.py`**
- `get_bypass_db()` must work outside a request scope. Verify it doesn't read `request.state`. If it does, add a `get_db_session()` variant that doesn't require a Request.

### Migration: `0025_drop_engine_dispatch_tables.py`

```python
def upgrade():
    op.drop_table("engine_token_uses")
    op.drop_table("engine_dispatch_tokens")

def downgrade():
    # Recreates table structure; in-flight dispatches at rollback time are
    # unrecoverable. Documented rollback hazard.
    op.create_table("engine_dispatch_tokens", ...)  # full schema from 0024
    op.create_table("engine_token_uses", ...)
```

Plus update `_TENANT_SCOPED_TABLES` in `app/main.py` — remove `engine_dispatch_tokens`.

### What's deleted
- `backend/interview_engine/` directory (entire).
- `app/modules/interview_runtime/router.py`.
- `mint_engine_dispatch_jwt`, `verify_engine_token`, `EngineTokenInvalidError`.
- `nexus_client.py`.
- `engine_dispatch_tokens` and `engine_token_uses` tables.
- `tests/test_engine_token_*.py`, `tests/test_interview_runtime_router.py`.
- `INTERVIEW_ENGINE_JWT_SECRET`, `NEXUS_INTERNAL_BASE_URL` env vars.

### What stays exactly the same
- `/api/candidate-session/{token}/start` request/response.
- LiveKit room name format, candidate token TTL, room join semantics.
- Engine-published `session_outcome` participant attribute behavior.
- Progress attributes.
- The `SessionConfig` and `SessionResult` schemas — same Pydantic models, same field names, just no longer carried over HTTP.

### Risk + rollback
- **Risk:** *high.* Candidate session path is the highest-availability surface (99.95% target). Migration drops tables.
- **Mitigations:**
  - Staging cutover with manual full-interview smoke before main.
  - `tests/test_engine_dispatch_in_process.py` — `start_session` populates correctly + `build_session_config` / `record_session_result` work in-process.
  - `backend/nexus/scripts/engine_dispatch_smoke.py` — pre-flight check simulating a LiveKit dispatch against a seeded session.
  - **Optional fallback path** (NOT recommended; flagged for completeness): keep both old HTTP endpoints AND in-process path alive temporarily. Doubles surface area; default is clean cutover.
- **Rollback:** revert PR + reapply migration 0024-state. Run rollback during a quiet window (no candidates with sessions in `state='active'`).

### Verification gates
1. End-to-end candidate interview completes in staging against real LiveKit Cloud.
2. `nexus-engine` container boots, registers with LiveKit Cloud, joins a room when dispatched.
3. `build_session_config` and `record_session_result` succeed when called from the engine process.
4. Session state machine transitions correctly: `active` → `completed` after engine close.
5. `session_outcome` LiveKit attribute still published before engine shutdown.
6. Mid-session `/rejoin` still works (re-mints LiveKit token without re-dispatching engine).
7. `pytest` full suite green, including new engine integration tests.
8. `_assert_rls_completeness` passes after migration 0025.
9. `pip-audit` clean.
10. `grep -r "engine_jwt\|engine_dispatch_token\|engine_token_uses\|nexus_internal_base_url\|interview_engine_jwt_secret" backend/` returns zero hits outside migration history.
11. **`docs/security/threat-model.md` updated** with the engine-dispatch trust-boundary change (RLS-only defense; residual threat documented).

### Decisions flagged for spec self-review
- **`get_bypass_db()` outside request scope** — verify and adjust if needed.
- **`nexus-engine` health check** — equivalent of uvicorn liveness probe for a LiveKit worker process. Defer to implementation plan.

---

## Phase 4 — Modular Monolith Refactor

Mechanical refactor across 8 sub-commits in one PR. Lowest behavioral risk, highest line count.

### 4a. Split `app/models.py` per module

| Destination | Models |
|---|---|
| `app/modules/auth/models.py` | `User`, `UserRoleAssignment`, `UserInvite` |
| `app/modules/roles/models.py` | `Role` |
| `app/modules/org_units/models.py` | `Client`, `OrganizationalUnit` |
| `app/modules/audit/models.py` | `AuditLog` |
| `app/modules/jd/models.py` | `JobPosting`, `JobPostingSignalSnapshot` |
| `app/modules/pipelines/models.py` | `PipelineTemplate`, `PipelineTemplateStage`, `JobPipelineInstance`, `JobPipelineStage`, `PipelineStageParticipant` |
| `app/modules/question_bank/models.py` | `StageQuestionBank`, `StageQuestion` |
| `app/modules/candidates/models.py` | `Candidate`, `CandidateJobAssignment`, `CandidateStageProgress` |
| `app/modules/session/models.py` | `Session`, `CandidateSessionToken` |
| **deleted** | `EngineDispatchToken`, `EngineTokenUse` (already retired in Phase 3) |

`Base` moves from `app/models.py` to `app/database.py`.

`app/models.py` becomes a transitional re-export shim, retired in Phase 4d once cross-module imports are migrated to public APIs.

Cross-module FKs use string references throughout — already idiomatic in most of the codebase. Add explicit `Base.registry.configure()` to startup so mapper-config failures are loud at boot, not at first request.

### 4b. Hoist late imports

Confirmed list:

| File:line | Import |
|---|---|
| `jd/service.py:389` | `from app.modules.question_bank.service import recompute_and_persist_stale` |
| `jd/service.py:495` | `from app.modules.pipelines.categories import ...` |
| `jd/service.py:677–678` | `from app.modules.audit import actions, service as audit_service` |
| `jd/state_machine.py:72` | `from app.modules.audit.service import log_event` |

Plus any others surfaced by `grep -rn "    from app\." app/modules/` during implementation.

Each becomes a top-level import. **Escalation rule:** if hoisting an import surfaces a true cycle (`A → B → A` resolved at import-time, not just at function-call time), extract the shared symbol to `app/shared/<topic>.py` (a flat, dependency-free utility namespace). Do NOT introduce mutual top-level imports between domain modules.

The `jd → question_bank` edge gets a CLAUDE.md note: JD signal changes invalidate question banks; the directional dependency is real and intentional.

### 4c. Fix inverted edge

Already absorbed by Phase 3 (deletion of `EngineTokenInvalidError`). Phase 4 verifies no other inverted edges across `auth`, `audit`, `notifications` via grep:

```bash
grep -rn "from app\.modules\." app/modules/auth/ app/modules/audit/ app/modules/notifications/ \
  --include="*.py" | grep -v test_
```

These three modules should only import from each other and from non-modules. Any upward import is a bug.

### 4d. Module public-API discipline

**1. `__init__.py` re-exports.** Each module exports its public surface explicitly via `__all__`. Routers and Dramatiq actors are NOT exported.

**2. Cross-module import sweep.** Every `from app.modules.<name>.<internal>` outside the module gets rewritten as `from app.modules.<name> import ...`. Roughly 60–80 import statements based on the cross-module dependency graph. Mechanical.

The `app/models.py` shim from 4a gets retired in this step.

**3. Lint enforcement via test.** `tests/test_module_boundaries.py` walks the AST of every file under `app/modules/` and asserts no cross-module deep imports. ~30 lines. Runs as part of pytest.

**4. CLAUDE.md update.** Add a "Module public API" section explaining the rule, with the test as the citation.

### What does NOT change
- Database schema.
- Public API surface.
- Migration history.
- Behavioral semantics.

### Risk + rollback
- **Risk:** *low-medium.* Dangers: SQLAlchemy mapper-config failures (mitigated by `Base.registry.configure()` at boot); latent bugs from import-order changes (caught by tests); public-API enforcement test failures (these are the goal — fix violations, don't waive).
- **Rollback:** pure git revert per sub-step. Zero data impact.

### Verification gates
1. ✅ Full pytest green, including `test_module_boundaries.py`.
2. ✅ `app/main.py` startup runs `Base.registry.configure()` without errors.
3. ✅ `_assert_rls_completeness` passes (table list updated for 0025).
4. ✅ `ruff check` clean.
5. ✅ `mypy app/` clean.
6. ✅ `grep -rn "from app\.modules\.[a-z_]\+\.[a-z_]" app/modules/` returns zero hits except intra-module.
7. ✅ `grep -rn "from app\.models import" app/` returns zero hits.

### Sub-commit ordering inside Phase 4

| Commit | Subject |
|---|---|
| 4a-1 | Move ORM classes to per-module `models.py`; add re-export shim; add `Base.registry.configure()` to startup |
| 4a-2 | Run full test suite, verify green (no behavior change) |
| 4b | Hoist all late imports to module top-level |
| 4c | Sweep `auth`, `audit`, `notifications` for residual inverted edges |
| 4d-1 | Add `__init__.py` exports for every module |
| 4d-2 | Sweep cross-module imports to use public API; retire `app/models.py` shim |
| 4d-3 | Add `tests/test_module_boundaries.py` |
| 4d-4 | Add CLAUDE.md "Module public API" section |

---

## Phase 5 — Package Upgrade Sweep (Backend + Frontend)

### Scope
- `backend/nexus/pyproject.toml` + `uv.lock`
- `frontend/app/package.json` + `package-lock.json`
- `frontend/admin/package.json` + `package-lock.json`

### Discipline (per Q4)
**Forced + patch/minor + OTel current. No speculative majors.** For each pin:

1. context7 (or PyPI/npm) for latest stable on current major.
2. Update specifier; same major upper bound.
3. Regenerate lockfile.
4. Run full test suite; if red, isolate and fix or pin to last-good.

### Backend pin sweep

Already bumped in earlier phases (do not re-do): `langfuse` (removed), `openai`, `instructor`, `python`, `livekit-*`, `opentelemetry-*`.

Remaining backend pins to sweep:

| Package | Current pin | Action |
|---|---|---|
| `fastapi` | `>=0.115,<1` | bump minor |
| `uvicorn[standard]` | `>=0.34,<1` | bump minor |
| `pydantic[email]` | `>=2.10,<3` | bump minor |
| `pydantic-settings` | `>=2.7,<3` | bump minor |
| `sqlalchemy[asyncio]` | `>=2.0,<3` | bump minor |
| `asyncpg` | `>=0.30,<1` | bump minor |
| `alembic` | `>=1.14,<2` | bump minor |
| `dramatiq[redis]` | `>=1.17,<2` | bump minor |
| `redis` | `>=5.2,<6` | bump minor |
| `orjson` | `>=3.10,<4` | bump minor |
| `PyJWT[crypto]` | `>=2.10,<3` | bump minor |
| `httpx` | `>=0.28,<1` | bump minor |
| `boto3` | `>=1.35,<2` | bump minor |
| `resend` | `>=2.0,<3` | bump minor |
| `jinja2` | `>=3.1,<4` | bump minor |
| `structlog` | `>=24.4,<25` | **major check** — current is 25.x; if so, bump within `<26` |
| `sentry-sdk[fastapi]` | `>=2.19,<3` | bump minor |
| `livekit-api` | `>=1.0,<2` | bump minor |
| `sse-starlette` | `>=2.1,<3` | bump minor |
| `pytest` | `>=8.3,<9` | bump minor |
| `pytest-asyncio` | `>=0.25,<1` | bump minor |
| `pytest-cov` | `>=6.0,<7` | bump minor |
| `ruff` | `>=0.8,<1` | bump minor |
| `mypy` | `>=1.14,<2` | bump minor |

`structlog` 24 → 25 is permitted under "current major" rule but flagged explicitly so it isn't a stealth change.

### Frontend pin sweep
Resolve via context7/npm at implementation time. Major-version locks NOT crossed: `next` (16), `react` (19), `@livekit/components-react`, `vitest`, `eslint`.

Both `frontend/app/` and `frontend/admin/` follow the same recipe.

### Audit gates (mandatory, per CLAUDE.md)
1. `pip-audit` on regenerated lockfile — zero criticals.
2. `npm audit --omit=dev` on each frontend lockfile — zero criticals.
3. Critical CVE → block merge. High CVE → document in PR body.

### Verification gates
1. ✅ `uv lock` + `npm install` (both apps) regenerate lockfiles without errors.
2. ✅ `docker compose up --build` from clean state.
3. ✅ Full pytest backend suite green.
4. ✅ Full vitest suite green for both `frontend/app` and `frontend/admin`.
5. ✅ `npm run build` succeeds for both apps.
6. ✅ `pip-audit` clean.
7. ✅ `npm audit --omit=dev` clean for both apps.
8. ✅ **Manual browser smoke-test** — recruiter dashboard, candidate full flow (invite → pre-check → consent → OTP → start → engine → end → completion screen), admin app login. Use Claude-in-Chrome tools.
9. ✅ `_assert_rls_completeness` passes.

### Risk + rollback
- **Risk:** *low.* Patch/minor only.
- **Rollback:** revert `pyproject.toml` + lockfile (and equivalent frontend files). Per-app rollback.

### Sub-commits
| Commit | Subject |
|---|---|
| 5a | Backend: bulk bump every minor pin; regenerate `uv.lock`; run tests |
| 5b | Frontend `app/`: bulk bump; regenerate lockfile; run vitest + build |
| 5c | Frontend `admin/`: same |
| 5d | Run pip-audit + npm audit (both apps); fix or document CVEs |
| 5e | Manual browser smoke-test attestation in PR body |

PR body must include `uv pip list --outdated` (and `npm outdated`) delta showing bumped vs not-bumped — enterprise-grade transparency.

---

## Cross-Cutting Concerns

### Test strategy

Per-phase verification is in each phase. Cross-phase additions:

- **`tests/test_startup_integrity.py`** (added Phase 1, extended thereafter): asserts `Base.registry.configure()` resolves cleanly, `_assert_rls_completeness` passes, OTel TracerProvider is registered.
- **End-to-end candidate-flow integration test** — already partial; extended in Phase 3 to assert no `/api/internal/*` HTTP calls during a session.

Coverage gates from CLAUDE.md preserved: 80% line / 100% branch on auth, middleware, session, database paths.

### Consolidated risk register

| # | Risk | Phase | Severity | Mitigation |
|---|---|---|---|---|
| 1 | OTel exporter unset → no live LLM dashboard until a sink is wired | 1 | Low (accepted trade) | structlog correlation_id; Console exporter for dev; documented in CLAUDE.md |
| 2 | Pydantic model validators using openai 1.x exception types break on 2.x | 2 | Med | context7-verify exception hierarchy; update `_PERMANENT_EXCEPTIONS`; integration test |
| 3 | Instructor 2.x API reshape breaks `app/ai/client.py` | 2 | Med | context7-verify factory signature; smoke test |
| 4 | Python 3.13 breaks an unforeseen package | 2 | Low | Build image first as smoke test; pin to last-good if a wheel is missing |
| 5 | Engine dispatch fails after merge → candidate can't start interview | 3 | **HIGH** | Staging cutover; manual interview smoke; integration test; pre-flight smoke harness |
| 6 | RLS-only defense for engine dispatch is weaker than HMAC-JWT | 3 | Low (accepted) | Threat model documented; if LiveKit creds leak, re-add signed dispatch envelope |
| 7 | Migration 0025 drops tables; rollback during in-flight session loses dispatch state | 3 | Med | Run rollback during quiet window; downgrade path is structural-only |
| 8 | SQLAlchemy mapper-config silently fails after model split | 4 | Med | Explicit `Base.registry.configure()` at startup; `test_startup_integrity.py` |
| 9 | Late-import hoist surfaces a real circular dep | 4 | Low | Resolve per file; lift shared logic if cyclic |
| 10 | Public-API enforcement test fails for legitimate intra-module imports | 4 | Low | Test logic excludes intra-module; review carefully |
| 11 | Patch/minor bump introduces upstream regression | 5 | Low | Per-bump test pass; isolate breakage; pin to last-good if needed |
| 12 | Critical CVE on a dep we can't easily upgrade | 5 | Variable | Block merge; escalate; pin around it with documented exception |
| 13 | Frontend `npm audit` flag triggers an unrelated dep tangle | 5 | Low | Separate frontend bumps from backend bumps |

### Rollback summary

Each phase is a separately-mergeable PR. Cross-phase rollback caveats:
- Phase 1 reverts cleanly even after Phase 2 has merged.
- Phase 3 rollback after Phase 4 has shipped is complicated (Phase 4 assumed Phase 3 deletions). **Don't ship Phase 4 until Phase 3 has soaked ≥1 week in production.**
- Phase 4 reverts cleanly even after Phase 5 has merged (Phase 5 is dep bumps only).

### Frontend impact attestation (frozen contract, Q5)

Surfaces that MUST stay byte-compatible through Phases 1–4:

| Surface | Where consumed |
|---|---|
| `/api/auth/*` | `frontend/app/lib/api/auth.ts` |
| `/api/admin/*` | `frontend/admin/lib/api/*` |
| `/api/jobs/*`, `/api/jd/*` SSE | recruiter dashboard |
| `/api/jobs/{id}/banks/*` SSE | question-bank UI |
| `/api/candidates/*`, `/api/candidates/kanban` | candidates module |
| `/api/scheduler/*` | invites UI |
| `/api/candidate-session/{token}/*` | candidate flow |
| `/api/sessions/*` | recruiter dashboard |
| `/api/org-units/*`, `/api/roles`, `/api/settings/team/*` | settings UI |
| LiveKit room name format, candidate token TTL | `<AgentSessionView_01>` |
| `session_outcome` participant attribute | `use-session-outcome.ts` |
| Progress attributes (`current_question_index`, `total_questions`, `time_remaining_seconds`) | `<ProgressBanner>` |

`/api/internal/*` is NOT in this list — frontend never consumed it. Phase 3 deletes it safely.

Phase 5 is the only phase touching frontend code (dep bumps). Phases 1–4 do not touch the frontend.

### Documentation updates per phase

| Phase | Updates |
|---|---|
| 1 | `nexus/CLAUDE.md` AI Provider section: replace langfuse references with OTel; remove "self-hosted only" guard; remove the `app/ai/realtime.py` AIConfig carve-out comment that mentioned langfuse |
| 2 | `nexus/CLAUDE.md` Python version note; pyproject pin documentation |
| 3 | `nexus/CLAUDE.md`: delete two-venv hack section; replace with single-image three-process model; add `interview_engine` module to module table; update Phase 3C.2 status. **`docs/security/threat-model.md`**: update for engine-dispatch trust-boundary change. Delete `backend/interview_engine/AGENTS.md`. **Root `CLAUDE.md`**: update Phase 3C.2 status row to "engine merged into nexus monolith." |
| 4 | `nexus/CLAUDE.md`: add "Module public API" section explaining `__init__.py` exports + `test_module_boundaries.py` enforcement |
| 5 | `nexus/README.md` minor updates if any package upgrades change behavior; `frontend/app/AGENTS.md` and `frontend/admin/AGENTS.md` if any frontend pin family changes |

### Calendar realism

Estimated work-days: ~13 (3+2+3+3+2). Calendar time:
- All phases without inter-phase soak: ~3 weeks.
- Phase 3 soaks 1 week, others back-to-back: ~4 weeks.
- Every phase soaks 1 week: ~5 weeks.

Recommended cadence: **Phase 3 soaks ≥1 week before Phase 4 ships**, given the 99.95% candidate-session SLA. Other phases can be back-to-back if desired.

---

## Open Items for Implementation Plan

Items the implementation plan will resolve concretely (the spec defines the architecture; the plan defines the line-by-line):

1. Exact pin versions for `openai`, `instructor`, `opentelemetry-instrumentation-openai-v2` (context7-verified at implementation time).
2. Exact frontend pin sweep list per `package.json`.
3. `nexus-engine` health check mechanism (process liveness vs LiveKit registration check).
4. `get_bypass_db()` Request-coupling check (verify it doesn't read `request.state`; refactor if it does).
5. Resolution of any latent circular import surfaced by hoisting late imports (Phase 4b).
6. Resolution of any `instructor.exceptions.InstructorRetryException` relocation in 2.x.

---

**End of design.**
