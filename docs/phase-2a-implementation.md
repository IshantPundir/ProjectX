# Phase 2A Implementation — Developer Documentation

**Scope:** JD pipeline ship cut — create → extract → review. Introduces the `job_postings` + `job_posting_signal_snapshots` tables, the JD state machine, the `app/ai/` provider-agnostic AI layer, the Dramatiq worker, the Call 1 actor, and the three-panel review frontend.
**Status:** Complete and functional (ship cut 2026-04-09)
**Last updated:** 2026-04-15

See also:
- Design spec: `docs/superpowers/specs/2026-04-08-phase-2a-jd-pipeline-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-09-phase-2a-implementation.md`
- Phase 1 walkthrough: `docs/phase-1-implementation.md`
- Phase 2B walkthrough: `docs/phase-2b-implementation.md` (signal editing, confirmation, Call 2 re-enrichment — extends everything below)
- Phase 2C.1 walkthrough: `docs/phase-2c1-implementation.md`
- Phase 2C.2 walkthrough: `docs/phase-2c2-implementation.md`
- Post-2C hardening: `docs/phase-hardening-implementation.md` (migrations 0008–0012 repair the ship-time RLS shape on `job_postings` / `job_posting_signal_snapshots` / `sessions`)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [Auth & Permissions](#3-auth--permissions)
4. [JD State Machine](#4-jd-state-machine)
5. [Company Profile Gate](#5-company-profile-gate)
6. [Call 1 Extraction (the AI Actor Path)](#6-call-1-extraction-the-ai-actor-path)
7. [`app/ai/` Layer (Provider-Agnostic)](#7-appai-layer-provider-agnostic)
8. [API Reference](#8-api-reference)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Module Layout](#10-module-layout)
11. [How to Add a New Prompt Version](#11-how-to-add-a-new-prompt-version)
12. [How to Swap the OpenAI Model for a Task](#12-how-to-swap-the-openai-model-for-a-task)
13. [Troubleshooting](#13-troubleshooting)
14. [Known Gaps](#14-known-gaps)

---

## 1. Architecture Overview

Phase 1 shipped auth, tenants, org units, roles/permissions, invites, and audit logging — the reusable spine. Phase 2A is the first product feature to ride on that spine: the JD pipeline. It turns a raw paste of a job description into a structured, provenance-aware signal snapshot that downstream phases (pipeline builder → question bank → live session) will consume.

The shipped flow, end to end:

1. Recruiter opens the dashboard and clicks **+ New JD**. The paste form collects a title, raw JD text, optional project scope, and a target `org_unit_id` inside their tenant.
2. Frontend `POST /api/jobs` reaches `create_job_posting()` in `app/modules/jd/service.py`. The service first walks the target org unit's ancestry via `find_company_profile_in_ancestry` and refuses to insert the row if no ancestor has a completed `company_profile` — this is the company-profile gate, raised as `CompanyProfileIncompleteError` → HTTP 422 with the offending `org_unit_id` in the body so the frontend can deep-link to the Company Profile tab.
3. Profile present → INSERT `job_postings(status='draft')`. The state machine transitions `draft → signals_extracting`, writes a `job_posting.status_changed` audit row, and the service schedules the `extract_and_enhance_jd` Dramatiq actor (lazy import to break the `actors ↔ service` circular reference). The HTTP response is `201 Created` with the fresh job and a null `latest_snapshot`.
4. The `nexus-worker` container picks up the message from the `jd_extraction` Redis queue. The actor opens a `get_bypass_session`, sets `app.current_tenant` via `SET LOCAL`, delegates to `_run_extraction`, and drives the Call 1 LLM call through the `app/ai/` layer: `instructor` for structured-output enforcement (`ExtractionOutput` Pydantic model), `langfuse.openai` for LLM tracing, `OpenAIConfig` for the model id and `reasoning_effort` (env-driven, never hardcoded).
5. On success, the actor writes `description_enriched` back to the job row, inserts a `job_posting_signal_snapshots` row with `version=1`, transitions `signals_extracting → signals_extracted`, and commits. On failure (OpenAI 4xx/5xx, `InstructorRetryException`, schema drift), Dramatiq retries up to 3 total attempts with exponential backoff; the final retry writes `sanitize_error_for_user(exc)` to `status_error` and transitions to `signals_extraction_failed`.
6. Throughout, the frontend holds a Server-Sent Events connection open at `GET /api/jobs/{id}/status/stream`. The backend polls the row every 1.5s, dedupes, and emits a `status` event on change. Terminal states (`signals_extracted`, `signals_extraction_failed`) close the stream. The frontend's `useJobStatusStream` hook invalidates the TanStack Query `['jobs', jobId]` key on every event so the review surface re-fetches and the skeleton swaps out for the three-panel view.

Phase 2A ships **no** editing, confirmation, or re-enrichment. The review surface is deliberately read-only. Those workflows — signal editing, `Confirm Signals`, and Call 2 re-enrichment — land in Phase 2B and are covered in `phase-2b-implementation.md`.

Four new primitives land with Phase 2A and are reused by every later phase:

1. **The JD state machine** (`app/modules/jd/state_machine.py`) — a single `LEGAL_TRANSITIONS` map plus a `transition()` helper that atomically flips `job.status` and writes an audit row. Phase 2B adds `signals_confirmed` to the map; Phase 2C.2 keys off these states for stage question-bank generation preconditions.
2. **The Dramatiq worker path** — `app/worker.py` imports `app/brokers.py` (shared RedisBroker init) then imports actor modules to register them. Phase 2B adds `reenrich_jd`, Phase 2C.2 adds `generate_question_bank_stage`; the broker + session pattern is identical every time.
3. **The `app/ai/` provider-agnostic layer** — `AIConfig`, `PromptLoader`, `get_openai_client()`, and the structured-output schemas in `app/ai/schemas.py`. Phase 2B and Phase 2C.2 add new prompts, new schemas, and new `AIConfig` properties, but the import surface is frozen: business logic never touches `openai`, `instructor`, or `langfuse.openai` directly.
4. **Ancestry-walking authz** — `require_job_access()` in `app/modules/jd/authz.py` walks `organizational_units.parent_unit_id` from the job's unit up to root and checks `jobs.view` / `jobs.manage` on each ancestor. Phase 2C.1 (`require_template_access`) and Phase 2C.2 (`require_bank_access_by_stage`) copy the pattern.

---

## 2. Database Schema

Phase 2A's schema landed as a **Supabase CLI migration**, not an Alembic revision — `backend/supabase/migrations/20260410000001_phase_2a_job_postings.sql`. Alembic wasn't in use yet; the first Alembic revision (`0001_phase_2b_columns`) lands with Phase 2B. Two companion Supabase migrations round out the 2A database delta:

- `20260410000000_phase_2a_company_profile_reset.sql` — adds `company_profile_completed_at` + `company_profile_completed_by` to `organizational_units`, and nulls any existing profile that doesn't match the new 4-field shape (hard cutover, pre-MVP dev data only).
- `20260410000001_phase_2a_job_postings.sql` — defines the `public.set_updated_at()` trigger function, creates `job_postings`, `job_posting_signal_snapshots`, and the `sessions` stub, and enables RLS on all three.
- `20260410000002_phase_2a_jobs_view_permission.sql` — seeds `jobs.view` into the `Admin`, `Recruiter`, and `Hiring Manager` system roles (idempotent, guarded by `NOT (permissions ? 'jobs.view')`).

### `job_postings`

The main table. One row per recruiter JD upload, scoped to a tenant and owning org unit.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | UUID PK | no | `gen_random_uuid()` | |
| `tenant_id` | UUID NOT NULL | no | — | FK → `clients.id`, RLS scoping |
| `org_unit_id` | UUID NOT NULL | no | — | FK → `organizational_units.id` |
| `title` | TEXT NOT NULL | no | — | Recruiter-supplied, 1–300 chars (Pydantic) |
| `description_raw` | TEXT NOT NULL | no | — | 50–50,000 chars (Pydantic) |
| `project_scope_raw` | TEXT | yes | — | Optional, ≤ 20,000 chars |
| `description_enriched` | TEXT | yes | — | Written by Call 1 on success; null until then |
| `enriched_manually_edited` | BOOLEAN NOT NULL | no | `false` | Load-bearing for Phase 2B Call 2 — reset to `true` on manual edits |
| `status` | TEXT NOT NULL | no | `'draft'` | State machine — see Section 4 |
| `status_error` | TEXT | yes | — | Sanitized user-facing message from `sanitize_error_for_user` |
| `source` | TEXT NOT NULL | no | `'native'` | `'native'` for direct paste; reserved for `'ats_ceipal'` etc. |
| `external_id` | TEXT | yes | — | ATS correlation id (unused in 2A) |
| `target_headcount` | INTEGER | yes | — | 1–10,000 (Pydantic) |
| `deadline` | DATE | yes | — | |
| `created_by` | UUID NOT NULL | no | — | FK → `users.id` |
| `created_at` | TIMESTAMPTZ NOT NULL | no | `NOW()` | |
| `updated_at` | TIMESTAMPTZ NOT NULL | no | `NOW()` | Maintained by `set_updated_at` BEFORE UPDATE trigger |

**Indexes:**
- `idx_job_postings_tenant_org_unit (tenant_id, org_unit_id)`
- `idx_job_postings_status (tenant_id, status)`
- `idx_job_postings_created_at (tenant_id, created_at DESC)`

**Trigger:** `set_job_postings_updated_at` — `BEFORE UPDATE FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()`. Phase 1 tables don't carry the trigger — retrofitting is tracked under Known Gaps.

### `job_posting_signal_snapshots`

Immutable versioned snapshot of extracted + inferred signals. In Phase 2A the actor writes `version=1` exactly once per successful Call 1. Phase 2B turns the table into a full version history through `save_signals`.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | UUID PK | no | `gen_random_uuid()` | |
| `tenant_id` | UUID NOT NULL | no | — | FK → `clients.id`, RLS scoping |
| `job_posting_id` | UUID NOT NULL | no | — | FK → `job_postings.id` **ON DELETE CASCADE** |
| `version` | INTEGER NOT NULL | no | — | Monotonic per-job. Starts at 1. |
| `required_skills` | JSONB NOT NULL | no | — | Array of `SignalItem` dicts (legacy 4-bucket schema) |
| `preferred_skills` | JSONB NOT NULL | no | — | |
| `must_haves` | JSONB NOT NULL | no | — | |
| `good_to_haves` | JSONB NOT NULL | no | — | |
| `min_experience_years` | INTEGER NOT NULL | no | — | 0–50 |
| `seniority_level` | TEXT NOT NULL | no | — | `junior` / `mid` / `senior` / `lead` / `principal` |
| `role_summary` | TEXT NOT NULL | no | — | 10–2000 chars |
| `confirmed_by` | UUID | yes | — | FK → `users.id`. Always null in 2A — Phase 2B stamps it. |
| `confirmed_at` | TIMESTAMPTZ | yes | — | Always null in 2A. |
| `created_at` | TIMESTAMPTZ NOT NULL | no | `NOW()` | |

**Constraints:** `UNIQUE (job_posting_id, version)` via `uq_snapshot_job_version`.

**Index:** `idx_signal_snapshots_job_posting (job_posting_id, version DESC)`.

**Signal item shape (legacy 4-bucket):** Each element in the four JSONB arrays is a `SignalItem` with `value: str`, `source: 'ai_extracted'|'ai_inferred'`, and `inference_basis: str | null`. Provenance is enforced by the Pydantic `check_basis_matches_source` validator in `app/ai/schemas.py` (see Section 7).

**Spec drift — signal schema evolves in Phase 2B.** At ship time Phase 2A stores signals in four separate JSONB arrays (`required_skills`, `preferred_skills`, `must_haves`, `good_to_haves`) plus `min_experience_years`. Phase 2B migration `0003_signal_schema_v2` replaces all five legacy columns with a single flat `signals` JSONB column, and the signal item schema gains `type`, `priority`, `weight`, `knockout`, `stage`, `evaluation_method`, `evaluation_hint`, and a `recruiter` provenance source. See `phase-2b-implementation.md` Section 2 for the v2 schema and the clean-slate rewrite rationale.

### `sessions` stub

Created in 2A so future FKs have a parent. Never written by any 2A code path — it exists purely so Phase 3 can add the candidates table without needing to backfill.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | UUID PK | no | `gen_random_uuid()` | |
| `tenant_id` | UUID NOT NULL | no | — | FK → `clients.id`, RLS scoping |
| `job_posting_id` | UUID NOT NULL | no | — | FK → `job_postings.id` |
| `candidate_id` | UUID | yes | — | **No FK** — Phase 3 creates `candidates` and adds the constraint |
| `status` | TEXT NOT NULL | no | `'scheduled'` | |
| `started_at` | TIMESTAMPTZ | yes | — | |
| `completed_at` | TIMESTAMPTZ | yes | — | |
| `created_at` | TIMESTAMPTZ NOT NULL | no | `NOW()` | |

### Ship-time RLS on Phase 2A tables

Each of `job_postings`, `job_posting_signal_snapshots`, and `sessions` enables RLS in the Supabase migration with:

```sql
CREATE POLICY "tenant_isolation" ON <table>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);

CREATE POLICY "service_role_bypass" ON <table>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Ship-time RLS drift (the same three defects Phase 2C.1 carried):**

1. `tenant_isolation` has `USING` only — **no `WITH CHECK`**. The canonical full-command form requires both. In principle this silently blocks writes from tenant sessions (root `CLAUDE.md` → "RLS Pattern" → the `FOR SELECT`/no-`WITH CHECK` trap). It "worked" in dev because the backend still connected as Supabase's `postgres` role which has `rolbypassrls=true` — every policy was a no-op at runtime.
2. The bypass policy is named `service_role_bypass` instead of the canonical `service_bypass`.
3. The tenant predicate uses raw `::uuid` instead of `NULLIF(..., '')::uuid`. `SET LOCAL app.current_tenant` reverts a custom GUC to `""` (empty string, **not** NULL) at transaction end, so a subsequent pooled-connection request that evaluates the policy before setting the GUC crashes with `invalid input syntax for type uuid: ""`. The crash was invisible until the runtime role switch (0010) made policies actually fire.

All three defects are repaired by the post-Phase-2C hardening batches and are safe today (head = `0012_rename_service_role_bypass`):

- **Migration 0009** (`phase1_rls_full_command`) rewrites Phase 1 tenant policies with the canonical `USING … WITH CHECK` pair. It doesn't touch Phase 2A tables directly, but it's the template that 0011 uses.
- **Migration 0010** (`create_nexus_app_role`) creates the dedicated `nexus_app` role (`NOBYPASSRLS`) and flips `get_tenant_db` / `get_bypass_db` to `SET LOCAL ROLE nexus_app` at session start. Every Phase 2A policy starts actually firing at this moment.
- **Migration 0011** (`rls_nullif_tenant`) drops every `tenant_isolation` policy on Phase 2A/2C tables and recreates them with the full-command form (`USING (...) WITH CHECK (...)`) and `NULLIF(current_setting('app.current_tenant', true), '')::uuid`. This is the migration that makes `job_postings`, `job_posting_signal_snapshots`, and `sessions` finally enforce correct tenant isolation under real connection pooling.
- **Migration 0012** (`rename_service_role_bypass`) drops `service_role_bypass` from Phase 2A/2C tables and recreates it as `service_bypass` for naming consistency.
- **Startup assertion** `_assert_rls_completeness` in `app/main.py` enumerates `job_postings`, `job_posting_signal_snapshots`, and `sessions` (alongside every other tenant-scoped table) and aborts boot with a structured CRITICAL log if either canonical policy is missing or `tenant_isolation` lacks a non-NULL `WITH CHECK`. See `phase-hardening-implementation.md` Section 7.

The net state at runtime today is the correct full-command, NULLIF-wrapped, canonically-named policy pair on all three Phase 2A tables. The flawed ship-time DDL is history — do not copy it into new migrations.

---

## 3. Auth & Permissions

### The `jobs.view` permission

Phase 2A adds `jobs.view` to the canonical permission frozenset in `app/modules/auth/permissions.py`. Phase 1 had already shipped `jobs.create` and `jobs.manage` in the initial permission set, but not `jobs.view`:

```python
ALL_PERMISSIONS: frozenset[str] = frozenset({
    "users.invite_admins",
    "users.invite_users",
    "users.deactivate",
    "org_units.create",
    "org_units.manage",
    "jobs.view",          # NEW in 2A
    "jobs.create",
    "jobs.manage",
    "candidates.view",
    ...
})
```

The Supabase migration `20260410000002_phase_2a_jobs_view_permission.sql` seeds it into the `Admin`, `Recruiter`, and `Hiring Manager` system roles via an idempotent `UPDATE roles SET permissions = permissions || '["jobs.view"]'::jsonb` guarded by `NOT (permissions ? 'jobs.view')`. Re-running the migration is a no-op.

Phase 2A uses the three `jobs.*` permissions as follows:

| Permission | Enforced at | For |
|---|---|---|
| `jobs.create` | `POST /api/jobs` router dependency — ancestry walk via `_get_org_unit_ancestry` on `body.org_unit_id` | Creating new JDs |
| `jobs.view` | `GET /api/jobs/{id}`, `GET /api/jobs/{id}/status/stream`, and the `list_jobs` visibility filter | Reading a single job or listing the user's visible jobs |
| `jobs.manage` | `POST /api/jobs/{id}/retry` | Retrying a failed extraction (and, in Phase 2B, editing/confirming/re-enriching) |

### `require_job_access()` — ancestry-walking guard

`app/modules/jd/authz.py` defines the load-bearing authz helper:

```python
async def require_job_access(
    db: AsyncSession,
    job_id: UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> JobPosting:
    ...
```

It loads the job (RLS scopes to the current tenant automatically, so a cross-tenant job id returns 404), short-circuits for super admins, and otherwise walks `organizational_units.parent_unit_id` from the job's unit up to root. For each ancestor it calls `user.has_permission_in_unit(unit.id, f"jobs.{action}")`; the first match wins. No match raises `HTTPException(403, "Missing jobs.<action> in job's org unit ancestry")`.

The walk is **primary**, not defensive: Day-1 Task 1 confirmed that `UserContext.has_permission_in_unit()` does exact-match only — it does not inherit from ancestors. Without this ancestry walk, a recruiter holding `jobs.manage` on a parent division would silently 403 on jobs under a child team. `_get_org_unit_ancestry` uses a cycle-safe `seen` set so corrupted data cannot hang a request.

### `list_jobs` — visibility filter

The list endpoint doesn't call `require_job_access` per-row (N+1). Instead the router computes `_visible_unit_ids(user, "jobs.view")`:

- Super admin → `None` (no filter, RLS alone scopes to tenant).
- Otherwise → the flat list of `org_unit_id`s where the user holds `jobs.view` as a direct grant.

The service layer's `list_job_postings(visible_org_unit_ids=...)` adds a `WHERE job_postings.org_unit_id = ANY (:ids)` clause when the list is not None. This is the **immediate-grant set**, not the ancestry-expanded set — which is the right semantic: the user sees jobs whose org unit is one where they directly hold `jobs.view`, which means any ancestor grant has already been flattened into that unit by the role-assignment model.

### Role assignments

Roles are assigned to users at specific org unit levels via `user_role_assignments` (Phase 1 infrastructure — see `phase-1-implementation.md`). Phase 2A adds nothing new to that model; it only adds a new permission string that the three existing system roles pick up via the migration seed. Tenant-custom roles created before the migration will **not** automatically gain `jobs.view`; a Company Admin has to add it via the roles UI (or the next seed-on-upgrade migration) to let recruiters on a custom role see JDs.

---

## 4. JD State Machine

`app/modules/jd/state_machine.py` is the single source of truth for legal transitions of `job_postings.status`. Every path that mutates `status` must go through `transition()` — the Dramatiq actor, the service layer, and any future admin tooling.

### Legal transitions (Phase 2A)

```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},  # retry
    "signals_extracted": set(),                           # terminal in 2A
}
```

Four states, four edges. `signals_extracted` is terminal at Phase 2A ship time. Phase 2B adds `signals_confirmed` + the `signals_extracted → signals_confirmed` and `signals_confirmed → signals_extracted` edges — see `phase-2b-implementation.md` Section 1.

### `transition()` — atomic state + audit

```python
async def transition(
    db: AsyncSession,
    job,
    *,
    to_state: str,
    actor_id: UUID | None,
    correlation_id: str,
) -> None:
```

The helper:

1. Checks `is_legal_transition(job.status, to_state)`; raises `IllegalTransitionError(from_state, to_state)` if not in `LEGAL_TRANSITIONS`.
2. Sets `job.status = to_state` on the ORM instance (flushed by the caller's transaction).
3. Lazy-imports `app.modules.audit.service.log_event` (breaks the `jd ↔ audit` circular reference) and writes an `audit_log` row: `action="job_posting.status_changed"`, `resource="job_posting"`, `resource_id=job.id`, `payload={"from": from_state, "to": to_state, "correlation_id": correlation_id}`, `actor_email=None` (email isn't available in the actor context — `actor_id` is sufficient for forensics).
4. Logs `jd.state_machine.transition` with structured fields.

The caller owns `db.commit()` / `rollback()`. This is deliberate: the actor path commits the entire `_run_extraction` result in one shot (including the snapshot insert and the state flip), and the service-layer path commits via the FastAPI transaction scope.

### `IllegalTransitionError` → HTTP 409

`app/modules/jd/errors.py` defines `IllegalTransitionError(from_state, to_state)` with both fields stored as attributes. The FastAPI exception handler in `app/main.py::illegal_transition_handler` maps the exception to HTTP 409 Conflict using a state-pair keyed message table:

```python
_ILLEGAL_TRANSITION_MESSAGES: dict[tuple[str, str], str] = {
    ("signals_extracting", "signals_extracting"):
        "Job is already being processed",
    ("signals_extracted", "signals_extracting"):
        "This job has already been extracted successfully — "
        "retry is only valid after an extraction failure",
    ("draft", "signals_extracted"):
        "Job cannot transition directly from draft to extracted",
}
```

Unknown pairs fall back to the generic `f"Cannot transition job from {from_state} to {to_state}"`. Phase 2B extends this table with `signals_confirmed`-keyed entries.

### Audit trail — every transition leaves a row

Every `transition()` call writes exactly one `audit_log` row with the correlation id in the payload. Cross-referencing by correlation id ties the frontend SSE event, the backend structlog lines, the Dramatiq message, the OpenAI HTTP logs, and the Langfuse trace together. This is what "correlation ID end-to-end" means in the root `CLAUDE.md` — and the audit row is the persistent record, independent of ephemeral log retention.

---

## 5. Company Profile Gate

Before a JD can enter the extraction pipeline, the target org unit must have an ancestor with a completed `company_profile`. The helper that enforces this is `find_company_profile_in_ancestry(db, org_unit_id)` in `app/modules/org_units/service.py`, introduced in Phase 2A.

```python
async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
    """Walk parent_unit_id chain from the given unit up to root.
    Return the first company_profile dict encountered. None if no ancestor
    has one."""
    current_id: UUID | None = org_unit_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            return None  # defensive: corrupted data loop
        seen.add(current_id)
        result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == current_id)
        )
        unit = result.scalar_one_or_none()
        if unit is None:
            return None
        if unit.company_profile:
            return unit.company_profile
        current_id = unit.parent_unit_id
    return None
```

The walk is cycle-safe and returns the first populated `company_profile` dict in the chain — so a child `team` under a `division` under a `company` inherits its profile from the `company` row.

### 4-field strict schema

`app/modules/org_units/company_profile.py` defines the `CompanyProfile` Pydantic model with exactly four required fields: `about`, `industry` (enum), `company_stage` (enum), `hiring_bar`. The Supabase migration `20260410000000_phase_2a_company_profile_reset.sql` performs a hard cutover — any existing JSONB profile missing one of the four keys is nulled. App-layer validation (`_validate_and_normalize_company_profile` in `org_units/service.py`) enforces character limits and enum values on create / update. Tracking stamps `company_profile_completed_at` and `company_profile_completed_by` are added to `organizational_units` in the same migration.

### `create_job_posting` — the gate check

`app/modules/jd/service.py::create_job_posting` calls the helper before anything else:

```python
profile = await find_company_profile_in_ancestry(db, org_unit_id)
if profile is None:
    raise CompanyProfileIncompleteError(org_unit_id)
```

`CompanyProfileIncompleteError` stores the offending `org_unit_id` and is mapped to HTTP 422 by `app/main.py::company_profile_incomplete_handler`:

```python
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

The `org_unit_id` in the body is load-bearing: the frontend's `/jobs/new` form reads it on 422, toasts the error, and deep-links the user to `Settings → Org Units → [unit] → Company Profile` so they can complete the profile without losing their paste.

### Reused downstream

The helper is called by:

| Caller | File | Purpose |
|---|---|---|
| `create_job_posting` | `app/modules/jd/service.py` | Creation-time gate (raises 422 on miss) |
| `_run_extraction` (Call 1) | `app/modules/jd/actors.py` | Defensive re-check inside the actor. Should never miss because creation blocked it, but failing open is fail-safe: if it ever does, the actor transitions to `signals_extraction_failed` with a clear `status_error`. |

Phase 2B adds a third caller (`_run_reenrichment` for Call 2), and Phase 2C.2 adds a fourth (`generate_question_bank_stage` reads the profile as prompt context). The helper itself is unchanged across all four — see `phase-2b-implementation.md` Section 6 for the full caller table at current head.

---

## 6. Call 1 Extraction (The AI Actor Path)

Call 1 is the JD-to-signals extraction. It runs as a Dramatiq actor on `nexus-worker`, not inline in the API request cycle — LLM calls routinely take 20–60s with `reasoning_effort='medium'`, and blocking the HTTP request for that long is a non-starter.

### Dispatch from the service layer

`create_job_posting()` ends with:

```python
# Lazy import to avoid circular dependency (actors → service for persist)
from app.modules.jd.actors import extract_and_enhance_jd

extract_and_enhance_jd.send(
    job_posting_id=str(job.id),
    tenant_id=str(tenant_id),
    correlation_id=correlation_id,
)
```

The lazy import breaks a cycle — `actors.py` imports from `app.modules.jd.state_machine` (which is safe) but the service is the only thing that knows when to dispatch. `.send()` is synchronous from the caller's perspective (it pushes the message to the Redis broker); the FastAPI transaction then commits.

The `retry_failed_extraction` service function takes the same path: transition `signals_extraction_failed → signals_extracting`, clear `status_error`, dispatch the actor.

### Actor entry point (`app/modules/jd/actors.py::extract_and_enhance_jd`)

Registered via `@dramatiq.actor(max_retries=3, min_backoff=2_000, max_backoff=60_000, queue_name="jd_extraction")` with signature `async def extract_and_enhance_jd(job_posting_id: str, tenant_id: str, correlation_id: str)`. `max_retries=3` gives 4 total attempts.

The outer wrapper:

1. Reads `retries_so_far` from `CurrentMessage.get_current_message().options.get("retries", 0)`. Dramatiq middleware stamps this on the message options so the actor knows which attempt it is.
2. Opens `get_bypass_session()` — the worker has no HTTP request, so there is no request-scoped tenant session available. Bypass sessions connect directly and set `app.bypass_rls='true'`.
3. Calls `await db.execute(text("SET LOCAL app.current_tenant = :t"), {"t": tenant_id})` to re-establish tenant scoping for RLS. **Ship-time note:** the bind-parameter form was later changed to literal interpolation in commit `d55f895` (`fix(jd): actor SET LOCAL must use literal interpolation, not bind params`) because asyncpg's prepared-statement cache and `SET LOCAL` combine badly under load; the tenant id is round-tripped through `UUID(...)` for injection safety.
4. Delegates to `_run_extraction(db, ...)` and commits on success.
5. On exception: if `retries_so_far >= 2` (final retry), commits so the user sees `signals_extraction_failed`; otherwise rollbacks to leave state unchanged for the next retry. Re-raises either way so Dramatiq logs the failure and schedules the backoff.

### `_run_extraction` — the actual work

The inner coroutine is deliberately split out so unit tests can drive it with a transactional session and a mocked `get_openai_client()` without spinning up Dramatiq's scheduler.

1. **Load the job.** `SELECT * FROM job_postings WHERE id = :id`. Missing → `log.warn("jd.actor.job_not_found")` and return. This can happen if the job was deleted between dispatch and pickup.
2. **Idempotency guard.** `if job.status != "signals_extracting": return`. Prevents double-processing when a duplicate message is delivered or when the actor picks up a message after the state has already transitioned (e.g., due to a manual operator intervention).
3. **Load the company profile.** `profile = await find_company_profile_in_ancestry(db, job.org_unit_id)`. The service layer already validated this at creation time, but the actor re-checks defensively — a missing profile transitions the job to `signals_extraction_failed` with `"Company profile missing — create_job_posting should have blocked this"` rather than crashing the worker.
4. **Build the user message in the mandatory ordering.** `_build_user_message(job, profile)` concatenates three sections in this exact order: **company profile → raw JD → project scope**. The ordering is load-bearing per the user-memory preference ("context before document, always"): the model needs the company context before it reads the JD so "what strong looks like" is primed from the first token.

   ```python
   parts = [
       "## Company Profile\n"
       f"- About: {profile['about']}\n"
       f"- Industry: {profile['industry']}\n"
       f"- Company stage: {profile['company_stage']}\n"
       f"- Hiring bar: {profile['hiring_bar']}\n",
       f"## Raw Job Description\n\n{job.description_raw}\n",
   ]
   if job.project_scope_raw:
       parts.append(f"## Project Scope\n\n{job.project_scope_raw}\n")
   return "\n".join(parts)
   ```

5. **Call OpenAI via `instructor`.** `get_openai_client()` returns the memoized `instructor.AsyncInstructor`; the prompt comes from `prompt_loader.get("jd_enhancement")`. The call passes `model=ai_config.extraction_model`, `reasoning_effort=ai_config.extraction_effort`, `response_model=ExtractionOutput`, the system + user message pair, and a `metadata` dict carrying `{correlation_id, job_posting_id, tenant_id, prompt_version: 'v1'}` for Langfuse trace attachment. `response_model` is the instructor-strict structured output — see Section 7 for the schema.

6. **Error path.** The `try/except` catches `Exception` and branches on `retries_so_far`:

   ```python
   except Exception as exc:
       log.error("jd.actor.call1_failed", exc_info=exc)
       if retries_so_far >= 2:
           job.status_error = sanitize_error_for_user(exc)
           await transition(db, job, to_state="signals_extraction_failed", ...)
       raise  # Dramatiq retries on all non-final exceptions
   ```

   `sanitize_error_for_user` (`app/modules/jd/errors.py`) maps exception types to fixed user-facing strings — never `str(exc)`, to avoid leaking API URLs, keys, request IDs, or prompt payloads into `job_posting.status_error`. The mapping at ship time:

   | Exception type | User-facing message |
   |---|---|
   | `openai.RateLimitError` | "Our AI provider is rate-limiting us. Please retry in a minute." |
   | `openai.APITimeoutError` | "The AI provider timed out. Please retry." |
   | `openai.APIConnectionError` | "Could not reach the AI provider. Please retry." |
   | `openai.AuthenticationError` | "AI provider authentication failed. Contact support." |
   | `openai.BadRequestError` | "The job description could not be processed. Please check the input and retry." |
   | `instructor.core.InstructorRetryException` | "The AI response did not match the expected format after retries. Please retry." |
   | _anything else_ | "Extraction failed — please retry. Contact support if this persists." |

   Rich exception detail still flows to structlog and Sentry; only the DB `status_error` field and the frontend body are sanitized.

7. **Success path.** `_persist_enriched(db, job, result)` writes `job.description_enriched = result.enriched_jd` and inserts a `JobPostingSignalSnapshot` with `version=1`, the four signal arrays (`result.signals.required_skills.model_dump()` etc.), `min_experience_years`, `seniority_level`, and `role_summary`. Then `transition(db, job, to_state="signals_extracted", ...)` flips the state and writes the audit row. The outer wrapper commits.

### Retry middleware behavior

Dramatiq's default `Retries` middleware is active. With `max_retries=3, min_backoff=2_000, max_backoff=60_000`:

- **Attempt 1** (original) — `retries_so_far=0`. Transient failure → rollback, raise, backoff ~2s.
- **Attempt 2** — `retries_so_far=1`. Transient failure → rollback, raise, exponential backoff.
- **Attempt 3** — `retries_so_far=2`. Transient failure **or final** → persist `status_error` + transition to `signals_extraction_failed`, commit, raise. Dramatiq may schedule one more retry (`max_retries=3`) but the state is already terminal; the guard `if job.status != "signals_extracting": return` on the next pickup short-circuits cleanly.

This retry shape was tuned for Phase 2A's failure profile — OpenAI 429s and transient 5xxs resolve on the next attempt; structural failures (bad JSON schema, bad model id) fail fast via `openai.BadRequestError` on the first attempt. The retries are stateless; the actor only reads `retries_so_far` to decide whether to persist the terminal failure or rollback silently.

Phase 2B's Call 2 actor (`reenrich_jd`) uses `max_retries=1` instead — re-enrichment is user-initiated and should fail fast so the recruiter sees the error immediately rather than waiting through a 60s backoff chain. See `phase-2b-implementation.md` Section 5.

### SSE status stream

While the actor is running, the frontend holds `GET /api/jobs/{id}/status/stream` open. `app/modules/jd/sse.py::job_status_event_generator`:

```python
POLL_INTERVAL_SECONDS: float = 1.5
TERMINAL_STATES: frozenset[str] = frozenset(
    {"signals_extracted", "signals_extraction_failed"}
)

async def job_status_event_generator(db, job_id, request):
    last_status: str | None = None
    while True:
        if await request.is_disconnected():
            return
        event = await get_job_status(db, job_id)
        if event is None:
            return
        if event.status != last_status:
            yield {"event": "status", "data": event.model_dump_json()}
            last_status = event.status
        if event.status in TERMINAL_STATES:
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

The generator polls every 1.5s, dedupes on `status`, and closes the stream on client disconnect or terminal state. RBAC is **not** enforced here — the router dependency `require_job_access(db, job_id, user, "view")` has already validated access before the generator is invoked.

**Spec drift — SSE RLS at ship time:** The SSE generator shipped accepting `db: AsyncSession = Depends(get_tenant_db)` directly. That looked right (tenant-scoped session) but opened a subtle lifetime problem: the FastAPI dependency's transaction scope ends when the handler returns, which for a long-lived SSE stream means queries inside the generator were racing a closed transaction. Batch F (commit `bd4b6bb`) rewrote `sse.py` to pull sessions on demand via `get_tenant_session` inside the generator loop. See `phase-hardening-implementation.md` Section 10. The 2A behavior described above still holds — poll interval, de-dup, terminal close — only the session acquisition pattern changed.

---

## 7. `app/ai/` Layer (Provider-Agnostic)

`app/ai/` is the provider-agnostic AI layer. Business logic imports `get_openai_client()`, `prompt_loader`, and the structured-output schemas from `app.ai.*` — never `openai`, `instructor`, or `langfuse.openai` directly. This is the single swap point for a future provider change.

### `AIConfig` (`app/ai/config.py`)

The single source of truth for model IDs and `reasoning_effort`. Env-driven via `app.config.settings`, read on every access so a worker restart picks up new values with no image rebuild.

```python
class AIConfig:
    @property
    def extraction_model(self) -> str:
        return settings.openai_extraction_model

    @property
    def extraction_effort(self) -> str:
        return settings.openai_extraction_effort

    @property
    def request_timeout_seconds(self) -> float:
        return settings.openai_request_timeout_seconds

    @property
    def max_schema_retries(self) -> int:
        return settings.openai_max_retries
```

Phase 2A ships only `extraction_*` properties. Phase 2B adds `reenrichment_*`, Phase 2C.2 adds `question_bank_*` — each new task type is a new pair of properties plus two new env vars. Hardcoding a model name anywhere else in the codebase is a review-blocker per the root `CLAUDE.md` AI-provider rules.

The settings read from `.env`:

| Setting | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | `""` | Raw OpenAI API key |
| `OPENAI_EXTRACTION_MODEL` | `gpt-5.2` | Model id for Call 1 |
| `OPENAI_EXTRACTION_EFFORT` | `medium` | `reasoning_effort` for Call 1 |
| `OPENAI_REQUEST_TIMEOUT_SECONDS` | `120.0` | httpx client timeout |
| `OPENAI_MAX_RETRIES` | `2` | instructor-level schema retries (not SDK-level) |

Phase 1's Anthropic API key setting was removed in the same pass (`backend/nexus/app/config.py` — `-anthropic_api_key`).

### `PromptLoader` (`app/ai/prompts.py`)

Versioned prompts read from `backend/nexus/prompts/v{version}/<name>.txt` on first access, cached in memory. Failures are loud — the caller gets `FileNotFoundError`, never a silent empty string.

```python
class PromptLoader:
    def __init__(self, version: str = "v1") -> None:
        self._version = version
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = PROMPTS_ROOT / self._version / f"{name}.txt"
            if not path.exists():
                raise FileNotFoundError(...)
            content = path.read_text(encoding="utf-8")
            self._cache[name] = content
        return self._cache[name]

prompt_loader = PromptLoader()  # default: v1
```

`PROMPTS_ROOT` is computed via `Path(__file__).resolve().parents[2] / "prompts"` — from `backend/nexus/app/ai/prompts.py` that resolves to `backend/nexus/prompts/`. A future hot-reload endpoint can bust the cache without a restart (deferred — see Known Gaps).

Phase 2A ships exactly one prompt: `prompts/v1/jd_enhancement.txt`. Phase 2B adds `jd_reenrichment.txt`, Phase 2C.2 adds `question_bank_common.txt` + one per stage type. The `load_pair(common_name, type_name)` helper (used by Phase 2C.2) concatenates two prompt files with `"\n\n"` between them — it's present at ship time but not used until Phase 2C.2.

### `get_openai_client()` (`app/ai/client.py`)

The factory returns an `instructor.AsyncInstructor` wrapped around a `langfuse.openai.AsyncOpenAI`. Both the instructor structured-output wrapping and the Langfuse auto-tracing happen inside this single call. `@lru_cache(maxsize=1)` memoizes the result across the process.

Key construction points:

- **Raw `AsyncOpenAI`** with `max_retries=1` (default is 2, which cascades badly with reasoning models — a single retry on a 4-minute call silently burns 8 minutes). One retry covers spurious network blips; anything worse should surface.
- **httpx event hooks** (`_log_request`, `_log_response`) log every outbound HTTP attempt — including SDK-level retries — plus status, `x-request-id`, and the `x-ratelimit-remaining-tokens` / `x-ratelimit-reset-tokens` headers. This is what makes silent retry cascades visible in structlog without relying on instructor-level logging.
- **`instructor.from_openai(raw, mode=instructor.Mode.TOOLS_STRICT)`** — OpenAI function-calling with strict schema enforcement. Malformed payloads are retried up to `max_schema_retries` times before raising `InstructorRetryException` from `instructor.core`.
- **No factory-level `max_retries`.** The ship-time code set it and hit `TypeError: got multiple values for keyword argument 'max_retries'` because instructor's per-call `create()` has its own internal default. Commit `76b9418` dropped the argument; if a non-default schema-retry count is ever needed, pass it per-call via `max_retries=` on `chat.completions.create()`.

### Langfuse — self-hosted-only guard

`_ensure_langfuse_configured()` is called before the client is built. It reads `LANGFUSE_HOST` / `LANGFUSE_BASE_URL` and both Langfuse keys from `settings`, disables tracing entirely if any are missing, and — **critically** — refuses to configure a `*.langfuse.com` cloud host outside `ENVIRONMENT=development`. `_is_langfuse_cloud_host(url)` parses the host portion (stripping scheme, port, and path) and returns true for `langfuse.com` or any `*.langfuse.com` subdomain. If matched outside dev, it raises `RuntimeError` at process boot with the message `"Langfuse cloud is prohibited in non-development environments — use self-hosted per CLAUDE.md. ..."`. In dev it still allows it for quick benchmarking but logs a loud `langfuse.cloud_host_in_dev` warning.

This is the mechanical enforcement of the root `CLAUDE.md` rule that candidate evaluation data must not flow through managed Langfuse cloud (AIVIA + third-party sub-processor concerns). Production / staging fails closed at process boot.

`flush_langfuse()` and `shutdown_langfuse()` are the cleanup hooks — the worker calls `flush_langfuse()` in an `asyncio.to_thread` after each actor completes so traces are not lost when the coroutine returns.

### `ExtractionOutput` schemas (`app/ai/schemas.py`)

The strict Pydantic model `instructor` enforces as the Call 1 structured output **at Phase 2A ship time** (pre signal schema v2):

- **`SignalItem`** — `value: str (min_length=1)`, `source: Literal['ai_extracted','ai_inferred']`, `inference_basis: str | None`. The `check_basis_matches_source` model validator enforces that `ai_inferred` has a non-null basis and `ai_extracted` has a null basis.
- **`ExtractedSignals`** — four `list[SignalItem]` fields (`required_skills`, `preferred_skills`, `must_haves`, `good_to_haves`), `min_experience_years: int (ge=0, le=50)`, `seniority_level: Literal['junior'|'mid'|'senior'|'lead'|'principal']`, `role_summary: str (min_length=10, max_length=2000)`.
- **`ExtractionOutput`** — `enriched_jd: str (min_length=50)` + `signals: ExtractedSignals`. The dual-output shape couples the enriched prose and the signal set in a single strict call.

**Provenance validators** are load-bearing: every `ai_inferred` signal must carry an `inference_basis` string explaining the chain-of-thought used to infer it. The frontend's `SignalChip` uses this to render inferred chips with a dashed amber border + an on-hover tooltip showing the basis (see Section 9).

**Spec drift — schema evolves in Phase 2B.** This 4-bucket schema is replaced in migration `0003_signal_schema_v2` with a single flat `signals: list[SignalItemV2]` where each item carries `type`, `priority`, `weight`, `knockout`, `stage`, and `evaluation_method` alongside the `value`/`source`/`inference_basis` triple. `ExtractedSignals` gains coverage validators (≥5 signals, ≥1 screen + ≥1 interview, ≥1 competency, ≤5 knockouts). Current repo = v2 — see `phase-2b-implementation.md` Section 2.

---

## 8. API Reference

All Phase 2A endpoints live under `/api/jobs` — the prefix is set on the router in `app/modules/jd/router.py`. Auth is `Bearer <supabase-jwt>`. RBAC is enforced by `require_job_access(db, job_id, user, action)` walking the job's org unit ancestry on per-row endpoints, or by `_visible_unit_ids(user, "jobs.view")` on the list endpoint. Super admins short-circuit every check.

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `POST` | `/api/jobs` | `jobs.create` in ancestry of `body.org_unit_id` | Create a new JD, dispatch Call 1, return `201` with the fresh job + null snapshot |
| `GET` | `/api/jobs` | `jobs.view` (visibility filter) | List visible JDs with optional `org_unit_id=` and `status=` query params |
| `GET` | `/api/jobs/{job_id}` | `jobs.view` in ancestry | Full payload with latest snapshot |
| `GET` | `/api/jobs/{job_id}/status/stream` | `jobs.view` in ancestry | SSE stream of status events |
| `POST` | `/api/jobs/{job_id}/retry` | `jobs.manage` in ancestry | Retry a failed extraction — only valid from `signals_extraction_failed` |

**Phase 2B adds three more endpoints** to the same router (`PATCH /api/jobs/{id}/signals`, `POST /api/jobs/{id}/signals/confirm`, `POST /api/jobs/{id}/enrich`) plus response-shape changes on `GET /api/jobs` and `GET /api/jobs/{id}`. Those are documented in `phase-2b-implementation.md` Section 7 — not here.

### Error shapes

| Error | HTTP | Body |
|---|---|---|
| Missing company profile on create | 422 | `{"detail": "Company profile must be completed ...", "org_unit_id": "<uuid>"}` |
| Illegal state transition | 409 | `{"detail": "<keyed message or generic fallback>"}` |
| Missing `jobs.*` in ancestry | 403 | `{"detail": "Missing jobs.<action> in job's org unit ancestry"}` |
| Cross-tenant or missing job | 404 | `{"detail": "Job not found"}` |
| Missing `jobs.create` on create | 403 | `{"detail": "Missing jobs.create in ancestry"}` |

### `POST /api/jobs`

**Body (`JobPostingCreate`):**

```json
{
  "org_unit_id": "uuid",
  "title": "Senior Backend Engineer",
  "description_raw": "We are looking for a Senior Backend Engineer...",
  "project_scope_raw": null,
  "target_headcount": 2,
  "deadline": "2026-06-01"
}
```

Constraints (Pydantic `ConfigDict(extra='forbid')`): `title` 1–300 chars, `description_raw` 50–50,000 chars, `project_scope_raw` ≤ 20,000 chars or null, `target_headcount` 1–10,000 or null, `deadline` ISO date or null.

**Response (`JobPostingWithSnapshot`, `201`):** full job row — `id`, `title`, `org_unit_id`, `description_raw`, `project_scope_raw`, `description_enriched` (null), `status: "signals_extracting"`, `status_error: null`, `target_headcount`, `deadline`, `created_at`, `updated_at`, `latest_snapshot: null`. The actor has been dispatched; the frontend immediately opens the SSE stream.

### `GET /api/jobs`

**Query params:** `org_unit_id=<uuid>` (optional filter), `status=<state>` (optional filter). Both are `None`-safe.

**Response:** `list[JobPostingSummary]` — `id`, `title`, `org_unit_id`, `status`, `status_error`, `created_at`, `updated_at`. Sorted by `created_at DESC`. Super admins see all tenant rows (RLS + no visibility filter); others see only rows whose `org_unit_id` is in `_visible_unit_ids(user, "jobs.view")` — the immediate-grant set, not the ancestry-expanded set.

### `GET /api/jobs/{job_id}`

**Response (`JobPostingWithSnapshot`):** same shape as the create response. `latest_snapshot` is populated from the single `SELECT ... ORDER BY version DESC LIMIT 1` executed by `get_job_posting_with_latest_snapshot(db, job_id)`.

### `GET /api/jobs/{job_id}/status/stream`

**Response:** `text/event-stream` via `sse_starlette.EventSourceResponse`. Event shape:

```
event: status
data: {"job_id":"<uuid>","status":"signals_extracting","error":null,"signal_snapshot_version":null}
```

The generator polls every 1.5s and emits only on change. Terminal states (`signals_extracted`, `signals_extraction_failed`) close the stream. Client disconnect closes the stream immediately.

### `POST /api/jobs/{job_id}/retry`

No body. Returns `202 Accepted` with `JobPostingSummary`. Side effects: `retry_failed_extraction` transitions `signals_extraction_failed → signals_extracting` (via `state_machine.transition`, which enforces the precondition and raises `IllegalTransitionError` → 409 if the job is in any other state), clears `job.status_error`, re-dispatches `extract_and_enhance_jd`. `correlation_id` is taken from the `x-correlation-id` request header or generated fresh.

---

## 9. Frontend Architecture

Phase 2A is the first frontend surface in `frontend/app/` that talks to the new JD module. It ships:

- An entry shell for the dashboard via `DashboardProviders` (Phase 1 had the dashboard layout but no TanStack Query provider — 2A adds it).
- A typed API namespace `lib/api/jobs.ts`.
- Two hooks: `useJob(jobId)` (TanStack Query GET) and `useJobStatusStream(jobId)` (fetch-event-source SSE).
- Three pages: `/jobs` (list), `/jobs/new` (paste form), `/jobs/[jobId]` (three-panel review).
- The `components/dashboard/jd-panels/` component tree — `SignalChip`, `OriginalJdPanel`, `EnrichedJdPanel`, `SignalsPanel`, `LoadingSkeleton`, `ErrorBanner`.
- A sidebar link in `dashboard/sidebar.tsx` (commit `2a0e709`).

### `DashboardProviders` (`components/dashboard/providers.tsx`)

The TanStack Query root mounted inside the `(dashboard)` route group layout. Single `QueryClient` memoized via `useState(() => new QueryClient(...))`, defaults `staleTime: 10_000` and `refetchOnWindowFocus: false` so SSE-driven invalidations drive re-fetches without the review page clobbering itself when the user tabs back. Renders `<Toaster />` (the `sonner` root — `ErrorBanner` and later 2B hooks use it) and `<ReactQueryDevtools />` in development.

### `lib/api/jobs.ts`

The typed API namespace. Four methods at ship time — `list`, `get`, `create`, `retry` — plus TypeScript types mirroring the backend Pydantic schemas: `SignalItem` (`value`, `source`, `inference_basis`), `SignalSnapshot` (the 4-bucket legacy shape — `required_skills`, `preferred_skills`, `must_haves`, `good_to_haves`, plus `min_experience_years`, `seniority_level`, `role_summary`), `JobStatus` (the 4-state Literal), `JobPostingSummary`, `JobPostingWithSnapshot`, `JobStatusEvent`, and `CreateJobBody`.

`recruiter` as a `source` value is included in the TS type even though Phase 2A never emits it — 2B will. Phase 2B adds `saveSignals`, `confirmSignals`, `triggerEnrich` and rewrites the types to match the v2 signal schema; see `phase-2b-implementation.md` Section 8.

### `useJob(jobId)` (`lib/hooks/use-job.ts`)

Thin TanStack Query wrapper:

```ts
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

`queryKey: ['jobs', jobId]` is the shape both the SSE hook and the `ErrorBanner` retry use for invalidation.

**Spec drift — list vs detail cache collision.** Commit `1369b42` (`fix(jobs): narrow list query key to avoid clobbering detail caches`) narrowed the list view's key from `['jobs']` to `['jobs', 'list', { filters }]` so that `invalidateQueries({ queryKey: ['jobs', jobId] })` only touches the detail cache. The fix is post-2A — mentioned here for cross-reference.

### `useJobStatusStream(jobId)` (`lib/hooks/use-job-status-stream.ts`)

Opens an SSE connection via `@microsoft/fetch-event-source` (not the native `EventSource`, which can't send `Authorization` headers) inside a `useEffect`. The Supabase token is fetched first via `getFreshSupabaseToken()`, then `.then()`-chained into `fetchEventSource(...)` because `await` is impossible inside the sync options object. On each `onmessage` it parses the payload into `JobStatusEvent`, stores it in local `useState`, and calls `queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })` so `useJob` re-fetches. An `AbortController` is the cleanup; navigating away aborts the stream and the `return` in `job_status_event_generator` clips the backend side.

**Spec drift — absolute reconnect ceiling.** Commit `4dc26b9` added an absolute reconnect ceiling so a dead backend can't spin the frontend into an infinite reconnect loop. Post-ship fix — see `phase-hardening-implementation.md` Section 14.

### The three pages

- **`/jobs` — list.** Uses `useQuery({ queryKey: ['jobs'], ... })` to fetch the summary list, renders a shadcn `<table>` with status `<Badge>`s or an empty state. `STATUS_LABELS` maps each state to a recruiter-friendly label (`draft → Draft`, `signals_extracting → Extracting`, `signals_extraction_failed → Failed`, `signals_extracted → Ready`).
- **`/jobs/new` — paste form.** Wired to `react-hook-form` + `zod` with title / raw JD / org unit selector fields. On `201` it navigates to `/jobs/[id]`; on `422` it toasts the `detail` and deep-links to Settings → Org Units → [unit] → Company Profile so the recruiter can fill the missing profile.
- **`/jobs/[jobId]` — three-panel review.** The review shell. Reads `useJob(jobId)` + `useJobStatusStream(jobId)`, branches on `job.status`:
  1. `draft` / `signals_extracting` → `<LoadingSkeleton status={status} />`.
  2. `signals_extraction_failed` → `<ErrorBanner jobId={jobId} error={job.status_error} />`.
  3. `signals_extracted` + `latest_snapshot` + `description_enriched` → the three-panel grid.

  The grid uses a custom `3xl` Tailwind breakpoint (1440px+, defined in `tailwind.config`) to switch from a 2-column layout (rail + content) to a 3-column layout (`1fr_2fr_1.2fr`).

### The three panels

- **`OriginalJdPanel`** (`components/dashboard/jd-panels/OriginalJdPanel.tsx`). Renders `description_raw` + optional `project_scope_raw` inside a `<pre>` with whitespace-preserved font-mono styling. At 3xl it's a full column; below 3xl it collapses to a vertical rail with a rotated "View raw JD" label that opens an overlay modal when clicked. `aria-modal` + Escape-to-close + click-outside-to-close + `role="dialog"` are all wired.
- **`EnrichedJdPanel`** (`components/dashboard/jd-panels/EnrichedJdPanel.tsx`). Renders `description_enriched` as whitespace-preserved prose in the center column. At 3xl it's `col-span-2`, below 3xl it's `col-span-1`. The panel is deliberately minimal in 2A — no inline editing, no diff view. Phase 2B adds a `banner` slot that `StaleBanner` hooks into.
- **`SignalsPanel`** (`components/dashboard/jd-panels/SignalsPanel.tsx`). Right column. Renders `role_summary` at the top, then four sectioned groups (`Required Skills`, `Preferred Skills`, `Must Haves`, `Good to Haves`) each as a wrap-flex of `SignalChip`s, then a two-column footer with `min_experience_years` and `seniority_level`. **Read-only** in 2A — the only action available when signals look wrong is the `ErrorBanner` retry, which just re-runs Call 1 with the same inputs. Phase 2B wraps this panel in a `SignalsPanelWrapper` that toggles between the read-only panel and an `EditableSignalsPanel`.

### `SignalChip` — provenance-aware

`components/dashboard/jd-panels/SignalChip.tsx` is the chip primitive the Signals panel (and every later phase's signal surface) uses. It branches on `item.source`:

- **`ai_extracted`** → blue solid pill with a blue dot prefix.
- **`ai_inferred`** → amber pill with a **dashed** border, amber dot prefix, and a Base UI `<Tooltip>` wrapping the chip. On hover the tooltip shows `"AI-inferred signal"` / `item.inference_basis` / `"Verify before confirming."`. This is how the frontend surfaces the backend's provenance contract — recruiters can tell at a glance which signals the AI read off the JD verbatim and which it inferred, with a visible rationale for each inferred chip.
- **`recruiter`** → green solid pill. Unused in 2A, supported for Phase 2B's edit mode.

**Base UI quirk:** shadcn v4 uses Base UI primitives, not Radix. `<TooltipProvider delay={150}>` (not `delayDuration`); `<TooltipTrigger render={<span>...</span>} />` (not `asChild`). Documented in `frontend/app/CLAUDE.md` → "shadcn v4 / Base UI".

### `LoadingSkeleton` and `ErrorBanner`

**`LoadingSkeleton`** (`components/dashboard/jd-panels/LoadingSkeleton.tsx`) accepts `status: JobStatusEvent | null` and renders the same three-column grid as the real view with `<Skeleton>` rows. Section labels (`Original JD`, `Enriched JD`, `Signals`, `Role Summary`, `Required Skills`, `Must Haves`) are **pre-rendered** in the skeleton — not shimmered — so the transition to real content feels like filling in blanks. The center column carries an SSE-bound status pill (`"Dispatching extraction job…"` → `"Extracting signals and enriching JD…"` once the first event arrives) with an animated blue dot.

**`ErrorBanner`** (`components/dashboard/jd-panels/ErrorBanner.tsx`) renders a red alert with the sanitized `status_error` and a **Retry extraction** button. On click it calls `jobsApi.retry(token, jobId)`, invalidates `['jobs', jobId]`, and toasts the result. The backend re-dispatches the actor; the SSE stream picks up the state change and the banner is replaced by the skeleton on the next poll.

### Cache invalidation flow

1. Page mounts → `useJob(jobId)` fires `GET /api/jobs/{id}` → returns `status: 'signals_extracting'`, `latest_snapshot: null`.
2. `useJobStatusStream(jobId)` opens the SSE stream in parallel.
3. Page renders `<LoadingSkeleton status={null} />` for a beat, then `<LoadingSkeleton status={status} />` once the first event arrives.
4. Backend actor finishes → SSE emits a `status` event with `signals_extracted` → client invalidates `['jobs', jobId]` → SSE closes (terminal state).
5. TanStack re-fetches `/api/jobs/{id}` and now returns `latest_snapshot` + `description_enriched`.
6. Page re-renders → `showPanels` flips true → skeleton is replaced by the three-panel grid.

On failure the same flow runs with `signals_extraction_failed`, `showError` flips true, and `ErrorBanner` renders with the sanitized message.

---

## 10. Module Layout

New / modified files relative to Phase 1:

```
backend/nexus/
├── app/
│   ├── config.py                                      ← +openai_* settings, -anthropic_api_key
│   ├── main.py                                        ← +exception handlers (409, 422)
│   ├── models.py                                      ← +JobPosting, JobPostingSignalSnapshot, Session
│   ├── worker.py                                      ← NEW — Dramatiq entrypoint
│   ├── ai/                                            ← NEW package
│   │   ├── __init__.py
│   │   ├── config.py                                  ← AIConfig (env-driven)
│   │   ├── client.py                                  ← get_openai_client() — instructor + langfuse
│   │   ├── prompts.py                                 ← PromptLoader
│   │   └── schemas.py                                 ← ExtractionOutput + provenance validators
│   └── modules/
│       ├── auth/permissions.py                        ← +jobs.view
│       ├── jd/                                        ← fleshed from Phase 1 stub
│       │   ├── __init__.py
│       │   ├── actors.py                              ← extract_and_enhance_jd Dramatiq actor
│       │   ├── authz.py                               ← require_job_access() ancestry walk
│       │   ├── errors.py                              ← IllegalTransitionError, CompanyProfileIncompleteError, sanitize_error_for_user
│       │   ├── router.py                              ← 5 endpoints under /api/jobs
│       │   ├── schemas.py                             ← Pydantic request/response
│       │   ├── service.py                             ← create_job_posting, list, get, retry, status
│       │   ├── sse.py                                 ← job_status_event_generator
│       │   └── state_machine.py                       ← LEGAL_TRANSITIONS + transition() helper
│       └── org_units/
│           ├── company_profile.py                     ← NEW — strict Pydantic schema
│           └── service.py                             ← +find_company_profile_in_ancestry, profile validation, completed_at/by stamps
├── prompts/
│   └── v1/
│       └── jd_enhancement.txt                         ← NEW — Call 1 system prompt
├── tests/
│   └── fixtures/
│       └── company_profile_enums.json                 ← NEW — enum parity source of truth
└── docker-compose.yml                                 ← +nexus-worker service
```

Frontend (`frontend/app/`):

```
frontend/app/
├── app/(dashboard)/jobs/
│   ├── page.tsx                                       ← NEW — list page
│   ├── new/page.tsx                                   ← NEW — paste form (RHF + Zod + org unit selector)
│   └── [jobId]/page.tsx                               ← NEW — three-panel review
├── components/dashboard/
│   ├── providers.tsx                                  ← NEW — TanStack Query + Toaster
│   ├── sidebar.tsx                                    ← +Jobs link
│   └── jd-panels/                                     ← NEW
│       ├── SignalChip.tsx
│       ├── OriginalJdPanel.tsx
│       ├── EnrichedJdPanel.tsx
│       ├── SignalsPanel.tsx
│       ├── LoadingSkeleton.tsx
│       └── ErrorBanner.tsx
└── lib/
    ├── api/jobs.ts                                    ← NEW — typed API namespace
    └── hooks/
        ├── use-job.ts                                 ← NEW — TanStack Query GET
        └── use-job-status-stream.ts                   ← NEW — fetch-event-source SSE
```

---

## 11. How to Add a New Prompt Version

1. Create `backend/nexus/prompts/v2/` and copy + edit the prompt file.
2. Instantiate `PromptLoader(version="v2")` in the code path that should use the new version (or switch the default in `app/ai/prompts.py`).
3. Restart the worker: `docker compose restart nexus-worker`.
4. The next Call 1 dispatch picks up the new prompt.

A hot-reload endpoint is deferred — see the design spec's Deferred Hardening section.

---

## 12. How to Swap the OpenAI Model for a Task

1. Edit `.env`: set `OPENAI_EXTRACTION_MODEL=<new-model-id>` (and optionally `OPENAI_EXTRACTION_EFFORT=<effort>`).
2. Restart the worker: `docker compose restart nexus-worker`.
3. The next dispatch uses the new model. No code change, no redeploy.

`AIConfig` properties read from `settings` on every access, so restarting the worker is sufficient — no need to rebuild the image.

---

## 13. Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Job stuck in `signals_extracting` forever | Dramatiq enqueue succeeded but no worker consumed the message (worker down, Redis unreachable when `.send` was called) | `docker compose ps nexus-worker`; `docker compose logs nexus-worker`. No automatic recovery in 2A — operator must manually `UPDATE job_postings SET status = 'signals_extraction_failed' WHERE id = ...` then use the retry button. |
| All Call 1 attempts fail with `signals_extraction_failed` | Wrong model ID in `.env`, or `reasoning_effort` parameter shape mismatch for the model | `docker compose logs nexus-worker \| grep jd.actor.call1_failed` — the structlog `exc_info` will show the exception type. Check `.env` `OPENAI_EXTRACTION_MODEL`. |
| Langfuse trace not appearing | `LANGFUSE_HOST` empty or Langfuse instance unreachable | Langfuse is intentionally a no-op when the host is unset; set `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` in `.env` to enable. |
| 422 on JD creation | Target org unit has no ancestor with a completed profile | Visit Settings → Org Units → [company] → Company Profile tab and fill all four fields. |
| 409 Conflict on retry | Job is not in `signals_extraction_failed` state | Only failed jobs can be retried. The retry endpoint's precondition is enforced by the state machine. |
| Dramatiq worker exits on boot with `--watch` error | `--watch` flag requires `watchdog` dependency not installed in 2A | The docker-compose command was trimmed to `dramatiq app.worker --processes 2 --threads 4`. For dev hot-reload, add `watchdog` to `pyproject.toml` extras and re-add `--watch /app/app`. |
| `supabase db reset` wipes `projectx_test` database | The reset command drops all databases; `projectx_test` isn't recreated automatically | After `supabase db reset`, run `docker exec supabase_db_<project> psql -U postgres -c "CREATE DATABASE projectx_test;"` before running pytest. |
| `pytest` in container fails with `ConnectionRefusedError` to `127.0.0.1:54322` | Phase 1 had this as the default, unreachable from inside Docker | Phase 2A fixed the default in `conftest.py` to `host.docker.internal:54322`. Override via `TEST_DATABASE_URL` env var for non-container runs. |

---

## 14. Known Gaps

See the Deferred Hardening section of the design spec for the full list. The most important for operators:

1. **Dual-write risk**: if Redis is down when a job is created, the row sits in `signals_extracting` with no automatic recovery in 2A. Manual fix: update the row to `signals_extraction_failed` and use the retry button.
2. **`updated_at` trigger only on Phase 2A tables**: Phase 1 tables (`clients`, `users`, etc.) don't have the trigger. `public.set_updated_at()` is defined globally in migration `20260410000001` and can be applied to Phase 1 tables in a future cleanup.
3. **No frontend tests**: Vitest is deferred to Phase 2B.
4. **`--watch` hot-reload not enabled in dev**: requires adding `watchdog` to `pyproject.toml` and re-adding the flag to the docker-compose worker command.
5. **Prompt hot-reload endpoint not built**: restart the worker to pick up new prompt files.
