# Phase 2C.1 Implementation — Developer Documentation

**Scope:** Pipeline builder — template library, per-job pipeline instances, stage CRUD, drag-to-reorder, auto-apply on signal confirmation
**Status:** Complete and functional
**Last updated:** 2026-04-15

See also:
- Design spec: `docs/superpowers/specs/2026-04-12-phase-2c1-pipeline-builder-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-12-phase-2c1-pipeline-builder.md`
- Phase 2B walkthrough: `docs/phase-2b-implementation.md`
- Phase 2C.2 walkthrough: `docs/phase-2c2-implementation.md` (question-bank generation, which consumes the pipeline)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema (migrations 0004, 0005)](#2-database-schema-migrations-0004-0005)
3. [Template Library](#3-template-library)
4. [Per-Job Pipeline Instance](#4-per-job-pipeline-instance)
5. [Stage Configuration](#5-stage-configuration)
6. [Auto-Apply on Signal Confirmation](#6-auto-apply-on-signal-confirmation)
7. [API Reference](#7-api-reference)
8. [Frontend Architecture](#8-frontend-architecture)
9. [Known Gaps](#9-known-gaps)
10. [Cross-references](#10-cross-references)

---

## 1. Architecture Overview

Phase 2B ended with a job sitting in `signals_confirmed`. Phase 2C.1 turns that confirmation event into a first-class pipeline object: a tenant-scoped template library, a per-job pipeline instance snapshotted from a template, and a funnel UI that lets recruiters reorder, edit, swap, reset, or save-as-template without AI in the loop. Phase 2C.2 then attaches per-stage question banks to those stage rows.

Three things ship as one unit:

1. **Template library, per org unit.** `pipeline_templates` + `pipeline_template_stages` are tenant-scoped tables keyed by `org_unit_id`. A hand-written 6-entry **starter pack** lives in Python code (not the database) and is offered via a dedicated endpoint so recruiters can clone any starter into their library with one POST. Ancestry-walking authz (`require_template_access`) reuses the same pattern as `require_job_access` — super admin short-circuits, otherwise walk the template's org unit ancestry looking for `org_units.manage`.
2. **Per-job pipeline instance, snapshotted.** `job_pipeline_instances` is 1:1 with `job_postings` (unique index on `job_posting_id`). Each instance owns its own `job_pipeline_stages` rows, which are **copies** of the source template stages — editing them does not touch the template, and `source_template_id` is nullable with `ON DELETE SET NULL` so the instance survives template deletion. CRUD on stages uses a diff-and-sync update that matches incoming stages by `id` to preserve UUIDs across edits; this is load-bearing for Phase 2C.2, because question banks FK to `job_pipeline_stages.id`.
3. **Auto-apply on signal confirmation.** `jd.service.confirm_signals` calls `auto_apply_pipeline_on_confirmation` after the status transition flushes. The helper walks a three-step resolution chain (last-used → org unit default → system fallback) and creates an instance. The call is wrapped in a `try/except` that treats `PipelineAlreadyExistsError` as idempotent (debug log only) and routes every other exception to `error` logging plus a `job_pipeline.auto_apply_failed` audit event. The confirmation itself is never rolled back by auto-apply failure — pipeline auto-apply is a convenience, not a precondition.

Phase 2C.1 adds no new state machine. The existing JD states are unchanged; a pipeline is a sibling row with its own lifecycle (none yet — Phase 2C.2 introduces per-stage question-bank status).

### Module layout

```
backend/nexus/app/modules/pipelines/
├── __init__.py
├── router.py         ← /api/pipeline-templates/*, /api/org-units/{id}/pipeline-templates,
│                        /api/jobs/{id}/pipeline/*
├── service.py        ← template CRUD, instance CRUD, auto-apply hook
├── schemas.py        ← Pydantic request/response, discriminated unions
├── starter_pack.py   ← STARTER_TEMPLATES dict + SYSTEM_FALLBACK_STARTER
├── authz.py          ← require_template_access, require_instance_access
└── errors.py         ← CannotDeleteDefault, NoSourceTemplate, PipelineAlreadyExists, …
```

Frontend surface (`frontend/app/`):

```
components/dashboard/pipeline/
├── PipelineFunnel.tsx              ← reusable funnel render primitive
├── PipelineFlowColumn.tsx          ← drag-to-reorder sortable column (UnifiedPipelineView)
├── UnifiedPipelineView.tsx         ← split view: flow column + StageInspectorPanel
├── StageSlab.tsx, SortableStageCard.tsx, StageFlowCard.tsx
├── StageInspectorPanel.tsx, StageConfigurationTab.tsx
├── StageConfigDrawer.tsx           ← modal editor — focus mgmt from commit dd2f528
├── SignalFilterEditor.tsx, PassCriteriaEditor.tsx, DifficultySlider.tsx
├── TemplatePickerDialog.tsx        ← focus mgmt from commit dd2f528
├── StarterPackBrowser.tsx, TemplateLibraryCard.tsx
├── StageActionsMenu.tsx, StageConnectorOverlay.tsx, EmptyInspectorState.tsx

app/(dashboard)/
├── jobs/[jobId]/
│   ├── layout.tsx                  ← tab bar: "Job Description" | "Pipeline"
│   ├── page.tsx                    ← JD review; redirects to /pipeline when confirmed
│   └── pipeline/page.tsx           ← pipeline editor shell (TemplatePickerDialog or UnifiedPipelineView)
└── settings/org-units/[unitId]/pipeline-templates/
    ├── page.tsx                    ← library grid
    ├── new/page.tsx                ← create from scratch
    └── [templateId]/page.tsx       ← edit template

lib/api/pipelines.ts                ← typed API namespace
lib/hooks/use-{pipeline-templates,starter-pack,job-pipeline,
              save-pipeline-template,save-job-pipeline,create-job-pipeline}.ts
```

---

## 2. Database Schema (migrations 0004, 0005)

Phase 2C.1 ships two Alembic migrations. `0004_pipeline_builder` creates the four pipeline tables and their constraints. `0005_simplify_signal_filter` flattens the JSONB `signal_filter` column on both stage tables down to a single `include_types` field.

Head after 0005: `0005_simplify_signal_filter`. Subsequent migrations (0006–0012) bolt on Phase 2C.2 and the RLS hardening pass — they also touch the pipeline tables and are described where relevant below.

### `0004_pipeline_builder`

Down revision: `0003_signal_schema_v2`. Four new tables, all tenant-scoped, with RLS enabled on each.

#### `pipeline_templates`

Reusable interview pipelines per org unit. Templates are independent of jobs — editing a template does NOT affect existing job pipeline instances.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID NOT NULL, FK → `clients.id` | RLS scoping |
| `org_unit_id` | UUID NOT NULL, FK → `organizational_units.id` | Owning unit |
| `name` | TEXT NOT NULL | Recruiter-facing label |
| `description` | TEXT NULL | Optional |
| `is_default` | BOOLEAN NOT NULL DEFAULT `false` | Auto-apply chain uses this |
| `from_starter` | TEXT NULL | Starter-pack key if copied from a starter, else NULL |
| `created_by` | UUID NOT NULL, FK → `users.id` | |
| `updated_by` | UUID NULL, FK → `users.id` | Stamped by `update_template` / `set_template_as_default` |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | Service writes it; no DB trigger |

**Constraints:**
- Partial unique index `ix_pipeline_templates_org_unit_default ON pipeline_templates (org_unit_id) WHERE is_default = true` — at most one default per org unit.

**Spec drift — updated-at trigger:** The spec calls for a reused `set_updated_at()` trigger. None of the pipeline tables carry a trigger in 0004; the service layer writes `updated_at` directly from Python whenever a template or instance is mutated (`_now_utc()` in `service.py`).

#### `pipeline_template_stages`

Ordered stages within a template. Positions are 0-indexed.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL, FK → `clients.id` | |
| `template_id` | UUID NOT NULL, FK → `pipeline_templates.id` **ON DELETE CASCADE** | |
| `position` | INTEGER NOT NULL | 0-indexed |
| `name` | TEXT NOT NULL | Recruiter-editable label |
| `stage_type` | VARCHAR NOT NULL | CHECK `IN ('phone_screen','ai_interview','human_interview','panel_interview','take_home')` |
| `duration_minutes` | INTEGER NOT NULL | CHECK `> 0 AND <= 240` |
| `difficulty` | VARCHAR NOT NULL | CHECK `IN ('easy','medium','hard')` |
| `signal_filter` | JSONB NOT NULL | See [Stage Configuration](#5-stage-configuration) |
| `pass_criteria` | JSONB NOT NULL | Discriminated union |
| `advance_behavior` | VARCHAR NOT NULL | CHECK `IN ('auto_advance','manual_review')` |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | |

**Constraints:**
- `UNIQUE (template_id, position)` via `uq_template_stage_position` — enforces unique position within a template.
- Four CHECK constraints (stage type, difficulty, advance behavior, duration) defined at migration time via `op.create_check_constraint`.

#### `job_pipeline_instances`

Per-job pipeline snapshots. 1:1 with `job_postings`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL, FK → `clients.id` | |
| `job_posting_id` | UUID NOT NULL, FK → `job_postings.id` **ON DELETE CASCADE** | |
| `source_template_id` | UUID NULL, FK → `pipeline_templates.id` **ON DELETE SET NULL** | Nullable — scratch / starter-direct / deleted-source |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | Stamped from Python on every stage-sync |

**Constraints:**
- `UNIQUE (job_posting_id)` via `uq_job_pipeline_instance_job` — one pipeline per job.

No `status` column. Phase 2C.2 adds status to the question-bank rows, not to the instance.

#### `job_pipeline_stages`

Same shape as `pipeline_template_stages`, but FK to `job_pipeline_instances`. These are the editable snapshot copies.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Stable across edits — load-bearing for Phase 2C.2 question-bank FKs |
| `tenant_id` | UUID NOT NULL, FK → `clients.id` | |
| `instance_id` | UUID NOT NULL, FK → `job_pipeline_instances.id` **ON DELETE CASCADE** | |
| `position` | INTEGER NOT NULL | 0-indexed |
| `name` | TEXT NOT NULL | |
| `stage_type` | VARCHAR NOT NULL | CHECK — same enum |
| `duration_minutes` | INTEGER NOT NULL | CHECK `> 0 AND <= 240` |
| `difficulty` | VARCHAR NOT NULL | CHECK — same enum |
| `signal_filter` | JSONB NOT NULL | |
| `pass_criteria` | JSONB NOT NULL | |
| `advance_behavior` | VARCHAR NOT NULL | CHECK |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT `NOW()` | |

**Constraints:**
- `UNIQUE (instance_id, position)` via `uq_job_pipeline_stage_position`.

#### RLS on all four tables

Each of `pipeline_templates`, `pipeline_template_stages`, `job_pipeline_instances`, and `job_pipeline_stages` enables RLS in 0004 with:

```sql
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);

CREATE POLICY service_role_bypass ON <table>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Spec drift — RLS pattern at ship time vs. canonical form:** The shipped 0004 policies deviate from the canonical form in three ways:

1. `tenant_isolation` has `USING` only — no `WITH CHECK`. In principle this blocks writes from tenant sessions (the trap documented in root `CLAUDE.md` under "RLS Pattern"). It worked in development because until migration 0010 the application connected as Supabase's `postgres` role which has `rolbypassrls=true` — every policy is silently skipped. See the hardening phase doc for the full story.
2. The bypass policy is named `service_role_bypass` instead of the canonical `service_bypass`.
3. The tenant predicate uses raw `::uuid` instead of `NULLIF(..., '')::uuid`, which crashes on the pooled-connection empty-GUC case that migration 0011 fixes.

All three issues are corrected by later migrations and are safe **today**:

- **Migration 0011** (`rls_nullif_tenant`) drops every `tenant_isolation` policy on the four pipeline tables and recreates them with the full-command form (`USING (...) WITH CHECK (...)`) **and** the `NULLIF(current_setting(...), '')::uuid` wrapping.
- **Migration 0012** (`rename_service_role_bypass`) drops `service_role_bypass` on the four pipeline tables and recreates it as `service_bypass`.
- **Migration 0010** introduces the `nexus_app` role (`NOBYPASSRLS`) and `get_tenant_db` / `get_bypass_db` switch to it at session start.
- **Startup assertion** `_assert_rls_completeness` in `app/main.py` enumerates all four pipeline tables in `_TENANT_SCOPED_TABLES` and aborts boot if either canonical policy is missing with non-NULL `WITH CHECK`.

The net effect at runtime (head = `0012_rename_service_bypass`) is the correct full-command, NULLIF-wrapped, canonically-named pair on all four tables. The flawed 0004 DDL is history — do not copy it.

### `0005_simplify_signal_filter`

Down revision: `0004_pipeline_builder`. No schema changes — this is a JSONB data migration that rewrites every existing `signal_filter` blob on both stage tables:

```sql
UPDATE pipeline_template_stages
SET signal_filter = jsonb_build_object(
    'include_types',
    COALESCE(
        signal_filter->'include_types',
        '["competency","experience","credential","behavioral"]'::jsonb
    )
);
-- same for job_pipeline_stages
```

This drops `include_stages`, `include_weights`, and `include_priority` from every row and leaves only `include_types`. The `downgrade()` best-effort restores the dropped fields with permissive defaults.

**Spec drift — signal filter shape:** The design spec's filter is a 4-field JSON object:

```json
{
  "include_types": ["competency","experience","credential","behavioral"],
  "include_stages": ["screen"],
  "include_weights": [1,2,3],
  "include_priority": ["required","preferred"]
}
```

The shipped schema (after 0005) is a 1-field object:

```json
{ "include_types": ["competency","experience","credential","behavioral"] }
```

The Pydantic `SignalFilter` model in `schemas.py` uses `model_config = ConfigDict(extra="forbid")` so any extra legacy keys are rejected at the API boundary. The docstring on `SignalFilter` calls this out explicitly — stage-level filtering by weight, priority, and origin stage was dropped because "question-generation at runtime (Phase 2C.2) will allocate probe time across signals based on weight × priority × stage depth", leaving `include_types` as the only dimension recruiters still want to control per stage (credentials are verified via documents, not interviews; behavioral is best probed by humans). The starter pack and frontend TS types match the simplified shape.

---

## 3. Template Library

The template library is the per-org-unit store of reusable pipelines. It is not global — templates live under an org unit and are visible through ancestry-walking authz, the same pattern as `jobs`. Starter packs are not stored here; they live in Python and are copied in on demand.

### Starter pack (`starter_pack.py`)

`STARTER_TEMPLATES: Final[dict[str, dict[str, Any]]]` holds six hand-written entries, keyed by:

| Key | Stages |
|---|---|
| `standard_technical` | Phone Screen → AI Technical Interview → Hiring Manager Panel |
| `fast_track` | Phone Screen → AI Interview |
| `screening_only` | Phone Screen |
| `senior_leadership` | Phone Screen → AI Technical Interview → Hiring Manager Panel → Executive Interview |
| `sales_commercial` | Phone Screen → Human Interview |
| `volume_hiring` | Phone Screen |

`SYSTEM_FALLBACK_STARTER: Final[str] = "standard_technical"` is the last-resort pick for auto-apply. The dict is `Final` and immutable at runtime. The starter stages use the post-0005 `signal_filter` shape (`include_types` only).

Starter content is served unchanged by `GET /api/pipeline-templates/starter-pack`, which is available to any authenticated user and has no tenant scoping — the data is static and identical across tenants.

### Template creation

`POST /api/org-units/{unit_id}/pipeline-templates` accepts a `CreateTemplateRequest` discriminated union:

1. **From scratch** — `{"source": "scratch", "name": str, "description": str|null, "is_default": bool, "stages": [PipelineStageInput, …]}`. `stages` must be non-empty and positions must be a sequential `[0, 1, …, N-1]` (validated by a Pydantic `model_validator`). Service `create_template_from_scratch` inserts the template row, then inserts each stage.
2. **From starter** — `{"source": "starter", "starter_key": str, "name": str, "description": str|null, "is_default": bool}`. Service `create_template_from_starter` raises `StarterKeyNotFoundError` (→ 404) if the key is unknown, otherwise clones the stages into the library.

In both cases:
- The endpoint calls `_require_org_unit_manage(db, unit_id, user)` — super admin short-circuits, otherwise walks the unit's ancestry and checks `org_units.manage`. Rejects with 403 if missing.
- If `is_default=true`, `_clear_existing_default(db, org_unit_id)` runs first, setting `is_default=false` on any existing default in the same unit. This ensures the partial unique index never trips.
- Response is a full `PipelineTemplateResponse` loaded via `get_template_with_stages` so the caller gets the stages list and resolved IDs in one round trip.

### Template listing, update, default toggle, delete

| Flow | Endpoint | Service | Notes |
|---|---|---|---|
| List templates in a unit | `GET /api/org-units/{unit_id}/pipeline-templates` | `list_templates_for_org_unit` | 2-query load: one for templates, one for all their stages; grouped in Python. Sort order: `is_default DESC, created_at ASC`. |
| Update name / description / stages | `PATCH /api/pipeline-templates/{id}` | `update_template` | Partial. If `stages` is provided, all existing stages are deleted and reinserted in one flush — atomic replace, not diff. Does not mutate `is_default` (use dedicated endpoint). Stamps `updated_by` + `updated_at`. |
| Set default | `POST /api/pipeline-templates/{id}/set-default` | `set_template_as_default` | Calls `_clear_existing_default` then flips `is_default=true` on the target. Stamps `updated_by` + `updated_at`. |
| Delete | `DELETE /api/pipeline-templates/{id}` | `delete_template` | Raises `CannotDeleteDefaultError` (→ 409) if `is_default=true`. Otherwise deletes; CASCADE drops stages. Existing `job_pipeline_instances.source_template_id` values become NULL via the `ON DELETE SET NULL` FK. |

All four write endpoints call `require_template_access(db, template_id, user, "manage")` — loads the template by id, returns 404 if missing, super admin short-circuit, otherwise walks the template's org unit ancestry looking for `org_units.manage`. The `action` argument is accepted for symmetry with `require_job_access` but templates always require `org_units.manage` — the docstring flags this explicitly.

---

## 4. Per-Job Pipeline Instance

A job pipeline instance is a 1:1 snapshot of a template onto a `job_postings` row. Editing an instance never propagates back to the source template — that is the `update_source_template_from_job` endpoint's job, and it is an explicit recruiter action.

### Instance creation — three sources

`POST /api/jobs/{job_id}/pipeline` accepts a `CreateJobPipelineRequest` discriminated union:

1. **From template** — `{"source": "template", "template_id": UUID}`. Service `create_job_pipeline_from_template` copies stages row-by-row from `pipeline_template_stages` into `job_pipeline_stages`, with `source_template_id` set to the source template.
2. **From starter** — `{"source": "starter", "starter_key": str}`. Service `create_job_pipeline_from_starter` reads `STARTER_TEMPLATES[starter_key]` and inserts stages directly from the Python dict. `source_template_id` is NULL — no library template is involved.
3. **From scratch** — `{"source": "scratch", "stages": [...]}`. Service `create_job_pipeline_from_scratch` takes explicit stage inputs (same validation as template creation: non-empty, sequential positions). `source_template_id` is NULL.

All three helpers enforce two preconditions:
- `job.status == 'signals_confirmed'`, else raise `JobNotInConfirmedStateError` → 409.
- No existing instance for this job, else raise `PipelineAlreadyExistsError` → 409.

The router handler calls `require_instance_access(db, job_id, user, "manage")` before dispatching — ancestry walk for `jobs.manage`, returns `(job, instance_or_none)`. 404 if the job is missing; 403 if the permission walk fails.

### Instance read

`GET /api/jobs/{job_id}/pipeline` → `get_job_pipeline_with_stages(db, job_id)` returns `(instance, stages, source_template | None)`. Stages ordered by `position`. The source template is resolved in a separate query only if `source_template_id` is non-null. Returns 404 if no instance exists (the frontend treats 404 as "no pipeline yet" — see `pipelinesApi.getJobPipeline`).

### Stage CRUD — diff-and-sync update

`PATCH /api/jobs/{job_id}/pipeline` accepts `{"stages": [PipelineStageUpdateInput, ...]}` and delegates to `update_job_pipeline_stages`. The update is a **diff-and-sync**, not an atomic replace — this distinction is load-bearing:

1. Load all existing `job_pipeline_stages` for the instance.
2. Partition incoming stages by id presence: incoming items with an `id` are matched against existing rows; items with `id=None` (or bare `PipelineStageInput`) are new inserts.
3. For each existing row matched by id, overwrite its fields (position, name, type, duration, difficulty, signal_filter, pass_criteria, advance_behavior) **in place** — preserving the row UUID.
4. Existing rows not in the incoming list are `db.delete()`d.
5. Flush deletions first to free up `(instance_id, position)` unique-index slots before inserts (otherwise a reorder-plus-insert at the same position trips the constraint).
6. Insert new stages from the remainder.
7. Stamp `instance.updated_at = _now_utc()`, flush, log.

The matching rule is what makes Phase 2C.2's per-stage question banks survive stage edits: question banks FK to `job_pipeline_stages.id`, and in-place updates preserve that UUID even when position, name, or any other field changes. This is called out directly in the `update_job_pipeline_stages` docstring and in `PipelineStageUpdateInput`'s model docstring.

### Reorder, swap, reset, save-as-template, update-source

| Flow | Endpoint | Service | Behaviour |
|---|---|---|---|
| Reorder | same `PATCH /api/jobs/{job_id}/pipeline` | `update_job_pipeline_stages` | Frontend rewrites `position` on every stage by array index after drag end, then sends the whole list. Diff-and-sync matches existing rows by id and updates in place. |
| Swap template | `POST /api/jobs/{job_id}/pipeline/swap` | `swap_job_pipeline` | Accepts the same `CreateJobPipelineRequest` shape. Deletes the existing instance (CASCADE drops stages), then calls one of the three creators. The new instance has a **new `id`**; callers reload via `get_job_pipeline_with_stages`. |
| Reset to source | `POST /api/jobs/{job_id}/pipeline/reset` | `reset_job_pipeline_to_source` | Requires `source_template_id != NULL`, else raises `NoSourceTemplateError` → 409. Deletes all job stages, re-copies from the template. Stamps `instance.updated_at`. |
| Save-as-template | `POST /api/jobs/{job_id}/pipeline/save-as-template` | `save_job_pipeline_as_template` | Cross-cutting write. Requires both `jobs.manage` (via `require_instance_access`) **and** `org_units.manage` on the job's org unit (explicit `_require_org_unit_manage` call). Creates a new `pipeline_templates` row in the job's org unit with the current stages cloned in. Rejects empty pipelines with `ValueError`. |
| Update source template | `POST /api/jobs/{job_id}/pipeline/update-source-template` | `update_source_template_from_job` | Cross-cutting write. Requires both `jobs.manage` on the job and `org_units.manage` on the source template (via `require_template_access`). Deletes the source template's stages and re-writes them from the current job stages. Rejects if `source_template_id` is NULL. |

**Spec drift — 2C.1 deferred drag-to-reorder; it shipped anyway.** The design spec lists drag-and-drop reorder in the "Non-Goals" deferred list (up/down buttons as the initial shape). What actually shipped is drag-to-reorder via `@dnd-kit` with full `KeyboardSensor` a11y wiring — see [Section 8](#8-frontend-architecture). The 2C.1 doc now reflects shipped behaviour.

---

## 5. Stage Configuration

Stage rows carry eight configuration fields. Pydantic schemas in `schemas.py` are the source of truth; the JSONB columns are typed through `SignalFilter`, the `PassCriteria` discriminated union, and string Literals.

| Field | Type | Default | Purpose |
|---|---|---|---|
| `position` | `int`, `ge=0` | — (required) | 0-indexed order within the pipeline |
| `name` | `str`, `min_length=1, max_length=200` | — | Recruiter-facing stage label |
| `stage_type` | Literal: `phone_screen` / `ai_interview` / `human_interview` / `panel_interview` / `take_home` | — | Determines conductor (AI vs human) and medium (audio/video/async) — fixed, no override |
| `duration_minutes` | `int`, `gt=0, le=240` | — | Session time budget (Phase 2C.2 uses this as the *session limit*, not the generation budget) |
| `difficulty` | Literal: `easy` / `medium` / `hard` | — | Drives question-bank prompt difficulty |
| `signal_filter` | `SignalFilter` object | — | `{"include_types": SignalFilterType[]}` — the only filter dimension after migration 0005 |
| `pass_criteria` | Discriminated union (by `type`) | — | See below |
| `advance_behavior` | Literal: `auto_advance` / `manual_review` | — | Whether a passing candidate flows through automatically |

### `SignalFilter` (`SignalFilter`, `schemas.py`)

```python
class SignalFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_types: list[SignalFilterType]   # competency / experience / credential / behavioral
```

Extra keys are rejected at the API boundary; legacy 4-field filters (`include_stages`, `include_weights`, `include_priority`) are not accepted. See [spec drift note in Section 2](#2-database-schema-migrations-0004-0005).

### `PassCriteria` (discriminated union)

Three variants, discriminated by a `type` field:

| Variant | Shape | Meaning |
|---|---|---|
| `PassCriteriaKnockout` | `{"type": "all_knockouts_pass"}` | Passes if every knockout signal was met |
| `PassCriteriaThreshold` | `{"type": "score_threshold", "threshold": int}` with `threshold` constrained to `ge=0, le=100` | Passes if composite score ≥ threshold |
| `PassCriteriaManual` | `{"type": "manual_review"}` | Human decision required |

### Stage input variants

- `PipelineStageInput` — bare input with the 8 fields above, `extra="forbid"`. Used by create-from-scratch flows (templates and job pipelines).
- `PipelineStageUpdateInput` — extends `PipelineStageInput` and adds an optional `id: UUID | None = None`. Used on `PATCH /api/jobs/{job_id}/pipeline` so existing stages carry their id (enabling the diff-and-sync update) while newly-added stages omit it.
- `PipelineStageResponse` — `PipelineStageInput` + `id: UUID`, always present on GET responses.

---

## 6. Auto-Apply on Signal Confirmation

`auto_apply_pipeline_on_confirmation(db, *, job, actor_id)` lives in `pipelines/service.py` and is the only cross-module integration point between `jd` and `pipelines`. It runs inside `jd.service.confirm_signals` **after** the state transition `signals_extracted → signals_confirmed` has flushed, so the job is already marked confirmed by the time the pipeline creator runs.

### Call flow in `confirm_signals` (`jd/service.py`)

1. Load the latest snapshot, stamp `confirmed_by` / `confirmed_at`, stamp `job.updated_by`.
2. `transition(db, job, to_state='signals_confirmed', ...)` writes the audit row and flips the status.
3. `db.flush()` persists the transition.
4. Log `jd.service.signals_confirmed`.
5. Enter a `try` block that imports `auto_apply_pipeline_on_confirmation` and `PipelineAlreadyExistsError` lazily (local import inside the try — defers the cross-module dependency until signal confirmation actually runs).
6. Call `auto_apply_pipeline_on_confirmation(db, job=job, actor_id=actor_id)`.
7. Catch `PipelineAlreadyExistsError` → `logger.debug('jd.pipeline_auto_apply_skipped_existing', ...)` with `reason='pipeline_already_exists'`. **This is the idempotency path**: re-confirming a job that was previously confirmed (and so already has a pipeline) hits this branch every time. Demoting to `debug` prevents the steady noise floor from burying real errors. Shipped in commit `a7ba2ea` — see Phase 2B walkthrough Section 4.
8. Catch any other exception → `logger.error('jd.pipeline_auto_apply_failed', exc_info=exc)`, then write a `job_pipeline.auto_apply_failed` audit row with a 500-char-truncated error message. A nested `try/except: pass` around the audit write ensures an audit-log failure never cascades back into the confirmation path.
9. Return the job. `confirm_signals` does **not** roll back the state transition on auto-apply failure — the pipeline is a convenience, the confirmation is the source of truth.

### Auto-apply resolution order (service layer)

`auto_apply_pipeline_on_confirmation` runs three checks in order and short-circuits on the first hit:

1. **Guard — instance already exists.** Early-return `None` after logging `pipelines.auto_apply_skipped_existing`. Only fires when the upstream guard in `confirm_signals` somehow gets bypassed (e.g. re-confirming an externally-edited job); the normal re-confirm path is caught by `PipelineAlreadyExistsError` downstream because each creator re-checks existence.
2. **Last-used template in this org unit.** Query `job_pipeline_instances JOIN job_postings` filtered to `org_unit_id = job.org_unit_id AND source_template_id IS NOT NULL`, ordered by `created_at DESC LIMIT 1`. If found **and** the template still exists (second query to verify — templates can be deleted, at which point `source_template_id` is preserved via `ON DELETE SET NULL` on the FK but the template row is gone), call `create_job_pipeline_from_template` with that template.
3. **Org unit default template.** Query `pipeline_templates WHERE org_unit_id = job.org_unit_id AND is_default = true`. If found, call `create_job_pipeline_from_template` with it.
4. **System fallback starter.** Call `create_job_pipeline_from_starter(db, job=job, starter_key=SYSTEM_FALLBACK_STARTER)`. This creates an instance directly from the starter pack data with `source_template_id=NULL` — **no template is added to the library**. Logged as `pipelines.auto_apply_using_system_fallback`.

Each successful branch logs `pipelines.auto_apply_using_{last_used|org_default|system_fallback}` with the job id and template/starter identifier.

### What auto-apply does NOT do

- It does not check whether the recruiter wants a pipeline. Auto-apply is unconditional for confirmed jobs.
- It does not mutate the JD state machine. A successful auto-apply is a side-effect row insert; the job remains in `signals_confirmed`.
- It does not write an audit-log row on success — only logs on failure.
- It does not create a template in the library when the system fallback fires; the starter-pack stages are cloned straight into the job instance.

---

## 7. API Reference

All endpoints live in `app/modules/pipelines/router.py`, mounted by `app/main.py` with no prefix (the routes are absolute `/api/...`). Auth is Bearer Supabase JWT. Tenant scoping is via `get_tenant_db` and `require_template_access` / `require_instance_access` / `_require_org_unit_manage` ancestry walks. Super admin short-circuits every permission check.

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `GET` | `/api/pipeline-templates/starter-pack` | any authenticated user | Return static starter pack (6 entries) |
| `GET` | `/api/org-units/{unit_id}/pipeline-templates` | `org_units.manage` in ancestry | List templates in this org unit with their stages |
| `POST` | `/api/org-units/{unit_id}/pipeline-templates` | `org_units.manage` in ancestry | Create template (from scratch or from starter) |
| `PATCH` | `/api/pipeline-templates/{template_id}` | `org_units.manage` in template's ancestry | Update name, description, and/or stages (stages are atomically replaced) |
| `POST` | `/api/pipeline-templates/{template_id}/set-default` | `org_units.manage` in template's ancestry | Make this the default template in its org unit, clear any existing default |
| `DELETE` | `/api/pipeline-templates/{template_id}` | `org_units.manage` in template's ancestry | Delete; 409 if `is_default=true` |
| `GET` | `/api/jobs/{job_id}/pipeline` | `jobs.view` in job's ancestry | Return the job's pipeline instance with stages; 404 if none |
| `POST` | `/api/jobs/{job_id}/pipeline` | `jobs.manage` in job's ancestry | Create instance (from template, starter, or scratch); 409 if job ≠ `signals_confirmed` or instance exists |
| `PATCH` | `/api/jobs/{job_id}/pipeline` | `jobs.manage` in job's ancestry | Diff-and-sync update of stages (preserves stage UUIDs) |
| `POST` | `/api/jobs/{job_id}/pipeline/swap` | `jobs.manage` in job's ancestry | Delete + recreate instance atomically from a different template / starter / scratch |
| `POST` | `/api/jobs/{job_id}/pipeline/reset` | `jobs.manage` in job's ancestry | Re-copy stages from `source_template_id`; 409 if source is NULL |
| `POST` | `/api/jobs/{job_id}/pipeline/save-as-template` | `jobs.manage` on job + `org_units.manage` on job's org unit | Create a new template in the org unit library from the current job stages |
| `POST` | `/api/jobs/{job_id}/pipeline/update-source-template` | `jobs.manage` on job + `org_units.manage` on template | Write the job's current stages back to the source template; 409 if no source |

### Error shapes

| Error class | HTTP | Body | Raised by |
|---|---|---|---|
| `JobNotInConfirmedStateError` | 409 | `{"detail": "Pipelines can only be created for jobs in signals_confirmed state. This job is in '<state>'."}` | `create_job_pipeline_from_{template,starter,scratch}` |
| `PipelineAlreadyExistsError` | 409 | `{"detail": "This job already has a pipeline instance. Use PATCH to update it."}` | `create_job_pipeline_from_*`; also caught by `confirm_signals` as the idempotency path |
| `StarterKeyNotFoundError` | 404 | `{"detail": "Unknown starter_key: <key>"}` | Template and job-pipeline starter flows; swap flow |
| `CannotDeleteDefaultError` | 409 | `{"detail": "Cannot delete the default template. Set another template as default first, then delete this one."}` | `delete_template` |
| `NoSourceTemplateError` | 409 | `{"detail": "This pipeline has no source template (built from scratch). Nothing to reset or update."}` | `reset_job_pipeline_to_source`, `update_source_template_from_job` |
| Missing permission | 403 | `{"detail": "Missing <permission> in <scope>"}` | Every authz guard |
| Missing job / template / pipeline | 404 | `{"detail": "<resource> not found"}` / `"No pipeline for this job"` | `require_*_access`, GET handlers |

### `POST /api/org-units/{unit_id}/pipeline-templates` — request bodies

**From scratch (`CreateTemplateFromScratch`):**

```json
{
  "source": "scratch",
  "name": "Engineering — Standard",
  "description": "Default engineering pipeline",
  "is_default": false,
  "stages": [
    {
      "position": 0,
      "name": "Phone Screen",
      "stage_type": "phone_screen",
      "duration_minutes": 10,
      "difficulty": "easy",
      "signal_filter": { "include_types": ["competency", "experience"] },
      "pass_criteria": { "type": "all_knockouts_pass" },
      "advance_behavior": "auto_advance"
    }
  ]
}
```

**From starter (`CreateTemplateFromStarter`):**

```json
{
  "source": "starter",
  "starter_key": "standard_technical",
  "name": "Engineering — Standard",
  "description": null,
  "is_default": false
}
```

Description defaults to the starter's own description when `null`. Stage positions must form a contiguous `[0, 1, …, N-1]` sequence — validated by a Pydantic `model_validator` on the scratch variant.

### `PATCH /api/jobs/{job_id}/pipeline` — request body

```json
{
  "stages": [
    {
      "id": "11111111-...",   // existing row — preserved through update
      "position": 0,
      "name": "Phone Screen",
      "stage_type": "phone_screen",
      "duration_minutes": 10,
      "difficulty": "easy",
      "signal_filter": { "include_types": ["competency","experience","credential","behavioral"] },
      "pass_criteria": { "type": "all_knockouts_pass" },
      "advance_behavior": "auto_advance"
    },
    {
      // no id — newly added stage; backend inserts and assigns a UUID
      "position": 1,
      "name": "AI Interview",
      "stage_type": "ai_interview",
      "duration_minutes": 30,
      "difficulty": "medium",
      "signal_filter": { "include_types": ["competency","experience"] },
      "pass_criteria": { "type": "score_threshold", "threshold": 70 },
      "advance_behavior": "auto_advance"
    }
  ]
}
```

Response is `JobPipelineInstanceResponse` with stages reloaded via `get_job_pipeline_with_stages` so freshly-inserted stages come back with their assigned UUIDs.

---

## 8. Frontend Architecture

All files below are under `frontend/app/`. The pipeline surface spans one typed API namespace, six hooks, eighteen components, and four route pages. This section documents shipped structure — see the tree in [Section 1](#1-architecture-overview) for file paths.

### API client (`lib/api/pipelines.ts`)

Typed wrapper over `apiFetch`. Types mirror the backend Pydantic schemas one-to-one, including the simplified `SignalFilter` shape (`{ include_types: (...)[] }`) and the `PassCriteria` discriminated union. The `pipelinesApi` object exposes:

- `getStarterPack`, `listTemplates`, `createTemplate`, `updateTemplate`, `setDefault`, `deleteTemplate`
- `getJobPipeline` — wraps a 404 from the backend as `Promise<null>` (status-based check via `err instanceof ApiError && err.status === 404`, not substring matching, so backend detail messages can change freely)
- `createJobPipeline`, `updateJobPipeline`, `resetJobPipeline`, `saveAsTemplate`, `updateSourceTemplate`, `swapJobPipeline`

### Hooks (`lib/hooks/`)

| Hook | Query/mutation | Key |
|---|---|---|
| `usePipelineTemplates(unitId, { enabled? })` | Query — list templates in a unit | `['pipeline-templates', unitId]` |
| `useStarterPack()` | Query — static starter pack | (see file) |
| `useJobPipeline(jobId)` | Query — single instance (null on 404) | `['job-pipeline', jobId]` |
| `useCreateJobPipeline(jobId)` | Mutation — POST instance | invalidates `['job-pipeline', jobId]` |
| `useSaveJobPipeline(jobId)` | Mutation — PATCH stages (autosave). **No success toast** — the mutation runs every ~800 ms from the autosave debounce and the inline "All changes saved / Saving…" indicator is the authoritative signal. | invalidates `['job-pipeline', jobId]` |
| `useResetJobPipeline(jobId)` | Mutation — POST /reset. Success toast. | invalidates `['job-pipeline', jobId]` |
| `useSwapJobPipeline(jobId)` | Mutation — POST /swap. | invalidates `['job-pipeline', jobId]` |
| `useCreateTemplate / useUpdateTemplate / useSetDefault / useDeleteTemplate` (`use-save-pipeline-template.ts`) | Template mutations. All success-toast and invalidate `['pipeline-templates', unitId]`. | |

All mutation hooks call `getFreshSupabaseToken()` inside `mutationFn` — no inline `supabase.auth.getSession()` calls anywhere in the pipeline surface.

### Routes and shell

| Route | File | Role |
|---|---|---|
| `/settings/org-units/{unitId}/pipeline-templates` | `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx` | Library grid. Shows `<StarterPackBrowser>` (collapsible), `+ Create custom template` link, and `<TemplateLibraryCard>` for each saved template. First-created template is auto-set as default. |
| `/settings/org-units/{unitId}/pipeline-templates/new` | `.../new/page.tsx` | Scratch create form — single PATCH on save |
| `/settings/org-units/{unitId}/pipeline-templates/{templateId}` | `.../[templateId]/page.tsx` | Edit existing template |
| `/jobs/{jobId}` | `app/(dashboard)/jobs/[jobId]/page.tsx` | JD review. Redirects to `/jobs/{id}/pipeline` when `job.status === 'signals_confirmed'` and a pipeline exists and the user did not pass `?tab=jd` — the redirect is deliberately gated on status to avoid trapping users on re-extracted jobs whose old pipeline row still exists. |
| `/jobs/{jobId}/pipeline` | `.../pipeline/page.tsx` | Pipeline shell. If no instance yet: renders a `Pick a pipeline` button that opens `<TemplatePickerDialog>` and calls `useCreateJobPipeline`. If an instance exists: renders `<UnifiedPipelineView>`. |

**Spec drift — `/jobs/[jobId]` tab bar replaced the `Build Pipeline` button.** The spec calls for a "Build Pipeline" / "View Pipeline" button on the job header. What shipped is a two-tab layout (`app/(dashboard)/jobs/[jobId]/layout.tsx`) with tabs for "Job Description" and "Pipeline"; the Pipeline tab is disabled until `job.status === 'signals_confirmed'` with a `title="Confirm signals first"` tooltip. Functionally equivalent — same entry points, same permission gate — but the visual treatment differs.

### `UnifiedPipelineView.tsx`

The heart of the pipeline editor. A split view with a drag-to-reorder flow column on the left (`PipelineFlowColumn`) and a stage inspector panel on the right (`StageInspectorPanel`, with `questions` and `config` tabs — the `questions` tab is Phase 2C.2).

Key behaviours:

- **Local stage state with autosave.** `const [stages, setStages] = useState(...)` is seeded from `pipeline.stages` on mount (the component is keyed by `pipeline.id`). Every edit calls `scheduleSave(nextStages)` which debounces a `saveMutation.mutate` by 800 ms (`AUTOSAVE_DEBOUNCE_MS`). An `editGenRef` tracks the latest edit so a late-returning save doesn't clear `isDirty` if the user has typed more in the meantime.
- **New-stage ID merge after autosave.** `onSuccess` inspects whether any local stage still has `id=undefined` and merges backend-assigned IDs in by position — otherwise newly-added stages would remain in "Saving…" state until a full page refresh. See `use-save-job-pipeline` cache invalidation + the `onSuccess` handler in `UnifiedPipelineView`.
- **Flush-on-unmount.** A cleanup effect flushes any pending debounced save by calling `saveMutation.mutate({ stages: stagesRef.current })` — so leaving the page mid-edit still persists the last keystroke.
- **Selected stage in URL.** `?stage={stageId}` and `?tab={questions|config}` live in the URL, read via `useSearchParams`, written via two `useCallback`s (`selectStage`, `selectStageAndTab`) that both go through `router.replace(..., { scroll: false })`.
- **Auto-select first un-confirmed stage on mount.** An effect runs when the page mounts and no `?stage` is set, pulling the first bank with `status !== 'confirmed'` from `useBanksOverview` (Phase 2C.2) and selecting it; falls back to the first stage. Refactored in commit `73adb68` to drop the `eslint-disable react-hooks/exhaustive-deps` — the effect now depends on `stages.length` (not `stages` itself) and reads the latest list via `stagesRef.current`, preventing the listener from re-running on every keystroke.
- **Keyboard shortcuts.** `Escape` clears selection; `ArrowDown` / `ArrowUp` step through stages; `q` / `c` switch tabs. The keydown handler reads `stagesRef.current` and `selectedStageIdRef.current` so its `useEffect` dep array stays stable at `[selectStage, setActiveTab]` — without the ref mirroring, the document listener would tear down and reattach on every keystroke.
- **Swap and reset actions** in the header use `useSwapJobPipeline` and `useResetJobPipeline`; on success both reset the local `stages` state from the returned instance.

### `PipelineFlowColumn.tsx` — dnd-kit wiring

The sortable column uses `@dnd-kit/core` + `@dnd-kit/sortable` with two sensors:

```tsx
const sensors = useSensors(
  useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
)
```

`KeyboardSensor` with `sortableKeyboardCoordinates` is the non-negotiable a11y piece — without it, drag-to-reorder would be mouse-only (WCAG failure, and explicitly required by `frontend/app/CLAUDE.md` for any dnd-kit usage).

Modifiers restrict motion: `[restrictToVerticalAxis, restrictToParentElement]`. `SortableContext` uses `verticalListSortingStrategy`. `DragEndEvent` is translated to `arrayMove(stages, oldIndex, newIndex)` and passed up to `UnifiedPipelineView.reorderStages`, which rewrites every `position` by array index and schedules an autosave.

Only stages that already have a backend-assigned `id` participate in the sortable context — locally-created stages with `id=undefined` are rendered but not draggable until autosave returns.

### `PipelineFunnel.tsx` — stable keys

`PipelineFunnel` is the reusable funnel render primitive (used in template previews and starter-pack browsing). Commit `475df30` widened its prop type to accept stages whose `id` is optional and rewrote the React key from the old composite `${i}-${stage.name}` (which collided on rename and caused spurious `StageSlab` remounts) to `stage.id ?? scratch-${i}`. Saved stages use their stable UUID; freshly-added scratch stages fall back to the array index until they're persisted.

### `StageConfigDrawer.tsx` — focus management

Opened by clicking a stage. A centred modal (role=`dialog`, aria-modal, aria-labelledby). Focus management from commit `dd2f528` (WCAG 2.4.3):

```tsx
const nameInputRef = useRef<HTMLInputElement>(null)
useEffect(() => { nameInputRef.current?.focus() }, [])
```

The drawer is mounted conditionally by its parent (when a stage is selected), so `useEffect(..., [])` fires on open. Closes on `Escape` via a second effect that registers a `document.addEventListener('keydown', ...)` and removes it on unmount.

Internals: a Name text input, a `stage_type` select, a `duration_minutes` field, a `DifficultySlider`, and an "Advanced" expander that shows `SignalFilterEditor` + `PassCriteriaEditor` + `advance_behavior` select. Every field update calls `onChange({ ...stage, [key]: value })` so the parent's local state reflects the edit and the autosave timer kicks in.

### `TemplatePickerDialog.tsx` — focus management

Modal shown from both the empty-state "Pick a pipeline" button (on the pipeline page with no instance) and the "Swap template" button (on `UnifiedPipelineView`). Two tabs: "Your library" (from `usePipelineTemplates(orgUnitId, { enabled: open })` — `enabled` gating prevents the query from running until the dialog is opened) and "Starter pack" (`StarterPackBrowser`).

Focus management from commit `dd2f528`:

```tsx
const closeButtonRef = useRef<HTMLButtonElement>(null)
useEffect(() => {
  if (!open) return
  closeButtonRef.current?.focus()
}, [open])
```

The close button is always rendered (templates load async), so focusing it is race-free. Users tab forward into the tab list and template cards from there. An inline comment explicitly justifies not focusing a template card: "templates load asynchronously so focusing a template card would be racy".

### Query key discipline

All pipeline queries are keyed by `['job-pipeline', jobId]` (instance) or `['pipeline-templates', unitId]` (library list). These are distinct from `['jobs', jobId]` used by `useJob` — the pipeline surface never shares keys with the jobs surface, matching the Phase 2B + Batch G key-shape rule documented in `frontend/app/CLAUDE.md` ("Query key discipline").

---

## 9. Known Gaps

- **Spec-drift RLS at creation time.** Migration 0004 creates the four pipeline tables with `USING`-only tenant policies and the `service_role_bypass` alias. Migrations 0011 and 0012 repair both — today's runtime state is correct — but anyone reading 0004 in isolation should not copy its policy DDL. See Section 2 and the hardening-phase walkthrough.
- **Signal filter dropped three dimensions.** Migration 0005 flattened `signal_filter` to `include_types` only. Stage-level filtering by weight, priority, and source stage is no longer possible through the UI or API — Phase 2C.2's question-bank prompt does the weighting internally. Recovering the dropped dimensions would require a new migration and Pydantic schema revision.
- **No drag-and-drop on template edit.** `UnifiedPipelineView` (per-job) wires `@dnd-kit` for reorder, but the template editor under `/settings/org-units/{unitId}/pipeline-templates/{templateId}` does not — template stages are manipulated through the form UI only.
- **Atomic stage replace on templates.** `PATCH /api/pipeline-templates/{id}` deletes and reinserts all stages when the body contains a `stages` list — unlike the job pipeline PATCH, there is no id-preserving diff-and-sync. Question banks are not attached to template stages (they FK to `job_pipeline_stages.id`) so there is nothing to preserve, but any future template-scoped metadata would need to switch to diff-and-sync first.
- **`save_job_pipeline_as_template` rejects empty pipelines with `ValueError`**, not a typed exception. The router does not catch this — it would surface as a 500. Not reachable through the UI (you can't drag a pipeline to empty; the `Delete stage` button is gated on `stages.length > 1`), but a direct API call with zero stages would blow up.
- **Source-template deletion is silent.** `source_template_id` becomes NULL via the FK, but no notification, audit event, or UI cue fires. The job's pipeline keeps working, but "Reset to source" starts returning 409 with `NoSourceTemplateError` and recruiters have to reason it out.
- **Auto-apply failure only audits once per confirm.** The `try/except` around auto-apply writes exactly one `job_pipeline.auto_apply_failed` audit row per `confirm_signals` call. If the recruiter re-confirms, auto-apply re-runs and a second audit row is written. There is no consolidation or rate-limiting on the audit trail.
- **No "last-used" indicator in the UI.** The auto-apply chain prefers the last-used template in the org unit (resolution step 1), but the pipeline-editor swap dialog does not highlight which template was last applied. Recruiters who want to replicate a previous setup have to remember it.
- **Spec drift on `updated_at` triggers.** The design spec assumes a reused `set_updated_at()` trigger on `pipeline_templates` and `job_pipeline_instances`. Migration 0004 installs no triggers; service code stamps `_now_utc()` on every mutation. Direct DB writes (or a future service-layer helper that forgets) would leave `updated_at` stale.

---

## 10. Cross-references

- **Phase 2A walkthrough (`docs/phase-2a-implementation.md`)** — JD state machine, `require_job_access` ancestry walk pattern reused here, `get_tenant_db` / `get_bypass_db` session types.
- **Phase 2B walkthrough (`docs/phase-2b-implementation.md`)** — `confirm_signals` flow, `PipelineAlreadyExistsError` idempotency handling (commit `a7ba2ea`), signal schema v2.
- **Phase 2C.2 walkthrough (`docs/phase-2c2-implementation.md`)** — question-bank generation per pipeline stage, FK-to-stage-id contract that makes the diff-and-sync update in Section 4 load-bearing.
- **Hardening walkthrough (`docs/phase-hardening-implementation.md`)** — migrations 0008–0012, startup RLS completeness check, `nexus_app` role, the full story of how 0004's incomplete RLS pattern was repaired.
- **Root `CLAUDE.md`** — canonical RLS pattern, `NULLIF` requirement, `FOR SELECT USING` trap.
- **`backend/nexus/CLAUDE.md`** — `pipelines` module responsibilities table, auto-apply contract.
- **`frontend/app/CLAUDE.md`** — dnd-kit a11y requirements, StageConfigDrawer / TemplatePickerDialog focus management, query key discipline.
- **Design spec (`docs/superpowers/specs/2026-04-12-phase-2c1-pipeline-builder-design.md`)** — original scope and rationale for fields not documented here.
- **Implementation plan (`docs/superpowers/plans/2026-04-12-phase-2c1-pipeline-builder.md`)** — task-by-task breakdown and file map.
