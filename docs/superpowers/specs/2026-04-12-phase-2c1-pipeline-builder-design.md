# Phase 2C.1 — Pipeline Builder

> **Status:** Approved design — ready for implementation planning
> **Date:** 2026-04-12
> **Depends on:** Phase 2B (Signal Editing & Confirmation) — shipped
> **Prerequisite for:** Phase 2C.2 (Question Bank Generation per Stage)

---

## What This Delivers

Interview pipelines as a first-class concern. Recruiters compose ordered stages into reusable templates (per org unit), and each job gets a snapshotted pipeline instance that can be customized without affecting the template. A hand-written starter pack seeds new org units with 6 battle-tested templates so new clients get working pipelines immediately with zero AI cost or latency.

This phase ships the pipeline structure only — NO question generation. Phase 2C.2 adds Call 3 to generate questions per pipeline stage.

---

## Core Product Principle

**AI decides, human verifies.** The system auto-applies a pipeline template when signals are confirmed, using a fallback chain (last used → org unit default → system fallback). The recruiter only customizes when they want to — the default path works without any manual pipeline configuration.

---

## Scope

### In Scope
- `pipelines` module as a new top-level backend module
- Per-org-unit template library with CRUD
- System starter pack (6 hand-written templates, no AI generation)
- Per-job pipeline instances (snapshot from template, editable)
- Auto-apply hook: when signals are confirmed, create a pipeline instance using the fallback chain
- Funnel UI for template editing and job pipeline configuration
- "Build Pipeline" / "View Pipeline" button on job review page
- Template library page under Settings → Org Units
- Stage configuration: name, duration, difficulty, signal filter, pass criteria, advance behavior

### Out of Scope (Deferred)
- **Question bank generation per stage** — Phase 2C.2
- **Session execution** — Phase 3
- **Candidate routing between stages** — Phase 3
- **Scheduling / candidate notifications** — Phase 3
- **Per-stage signal exclusion by signal ID** — signals don't have stable IDs yet; YAGNI for now
- **AI-generated templates on org unit creation** — replaced by the starter pack

---

## Stage Types (Fixed Enum)

```python
StageType = Literal[
    "phone_screen",       # AI bot, audio-only, 10-15 min
    "ai_interview",       # AI bot, video, 30-60 min
    "human_interview",    # Human + AI copilot (Phase 3), video
    "panel_interview",    # Multiple humans + AI copilot (Phase 3), video
    "take_home",          # Async assessment (code exercise, case study)
]
```

**Stage type determines (read-only):**
- Conductor: `phone_screen` = AI bot, `ai_interview` = AI bot, `human_*` = human + copilot, `panel_*` = multiple humans + copilot, `take_home` = async
- Medium: phone = audio, others = video, take_home = text/code

**Stage type does NOT determine:**
- Name (editable — "Phone Screen" → "Quick Qualifier")
- Duration, difficulty, signal filter, pass criteria, advance behavior

---

## Data Model

### `pipeline_templates` (new table)

Stores reusable pipeline templates per org unit. Templates are independent of jobs — editing a template doesn't affect existing job pipelines.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → clients NOT NULL | RLS scoping |
| `org_unit_id` | UUID FK → organizational_units NOT NULL | Which unit owns this template |
| `name` | TEXT NOT NULL | e.g. "Engineering — Full Pipeline" |
| `description` | TEXT | Optional short description |
| `is_default` | BOOL NOT NULL DEFAULT false | Auto-applied to new jobs in this unit |
| `from_starter` | TEXT | Starter pack key if copied (null if from scratch) |
| `created_by` | UUID FK → users NOT NULL | |
| `updated_by` | UUID FK → users | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Auto-updated via trigger |

**Constraints:**
- Partial unique index: `CREATE UNIQUE INDEX ON pipeline_templates (org_unit_id) WHERE is_default = true` — enforces at most one default per org unit
- RLS policy: standard tenant isolation on `tenant_id`
- Updated-at trigger: reuses the existing `set_updated_at()` trigger pattern from other tables

### `pipeline_template_stages` (new table)

Ordered stages within a template.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → clients NOT NULL | RLS scoping |
| `template_id` | UUID FK → pipeline_templates (CASCADE) NOT NULL | Parent template |
| `position` | INT NOT NULL | 0-indexed stage order |
| `name` | TEXT NOT NULL | "Phone Screen", "Technical Deep Interview" |
| `stage_type` | TEXT NOT NULL + CHECK | `phone_screen \| ai_interview \| human_interview \| panel_interview \| take_home` |
| `duration_minutes` | INT NOT NULL + CHECK (> 0 AND ≤ 240) | Target length, AI uses as budget |
| `difficulty` | TEXT NOT NULL + CHECK | `easy \| medium \| hard` |
| `signal_filter` | JSONB NOT NULL | See Signal Filter shape below |
| `pass_criteria` | JSONB NOT NULL | See Pass Criteria shape below |
| `advance_behavior` | TEXT NOT NULL + CHECK | `auto_advance \| manual_review` |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Constraints:**
- `UNIQUE (template_id, position)` — enforces unique positions within a template
- RLS policy: tenant isolation on `tenant_id`

### `job_pipeline_instances` (new table)

Per-job pipeline instances — snapshotted from a template (or built from scratch). Edits to this instance do NOT propagate to the source template.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → clients NOT NULL | RLS scoping |
| `job_posting_id` | UUID FK → job_postings (CASCADE) NOT NULL | 1:1 with job |
| `source_template_id` | UUID FK → pipeline_templates ON DELETE SET NULL | Null if built from scratch or source deleted |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Constraints:**
- `UNIQUE (job_posting_id)` — one pipeline per job
- No status field (YAGNI — 2C.2 adds status when question generation becomes a concern)
- RLS policy: tenant isolation on `tenant_id`

### `job_pipeline_stages` (new table)

Same shape as `pipeline_template_stages`, but FK to `job_pipeline_instances`. These are the editable snapshot copies.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → clients NOT NULL | RLS |
| `instance_id` | UUID FK → job_pipeline_instances (CASCADE) NOT NULL | Parent instance |
| `position` | INT NOT NULL | 0-indexed |
| `name` | TEXT NOT NULL | |
| `stage_type` | TEXT NOT NULL + CHECK | Same CHECK as template stages |
| `duration_minutes` | INT NOT NULL + CHECK (> 0 AND ≤ 240) | |
| `difficulty` | TEXT NOT NULL + CHECK | |
| `signal_filter` | JSONB NOT NULL | |
| `pass_criteria` | JSONB NOT NULL | |
| `advance_behavior` | TEXT NOT NULL + CHECK | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Constraints:**
- `UNIQUE (instance_id, position)`
- RLS policy: tenant isolation

### Signal filter JSONB shape

```json
{
  "include_types": ["competency", "experience", "credential", "behavioral"],
  "include_stages": ["screen"],
  "include_weights": [1, 2, 3],
  "include_priority": ["required", "preferred"]
}
```

All filters are **positive/inclusive**. A signal is included in the stage's scope if it matches ALL active filters. Per-signal exclusion by ID is not supported (signals don't have stable IDs).

### Pass criteria JSONB shape

Discriminated union:

```json
{"type": "all_knockouts_pass"}
```
```json
{"type": "score_threshold", "threshold": 70}
```
```json
{"type": "manual_review"}
```

### No changes to `job_postings`

We don't add a `pipeline_instance_id` column. The reverse FK on `job_pipeline_instances.job_posting_id` is enough.

---

## Starter Pack

Lives in `app/modules/pipelines/starter_pack.py` as Python data (no DB rows). Six hand-written templates:

| Key | Name | Stages | Target Use |
|-----|------|--------|-----------|
| `standard_technical` | Standard Technical | Phone Screen (10m, easy) → AI Interview (45m, hard) → Panel (60m, medium) | Default engineering pipeline |
| `fast_track` | Fast Track | Phone Screen (10m, easy) → AI Interview (30m, medium) | Urgent backfills |
| `screening_only` | Screening Only | Phone Screen (15m, easy) | Client takes over after qualifier |
| `senior_leadership` | Senior Leadership | Phone Screen (15m, easy) → AI Interview (60m, hard) → Panel (60m, medium) → Human Interview (45m, medium) | Staff+, Principal, Director |
| `sales_commercial` | Sales & Commercial | Phone Screen (10m, easy) → Human Interview (45m, medium) | Sales, BD |
| `volume_hiring` | Volume Hiring | Phone Screen (8m, easy) | Ops, customer service, retail |

System fallback: `standard_technical` — used when auto-apply has no template to resolve to.

### Full starter pack definition

```python
# app/modules/pipelines/starter_pack.py
from typing import Any

STARTER_TEMPLATES: dict[str, dict[str, Any]] = {
    "standard_technical": {
        "name": "Standard Technical",
        "description": "Default pipeline for engineering and technical roles: phone screen, AI deep interview, panel review.",
        "stages": [
            {
                "position": 0,
                "name": "Phone Screen",
                "stage_type": "phone_screen",
                "duration_minutes": 10,
                "difficulty": "easy",
                "signal_filter": {"include_types": ["competency", "experience", "credential", "behavioral"], "include_stages": ["screen"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
                "pass_criteria": {"type": "all_knockouts_pass"},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 1,
                "name": "AI Technical Interview",
                "stage_type": "ai_interview",
                "duration_minutes": 45,
                "difficulty": "hard",
                "signal_filter": {"include_types": ["competency", "experience", "behavioral"], "include_stages": ["interview"], "include_weights": [2, 3], "include_priority": ["required", "preferred"]},
                "pass_criteria": {"type": "score_threshold", "threshold": 70},
                "advance_behavior": "auto_advance",
            },
            {
                "position": 2,
                "name": "Hiring Manager Panel",
                "stage_type": "panel_interview",
                "duration_minutes": 60,
                "difficulty": "medium",
                "signal_filter": {"include_types": ["competency", "behavioral"], "include_stages": ["interview"], "include_weights": [3], "include_priority": ["required"]},
                "pass_criteria": {"type": "manual_review"},
                "advance_behavior": "manual_review",
            },
        ],
    },
    "fast_track": {
        "name": "Fast Track",
        "description": "Accelerated 2-stage pipeline for urgent backfills — phone screen then AI interview.",
        "stages": [
            {"position": 0, "name": "Phone Screen", "stage_type": "phone_screen", "duration_minutes": 10, "difficulty": "easy",
             "signal_filter": {"include_types": ["competency", "experience", "credential", "behavioral"], "include_stages": ["screen"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "all_knockouts_pass"}, "advance_behavior": "auto_advance"},
            {"position": 1, "name": "AI Interview", "stage_type": "ai_interview", "duration_minutes": 30, "difficulty": "medium",
             "signal_filter": {"include_types": ["competency", "experience", "behavioral"], "include_stages": ["interview"], "include_weights": [2, 3], "include_priority": ["required"]},
             "pass_criteria": {"type": "score_threshold", "threshold": 65}, "advance_behavior": "manual_review"},
        ],
    },
    "screening_only": {
        "name": "Screening Only",
        "description": "Phone screen only — client takes over after qualifier.",
        "stages": [
            {"position": 0, "name": "Phone Screen", "stage_type": "phone_screen", "duration_minutes": 15, "difficulty": "easy",
             "signal_filter": {"include_types": ["competency", "experience", "credential", "behavioral"], "include_stages": ["screen"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "all_knockouts_pass"}, "advance_behavior": "manual_review"},
        ],
    },
    "senior_leadership": {
        "name": "Senior Leadership",
        "description": "Extended 4-stage pipeline for Staff+, Principal, and Director roles.",
        "stages": [
            {"position": 0, "name": "Phone Screen", "stage_type": "phone_screen", "duration_minutes": 15, "difficulty": "easy",
             "signal_filter": {"include_types": ["competency", "experience", "credential", "behavioral"], "include_stages": ["screen"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "all_knockouts_pass"}, "advance_behavior": "auto_advance"},
            {"position": 1, "name": "AI Technical Interview", "stage_type": "ai_interview", "duration_minutes": 60, "difficulty": "hard",
             "signal_filter": {"include_types": ["competency", "experience", "behavioral"], "include_stages": ["interview"], "include_weights": [2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "score_threshold", "threshold": 75}, "advance_behavior": "auto_advance"},
            {"position": 2, "name": "Hiring Manager Panel", "stage_type": "panel_interview", "duration_minutes": 60, "difficulty": "medium",
             "signal_filter": {"include_types": ["competency", "behavioral"], "include_stages": ["interview"], "include_weights": [3], "include_priority": ["required"]},
             "pass_criteria": {"type": "manual_review"}, "advance_behavior": "manual_review"},
            {"position": 3, "name": "Executive Interview", "stage_type": "human_interview", "duration_minutes": 45, "difficulty": "medium",
             "signal_filter": {"include_types": ["behavioral"], "include_stages": ["interview"], "include_weights": [3], "include_priority": ["required"]},
             "pass_criteria": {"type": "manual_review"}, "advance_behavior": "manual_review"},
        ],
    },
    "sales_commercial": {
        "name": "Sales & Commercial",
        "description": "2-stage pipeline for sales, BD, and commercial roles.",
        "stages": [
            {"position": 0, "name": "Phone Screen", "stage_type": "phone_screen", "duration_minutes": 10, "difficulty": "easy",
             "signal_filter": {"include_types": ["competency", "experience", "credential", "behavioral"], "include_stages": ["screen"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "all_knockouts_pass"}, "advance_behavior": "auto_advance"},
            {"position": 1, "name": "Human Interview", "stage_type": "human_interview", "duration_minutes": 45, "difficulty": "medium",
             "signal_filter": {"include_types": ["competency", "experience", "behavioral"], "include_stages": ["interview"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "manual_review"}, "advance_behavior": "manual_review"},
        ],
    },
    "volume_hiring": {
        "name": "Volume Hiring",
        "description": "Single-stage phone screen for high-volume roles (ops, customer service, retail).",
        "stages": [
            {"position": 0, "name": "Phone Screen", "stage_type": "phone_screen", "duration_minutes": 8, "difficulty": "easy",
             "signal_filter": {"include_types": ["competency", "experience", "credential"], "include_stages": ["screen"], "include_weights": [1, 2, 3], "include_priority": ["required", "preferred"]},
             "pass_criteria": {"type": "all_knockouts_pass"}, "advance_behavior": "auto_advance"},
        ],
    },
}

SYSTEM_FALLBACK_STARTER = "standard_technical"
```

---

## Backend Module Structure

```
app/modules/pipelines/
├── __init__.py
├── router.py         — /api/pipeline-templates/*, /api/jobs/{id}/pipeline/*, /api/org-units/{id}/pipeline-templates/*
├── service.py        — template CRUD, instance creation/mutation, auto-apply hook
├── schemas.py        — request/response Pydantic models, all Literal enums
├── starter_pack.py   — hand-written STARTER_TEMPLATES dict + SYSTEM_FALLBACK_STARTER constant
├── authz.py          — require_template_access, require_pipeline_instance_access
└── errors.py         — custom exceptions (e.g. NoDefaultToUnset, CannotDeleteDefault)
```

**Cross-module integration:**
- `app/modules/jd/service.py::confirm_signals()` imports from `app.modules.pipelines.service` and calls `auto_apply_pipeline_on_confirmation()` at the end. This call is wrapped in `try/except Exception` — failures are logged to `audit_log` but do NOT block the signal confirmation.
- `app/main.py` registers the new router in the app factory.

---

## Backend API

### Template endpoints

**`GET /api/pipeline-templates/starter-pack`**
- Returns the 6 hand-written starters from `starter_pack.py`
- Auth: authenticated user (any role)
- No tenant scoping (static content)
- Response: `list[StarterTemplate]`

**`GET /api/org-units/{unit_id}/pipeline-templates`**
- Lists all templates in an org unit's library, including their stages
- Permission: `org_units.manage` in the unit's ancestry
- Response: `list[PipelineTemplateResponse]`

**`POST /api/org-units/{unit_id}/pipeline-templates`**
- Creates a new template — either from a starter or from scratch
- Permission: `org_units.manage` in the unit's ancestry
- Body (discriminated by `source`):
  ```json
  {"source": "starter", "starter_key": "standard_technical", "name": "Engineering — Standard", "is_default": false}
  // OR
  {"source": "scratch", "name": "Custom Pipeline", "description": "...", "is_default": false, "stages": [...]}
  ```
- If `is_default = true`, the atomic toggle (see `set-default` endpoint) runs
- Returns the created `PipelineTemplateResponse`

**`PATCH /api/pipeline-templates/{template_id}`**
- Updates name, description, or stages (all optional — partial update)
- Does NOT touch `is_default` (use dedicated endpoint)
- Permission: `org_units.manage` in the template's org unit ancestry
- If `stages` is provided, atomically replaces all stages for this template
- Does NOT propagate to existing `job_pipeline_instances`

**`POST /api/pipeline-templates/{template_id}/set-default`**
- Atomically marks this template as the org unit's default, clearing `is_default` on any other template
- Permission: `org_units.manage` in the template's org unit ancestry
- No body
- Returns the updated `PipelineTemplateResponse`

**`DELETE /api/pipeline-templates/{template_id}`**
- Deletes a template and its stages
- Permission: `org_units.manage` in the template's org unit ancestry
- Rejects with 409 if `is_default = true` — caller must unset default first
- Existing `job_pipeline_instances.source_template_id` becomes NULL (ON DELETE SET NULL)

### Job pipeline endpoints

**`GET /api/jobs/{job_id}/pipeline`**
- Returns the job's current pipeline instance with stages
- Permission: `jobs.view` in the job's org unit ancestry
- Response: `JobPipelineInstanceResponse` or 404 if no instance exists

**`POST /api/jobs/{job_id}/pipeline`**
- Creates a pipeline instance for a job
- Permission: `jobs.manage` in the job's org unit ancestry
- Rejects if:
  - Job is not in `signals_confirmed` state (409)
  - Instance already exists (409 — use PATCH)
- Body (discriminated by `source`):
  ```json
  {"source": "template", "template_id": "uuid"}
  // OR
  {"source": "starter", "starter_key": "standard_technical"}
  // OR
  {"source": "scratch", "stages": [...]}
  ```
- Returns `JobPipelineInstanceResponse`

**`PATCH /api/jobs/{job_id}/pipeline`**
- Updates the job's pipeline instance stages
- Permission: `jobs.manage` in the job's org unit ancestry
- Body: `{"stages": [...]}` — atomically replaces all stages

**`POST /api/jobs/{job_id}/pipeline/reset`**
- Re-copies stages from `source_template_id`, discarding local edits
- Permission: `jobs.manage` in the job's org unit ancestry
- Rejects with 409 if `source_template_id` is NULL

**`POST /api/jobs/{job_id}/pipeline/save-as-template`**
- Saves the job's current pipeline as a new template in the org unit library
- Permission: `jobs.manage` on the job AND `org_units.manage` on the job's org unit
- Body: `{"name": "...", "description": "...", "is_default": false}`
- Returns the newly created `PipelineTemplateResponse`

**`POST /api/jobs/{job_id}/pipeline/update-source-template`**
- Writes the job's current pipeline stages back to the source template
- Permission: `jobs.manage` on the job AND `org_units.manage` on the template's org unit
- Rejects with 409 if `source_template_id` is NULL
- Frontend shows a confirmation dialog ("This affects future jobs") before calling

### Auto-apply hook (internal, not a route)

When `jd.service.confirm_signals()` transitions a job to `signals_confirmed`, it calls:

```python
# app/modules/jd/service.py::confirm_signals (at the end)
from app.modules.pipelines.service import auto_apply_pipeline_on_confirmation

try:
    await auto_apply_pipeline_on_confirmation(
        db, job=job, actor_id=actor_id,
    )
except Exception as exc:
    logger.error(
        "jd.pipeline_auto_apply_failed",
        job_posting_id=str(job.id),
        exc_info=exc,
    )
    # Write audit log so we can track failure rates
    from app.modules.audit.service import log_event
    await log_event(
        db,
        tenant_id=job.tenant_id,
        actor_id=actor_id,
        action="job_pipeline.auto_apply_failed",
        resource="job_posting",
        resource_id=job.id,
        payload={"error": str(exc)[:500]},
    )
```

### `auto_apply_pipeline_on_confirmation` resolution order

1. **Last used template in this org unit** — query the most recent `job_pipeline_instances` in the same org unit, use that `source_template_id` if it exists and the template still exists
2. **Org unit default template** — query `pipeline_templates WHERE org_unit_id = ? AND is_default = true`
3. **System fallback** — use `STARTER_TEMPLATES[SYSTEM_FALLBACK_STARTER]` and create a pipeline instance directly from starter pack data (does NOT create a new template in the org unit library)

In all three cases, the result is a `job_pipeline_instances` row + `job_pipeline_stages` rows. Case 3 has `source_template_id = NULL` (no template in the library to reference).

---

## Frontend Architecture

### New routes

| Route | Component | Purpose |
|-------|-----------|---------|
| `/settings/org-units/[unitId]/pipeline-templates` | `TemplateLibraryPage` | Grid of templates + starter pack browser |
| `/settings/org-units/[unitId]/pipeline-templates/new` | `TemplateEditorPage` | Create new template |
| `/settings/org-units/[unitId]/pipeline-templates/[templateId]` | `TemplateEditorPage` | Edit existing template |
| `/jobs/[jobId]/pipeline` | `JobPipelinePage` | Job-specific pipeline + stage config |

### Updated routes

| Route | Changes |
|-------|---------|
| `/settings/org-units/[unitId]` | Add "Pipeline Templates" section with template count + "Manage" link |
| `/jobs/[jobId]` | Add "Build Pipeline" / "View Pipeline" button when `status === 'signals_confirmed'` and `can_manage` |

### New components (`components/dashboard/pipeline/`)

| Component | Purpose |
|-----------|---------|
| `PipelineFunnel.tsx` | Inverted-pyramid funnel — reusable rendering primitive |
| `StageSlab.tsx` | Individual stage card with click handler |
| `StageConfigDrawer.tsx` | Right-side drawer: name, duration, difficulty, signal filter, pass criteria, advance behavior |
| `SignalFilterEditor.tsx` | Sub-component for include_types/stages/weights/priority |
| `PassCriteriaEditor.tsx` | Sub-component with discriminated UI (knockout / threshold / manual) |
| `TemplatePickerDialog.tsx` | Modal showing starter pack + library for "Swap template" action |
| `StarterPackBrowser.tsx` | Preview list of the 6 starters with "Use this" action |
| `TemplateLibraryCard.tsx` | Card showing name, stage preview, default badge |

### API client file (`lib/api/pipelines.ts`)

New file with types and `pipelinesApi` object. Types mirror backend Pydantic schemas exactly:

```typescript
export type StageType = 'phone_screen' | 'ai_interview' | 'human_interview' | 'panel_interview' | 'take_home'
export type StageDifficulty = 'easy' | 'medium' | 'hard'
export type AdvanceBehavior = 'auto_advance' | 'manual_review'

export type SignalFilter = {
  include_types: ('competency' | 'experience' | 'credential' | 'behavioral')[]
  include_stages: ('screen' | 'interview')[]
  include_weights: (1 | 2 | 3)[]
  include_priority: ('required' | 'preferred')[]
}

export type PassCriteria =
  | { type: 'all_knockouts_pass' }
  | { type: 'score_threshold'; threshold: number }
  | { type: 'manual_review' }

export type PipelineStage = {
  id: string
  position: number
  name: string
  stage_type: StageType
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
}

export type PipelineTemplate = {
  id: string
  org_unit_id: string
  name: string
  description: string | null
  is_default: boolean
  from_starter: string | null
  stages: PipelineStage[]
  created_at: string
  updated_at: string
}

export type StarterTemplate = {
  key: string
  name: string
  description: string
  stages: Omit<PipelineStage, 'id'>[]
}

export type JobPipelineInstance = {
  id: string
  job_posting_id: string
  source_template_id: string | null
  source_template_name: string | null
  stages: PipelineStage[]
  created_at: string
  updated_at: string
}
```

### Hooks (`lib/hooks/`)

| Hook | Purpose |
|------|---------|
| `use-pipeline-templates.ts` | `useQuery` for `listTemplates(unitId)` |
| `use-starter-pack.ts` | `useQuery` for `getStarterPack()` — cached globally, never invalidated |
| `use-job-pipeline.ts` | `useQuery` for `getJobPipeline(jobId)` — returns null if 404 |
| `use-save-pipeline-template.ts` | Mutation for PATCH template |
| `use-save-job-pipeline.ts` | Mutation for PATCH job pipeline |
| `use-create-job-pipeline.ts` | Mutation for POST job pipeline (from template/starter/scratch) |

### State management

- **TanStack Query** for all server state
- **Local `useState`** for staged edits in the funnel builder (single PATCH on save)
- **No Zustand** — pipeline editing doesn't need cross-component transient state; the page holds it

### Job review page changes

In `/jobs/[jobId]` page component:

```typescript
const { data: pipeline } = useJobPipeline(jobId)
const showPipelineButton = job.status === 'signals_confirmed' && job.can_manage
const pipelineButtonLabel = pipeline ? 'View Pipeline' : 'Build Pipeline'
```

Button renders in the job header (next to the title) as:
- **Primary blue** when no pipeline exists yet — prominent call to action
- **Secondary outline** when pipeline is already configured — still visible but lower contrast

---

## State Machine

No changes to the JD state machine. The existing states remain:
```
draft → signals_extracting → signals_extracted → signals_confirmed → (2C.2: questions_generating)
```

The pipeline instance is a side-effect of `signals_confirmed` — it exists in a sibling table and has no explicit status until Phase 2C.2 needs it.

---

## Permissions

Two existing permissions cover all endpoints:

| Action | Permission | Rationale |
|--------|-----------|-----------|
| View templates | `org_units.manage` (ancestry) | Templates are org unit admin concern |
| Create/edit/delete templates | `org_units.manage` (ancestry) | Same reason |
| View job pipeline | `jobs.view` (ancestry) | Pipeline is a job attribute |
| Create/edit job pipeline | `jobs.manage` (ancestry) | Same reason |
| Save job pipeline as new template | `jobs.manage` + `org_units.manage` | Cross-cutting — writes to both surfaces |

No new permission constants needed.

---

## Testing Strategy

### Backend tests

**Service layer tests:**
- `test_create_template_from_scratch` — POST /api/org-units/{id}/pipeline-templates with `source: scratch`
- `test_create_template_from_starter` — POST with `source: starter`
- `test_set_default_atomically_clears_other` — verify partial unique index + service logic
- `test_cannot_delete_default_template` — 409 when deleting the default
- `test_auto_apply_uses_last_used_template` — happy path
- `test_auto_apply_falls_back_to_org_unit_default` — when no last used
- `test_auto_apply_falls_back_to_system_default` — when no last used AND no org default
- `test_auto_apply_failure_does_not_block_signal_confirmation` — wraps in try/except
- `test_job_pipeline_reset_restores_from_source` — reset endpoint
- `test_job_pipeline_reset_rejects_when_no_source` — 409 when built from scratch
- `test_update_source_template_writes_back_stages` — update source endpoint
- `test_save_as_new_template_creates_library_entry` — save-as endpoint

**Router tests (happy path + permission errors):**
- `test_get_starter_pack_returns_six_templates`
- `test_create_template_requires_org_units_manage`
- `test_job_pipeline_requires_jobs_manage`
- `test_create_job_pipeline_rejects_non_confirmed_status`
- `test_create_job_pipeline_rejects_duplicate`

### Frontend tests (Vitest)

**Component tests:**
- `PipelineFunnel` renders stages in order
- `StageSlab` opens drawer on click
- `StageConfigDrawer` validates required fields
- `TemplatePickerDialog` lists starter pack + library
- `SignalFilterEditor` produces correct JSON

---

## Files Changed (17 total)

### Backend (12 files)

| File | Change |
|------|--------|
| `migrations/versions/0004_pipeline_builder.py` | NEW — 4 tables + RLS policies |
| `app/models.py` | Add `PipelineTemplate`, `PipelineTemplateStage`, `JobPipelineInstance`, `JobPipelineStage` ORM models |
| `app/modules/pipelines/__init__.py` | NEW |
| `app/modules/pipelines/router.py` | NEW |
| `app/modules/pipelines/service.py` | NEW |
| `app/modules/pipelines/schemas.py` | NEW |
| `app/modules/pipelines/starter_pack.py` | NEW — 6 templates + constant |
| `app/modules/pipelines/authz.py` | NEW |
| `app/modules/pipelines/errors.py` | NEW |
| `app/main.py` | Register pipelines router |
| `app/modules/jd/service.py` | Call `auto_apply_pipeline_on_confirmation` from `confirm_signals` |
| `tests/test_pipelines.py` | NEW — full test suite |

### Frontend (5 files + new directory)

| File | Change |
|------|--------|
| `lib/api/pipelines.ts` | NEW |
| `lib/hooks/use-*-pipeline-*.ts` | NEW — 6 hooks |
| `components/dashboard/pipeline/*.tsx` | NEW — 8 components |
| `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx` | NEW |
| `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/new/page.tsx` | NEW |
| `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/[templateId]/page.tsx` | NEW |
| `app/(dashboard)/jobs/[jobId]/pipeline/page.tsx` | NEW |
| `app/(dashboard)/jobs/[jobId]/page.tsx` | Add "Build Pipeline" button |
| `app/(dashboard)/settings/org-units/[unitId]/page.tsx` | Add "Pipeline Templates" section link |

---

## Non-Goals (deferred)

- **AI-generated templates on org unit creation** — replaced by hand-written starter pack
- **Question bank generation per stage** — Phase 2C.2
- **Session execution** — Phase 3
- **Candidate routing / progression tracking** — Phase 3
- **Scheduling / calendar integration** — Phase 3
- **Per-signal exclusion by signal ID** — signals lack stable IDs; YAGNI
- **Pipeline status lifecycle** (`draft / configured / active`) — YAGNI for 2C.1; Phase 2C.2 adds status for question generation
- **Drag-and-drop stage reordering** — stretch goal; can be achieved with up/down buttons initially
- **Template versioning / history** — editing a template is destructive; jobs are protected by snapshot
- **Template permissions finer than `org_units.manage`** — single permission covers all template actions
