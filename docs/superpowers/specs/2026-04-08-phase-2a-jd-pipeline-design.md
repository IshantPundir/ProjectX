# Phase 2A — Foundation, JD Pipeline & Signal Extraction

**Date:** 2026-04-08
**Status:** Approved
**Scope:** Backend (`backend/nexus/`, `backend/supabase/`) + Frontend (`frontend/app/`) + Root docs
**Successor phases:** 2B (chip editing + re-enrichment), 2C (question bank + template review), 2D (state machine lock + ATS stub)

---

## Summary

Phase 2A builds the first end-to-end slice of the interview instrument: a recruiter at a client tenant can complete their Company Profile (new 4-field schema), paste a raw JD plus optional project scope, and watch an asynchronous OpenAI call populate a read-only three-panel review showing the original JD, the enriched JD, and the extracted+inferred signal chips with provenance tagging. Navigation away and back to the same JD works. Nothing is editable in 2A beyond the Company Profile and the paste form itself — chip editing, re-enrichment, and question-bank generation are deferred to 2B and 2C.

Underneath the feature, 2A establishes the load-bearing infrastructure that every subsequent phase relies on:

- Provider-agnostic AI layer (`app/ai/`) wrapping OpenAI via `instructor` and `langfuse.openai`
- Env-driven `AIConfig` (no hardcoded model names or reasoning efforts)
- File-system versioned `PromptLoader` (`prompts/v1/`)
- Dramatiq worker infrastructure (first real async job in the system)
- SSE status streaming endpoint pattern
- shadcn/ui bootstrap on the frontend
- TanStack Query + React Hook Form + Zod + `@microsoft/fetch-event-source` frontend stack

The quality of 2B / 2C / 2D is gated by whether these foundations are correct.

---

## Goals

1. **Company Profile capture** — four required fields (`about`, `industry`, `company_stage`, `hiring_bar`) stored on `organizational_units.company_profile` for `company` and `client_account` units. Blocks JD creation until complete.
2. **Raw JD upload** — plain text paste form (no file upload, no rich text). Archived immediately as `description_raw`, never mutated.
3. **Call 1 signal extraction + JD enhancement** — single atomic OpenAI call via `instructor`, structured output of enriched JD + signals with per-chip provenance (`ai_extracted` / `ai_inferred` / `recruiter` — the latter unused in 2A but the schema carries it). Async via Dramatiq. Retries with exponential backoff; final failure transitions the job posting to `signals_extraction_failed`.
4. **Three-panel read-only review UI** — original JD (collapsible drawer below 1440px), enriched JD (center), signals (sticky right panel with provenance-colored chips). Skeleton loading state driven by SSE status events. Error banner + retry on failure.
5. **Navigable JD list** — recruiter can list, view, create, and retry JDs across sessions. Not just a one-shot flow.
6. **Production-grade infrastructure** — Dramatiq worker service, SSE status stream, Langfuse tracing, structured audit log, RLS on every new table, state machine guards with audit trail.
7. **Documentation** — root CLAUDE.md, backend CLAUDE.md, frontend CLAUDE.md updated as part of the deliverable. New `docs/phase-2a-implementation.md` mirrors the existing phase-1 doc.

## Non-Goals

- Chip editing (add / edit / delete signal chips) — **2B**
- Call 2 re-enrichment (delta-aware prose streaming) — **2B**
- Signal confirmation (versioned immutable snapshot) — **2B**
- Session configuration UI (duration, depth, mandatory signals, thresholds) — **2B/2C**
- Question bank generation (Call 3, `question_bank_generation.txt` prompt, `session_templates` / `evaluation_topics` / `question_bank_items` tables) — **2C**
- HM review workflow — **2C**
- Template approval + `screening_active` state machine lock — **2D**
- ATS adapter implementation beyond the Phase 1 stub — **2D** (stub already exists at `backend/nexus/app/modules/ats/adapter.py`)
- Candidates, sessions runtime, scoring — **Phase 3**
- File upload (PDF, DOCX) for JD or project scope — post-MVP
- Zustand global store — deferred to 2B when signals become editable
- Vitest / frontend test infrastructure — deferred to 2B (see Deferred Hardening)

---

## Decisions & Rationale

This spec is the product of 16 explicit clarifying questions answered by the user during brainstorming. Each decision is listed here with its rationale so future phases can challenge or extend it without re-litigating.

| # | Decision | Rationale |
|---|---|---|
| 1 | Brainstorming scope limited to **Phase 2A only** | Vision doc already decomposes Phase 2 into 2A/2B/2C/2D. Each sub-phase gets its own spec → plan → implementation cycle. Attempting to design all four as one spec produces something too large to review or adapt. |
| 2 | Company Profile UI lives in the **client app** (`frontend/app/`), not the internal admin app | The `frontend/admin/` surface is an internal ProjectX ops tool (provisioning clients) — explicitly not client-facing. The person filling in Company Profile is a Super Admin at a tenant, using the client dashboard. The vision's "admin app" phrasing was loose. |
| 3 | `prompts/` directory lives at **`backend/nexus/prompts/`** | Only the backend consumes prompts. Colocation simplifies the Docker build (prompts are part of the image) and matches the existing pattern for email templates (`app/modules/notifications/templates/`). |
| 4 | `AIConfig` is **env-driven via `pydantic-settings`** with defaults in code | Swapping a model or adjusting reasoning effort is a `.env` change, never a code change. Model IDs in the vision (`gpt-5.2`, `gpt-5.4-mini`) are placeholders until production deployment confirms real IDs. |
| 5 | **Langfuse wired up in 2A** as a drop-in OpenAI wrapper (`from langfuse.openai import AsyncOpenAI`) | Zero marginal code cost because it's a drop-in. No-op when `LANGFUSE_HOST` is empty, so dev contributors aren't forced to run a local Langfuse instance. Full tracing, token usage, and prompt versioning from day 1. |
| 6 | **Dramatiq worker runs as a separate docker-compose service** reusing the same image | Standard Dramatiq pattern. Independent scaling; worker crashes don't take down the API. Maps cleanly to Railway (second service) and ECS Fargate (second task definition). First real use of Dramatiq in the system. |
| 7 | **SSE authenticates via `Authorization: Bearer <jwt>` header** through `@microsoft/fetch-event-source` | Native `EventSource` can't set headers, but `fetch-event-source` can. Backend `AuthMiddleware` validates via JWKS exactly like every other endpoint — no special code path. Matches Phase 1's `apiFetch()` pattern. |
| 8 | **shadcn/ui introduced in 2A** | Three-panel review needs polished primitives (Skeleton, Badge, Tooltip, Button, Input, Textarea, Select, Dialog, Toast, Tabs, Form, Alert). Establishes the pattern once; every future phase benefits. Phase 2 is the planned introduction per `frontend/app/CLAUDE.md`. |
| 9 | **Zustand deferred to 2B** | 2A state needs are local: form state (React Hook Form), server state (TanStack Query), and transient review state (local `useState`). No truly global UI state crosses routes in 2A. YAGNI. |
| 10 | **Hard cutover on Company Profile** — null any existing profile that doesn't match the new 4-field shape, force re-entry | Pre-MVP dev data. Phase 1 writes a different 6-field shape (`display_name`, `industry`, `company_size`, `culture_summary`, `strong_hire`, `brand_voice`). `company_size` → `company_stage` is not a rename (headcount vs growth phase — values can't be auto-transformed). Preserving is worse than nuking. |
| 11 | **Job postings can attach to any non-root unit** whose ancestry contains a completed `company_profile` | Most flexible; matches "agency mode" where recruiters post under `client_account` → `division` → `team` hierarchies. AI reads the profile from the closest ancestor (`company` or `client_account`) that has one. |
| 12 | **Full minimum loop** of endpoints ships in 2A: `POST`, `GET list`, `GET single`, `GET status/stream`, `POST retry` | Without list + single-get, the recruiter can create a JD and never get back to it if they close the tab. `jobs.view` is added to `ALL_PERMISSIONS` and seeded to Admin + Recruiter + Hiring Manager system roles. |
| 13 | **Only `jd_enhancement.txt` is written in 2A** — no stubs for 2B/2C prompt files | Stub files invite stale placeholder text. Each phase writes its own prompt when context is fresh. Empty dir except `v1/jd_enhancement.txt`. |
| 14 | **Three-panel layout: Original JD collapses to a vertical drawer below 1440px**; Enriched + Signals stay full-width | Primary review surface is always visible. Custom Tailwind breakpoint `3xl: 1440px`. |
| 15 | **Signal chips use subtle tinted-fill style** with a small leading color-dot; `ai_inferred` chips get a dashed border and an `inference_basis` tooltip on hover | Enterprise-quiet visual, distinguishable at a glance without legend lookup. Dashed border carries all the "uncertain, verify me" weight for inferred chips. |
| 16 | **Company Profile form lives in a dedicated "Company Profile" tab** on the org unit detail page (only visible for `company` and `client_account` units) | Cleanest separation; the form gets its own space. Existing Phase 1 details tab is not polluted. Same form component is reused in the onboarding wizard step 2 rewrite. |
| 17 | **Skeleton loading state: content-aware** — status pill at top of center panel driven by SSE events; section labels (Role Summary, Required Skills, Must-Haves) pre-rendered as static text | Transition from skeleton to real content feels like filling in blanks instead of replacing the view. User understands what is being computed. |

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 16 client app)                                          │
│                                                                            │
│  /settings/org-units/[id]/company-profile  ←─ 4-field form (RHF + Zod)    │
│  /onboarding                               ←─ step 2 rewritten             │
│  /jobs                                     ←─ list                         │
│  /jobs/new                                 ←─ paste form                   │
│  /jobs/[id]                                ←─ three-panel review           │
│                                                    │                       │
│                  uses: TanStack Query + fetch-event-source + shadcn/ui    │
└────────────────────────────────────────────────────────────────────────────┘
                        │                             │
              HTTP (JSON)│                  SSE (EventSource over fetch)
                        │                             │
┌────────────────────────────────────────────────────────────────────────────┐
│  Backend (FastAPI — single container)                                      │
│                                                                            │
│  app/modules/jd/                                                           │
│    router.py        POST /api/jobs                                         │
│                     GET  /api/jobs                                         │
│                     GET  /api/jobs/{id}                                    │
│                     GET  /api/jobs/{id}/status/stream  (SSE, 1.5s poll)    │
│                     POST /api/jobs/{id}/retry                              │
│    service.py       create_job_posting, get_job, list_jobs, retry          │
│    actors.py        @dramatiq.actor extract_and_enhance_jd                 │
│    state_machine.py LEGAL_TRANSITIONS + transition() helper                │
│    authz.py         require_job_access() — ancestry walk                   │
│    errors.py        sanitize_error_for_user() — safe status_error strings  │
│    sse.py           event generator (poll job_postings.status every 1.5s)  │
│                                                                            │
│  app/ai/                                                                   │
│    config.py        AIConfig (env-driven)                                  │
│    client.py        get_openai_client() — instructor + langfuse.openai     │
│    prompts.py       PromptLoader (file-system, cached)                     │
│    schemas.py       ExtractionOutput Pydantic model                        │
│                                                                            │
│  app/worker.py      Dramatiq broker + actor registration                   │
└────────────────────────────────────────────────────────────────────────────┘
       │                                                     │
       │ asyncpg (SQLAlchemy async)                          │ Redis broker
       │                                                     │
┌────────────────────────────────────┐       ┌──────────────────────────────┐
│  Supabase (managed Postgres)       │       │  Dramatiq worker container   │
│  + RLS (app.current_tenant)        │       │  dramatiq app.worker         │
│  + job_postings                    │       │  --processes 2 --threads 4   │
│  + job_posting_signal_snapshots    │       │  queue: jd_extraction        │
│  + sessions (stub, no candidate FK)│       └──────────────────────────────┘
│  + organizational_units (mutated)  │                      │
└────────────────────────────────────┘                      │
                                                            │ httpx (OpenAI SDK)
                                                            ▼
                                           ┌────────────────────────────────┐
                                           │  OpenAI API (via langfuse.openai)│
                                           │  → gpt-5.2 @ effort=medium     │
                                           │  → instructor + strict schema  │
                                           └────────────────────────────────┘
                                                            │
                                                            │ (traces)
                                                            ▼
                                           ┌────────────────────────────────┐
                                           │  Langfuse (self-hosted, opt.)  │
                                           │  no-op if LANGFUSE_HOST empty  │
                                           └────────────────────────────────┘
```

---

## Data Model & Migrations

Three Supabase SQL migrations, run in order. Alembic stays empty (deferred indefinitely until cloud deployment per vision).

### Migration 1 — `20260410000000_phase_2a_company_profile_reset.sql`

```sql
-- Add tracking columns
ALTER TABLE organizational_units
    ADD COLUMN company_profile_completed_at TIMESTAMPTZ,
    ADD COLUMN company_profile_completed_by UUID REFERENCES users(id);

-- Hard cutover: null any profile that doesn't match the new 4-field shape.
-- Pre-MVP dev data only. App-layer validation enforces the rest (character
-- limits, enum values, required for company/client_account).
UPDATE organizational_units
   SET company_profile = NULL
 WHERE company_profile IS NOT NULL
   AND NOT (
        company_profile ? 'about'
    AND company_profile ? 'industry'
    AND company_profile ? 'company_stage'
    AND company_profile ? 'hiring_bar'
   );

-- Note: we deliberately do NOT add a CHECK constraint on the JSONB structure.
-- Constraints on JSONB are too brittle for future schema evolution;
-- validation lives in app/modules/org_units/schemas.py and service.py.
```

### Migration 2 — `20260410000001_phase_2a_job_postings.sql`

```sql
-- State machine states (documented; stored as TEXT for easy extension):
--   draft
--   signals_extracting
--   signals_extraction_failed
--   signals_extracted
-- (Future: signals_confirmed, template_generating, template_draft,
--  hm_review_pending, hm_reviewed, template_approved, screening_active, closed)

-- ----------------------------------------------------------------------------
-- updated_at trigger function (created fresh in 2A — Phase 1 has a latent gap:
-- updated_at columns exist across Phase 1 tables but are never updated on
-- UPDATE because no trigger function was ever created. Fixing Phase 1 tables
-- retroactively is out of scope for 2A and listed in Deferred Hardening, but
-- 2A tables get the correct behavior from the start.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE job_postings (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES clients(id),
    org_unit_id               UUID NOT NULL REFERENCES organizational_units(id),
    title                     TEXT NOT NULL,
    description_raw           TEXT NOT NULL,
    project_scope_raw         TEXT,
    description_enriched      TEXT,                    -- populated by Call 1
    enriched_manually_edited  BOOLEAN NOT NULL DEFAULT FALSE,
    status                    TEXT NOT NULL DEFAULT 'draft',
    status_error              TEXT,                    -- last error on _failed states
    source                    TEXT NOT NULL DEFAULT 'native',
    external_id               TEXT,                    -- nullable, ATS reference (2D+)
    target_headcount          INTEGER,
    deadline                  DATE,
    -- session_template_id added in 2C; omitted here
    created_by                UUID NOT NULL REFERENCES users(id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_job_postings_tenant_org_unit ON job_postings (tenant_id, org_unit_id);
CREATE INDEX idx_job_postings_status          ON job_postings (tenant_id, status);
CREATE INDEX idx_job_postings_created_at      ON job_postings (tenant_id, created_at DESC);

-- updated_at trigger — fires BEFORE UPDATE, stamps NOW() on every modification
CREATE TRIGGER set_job_postings_updated_at
    BEFORE UPDATE ON job_postings
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- RLS — mandatory per backend CLAUDE.md
ALTER TABLE job_postings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON job_postings
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON job_postings
  USING (current_setting('app.bypass_rls', true) = 'true');

-- ----------------------------------------------------------------------------

CREATE TABLE job_posting_signal_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES clients(id),
    job_posting_id        UUID NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    version               INTEGER NOT NULL,            -- 1 = initial extraction
    required_skills       JSONB NOT NULL,              -- [{value, source, inference_basis|null}]
    preferred_skills      JSONB NOT NULL,
    must_haves            JSONB NOT NULL,
    good_to_haves         JSONB NOT NULL,
    min_experience_years  INTEGER NOT NULL,
    seniority_level       TEXT NOT NULL,               -- junior|mid|senior|lead|principal
    role_summary          TEXT NOT NULL,
    confirmed_by          UUID REFERENCES users(id),   -- nullable in 2A
    confirmed_at          TIMESTAMPTZ,                 -- nullable in 2A
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (job_posting_id, version)
);

CREATE INDEX idx_signal_snapshots_job_posting
    ON job_posting_signal_snapshots (job_posting_id, version DESC);

ALTER TABLE job_posting_signal_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON job_posting_signal_snapshots
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON job_posting_signal_snapshots
  USING (current_setting('app.bypass_rls', true) = 'true');

-- ----------------------------------------------------------------------------

-- sessions stub — defined now so Phase 3 FKs have a parent.
-- candidate_id column exists but NO FK constraint until Phase 3 creates
-- the candidates table.
CREATE TABLE sessions (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                 UUID NOT NULL REFERENCES clients(id),
    job_posting_id            UUID NOT NULL REFERENCES job_postings(id),
    candidate_id              UUID,                    -- FK deferred to Phase 3
    -- session_template_id added in 2C
    status                    TEXT NOT NULL DEFAULT 'scheduled',
    started_at                TIMESTAMPTZ,
    completed_at              TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON sessions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
CREATE POLICY "service_role_bypass" ON sessions
  USING (current_setting('app.bypass_rls', true) = 'true');
```

### Migration 3 — `20260410000002_phase_2a_jobs_view_permission.sql`

```sql
-- Add jobs.view to Admin, Recruiter, and Hiring Manager system roles.
-- ALL_PERMISSIONS frozenset in app/modules/auth/permissions.py gets
-- updated in the same commit.

UPDATE roles
   SET permissions = permissions || '["jobs.view"]'::jsonb
 WHERE is_system = TRUE
   AND name IN ('Admin', 'Recruiter', 'Hiring Manager')
   AND NOT (permissions ? 'jobs.view');
```

### Data Model Decisions

1. **`tenant_id` is duplicated on every new table.** RLS policies need a column on the table itself — joining through `job_postings` to check `tenant_id` defeats RLS. Same pattern Phase 1 uses on `user_role_assignments`.
2. **`status_error TEXT`** — new column not in the vision. Stores the user-facing error message when a Dramatiq job fails after retries, so the frontend renders it in the retry banner without a separate error table.
3. **`draft` is a persisted state** but never observed by users. `POST /api/jobs` creates the row in `draft`, the service immediately transitions it to `signals_extracting` and enqueues the actor in the same DB transaction. User sees a single click, single state.
4. **`min_experience_years`, `seniority_level`, `role_summary` are `NOT NULL`** on snapshots. The Call 1 actor only writes snapshot rows on successful extraction, and `instructor` retries on Pydantic validation failure. If the model output is still invalid after retries, the actor transitions to `signals_extraction_failed` without writing a snapshot row.
5. **`confirmed_by` / `confirmed_at` nullable in 2A.** 2B populates these on explicit signal confirmation. 2A writes snapshot rows without a user confirmation action because chip editing doesn't exist yet.
6. **No prompt-version column on `job_posting_signal_snapshots`.** Prompt versioning is file-system based (`prompts/v1/`). Langfuse stores the prompt version in trace metadata. DB-side column deferred to 2B if needed.

---

## Backend Design

### `app/ai/` — Provider-agnostic AI layer

**`app/ai/config.py`**

```python
"""Env-driven AI configuration. Single source of truth for model IDs and
reasoning effort. Never hardcode a model name or effort level elsewhere."""

from app.config import settings


class AIConfig:
    @property
    def extraction_model(self) -> str:
        return settings.openai_extraction_model

    @property
    def extraction_effort(self) -> str:
        return settings.openai_extraction_effort

    # Future properties (2B/2C/Phase 3):
    # reenrichment_model / reenrichment_effort
    # generation_model / generation_effort
    # session_model / session_effort
    # scoring_model / scoring_effort


ai_config = AIConfig()
```

**`app/ai/client.py`**

```python
"""OpenAI client factory. Business logic imports get_openai_client() — never
imports openai or langfuse.openai directly. This is the load-bearing
abstraction that makes 'swap OpenAI for something else' a config change."""

from functools import lru_cache

import instructor
from langfuse.openai import AsyncOpenAI   # drop-in tracer; no-op if LANGFUSE_HOST empty

from app.config import settings


@lru_cache(maxsize=1)
def get_openai_client() -> instructor.AsyncInstructor:
    raw = AsyncOpenAI(api_key=settings.openai_api_key)
    return instructor.from_openai(raw, mode=instructor.Mode.TOOLS_STRICT)
```

**`app/ai/prompts.py`**

```python
"""PromptLoader — reads prompts/v{N}/<name>.txt at first access, caches in
memory. A future /api/admin/prompts/reload endpoint can bust the cache
without a restart (not in 2A)."""

from pathlib import Path

import structlog

logger = structlog.get_logger()
PROMPTS_ROOT = Path(__file__).parent.parent.parent / "prompts"


class PromptLoader:
    def __init__(self, version: str = "v1") -> None:
        self._version = version
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = PROMPTS_ROOT / self._version / f"{name}.txt"
            self._cache[name] = path.read_text(encoding="utf-8")
            logger.info(
                "prompts.loaded",
                name=name,
                version=self._version,
                chars=len(self._cache[name]),
            )
        return self._cache[name]


prompt_loader = PromptLoader()
```

**`app/ai/schemas.py`** — strict Pydantic output schema for Call 1

```python
from typing import Literal

from pydantic import BaseModel, Field


class SignalItem(BaseModel):
    value: str
    source: Literal["ai_extracted", "ai_inferred"]   # never "recruiter" from the model
    inference_basis: str | None = Field(
        default=None,
        description="Required when source='ai_inferred', else null",
    )


class ExtractedSignals(BaseModel):
    required_skills: list[SignalItem]
    preferred_skills: list[SignalItem]
    must_haves: list[SignalItem]
    good_to_haves: list[SignalItem]
    min_experience_years: int = Field(ge=0, le=50)
    seniority_level: Literal["junior", "mid", "senior", "lead", "principal"]
    role_summary: str = Field(min_length=10, max_length=2000)


class ExtractionOutput(BaseModel):
    enriched_jd: str = Field(min_length=50)
    signals: ExtractedSignals
```

### `app/modules/jd/` — Business logic

**API prefix convention: `/api/jobs` — matching Phase 1.**

Every Phase 1 router uses `/api/<module>` without any `/v1/` segment (verified across `auth`, `admin`, `settings`, `org-units`, `roles`, and the existing `jd/router.py` stub). The original vision document showed `/api/v1/jd/...` in some prose examples — that was an inconsistency with Phase 1, not a deliberate versioning strategy. **All 2A endpoints use `/api/jobs`**. If API versioning is introduced later, it will be applied as a cross-cutting migration to all modules at once, not one phase at a time.

**`app/modules/jd/router.py`** (selected endpoints)

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", status_code=201)
async def create_job(
    body: JobPostingCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingResponse:
    # Requires jobs.create on body.org_unit_id or ancestor
    ...


@router.get("")
async def list_jobs(
    org_unit_id: UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[JobPostingSummary]:
    # Filters to units where user has jobs.view in ancestry
    ...


@router.get("/{job_id}")
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingWithSnapshot:
    await require_job_access(db, job_id, user, "view")
    ...


@router.get("/{job_id}/status/stream")
async def stream_status(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> EventSourceResponse:
    await require_job_access(db, job_id, user, "view")
    # Delegates to app/modules/jd/sse.py — see below
    return EventSourceResponse(job_status_event_generator(db, job_id, request))


@router.post("/{job_id}/retry", status_code=202)
async def retry_extraction(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    await require_job_access(db, job_id, user, "manage")
    await retry_failed_extraction(db, job_id)
```

**`app/modules/jd/sse.py`** — event generator

The SSE event generator lives in its own module, not inline in the router. Rationale: keeps `router.py` focused on request/response orchestration; makes the generator unit-testable without spinning up FastAPI; the pattern is reusable for any future SSE endpoint (Call 2 re-enrichment in 2B, question bank generation status in 2C) so the contract (signature, termination semantics, polling cadence) is documented in one place.

```python
"""Server-Sent Events generator for job posting status updates.

Contract:
- Polls the job_postings row every POLL_INTERVAL_SECONDS (1.5s).
- Emits a 'status' event ONLY when job.status changes from the last observed
  value (de-duplication; no redundant frames).
- Terminates and closes the HTTP connection when job.status reaches a
  terminal state (signals_extracted or signals_extraction_failed).
- Terminates immediately if the client disconnects mid-stream.
- Does NOT enforce RBAC — the router's require_job_access() dependency has
  already validated access before this generator is invoked.
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.jd.service import get_job_status
from app.modules.jd.schemas import JobStatusEvent

POLL_INTERVAL_SECONDS: float = 1.5
TERMINAL_STATES: frozenset[str] = frozenset({
    "signals_extracted",
    "signals_extraction_failed",
})


async def job_status_event_generator(
    db: AsyncSession,
    job_id: UUID,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events until the job reaches a terminal state or the
    client disconnects. Each event is a dict shaped for sse-starlette's
    EventSourceResponse: {'event': 'status', 'data': <json string>}."""
    last_status: str | None = None
    while True:
        if await request.is_disconnected():
            return

        event: JobStatusEvent = await get_job_status(db, job_id)

        if event.status != last_status:
            yield {
                "event": "status",
                "data": event.model_dump_json(),
            }
            last_status = event.status

        if event.status in TERMINAL_STATES:
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

**Router error handling** — two custom exception types from the JD module are caught at the FastAPI layer and mapped to specific HTTP status codes. They MUST NEVER propagate as 500.

Both exception classes live in `app/modules/jd/errors.py` alongside `sanitize_error_for_user()` (single errors module for the module — not split across files):

```python
# app/modules/jd/errors.py (excerpt — full file also contains sanitize_error_for_user)

class CompanyProfileIncompleteError(Exception):
    """Raised by create_job_posting() when no ancestor of the target org unit
    has a completed company_profile. Mapped to HTTP 422 at the router layer."""

    def __init__(self, org_unit_id: UUID) -> None:
        self.org_unit_id = org_unit_id
        super().__init__(
            f"Org unit {org_unit_id} has no ancestor with a completed company profile"
        )


class IllegalTransitionError(Exception):
    """Raised when code attempts an illegal state transition.
    Mapped to HTTP 409 Conflict at the router layer with a state-specific message.
    (Also defined in app/modules/jd/state_machine.py — re-exported here for the
    exception handler to import alongside CompanyProfileIncompleteError.)"""
```

**FastAPI exception handlers** — registered in `app/main.py` after router registration:

```python
# app/main.py (additions)

from fastapi import Request
from fastapi.responses import JSONResponse

from app.modules.jd.errors import (
    CompanyProfileIncompleteError,
    IllegalTransitionError,
)


# Router-layer 409 mapping with state-specific messages.
# New (from, to) pairs extend this dict as phases add states.
_ILLEGAL_TRANSITION_MESSAGES: dict[tuple[str, str], str] = {
    ("signals_extracting", "signals_extracting"):
        "Job is already being processed",
    ("signals_extracted", "signals_extracting"):
        "This job has already been extracted successfully — "
        "retry is only valid after an extraction failure",
    ("draft", "signals_extracted"):
        "Job cannot transition directly from draft to extracted",
}


@application.exception_handler(IllegalTransitionError)
async def illegal_transition_handler(
    request: Request, exc: IllegalTransitionError
) -> JSONResponse:
    key = (exc.from_state, exc.to_state)
    detail = _ILLEGAL_TRANSITION_MESSAGES.get(
        key,
        f"Cannot transition job from {exc.from_state} to {exc.to_state}",
    )
    return JSONResponse(status_code=409, content={"detail": detail})


@application.exception_handler(CompanyProfileIncompleteError)
async def company_profile_incomplete_handler(
    request: Request, exc: CompanyProfileIncompleteError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": (
                "Company profile must be completed before creating a job description. "
                "Visit Settings → Org Units → [your company] → Company Profile to finish setup."
            ),
            "org_unit_id": str(exc.org_unit_id),
        },
    )
```

The `org_unit_id` is included in the 422 body so the frontend can deep-link directly to the correct Company Profile tab. Frontend treats the message as authoritative; it does not construct its own error text from the error code.

**`app/modules/jd/service.py`** — key signatures

```python
async def create_job_posting(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    created_by: UUID,
    org_unit_id: UUID,
    title: str,
    description_raw: str,
    project_scope_raw: str | None,
    target_headcount: int | None,
    deadline: date | None,
) -> JobPosting:
    """Atomic:
    1. Validate that company_profile is complete on the closest ancestor
       (company or client_account) via find_company_profile_in_ancestry().
    2. Create job_posting row in 'draft'.
    3. Transition to 'signals_extracting' via state_machine.transition().
    4. Write audit_log row.
    5. Enqueue extract_and_enhance_jd Dramatiq actor.
    6. Commit.
    Returns the created row.

    Raises:
        CompanyProfileIncompleteError — 422 at router layer
        IllegalTransitionError        — 409 at router layer (should never fire
                                        here since draft → signals_extracting
                                        is always legal; defensive)
    """


async def get_job_posting_with_latest_snapshot(
    db: AsyncSession, job_id: UUID
) -> JobPostingWithSnapshot: ...


async def list_job_postings(
    db: AsyncSession,
    user: UserContext,
    org_unit_id: UUID | None = None,
    status: str | None = None,
) -> list[JobPostingSummary]:
    """List jobs the user can view.

    IMPLEMENTATION NOTE — verify before implementing:
    A user may hold 'jobs.view' in multiple org units via different role
    assignments (e.g., recruiter in Division A AND in Division B). The
    listing query must return jobs visible in the UNION of all such
    ancestries, not just the first one.

    Two possible shapes:
      1. WHERE job.org_unit_id IN (<flat list of user's visible units +
         all descendants of those units>) — requires computing the set
         of visible unit IDs up-front via a recursive CTE.
      2. EXISTS correlated subquery against user_role_assignments walking
         ancestry per row — simpler query but O(N) per row.

    Option 1 is strictly better for pagination and count queries.
    Pick option 1 unless profiling says otherwise. The set of visible
    unit IDs is small (usually <100) so the IN clause is fine."""


async def retry_failed_extraction(db: AsyncSession, job_id: UUID) -> None:
    """Precondition: job.status == 'signals_extraction_failed'.
    Transitions to 'signals_extracting' via state_machine.transition()
    and re-enqueues the actor."""


async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Walk ancestry from org_unit_id up to root, return the first
    company_profile dict encountered. None if no ancestor has one."""
```

**`app/modules/jd/actors.py`** — Dramatiq actor for Call 1

```python
import dramatiq
import structlog
from dramatiq.middleware import CurrentMessage
from sqlalchemy import text

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.ai.schemas import ExtractionOutput

logger = structlog.get_logger()


@dramatiq.actor(
    max_retries=3,
    min_backoff=2_000,      # 2s
    max_backoff=60_000,     # 60s, jittered
    queue_name="jd_extraction",
)
async def extract_and_enhance_jd(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
    )

    async with get_bypass_db() as db:
        # Actor has no HTTP context; re-set app.current_tenant for RLS
        await db.execute(
            text("SET LOCAL app.current_tenant = :t"),
            {"t": tenant_id},
        )

        job = await get_job_posting_for_update(db, job_posting_id)
        if job.status != "signals_extracting":
            log.warn("jd.actor.skip_unexpected_state", state=job.status)
            return

        profile = await find_company_profile_in_ancestry(db, job.org_unit_id)

        try:
            client = get_openai_client()
            prompt = prompt_loader.get("jd_enhancement")
            result: ExtractionOutput = await client.chat.completions.create(
                model=ai_config.extraction_model,
                reasoning_effort=ai_config.extraction_effort,
                response_model=ExtractionOutput,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": _build_user_message(job, profile)},
                ],
                metadata={
                    "correlation_id": correlation_id,
                    "job_posting_id": job_posting_id,
                    "tenant_id": tenant_id,
                    "prompt_version": "v1",
                },
            )
        except Exception as exc:
            # Log full exception (exc_info) to structlog — this goes to stdout/Sentry
            # and is safe because log sinks are trusted.
            log.error("jd.actor.call1_failed", exc_info=exc)
            current = CurrentMessage.get_current_message()
            retries_so_far = current.options.get("retries", 0) if current else 0
            if retries_so_far >= 2:
                # Final attempt — transition to _failed and commit.
                # IMPORTANT: sanitize the exception before storing it in
                # job_posting.status_error. The raw str(exc) may contain API URLs,
                # keys, request IDs, or internal paths that must never reach the
                # frontend. sanitize_error_for_user() maps exception TYPES to
                # fixed user-facing strings; unrecognized types become the
                # generic "Extraction failed — please retry".
                safe_message = sanitize_error_for_user(exc)
                await mark_extraction_failed(db, job_posting_id, safe_message)
                await db.commit()
            raise   # Dramatiq retries on all non-final exceptions

        await persist_enriched_jd_and_snapshot(db, job, result)
        await db.commit()
        log.info("jd.actor.completed")


def _build_user_message(job: JobPosting, profile: dict | None) -> str:
    """Construct the user message for Call 1.

    CRITICAL ORDERING: context before document.
    1. Company profile (stable context)
    2. Raw JD (the document being enriched)
    3. Project scope (if present)

    Rationale: the model must read the frame before reading the thing being
    framed. Context-first primes the model correctly from the first token.
    """
    parts: list[str] = []
    if profile:
        parts.append(
            "## Company Profile\n"
            f"- About: {profile['about']}\n"
            f"- Industry: {profile['industry']}\n"
            f"- Company stage: {profile['company_stage']}\n"
            f"- Hiring bar: {profile['hiring_bar']}\n"
        )
    parts.append(f"## Raw Job Description\n\n{job.description_raw}\n")
    if job.project_scope_raw:
        parts.append(f"## Project Scope\n\n{job.project_scope_raw}\n")
    return "\n".join(parts)
```

**Failure transition timing:** the state transition to `signals_extraction_failed` happens **only on the final retry attempt**. Intermediate retries leave the row in `signals_extracting` so the frontend SSE stream keeps polling without showing "failed" flicker that self-heals on retry.

**`app/modules/jd/errors.py`** — custom exceptions + error sanitization helper

This file owns **three** responsibilities: the two JD-specific exception classes (`IllegalTransitionError`, `CompanyProfileIncompleteError`) and the `sanitize_error_for_user()` helper that maps third-party exception types to safe user-facing strings. Co-locating them keeps the "what can the JD module raise, and what reaches the user" surface area readable.

```python
"""JD module exceptions and user-facing error sanitization.

All JD-specific exception types live here, along with the sanitize helper
used by the Dramatiq actor to convert third-party exceptions into safe,
fixed user-facing strings before persisting them to job_posting.status_error.

Why sanitize? The raw str(exc) from an OpenAI / HTTP / validation failure
may leak:
- API URLs (https://api.openai.com/v1/chat/completions)
- API keys or bearer tokens embedded in headers
- Request IDs that identify internal infrastructure
- Stack-trace fragments with file paths
- Prompt content (on validation errors, instructor may echo the payload)

Rich exception detail is still captured in structlog / Sentry — we only
sanitize what reaches the DB and the frontend."""

from typing import Final
from uuid import UUID

import openai
from instructor.core import InstructorRetryException  # verified Day-1 Task 5 — use core path, not deprecated instructor.exceptions


# --- JD-specific exception classes ---------------------------------------

class IllegalTransitionError(Exception):
    """Raised by state_machine.transition() when a caller attempts a move
    that's not in LEGAL_TRANSITIONS. Mapped to HTTP 409 Conflict by the
    exception handler in app/main.py."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Illegal transition: {from_state} → {to_state}")


class CompanyProfileIncompleteError(Exception):
    """Raised by create_job_posting() when no ancestor of the target org unit
    has a completed company_profile. Mapped to HTTP 422 Unprocessable Entity
    by the exception handler in app/main.py, with org_unit_id in the body so
    the frontend can deep-link to the correct Company Profile tab."""

    def __init__(self, org_unit_id: UUID) -> None:
        self.org_unit_id = org_unit_id
        super().__init__(
            f"Org unit {org_unit_id} has no ancestor with a completed company profile"
        )


# --- Error sanitization for job_posting.status_error ---------------------
#
# Verified Day-1 (Task 5): use instructor.core.InstructorRetryException.
# instructor.exceptions.InstructorRetryException is deprecated in v1.12.0
# and will be removed in a future version. Both are the same class object,
# but the non-deprecated path must be used to avoid startup warnings.

_SAFE_MESSAGES: Final[dict[type[Exception], str]] = {
    openai.RateLimitError:
        "Our AI provider is rate-limiting us. Please retry in a minute.",
    openai.APITimeoutError:
        "The AI provider timed out. Please retry.",
    openai.APIConnectionError:
        "Could not reach the AI provider. Please retry.",
    openai.AuthenticationError:
        "AI provider authentication failed. Contact support.",
    openai.BadRequestError:
        "The job description could not be processed. Please check the input and retry.",
    InstructorRetryException:  # from instructor.core import InstructorRetryException
        "The AI response did not match the expected format after retries. Please retry.",
}

_DEFAULT_MESSAGE: Final[str] = (
    "Extraction failed — please retry. Contact support if this persists."
)


def sanitize_error_for_user(exc: Exception) -> str:
    """Return a safe user-facing message for the given exception.

    Never returns str(exc) or any fragment of the exception args — only
    fixed strings from the mapping above."""
    for exc_type, message in _SAFE_MESSAGES.items():
        if isinstance(exc, exc_type):
            return message
    return _DEFAULT_MESSAGE
```

The mapping starts minimal. New exception types are added as they're observed in production (via the unmapped-default branch, logged with exception type at WARN).

**`app/modules/jd/state_machine.py`**

```python
"""Single source of truth for job_posting.status transitions.

Every code path that mutates job_posting.status MUST go through transition().
No exceptions, including the Dramatiq actor."""

from typing import Final

# IllegalTransitionError is defined in app/modules/jd/errors.py so that
# exception handlers and other modules can import it without pulling in
# the state machine module itself.
from app.modules.jd.errors import IllegalTransitionError


LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},  # retry
    "signals_extracted": set(),  # terminal in 2A; 2B adds signals_confirmed
    # Future states (2B/2C/2D) added here:
    # "signals_confirmed", "template_generating", ...
}


async def transition(
    db: AsyncSession,
    job: JobPosting,
    *,
    to_state: str,
    actor_id: UUID | None,
    correlation_id: str,
) -> None:
    """Atomically transition job.status and write an audit_log row.
    Caller is responsible for db.commit()."""
    if to_state not in LEGAL_TRANSITIONS.get(job.status, set()):
        raise IllegalTransitionError(job.status, to_state)

    from_state = job.status
    job.status = to_state
    await write_audit_log(
        db,
        action="job_posting.status_changed",
        resource="job_posting",
        resource_id=job.id,
        actor_id=actor_id,
        payload={
            "from": from_state,
            "to": to_state,
            "correlation_id": correlation_id,
        },
    )
```

### `app/worker.py` — Dramatiq entrypoint

```python
"""Dramatiq worker entrypoint. Run via:

    dramatiq app.worker --processes 2 --threads 4

Imports every actor module so Dramatiq registers them at startup."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.config import settings

broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)

# Actors must be imported so they register with the broker
from app.modules.jd import actors as _jd_actors  # noqa: F401, E402
```

### `docker-compose.yml` addition

```yaml
  nexus-worker:
    build:
      context: .
      dockerfile: Dockerfile
    env_file: [.env]
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@host.docker.internal:54322/postgres
      - REDIS_URL=redis://redis:6379/0
      - SUPABASE_JWKS_URL=http://host.docker.internal:54321/auth/v1/.well-known/jwks.json
    extra_hosts: ["host.docker.internal:host-gateway"]
    depends_on:
      redis: { condition: service_healthy }
    volumes: [".:/app"]
    command: dramatiq app.worker --processes 2 --threads 4 --watch /app/app
```

### `app/config.py` updates

```python
# Remove: anthropic_api_key

# Add:
openai_api_key: str = ""

# AI model selection — env-driven, swappable without code change
openai_extraction_model: str = "gpt-5.2"
openai_extraction_effort: str = "medium"
# Future: openai_reenrichment_model, openai_generation_model, etc.
```

### SSE endpoint design decisions

**Polling over pub/sub.** SSE is driven by a 1.5s DB poll, not Redis pub/sub. Rationale: the only events 2A emits are state transitions that all land in the DB. Pub/sub would add a second source of truth and a race window between "Redis event fires" and "DB row is visible." Polling is dumb but correct. At 1.5s intervals per active stream, load is negligible for 2A (a few dozen concurrent streams max).

**Terminal states close the stream.** After emitting `signals_extracted` or `signals_extraction_failed`, the generator returns and the connection closes. Frontend then GETs `/api/jobs/{id}` for the full payload (including the snapshot).

**Event payload shape:**
```json
{"status": "signals_extracting",
 "job_id": "...", "error": null, "signal_snapshot_version": null}

{"status": "signals_extracted",
 "job_id": "...", "error": null, "signal_snapshot_version": 1}

{"status": "signals_extraction_failed",
 "job_id": "...", "error": "OpenAI rate limit exceeded", "signal_snapshot_version": null}
```

---

## Frontend Design

### Dependencies & bootstrap

Install in `frontend/app/`:

```bash
# Server state, forms, SSE
npm install @tanstack/react-query @tanstack/react-query-devtools \
            react-hook-form @hookform/resolvers zod \
            @microsoft/fetch-event-source

# shadcn/ui bootstrap — style 'new-york', neutral base, zinc accent
npx shadcn@latest init
npx shadcn@latest add button input textarea select label separator \
                      badge skeleton dialog tooltip toast sonner \
                      tabs card form alert
```

**AGENTS.md warning enforced:** before writing any new route or layout file, implementation MUST consult `node_modules/next/dist/docs/` for current Next 16 App Router conventions. Don't rely on training-data knowledge of Next.js.

**Custom Tailwind breakpoint** in `tailwind.config.ts`:
```typescript
theme: {
  extend: {
    screens: {
      '3xl': '1440px',  // three-panel review transitions at this width
    },
  },
},
```

**TanStack Query provider placement:** a new `<DashboardProviders>` client component wraps children in the `QueryClientProvider`. The auth-check server component in `app/(dashboard)/layout.tsx` stays server-side; providers live one layer down as a client boundary. Unauthenticated routes (`/login`, `/invite`) don't pay the bundle cost.

```typescript
// app/(dashboard)/providers.tsx — NEW
'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

export function DashboardProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: { staleTime: 10_000, refetchOnWindowFocus: false },
    },
  }))
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
```

### Component structure

```
frontend/app/
├── components/
│   ├── ui/                              ← shadcn primitives (auto-generated)
│   └── dashboard/
│       ├── company-profile-form.tsx     ← 4-field form, shared between
│       │                                  onboarding and settings tab
│       └── jd-panels/
│           ├── OriginalJdPanel.tsx
│           ├── EnrichedJdPanel.tsx
│           ├── SignalsPanel.tsx
│           ├── SignalChip.tsx           ← provenance-aware
│           ├── LoadingSkeleton.tsx      ← content-aware, with status pill
│           └── ErrorBanner.tsx          ← retry button
├── lib/
│   ├── api/
│   │   ├── client.ts                    ← existing apiFetch() — unchanged
│   │   └── jobs.ts                      ← NEW typed namespace
│   ├── auth/
│   │   └── tokens.ts                    ← NEW — getFreshSupabaseToken() helper
│   └── hooks/
│       ├── use-job-status-stream.ts     ← NEW — fetch-event-source wrapper
│       └── use-job.ts                   ← NEW — TanStack Query wrapper
└── app/(dashboard)/
    ├── providers.tsx                    ← NEW — DashboardProviders
    ├── layout.tsx                       ← existing — wraps children in providers
    ├── jobs/                            ← NEW route group
    │   ├── page.tsx                     ← list
    │   ├── new/page.tsx                 ← paste form
    │   └── [jobId]/page.tsx             ← three-panel review
    ├── onboarding/page.tsx              ← rewritten (new 4-field profile step)
    └── settings/org-units/[unitId]/
        ├── page.tsx                     ← existing — adds tab navigation
        └── company-profile/page.tsx     ← NEW tab (conditionally rendered)
```

### Company Profile form

**Shared Zod schema** — matches backend enums exactly. A backend unit test asserts the Python enum list equals this Zod list (via a JSON fixture committed in `backend/nexus/tests/fixtures/company_profile_enums.json`).

```typescript
// components/dashboard/company-profile-form.tsx — top of file

import { z } from 'zod'

export const INDUSTRY_OPTIONS = [
  'fintech_financial_services', 'healthcare_medtech', 'ecommerce_retail',
  'ai_ml_products', 'saas_enterprise_software', 'developer_tools_infrastructure',
  'agency_consulting_staffing', 'media_content', 'logistics_supply_chain', 'other',
] as const

export const COMPANY_STAGE_OPTIONS = [
  'pre_seed_seed', 'series_a_b', 'series_c_plus', 'large_enterprise',
] as const

export const companyProfileSchema = z.object({
  about: z.string()
    .min(30, 'Describe what you build in at least a sentence')
    .max(500, 'Keep it concise — 500 characters max'),
  industry: z.enum(INDUSTRY_OPTIONS),
  company_stage: z.enum(COMPANY_STAGE_OPTIONS),
  hiring_bar: z.string()
    .min(20, 'Describe what a strong hire looks like')
    .max(280, 'Twitter-length — 280 characters max'),
})

export type CompanyProfile = z.infer<typeof companyProfileSchema>
```

**Form UX:**
- Live character counters on `about` (xxx / 500) and `hiring_bar` (xxx / 280)
- Submit button disabled until the full schema validates
- Tooltips on `about` and `hiring_bar` with concrete examples from the vision doc
- Industry and company_stage rendered as shadcn `<Select>` with human-readable labels
- Error banner at top on API failure
- Shared between onboarding step 2 and the settings tab; consumer controls the submit handler

### JD creation flow

**`/jobs` (list)**
- TanStack Query fetches `GET /api/jobs`
- Table: title, org unit, status (badge), created at, actions
- Empty state with "Create your first JD" CTA linking to `/jobs/new`
- Sortable by status and created_at

**`/jobs/new` (paste form)**
- Pre-check: fetches user's accessible org units, filters to those with completed `company_profile` in ancestry. Blocks the form if the list is empty, shows an inline link to Settings → Org Units → [unit] → Company Profile.
- Fields: title, org unit selector, description (textarea, required), project scope (textarea, optional), target headcount (number, optional), deadline (date, optional)
- React Hook Form + Zod validation
- Submit: `POST /api/jobs`. On 201, `router.push(\`/jobs/${id}\`)`.
- No client-side state survives transition.

**`/jobs/[jobId]` (three-panel review)**

Two data sources:

1. **TanStack Query** — full job payload from `GET /api/jobs/{id}`. Cached, invalidated on status change.
2. **SSE status stream** — `useJobStatusStream(jobId)` hook that connects to `/api/jobs/{id}/status/stream`. On every status event, local state updates AND `queryClient.invalidateQueries(['jobs', jobId])` fires so the cached payload refreshes.

**Supporting module — `lib/auth/tokens.ts`:**

```typescript
// Fetches the current Supabase access token, refreshing if necessary.
// Used by useJobStatusStream and the apiFetch() client.

import { createClient } from '@/lib/supabase/client'

export async function getFreshSupabaseToken(): Promise<string> {
  const supabase = createClient()
  const { data, error } = await supabase.auth.getSession()
  if (error || !data.session) {
    throw new Error('No active Supabase session')
  }
  return data.session.access_token
}
```

No in-memory caching layer. `@supabase/ssr` already caches the session in cookies and auto-refreshes on expiry; re-calling `getSession()` is cheap. Adding a second cache layer would risk serving a stale token.

**Supporting hook — `lib/hooks/use-job.ts`:**

```typescript
// TanStack Query wrapper for a single job. Used by the /jobs/[jobId] page.

import { useQuery } from '@tanstack/react-query'
import { jobsApi, JobPostingWithSnapshot } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJob(jobId: string) {
  return useQuery<JobPostingWithSnapshot>({
    queryKey: ['jobs', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.get(token, jobId)
    },
    enabled: !!jobId,
    staleTime: 5_000,
  })
}
```

Query keys follow the pattern `['jobs', jobId]` so `useJobStatusStream` can invalidate them directly on every SSE event. No Zustand, no cross-hook coordination — the cache is the single source of truth.

**SSE hook — `lib/hooks/use-job-status-stream.ts`:**

```typescript
// Corrected pattern — token fetched before opening SSE connection.
// async/await CANNOT be used inside a synchronous object literal,
// so we fetch the token in a .then() before calling fetchEventSource.

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJobStatusStream(jobId: string) {
  const [status, setStatus] = useState<JobStatusEvent | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    const ctrl = new AbortController()

    // Fetch the token first (async), then open the SSE connection.
    // Cannot await inside a sync object literal.
    getFreshSupabaseToken().then((token) => {
      if (ctrl.signal.aborted) return
      fetchEventSource(`${API_URL}/api/jobs/${jobId}/status/stream`, {
        signal: ctrl.signal,
        headers: { Authorization: `Bearer ${token}` },
        onmessage(ev) {
          const payload = JSON.parse(ev.data) as JobStatusEvent
          setStatus(payload)
          queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
        },
        onerror(err) {
          // fetch-event-source auto-retries with backoff; don't throw unless fatal
          console.warn('SSE error', err)
        },
      })
    })

    return () => ctrl.abort()
  }, [jobId, queryClient])

  return status
}
```

**Three render states in `/jobs/[jobId]/page.tsx`:**

1. `draft | signals_extracting` → `<LoadingSkeleton>` with the status pill bound to the SSE event (content-aware skeleton per Q16).
2. `signals_extracted` → `<ThreePanelReview>`:
   - `<OriginalJdPanel>` (left, full column above 1440px; collapsible vertical drawer below)
   - `<EnrichedJdPanel>` (center, read-only in 2A)
   - `<SignalsPanel>` (right, sticky, chips use subtle provenance style from Q14)
3. `signals_extraction_failed` → `<ErrorBanner>` across the top of the center panel with `status_error` text and a Retry button that POSTs `/api/jobs/{jobId}/retry`.

### Typed API client (`lib/api/jobs.ts`)

Replaces per-page inline typing for the jobs module:

```typescript
import { apiFetch } from './client'

export type SignalItem = {
  value: string
  source: 'ai_extracted' | 'ai_inferred' | 'recruiter'
  inference_basis: string | null
}

export type JobPostingSummary = {
  id: string
  title: string
  org_unit_id: string
  status: JobStatus
  created_at: string
  updated_at: string
}

export type SignalSnapshot = {
  version: number
  required_skills: SignalItem[]
  preferred_skills: SignalItem[]
  must_haves: SignalItem[]
  good_to_haves: SignalItem[]
  min_experience_years: number
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}

export type JobPostingWithSnapshot = JobPostingSummary & {
  description_raw: string
  project_scope_raw: string | null
  description_enriched: string | null
  status_error: string | null
  latest_snapshot: SignalSnapshot | null
}

export type JobStatus =
  | 'draft' | 'signals_extracting'
  | 'signals_extraction_failed' | 'signals_extracted'

export type JobStatusEvent = {
  status: JobStatus
  job_id: string
  error: string | null
  signal_snapshot_version: number | null
}

export const jobsApi = {
  list: (token: string, orgUnitId?: string) =>
    apiFetch<JobPostingSummary[]>('/api/jobs', { token, params: { org_unit_id: orgUnitId } }),
  get: (token: string, id: string) =>
    apiFetch<JobPostingWithSnapshot>(`/api/jobs/${id}`, { token }),
  create: (token: string, body: CreateJobBody) =>
    apiFetch<JobPostingWithSnapshot>('/api/jobs', { token, method: 'POST', body }),
  retry: (token: string, id: string) =>
    apiFetch<void>(`/api/jobs/${id}/retry`, { token, method: 'POST' }),
}
```

---

## State Machine Enforcement

Enforcement lives in **three places — defense in depth**:

1. **Python state machine module** (`app/modules/jd/state_machine.py`) — single source of truth (`LEGAL_TRANSITIONS`) plus the `transition()` helper that raises `IllegalTransitionError`. Every path mutating `job_posting.status` must use it.
2. **Service-layer guards** — `retry_failed_extraction()` explicitly checks `job.status == 'signals_extraction_failed'`; `create_job_posting()` seeds `'draft'` then calls `transition()` to advance atomically.
3. **Audit log rows** — every transition writes an `audit_log` entry with `action='job_posting.status_changed'` and payload `{from, to, correlation_id}`. Forensic record for any unexpected state.

**Router error handling contract:** the FastAPI exception handler for `IllegalTransitionError` returns **HTTP 409 Conflict** with a human-readable message keyed on the `(from_state, to_state)` pair. It never propagates as 500. Required message mapping:

| From state | Attempted transition | 409 message |
|---|---|---|
| `signals_extracting` | `signals_extracting` (retry pressed twice) | `"Job is already being processed"` |
| `signals_extracted` | `signals_extracting` (retry on a completed job) | `"This job has already been extracted successfully — retry is only valid after an extraction failure"` |
| `draft` | `signals_extracted` (defensive; should never fire) | `"Job cannot transition directly from draft to extracted"` |
| fallback | any | `"Cannot transition job from {from} to {to}"` |

The mapping lives in a small dispatch dict inside the exception handler, keyed on `(exc.from_state, exc.to_state)`. New phases extend the dict as they add transitions.

**Not enforced at the DB layer in 2A.** A Postgres trigger or enum-based CHECK constraint is overkill when the app is the only writer and the transition set is small. Revisit in 2C when the state machine grows to 11+ states.

---

## Authorization

### New permission

`jobs.view` added to `ALL_PERMISSIONS` in `app/modules/auth/permissions.py`. Seeded into Admin, Recruiter, and Hiring Manager system roles via Migration 3.

### `require_job_access()` helper

```python
# app/modules/jd/authz.py

async def require_job_access(
    db: AsyncSession,
    job_id: UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> JobPosting:
    """Load the job, walk the org unit ancestry, check whether the user has
    the required permission in any ancestor. Raises HTTPException(403) if not.
    Returns the loaded job row so callers don't re-fetch.

    Super Admin short-circuits the ancestry walk (same pattern as Phase 1)."""

    job = await get_job_posting(db, job_id)
    if user.is_super_admin:
        return job

    permission = f"jobs.{action}"
    ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
    if not any(user.has_permission_in_unit(u.id, permission) for u in ancestry):
        raise HTTPException(
            status_code=403,
            detail=f"Missing {permission} in job's org unit ancestry",
        )
    return job
```

### Day-1 verification — critical before implementation

**Before any code lands in `authz.py`, implementation MUST verify whether Phase 1's `UserContext.has_permission_in_unit()` already walks the ancestry or only checks the exact unit ID.**

- If it **already walks ancestry** → the design above works as written; the `ancestry` walk in `require_job_access()` is belt-and-braces but safe.
- If it **only checks the exact unit** → `require_job_access()` must be the **primary** enforcement path, and `UserContext.has_permission_in_unit()` becomes a helper that's called per-ancestor inside the local walk.

Getting this wrong means correct permission grants silently fail 403. This is **Implementation Plan Task 1** — before any new code is written.

---

## Observability & Correlation

### Correlation ID flow

```
HTTP request (POST /api/jobs)
  ├── AuthMiddleware generates request_id, binds to structlog.contextvars
  ├── Correlation ID = request_id
  ↓
Service (create_job_posting)
  ├── Writes job_posting row (correlation_id NOT stored on row in 2A)
  ├── Dispatches Dramatiq actor with correlation_id in message payload
  ↓
Dramatiq actor (extract_and_enhance_jd)
  ├── Re-binds correlation_id to structlog context
  ├── Passes correlation_id in OpenAI call's metadata={...}
  ↓
Langfuse
  ├── Traces OpenAI call with correlation_id as trace attribute
  ├── Full request/response + token usage captured
  ↓
SSE status stream
  └── JobStatusEvent does NOT include correlation_id in 2A
      (add in 2B if helpful for client-side error reports)
```

### Langfuse configuration

Lives in `app/ai/client.py`. When `settings.langfuse_host` is empty, the `langfuse.openai` import acts as a passthrough — no API calls to any Langfuse backend. Ops provision a real Langfuse instance when they want observability; no code changes required.

### structlog

Already configured in `app/main.py` — no changes needed. Actor imports and binds in the same way as the API handlers.

### Sentry

Already in `pyproject.toml` with env var slots but not wired in Phase 1. **2A does not wire Sentry** — explicitly flagged as deferred hardening. Entire observability trail in 2A depends on structlog + Langfuse.

---

## Testing Strategy

### Backend (pytest — already configured)

| Layer | What's tested |
|---|---|
| **Unit** | `state_machine.transition()` — every legal + illegal transition. `ExtractionOutput` Pydantic validation. `PromptLoader` cache behavior. `CompanyProfile` enum coverage — one test asserting the Python enum list equals the frontend Zod enum list via `tests/fixtures/company_profile_enums.json`. `_build_user_message()` ordering — asserts company profile comes before raw JD comes before project scope. |
| **Integration (happy path)** | Full `create_job_posting()` + `extract_and_enhance_jd()` actor flow with a **mocked OpenAI client**. Asserts: row created in `draft`, transitioned to `signals_extracting`, snapshot persisted on success, transitioned to `signals_extracted`. Tests both the RLS path (`get_tenant_db`) and the bypass path (`get_bypass_db`). |
| **Integration (failure path)** | Mock OpenAI to raise; assert actor retries 3 times; on final retry, `signals_extraction_failed` row state + `status_error` populated. Intermediate retries do not flip state. |
| **Integration (RBAC)** | Endpoint-level tests for every JD route with three user contexts: super admin, recruiter in the job's org unit, recruiter in a sibling unit. Assert 200 / 200 / 403. |
| **Integration (SSE)** | Test client consuming the SSE endpoint. Mock DB state transitions across iterations; assert event stream order matches (`signals_extracting` → `signals_extracted`) and terminal state closes the connection. |
| **Integration (409 handling)** | POST /retry on a `signals_extracting` job → assert 409 with message. POST /retry on `signals_extracted` → assert 409 with message. |

### Frontend

**Vitest is NOT installed in 2A.** Phase 1 frontend has zero tests. Installing Vitest + adding a test layer is a separate concern that deserves its own design and a cross-cutting cleanup PR.

**Deferred to 2B** — by then, chip editing (the first truly interactive surface) justifies the infrastructure investment. 2A risk is limited because the UI is mostly render-only.

### Manual E2E acceptance (pre-ship checklist)

1. Create a tenant, super-admin signs in, completes onboarding with the new 4-field company profile (validation errors fire correctly).
2. Navigate to Settings → Org Units → [company unit] → Company Profile tab; verify edit + save round-trips.
3. Paste a real JD on `/jobs/new`, submit, watch the content-aware skeleton with the status pill cycle through states via SSE.
4. Land on `/jobs/[id]` three-panel view. Verify:
   - Original JD collapses to a vertical drawer below 1440px (use browser dev tools to resize).
   - Enriched JD renders in center, preserving sections per the prompt template.
   - Signal chips render with correct provenance colors (blue solid, amber dashed, green solid).
   - `ai_inferred` chip tooltip shows `inference_basis` on hover.
5. Trigger a failure path (stub `OPENAI_API_KEY` to empty, re-submit a JD); verify the error banner appears with the error message and a working retry button.
6. Click retry; verify recovery path works and the three-panel view renders correctly.
7. Close the browser tab, reopen, navigate to `/jobs`, click the JD; verify the review loads from cached DB state.
8. Create a second user in a sibling org unit; verify they cannot see the first user's JD (403).

---

## Documentation Deliverables

All four updates are **part of the Phase 2A deliverable** — not a followup.

### 1. `CLAUDE.md` (root)

- Replace `Anthropic Claude API` → `OpenAI API` in the "Two-Tier Architecture Philosophy" table (both `LLM async` and `LLM real-time` rows).
- Add a sentence to "Hard Rules": *"AI provider is OpenAI for the entire system. All LLM calls go through the `app/ai/` module, never directly to the SDK from business logic."*

### 2. `backend/nexus/CLAUDE.md`

- Replace all `Anthropic` / `Claude API` references with `OpenAI`.
- Add a new section **"AI Provider & Prompt Management"** documenting `app/ai/` responsibilities, `AIConfig` env-driven pattern, `PromptLoader` file-system-versioned prompts, Langfuse wrapping, `instructor` for structured output.
- Update "Module Structure" tree to add `app/ai/`, `app/worker.py`, `prompts/v1/`.
- Update "Phase 1 — Implemented" tables; add a new "Phase 2A — Implemented" section covering `jd`, `ai`, and the Dramatiq worker infrastructure.
- Update "Dev Commands" to include `docker compose up nexus-worker` and `dramatiq app.worker` examples.

### 3. `backend/nexus/docs/phase-2a-implementation.md` (NEW)

Mirrors the structure of the existing `phase-1-implementation.md`. Implementation-level walkthrough covering:

- Module responsibilities (`jd`, `ai`)
- Data flow through Call 1
- How to add a new prompt version (create `prompts/v2/` directory, bump loader version, A/B test)
- How to swap the OpenAI model for a task via env vars
- Troubleshooting — common failure modes and which logs to look at
- How to run the worker in dev, staging, and production

### 4. `frontend/app/CLAUDE.md`

- Move shadcn/ui, TanStack Query, React Hook Form, Zod, `@microsoft/fetch-event-source` from "Planned for Phase 2+" → "Currently Installed (Phase 2A)".
- Document `components/dashboard/` conventions, the shadcn `components/ui/` directory (auto-generated — do not edit), the custom `3xl: 1440px` breakpoint, and the `DashboardProviders` TanStack Query provider placement decision.
- Add a note: "AGENTS.md rule still applies — consult `node_modules/next/dist/docs/` before writing new route or layout files."

---

## Deferred Hardening (Known Gaps)

Items consciously accepted as 2A gaps, documented so future phases can pick them up.

| # | Gap | Rationale | Fix in |
|---|---|---|---|
| 1 | **Frontend testing infrastructure** | Vitest setup + `company-profile-form` tests + `useJobStatusStream` tests. Phase 1 frontend has zero tests; adding infra deserves its own design. 2A UI is mostly render-only. | 2B |
| 2 | **SSE token expiry mid-stream** | Relies on Supabase token staying valid for the extraction window. Low risk at <30s (extraction duration). Becomes load-bearing in 2B when chip editing keeps the connection open far longer. Fix: token refresh + SSE reconnect loop. | 2B |
| 3 | **Migration lint CI check** | No automated CI job currently fails when a table is missing RLS. Manual-review discipline until then. Backend CLAUDE.md already flags this as a known gap from Phase 1. | Cross-cutting cleanup PR |
| 4 | **Sentry initialization** | Not wired in 2A (Phase 1 also not wired). Observability trail depends on structlog + Langfuse alone. | Cross-cutting cleanup PR |
| 5 | **Prompt version provenance in DB** | Langfuse holds prompt version in trace metadata. DB-side column on `job_posting_signal_snapshots` (e.g., `prompt_version TEXT`) deferred until a concrete use case appears. | 2B if needed |
| 6 | **`/api/admin/prompts/reload` endpoint** | `PromptLoader` supports hot-reload conceptually; endpoint not built in 2A. Restart the worker to reload. | When A/B testing begins |
| 7 | **Per-tenant OpenAI API key isolation** | Single OpenAI key for the entire backend. Enterprise-mode per-tenant key support deferred. | Enterprise tier |
| 8 | **Correlation ID stored on `job_posting` row** | Langfuse has it via trace metadata, audit log has it. No DB column yet. Add if forensic search from the DB becomes necessary. | 2B if needed |
| 9 | **Dual-write risk: DB commit + Dramatiq enqueue are not atomic** | `create_job_posting()` commits the row and then enqueues the actor. If the enqueue call fails (Redis down, network partition) AFTER the commit succeeds, the row sits in `signals_extracting` forever with no actor to process it and **no automatic recovery in 2A**. The state machine only permits `signals_extraction_failed → signals_extracting`, and the retry endpoint's precondition is `status == 'signals_extraction_failed'` — a `signals_extracting` row cannot be rescued through the public API. **Manual recovery procedure in 2A:** operator uses direct DB access to run `UPDATE job_postings SET status = 'signals_extraction_failed', status_error = 'Stuck in extraction — manual reset' WHERE id = ...`, after which the recruiter can hit `POST /api/jobs/{id}/retry` from the UI. **Partial mitigation that IS in place:** the actor's idempotency guard (`if job.status != 'signals_extracting': return`) prevents double-processing if a duplicate dispatch ever reaches it. **Not mitigated:** (a) no detection of stuck jobs — operators must be told by users that something is broken; (b) no automatic timeout that flips `signals_extracting` to `signals_extraction_failed` after N minutes; (c) no background janitor that re-enqueues orphaned jobs. **Future fix paths:** (1) transactional outbox pattern — write an `outbox` row in the same DB transaction as the job_posting insert, a background worker drains the outbox and enqueues actors; (2) periodic sweep task that finds `signals_extracting` rows older than N minutes and transitions them to `signals_extraction_failed`; (3) API-level retry that explicitly allows `signals_extracting → signals_extracting` as an idempotent re-dispatch with admin-only RBAC. | Post-MVP hardening |
| 10 | **`updated_at` column frozen on Phase 1 tables** | Phase 1 has no trigger function for `updated_at` across `clients`, `users`, `organizational_units`, and other tables. Those columns stamp on INSERT via `DEFAULT NOW()` but never update on UPDATE. 2A's Migration 2 creates `public.set_updated_at()` as a reusable function and applies it to `job_postings`. Retrofitting Phase 1 tables is a small Supabase migration that should ship as a cross-cutting cleanup. | Cross-cutting cleanup PR |

---

## Day-1 Verification Tasks (for the implementation plan)

These MUST be the first tasks in the implementation plan before any new code is written:

### Task 1 — Verify `UserContext.has_permission_in_unit()` ancestry behavior

**Why:** The `require_job_access()` helper in `app/modules/jd/authz.py` assumes one of two possibilities — either Phase 1's helper already walks ancestry, or we must walk it ourselves. Getting this wrong means correct permission grants silently 403.

**How:** Read `app/modules/auth/context.py`. Trace `has_permission_in_unit()` against `user_role_assignments`. Write a targeted pytest case that creates a recruiter with a role on unit A, then asserts whether `has_permission_in_unit(child_of_A.id, "jobs.view")` returns True or False.

**If it returns True** (ancestry inheritance exists): the design above works as written. `require_job_access()` still walks ancestry defensively.

**If it returns False** (exact-match only): `require_job_access()` becomes the **primary** enforcement path. `UserContext.has_permission_in_unit()` is called per-ancestor inside the local walk. Update the spec and the implementation accordingly before writing any JD endpoint.

**Verification result (2026-04-09):**
- `has_permission_in_unit()` inheritance behavior: False
- Implementation observation from `app/modules/auth/context.py`: The method iterates over `self.assignments` and returns `True` only when `a.org_unit_id == org_unit_id` exactly matches — there is no ancestry walk or parent traversal of any kind.
- Implication: `require_job_access()` will be the primary enforcement path. The local ancestry walk in `app/modules/jd/authz.py::_get_org_unit_ancestry()` is required.

### Task 2 — Verify OpenAI model access

**Why:** The default `openai_extraction_model = "gpt-5.2"` is a placeholder. Before the first Call 1 dispatch, verify the real model ID against the OpenAI API key in the dev environment.

**How:** `curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" | jq '.data[].id'`. Pick the correct production model. Set `OPENAI_EXTRACTION_MODEL` in `.env.example` to the verified ID. Update the default in `config.py` to match.

**Verification result (2026-04-09):**
- API key confirmed working: yes
- Models available (gpt-* only, top 10): `gpt-4.1`, `gpt-4.1-2025-04-14`, `gpt-4o`, `gpt-4o-2024-11-20`, `gpt-5`, `gpt-5.2`, `gpt-5.2-2025-12-11`, `gpt-5.4`, `gpt-5.4-pro`, `gpt-5.4-pro-2026-03-05`
- Chosen `OPENAI_EXTRACTION_MODEL`: `gpt-5.2`
- Substitution from `gpt-5.2` placeholder: **no — `gpt-5.2` is available**, used as-is.
- Notes: The implementer's first pass selected `gpt-5.4-pro` per the plan's "strongest non-nano GPT-5" instruction, but the controller (Claude) overruled to align with the spec's deliberate model split: the user reserved `gpt-5.4-mini` for streaming/speed-critical tasks (re-enrichment, live session) and `gpt-5.2` for quality-critical structured outputs (extraction, generation, scoring). Spec fidelity wins over a quality upgrade. If `gpt-5.2` underperforms in production, swap via `OPENAI_EXTRACTION_MODEL=gpt-5.4-pro` in `.env` — single-line config change, no code edit. `reasoning_effort` support for `gpt-5.2` is unverified and will be probed in Task 4.

### Task 3 — Verify `langfuse.openai` drop-in import path

**Why:** The exact import path (`from langfuse.openai import AsyncOpenAI`) depends on the Langfuse SDK version already in `pyproject.toml`. SDK versions 2.x and 3.x differ on whether the wrapper lives at `langfuse.openai` or `langfuse.callback.openai` or similar.

**How:** `docker compose run nexus python -c "from langfuse.openai import AsyncOpenAI; print(AsyncOpenAI)"`. If import fails, pin the correct path and update the design before writing `app/ai/client.py`.

**Verification result (2026-04-09):**
- langfuse version: 2.60.10
- Working import: `from langfuse.openai import AsyncOpenAI` — confirmed correct. The `langfuse.openai` submodule exists and re-exports `AsyncOpenAI` from `openai` with tracing wrappers applied. The import failed in the initial probe only because `openai` was not yet installed (it is a Phase 2A dependency); once `openai>=1.60,<2` is added to `pyproject.toml`, the import resolves to `<class 'openai.AsyncOpenAI'>` wrapped by langfuse.
- Implication for Task 17: no changes needed — `client.py` should use `from langfuse.openai import AsyncOpenAI` exactly as sketched.

### Task 4 — Verify `reasoning_effort` parameter shape for the target model

**Why:** The actor sketch in this spec passes `reasoning_effort=ai_config.extraction_effort` as a top-level kwarg to `client.chat.completions.create(...)`. For GPT-5-series models the parameter **may not be a top-level kwarg** — depending on SDK version and model endpoint, it may need to go into `extra_body={"reasoning_effort": ...}` or `response_format={"reasoning_effort": ...}`, or it may only be supported on the `client.responses.create(...)` endpoint rather than `chat.completions.create`. Getting this wrong means every Call 1 returns `400 Bad Request` at runtime and the retry loop burns through all three attempts before transitioning to `signals_extraction_failed`.

**How:**
1. After Task 2 (verified model ID), run a minimal standalone script that calls the target model with `reasoning_effort="medium"` via the chosen SDK path:
   ```python
   import openai, asyncio
   async def probe():
       c = openai.AsyncOpenAI(api_key=KEY)
       r = await c.chat.completions.create(
           model="<verified_id>",
           reasoning_effort="medium",
           messages=[{"role": "user", "content": "Say hi"}],
       )
       print(r)
   asyncio.run(probe())
   ```
2. If it returns a 400 with a message about unknown parameters, try `extra_body={"reasoning_effort": "medium"}`. If the model belongs to the `responses` endpoint, switch to `client.responses.create(...)`.
3. Document the correct call shape in the Task 4 findings note **and update the actor code sketch in this spec before writing `app/modules/jd/actors.py`**.
4. Also verify the same call shape works via `instructor.from_openai()` — `instructor` wraps `chat.completions.create` by default but may need a different mode for the responses endpoint.

**Verification result (2026-04-09):**
- Working shape for `gpt-5.2` + `reasoning_effort=medium`: **top-level kwarg** — confirmed with `openai==1.109.1`.
- Sample call snippet:
  ```python
  await client.chat.completions.create(
      model="gpt-5.2",
      reasoning_effort="medium",
      messages=[{"role": "user", "content": "..."}],
  )
  ```
  Model responded with `"ready"` — no errors.
- Implication for Task 26 (actor): use top-level kwarg as-is. The actor sketch in this spec (`reasoning_effort=ai_config.extraction_effort`) is correct. No changes needed.

### Task 5 — Verify `instructor` exception class name

**Why:** `app/modules/jd/errors.py` references `instructor.exceptions.InstructorRetryException` in the `_SAFE_MESSAGES` mapping. The exact class name differs across `instructor` versions — it may be `InstructorRetryException`, `RetryException`, `IncompleteOutputException`, or `ValidationError` depending on which failure mode triggered the retry exhaustion. Referencing a wrong name at import time crashes the worker on boot; referencing a wrong name at runtime means the exception falls through to the generic "Extraction failed — please retry" message instead of the more specific "AI response did not match the expected format" message. Neither is a security issue (both are safe strings) but the import-time crash would block all Call 1 processing.

**How:**
```bash
docker compose run nexus python -c \
  "import instructor.exceptions; print(dir(instructor.exceptions))"
```

Also grep the installed package for the class that's actually raised after retry exhaustion:
```bash
docker compose run nexus python -c \
  "import inspect, instructor; print(inspect.getsourcefile(instructor))"
# then cat the exceptions module and confirm the class name raised after
# max_retries is exceeded
```

**If the class is named differently:** update the import path and the `_SAFE_MESSAGES` key in `errors.py` before writing the actor. If `instructor.exceptions` doesn't exist as a module at all (the package may have moved it), update the import accordingly.

**Verification result (2026-04-09):**
- instructor version: 1.12.0 (satisfies `>=1.7,<2`)
- Retry-exhausted class: `InstructorRetryException` — confirmed as the class raised at retry exhaustion (raised in `instructor/core/retry.py` lines 285 and 442).
- Import path status: `instructor.exceptions.InstructorRetryException` is still importable but **deprecated** in 1.12.0 with a `DeprecationWarning`: *"Importing from 'instructor.exceptions' is deprecated and will be removed in a future version. Please import from 'instructor.core' instead."*
- Required update for Task 18 (errors.py): change the import from `instructor.exceptions.InstructorRetryException` to `from instructor.core import InstructorRetryException`, and update the `_SAFE_MESSAGES` mapping key accordingly. Both classes are the same object (`instructor.exceptions.InstructorRetryException is instructor.core.InstructorRetryException` → `True`), but the `errors.py` sketch must use the non-deprecated path to avoid a startup warning that will pollute logs in production.

---

## Acceptance Criteria

Phase 2A is complete when **all of the following** are true:

**Backend**
- [ ] Three Supabase migrations land and run cleanly on a fresh DB and against the existing dev DB with Phase 1 data.
- [ ] `pyproject.toml` adds `openai`, `instructor`, `sse-starlette` dependencies; removes `anthropic`.
- [ ] `app/config.py` adds `openai_api_key`, `openai_extraction_model`, `openai_extraction_effort`; removes `anthropic_api_key`.
- [ ] `app/ai/` module exists with `config.py`, `client.py`, `prompts.py`, `schemas.py`.
- [ ] `prompts/v1/jd_enhancement.txt` exists and is loaded by `PromptLoader` at first use.
- [ ] `app/modules/jd/` implements: `router.py` (5 endpoints), `service.py`, `actors.py`, `state_machine.py`, `authz.py`, `sse.py`.
- [ ] `app/worker.py` exists; `docker-compose.yml` has the `nexus-worker` service.
- [ ] `ALL_PERMISSIONS` frozenset includes `jobs.view`; migration 3 seeds it into system roles.
- [ ] FastAPI exception handler catches `IllegalTransitionError` → 409 Conflict with the state-specific message mapping.
- [ ] Every `job_posting.status` transition writes an `audit_log` row with `action='job_posting.status_changed'` and payload containing `{from, to, correlation_id}`. Verified by an integration test that counts audit_log rows after a full happy-path flow.
- [ ] `job_postings.updated_at` correctly advances on every UPDATE (verified by a trivial integration test that reads the column before and after a status transition).
- [ ] `sanitize_error_for_user()` is called on every failure path before writing to `job_posting.status_error`. Integration test mocks `openai.RateLimitError` and asserts the stored message is the mapped string, NOT `str(exc)`.
- [ ] All backend tests listed in Testing Strategy pass.
- [ ] RLS enabled on `job_postings`, `job_posting_signal_snapshots`, `sessions` with both `tenant_isolation` and `service_role_bypass` policies.

**Frontend**
- [ ] `package.json` adds `@tanstack/react-query`, `@tanstack/react-query-devtools`, `react-hook-form`, `@hookform/resolvers`, `zod`, `@microsoft/fetch-event-source`.
- [ ] shadcn/ui is bootstrapped; the listed primitives are pulled into `components/ui/`.
- [ ] `tailwind.config.ts` adds the custom `3xl: 1440px` breakpoint.
- [ ] `DashboardProviders` client component wraps the dashboard layout.
- [ ] `components/dashboard/company-profile-form.tsx` exists and is consumed by both onboarding step 2 and the new settings tab.
- [ ] `/settings/org-units/[unitId]/company-profile/page.tsx` renders as a tab (conditionally for `company` / `client_account` units only).
- [ ] Onboarding wizard step 2 rewrites the old 6-field profile form with the new 4-field form.
- [ ] `/jobs`, `/jobs/new`, `/jobs/[jobId]` routes implemented.
- [ ] `lib/api/jobs.ts` typed namespace exists.
- [ ] `useJobStatusStream` hook uses `fetchEventSource` with the corrected pattern (token fetched first, not awaited inside object literal).
- [ ] Three-panel review renders all three states (loading / loaded / error) correctly.
- [ ] Manual E2E checklist passes.

**Documentation**
- [ ] Root `CLAUDE.md` updated.
- [ ] `backend/nexus/CLAUDE.md` updated.
- [ ] `backend/nexus/docs/phase-2a-implementation.md` exists.
- [ ] `frontend/app/CLAUDE.md` updated.

**Operational**
- [ ] `docker compose up --build` brings up nexus + nexus-worker + redis successfully.
- [ ] A new JD pasted via the frontend reaches `signals_extracted` within a reasonable time (<30s on `medium` effort).
- [ ] Langfuse tracing works when `LANGFUSE_HOST` is set; worker runs cleanly with `LANGFUSE_HOST=""`.
- [ ] Sibling org unit isolation verified manually (user in unit A cannot see JD in unit B).

---

## References

- Vision document — Phase 2 — provided in brainstorming session 2026-04-08
- Brainstorming transcript — 16 Q&A decisions captured in the "Decisions & Rationale" section above
- Phase 1 implementation — `backend/nexus/docs/phase-1-implementation.md`
- Root CLAUDE.md — `ProjectX/CLAUDE.md`
- Backend CLAUDE.md — `backend/nexus/CLAUDE.md`
- Frontend (client app) CLAUDE.md — `frontend/app/CLAUDE.md`
- Unit Types v2 design — `docs/superpowers/specs/2026-04-06-unit-types-v2-design.md` (for reference on unit type behavior)
