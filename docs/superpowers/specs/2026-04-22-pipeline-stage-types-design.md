# Pipeline stage types v5 — per-type behaviour and participant assignment

- **Author:** @IshantPundir (with Claude)
- **Date:** 2026-04-22
- **Status:** Approved for implementation plan

## 1. Problem

The job pipeline builder currently treats all nine `stage_type` values as interchangeable. The UI is a dropdown + a shared configuration form. In reality:

- Four existing types (`phone_screen`, `recruiter`, `human_interview`, `panel_interview`) are all "a human runs a session" — they should require assigning interviewers, which the UI does not capture anywhere.
- One (`ai_interview`) is an automated session — observers should be able to watch, but there is no way to specify them.
- `intake` is an entry point, not an interview — it has no participants.
- `debrief` is a final decision step requiring an assigned reviewer (typically a Hiring Manager) — it also has no participant capture today.
- `offer` is a stage type that no longer fits the product and needs to go.

There is currently no notion of participants anywhere in the data model. Pipelines hand off to Phase 3 (LiveKit session scheduling) with no staffing — every session will block on "who is running this?" without upstream capture.

## 2. Scope and non-goals

### In scope

- New stage-type taxonomy (6 values replacing 9).
- Backend data model, CHECK constraints, Alembic migration, RLS policies for a new `pipeline_stage_participants` table.
- API surface updates: participants on stage responses; eligibility-gated picker endpoint; per-endpoint behaviour clarifications (reset / swap / save-as-template / update-source-template drop participants; PATCH diff-syncs; etc).
- Frontend: per-category UI in the stage configuration drawer, a new participants editor component, category-aware label / icon / badge maps, starter-pack updates.
- Question-bank actor + router filtering so stages that don't generate questions (`intake`, `debrief`) don't produce placeholder banks or accidental draft rows.
- Tests (backend pytest + frontend vitest) and rollback script.

### Non-goals

- Session scheduler picking a specific interviewer from the pool. Phase 3.
- Per-user "my assignments" landing page. Phase 3 (table + indexes make it cheap when needed).
- `take_home` stage as a real UI flow — stays disabled with "Coming soon" label.
- ATS-wired `intake` — still manual, Phase 4 wires ATS pollers into it.
- Dark mode, pipeline-level staffing dashboard card.

## 3. Taxonomy

Six stage types. Category is derived (not persisted) and drives per-type UI.

| `stage_type` | Label | Category | Participant slot(s) |
|---|---|---|---|
| `intake` | Intake | entry | — |
| `phone_screen` | Phone Screen | human-led | **1+ Interviewers** (required) |
| `ai_screening` | AI Screening | ai-led | 0+ **Observers** (optional) |
| `human_interview` | Human Interview | human-led | **1+ Interviewers** (required) |
| `debrief` | Debrief | review | **1+ Reviewers** (required) |
| `take_home` | Take-home | disabled | — |

Removed outright (no aliases kept): `recruiter`, `panel_interview`, `offer`, `ai_interview`.

`human_interview` absorbs single-interviewer, recruiter-screen-after-AI, and multi-interviewer panel use cases. The distinction between a 1-on-1 and a panel is captured by the number of assigned interviewers, not by the type.

Role gating for the participant picker pool:

- Interviewer slot → users with `Interviewer` or `Hiring Manager` role assignment at the job's org unit ancestry.
- Observer slot → users with `Observer`, `Interviewer`, `Hiring Manager`, or `Recruiter` role assignment, same scoping.
- Reviewer slot → users with `Hiring Manager` role assignment, same scoping.

## 4. Data model

### 4.1 New table

```sql
CREATE TABLE pipeline_stage_participants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    stage_id    uuid NOT NULL REFERENCES job_pipeline_stages(id) ON DELETE CASCADE,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        text NOT NULL
                  CHECK (role IN ('interviewer', 'observer', 'reviewer')),
    assigned_by uuid REFERENCES users(id) ON DELETE SET NULL,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (stage_id, user_id, role)
);

CREATE INDEX ix_stage_participants_stage  ON pipeline_stage_participants (stage_id);
CREATE INDEX ix_stage_participants_user   ON pipeline_stage_participants (user_id);
CREATE INDEX ix_stage_participants_tenant ON pipeline_stage_participants (tenant_id);

ALTER TABLE pipeline_stage_participants ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON pipeline_stage_participants
  USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

CREATE POLICY "service_bypass" ON pipeline_stage_participants
  USING (current_setting('app.bypass_rls', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_stage_participants TO nexus_app;
```

- Only `job_pipeline_stages` gets participants. Templates stay staffing-agnostic (see §3 and §8.3 decision notes).
- `ON DELETE CASCADE` on `stage_id` means the existing diff-and-sync stage deletion (`update_job_pipeline_stages`) cleans up participants automatically.
- `ON DELETE CASCADE` on `user_id` matches current user-deactivation behaviour (`settings/service.py::_delete_auth_user()` already deletes the `users` row).
- Added to the `_assert_rls_completeness` enumerated list in `app/main.py` so startup aborts if the policy pair drifts.

### 4.2 Stage-type CHECK constraint

The existing CHECK on both `pipeline_template_stages` and `job_pipeline_stages` (from migration `0015_pipeline_stage_v4`) gets dropped and re-created with the new allowlist: `('intake', 'phone_screen', 'ai_screening', 'human_interview', 'debrief', 'take_home')`.

### 4.3 SQLAlchemy

- New `PipelineStageParticipant` ORM model in `app/models.py` with a `Mapped` relationship on `JobPipelineStage`.
- `JobPipelineStage.stage_type` and `PipelineTemplateStage.stage_type` stay as string columns — only the CHECK allowlist changes.

### 4.4 Migration: `0016_stage_type_v5_and_participants.py`

Runs in a single transaction, in this order:

1. Create `pipeline_stage_participants` table + indexes.
2. Enable RLS + add `tenant_isolation` and `service_bypass` policies.
3. Grant permissions to `nexus_app`.
4. **Drop** old CHECK constraints `ck_template_stages_stage_type` and `ck_job_pipeline_stages_stage_type` (from migration `0015_pipeline_stage_v4`). This must happen before the `UPDATE` step — otherwise renaming to `ai_screening` violates the old allowlist.
5. Update legacy stage rows (both tables):
   - `recruiter` → `human_interview`.
   - `panel_interview` → `human_interview`.
   - `ai_interview` → `ai_screening`.
6. Delete `offer` rows from both tables.
7. **Re-sequence positions** per pipeline instance / per template so remaining rows are `0..N-1`. Required because `UpdateJobPipelineRequest.check_positions_sequential` rejects gaps, and any pipeline that previously had an `offer` stage would 422 on the next auto-save PATCH otherwise:
   ```sql
   WITH renumbered AS (
       SELECT id,
              ROW_NUMBER() OVER (PARTITION BY instance_id ORDER BY position) - 1 AS new_pos
       FROM job_pipeline_stages
   )
   UPDATE job_pipeline_stages s
      SET position = r.new_pos
     FROM renumbered r
    WHERE s.id = r.id AND s.position <> r.new_pos;
   -- repeat with PARTITION BY template_id for pipeline_template_stages
   ```
8. Re-create CHECK constraints on both tables with the 6-value allowlist, reusing the same constraint names (`ck_template_stages_stage_type`, `ck_job_pipeline_stages_stage_type`).

Downgrade is lossy: deleted `offer` rows are not restored; rename-reverted rows keep their new UUID identities; re-sequenced positions stay re-sequenced. Documented in the migration header. A rollback script lives alongside per the root CLAUDE.md policy.

## 5. API surface

### 5.1 New endpoint — assignable users

```
GET /api/jobs/{job_id}/pipeline/assignable-users?role=interviewer|observer|reviewer
```

- Authz: `jobs.view` in the job's org unit ancestry.
- Logic: resolves ancestry of `job.org_unit_id`, joins `user_role_assignments` → `roles` → `users`, filters to active users whose role name matches the gate for the requested slot (§3).
- Response shape: `[{ user_id, full_name, email, role_labels: string[], org_unit_name: string }]`.
- Same tenant RLS as everything else. Reused by Phase 3 scheduler.

### 5.2 Stage schema additions (`app/modules/pipelines/schemas.py`)

```python
ParticipantRole = Literal["interviewer", "observer", "reviewer"]

class StageParticipantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    role: ParticipantRole

class StageParticipantResponse(StageParticipantInput):
    full_name: str
    email: str
```

- `PipelineStageInput` gains `participants: list[StageParticipantInput] = []`.
- `PipelineStageUpdateInput` gains `participants: list[StageParticipantInput] | None = None` — `None` means "don't touch", `[]` means "clear all".
- `PipelineStageResponse` gains `participants: list[StageParticipantResponse]` — always `[]` for template stages.

### 5.3 Validation rules (model-validator on `PipelineStageInput`)

Runs on every write path:

- `stage_type == 'debrief'` → all participants must have `role='reviewer'`.
- `stage_type in ('phone_screen', 'human_interview')` → all participants must have `role='interviewer'`.
- `stage_type == 'ai_screening'` → all participants must have `role='observer'`.
- `stage_type in ('intake', 'take_home')` → `participants` must be empty.
- Participants unique on `(user_id, role)` within a stage (also enforced by DB UNIQUE).

Separately, a service-level `_validate_participants_eligible(db, job, participants)` re-runs the role-gate query for every supplied `user_id` and rejects with 422 if any user isn't in the current pool. Server is authoritative — the picker UI is just a helper.

### 5.4 Behaviour per endpoint

| Endpoint | Participant handling |
|---|---|
| `POST /api/jobs/{id}/pipeline` (from template / starter) | Participants start empty. Templates + starters are staffing-agnostic. |
| `POST /api/jobs/{id}/pipeline` (from scratch) | Participants copied from input. Validator + eligibility check run. |
| `PATCH /api/jobs/{id}/pipeline` (auto-save) | Per stage: `participants=None` → untouched. Otherwise replace the stage's participant set via diff-and-sync. |
| `POST /api/jobs/{id}/pipeline/swap` | Participants cleared (new instance from a template/starter). |
| `POST /api/jobs/{id}/pipeline/reset` | Participants cleared. |
| `POST /api/jobs/{id}/pipeline/save-as-template` | Participants dropped during copy. |
| `POST /api/jobs/{id}/pipeline/update-source-template` | Participants dropped during copy. |
| `GET /api/jobs/{id}/pipeline` | Response includes participants (bulk `SELECT ... JOIN users WHERE stage_id = ANY(...)`). |
| Template endpoints (`GET`, `POST`, `PATCH`) | `participants=[]` always in response; request bodies with non-empty `participants` rejected (422). |

### 5.5 Service layer

New file `app/modules/pipelines/participants.py`:

- `replace_stage_participants(db, stage, participants, assigned_by)` — diff-and-sync for a single stage.
- `list_assignable_users(db, job, role)` — backend for the picker endpoint.
- `_validate_participants_eligible(db, job, participants)` — reused by create-from-scratch and update paths.

Extensions in `app/modules/pipelines/service.py`:

- `get_job_pipeline_with_stages` loads participants in one extra bulk query and attaches them to the response.
- `update_job_pipeline_stages` calls `replace_stage_participants` per stage when `participants` is not `None`. Deleted stages cascade their participants via FK.

### 5.6 Frontend client (`lib/api/pipelines.ts`)

- `StageType` rewritten to the 6 values.
- `ParticipantRole`, `StageParticipantInput`, `StageParticipantResponse` added.
- `PipelineStageInput`, `PipelineStageUpdateInput`, `PipelineStageResponse` gain the participants field per the semantics above.
- `pipelinesApi.getAssignableUsers(token, jobId, role)` method.

## 6. Frontend

### 6.1 New utility — `lib/pipelines/categories.ts`

Single source of truth for per-type UI behaviour:

```ts
export type StageCategory = 'entry' | 'human_led' | 'ai_led' | 'review' | 'disabled'

export function stageCategory(type: StageType): StageCategory { ... }
export function participantSlotsFor(type: StageType): ParticipantSlotSpec[] { ... }
export function isStageUnstaffed(stage: {...}): boolean { ... }
```

Every component reads from this file — no duplicated switch statements.

### 6.2 New hook — `lib/hooks/use-assignable-users.ts`

TanStack Query wrapper, key `['jobs', jobId, 'assignable-users', role]`, stale time 60s. Invalidated by role-assignment mutations in `settings/team`.

### 6.3 New component — `components/dashboard/pipeline/StageParticipantsEditor.tsx`

- Props: `stage`, `jobId`, `onChange`.
- Renders one section per slot returned by `participantSlotsFor(stage.stage_type)`.
- Each section: chip list of assigned users + searchable combobox backed by `use-assignable-users`. Combobox filters out already-assigned users.
- Does not mutate directly — hands changes back via `onChange`, the existing debounced auto-save in `JobPipelineFunnel` does the PATCH.

### 6.4 `StageConfigDrawer.tsx` changes

- Rewrite `STAGE_TYPES` array to the 6-value map with `<option disabled>` on `take_home` ("Coming soon" label).
- New optional prop `jobId?: string`. Participants editor renders only when `jobId` is defined **and** `participantSlotsFor(stage.stage_type).length > 0`. Template editor paths pass no `jobId`.
- When `stage_type` changes between categories, strip participants that are no longer valid for the new type.

### 6.5 `StageConfigurationTab.tsx` changes

Mirror the drawer changes — same type picker, same participants editor insertion point, same `jobId` gating.

### 6.6 `JobPipelineFunnel.tsx` changes

- `STAGE_TYPE_LABEL`, `stageTypeOptions`, `stageGate()`, `lead` computation all updated to the new 6-value set.
- Placeholder stage data (empty-pipeline preview) audited and updated.

### 6.7 `StageFlowCard.tsx`, `StageSlab.tsx`, `UnifiedPipelineView.tsx` changes

- Rewrite `STAGE_TYPE_LABELS`, `STAGE_TYPE_ICONS`, `STAGE_TYPE_ACCENT`, `STAGE_TYPE_TEXT` to the 6-value map.

Suggested icons (lucide-react):

| Type | Icon | Accent |
|---|---|---|
| `intake` | `Inbox` | zinc-400 |
| `phone_screen` | `Phone` | blue-500 |
| `ai_screening` | `Bot` | violet-500 |
| `human_interview` | `Users` | emerald-500 |
| `debrief` | `Gavel` | amber-500 |
| `take_home` | `FileText` | zinc-300 (muted) |

- Every card gains an "Unstaffed" badge when `isStageUnstaffed(stage)` is true.

### 6.8 `StageActionsMenu.tsx` changes

"Duplicate stage" clones the stage but clears participants — staffing is a runtime concern, not a stage-shape property.

### 6.9 Unstaffed enforcement

Soft-only in this change. Stages save freely. The "Unstaffed" badge is the only signal until Phase 3 session-start enforcement fires a hard block. (See §8.1 decision note.)

## 7. Question-bank, starter pack, backend side-effects

### 7.1 Prompt files (`backend/nexus/prompts/v1/`)

- Rename `question_bank_ai_interview.txt` → `question_bank_ai_screening.txt` (content unchanged).
- Delete `question_bank_panel_interview.txt` (`human_interview` prompt covers both 1-on-1 and panel).
- `question_bank_human_interview.txt`, `question_bank_phone_screen.txt`, `question_bank_take_home.txt`, `question_bank_common.txt`, `question_bank_regenerate_one.txt` — unchanged.
- No new files for `intake` or `debrief`.

### 7.2 `STAGE_TYPE_TO_PROMPT` (`app/modules/question_bank/actors.py`)

```python
STAGE_TYPE_TO_PROMPT = {
    "phone_screen":    "question_bank_phone_screen",
    "ai_screening":    "question_bank_ai_screening",
    "human_interview": "question_bank_human_interview",
    "take_home":       "question_bank_take_home",
}
```

The existing `RuntimeError` guard (actors.py:293-295) becomes the enforced contract for types that can't have questions.

### 7.3 Question-bank router filtering

- `list_banks` at router.py:270-306: loop body gains `continue` when `stage.stage_type not in STAGE_TYPE_TO_PROMPT`. No bank entry, no placeholder.
- `get_bank` at router.py:309-339: if `stage.stage_type not in STAGE_TYPE_TO_PROMPT`, return 409 `{detail: "Stage type does not support question banks"}` instead of auto-creating a draft bank.
- `_load_prior_stages_questions` + `_load_pipeline_context` in actors.py — no change.

### 7.4 Starter pack (`app/modules/pipelines/starter_pack.py`)

- `standard_technical`: `panel_interview` → `human_interview`, `ai_interview` → `ai_screening`.
- `fast_track`: `ai_interview` → `ai_screening`.
- `senior_leadership`: `panel_interview` → `human_interview`, `ai_interview` → `ai_screening` (final stage already `human_interview`, stays).
- `sales_commercial`, `volume_hiring`, `screening_only`: unchanged.

No participants in starters — they're staffing-agnostic. Default empty list on `PipelineStageInput` covers it.

### 7.5 Auto-apply hook

`auto_apply_pipeline_on_confirmation` unchanged in behaviour. All three sources (last-used template, org-default, system-fallback-starter) remain staffing-agnostic, so the created instance has zero participants and the "Unstaffed" badge fires immediately — the intended UX.

### 7.6 CLAUDE.md refresh (`backend/nexus/CLAUDE.md`)

Small addition under the pipelines module row:

- Stage types are categorised by a derived frontend helper; server validates per-type participant role constraints.
- `pipeline_stage_participants` is added to the `_assert_rls_completeness` enumerated list.
- `offer`, `recruiter`, `panel_interview`, `ai_interview` are hard-removed. Future code referencing them is dead code.

## 8. Decisions

### 8.1 Unstaffed enforcement is soft, not hard

Pipelines save freely with empty interviewer slots. Recruiter may build the pipeline and generate questions; Hiring Manager later reviews / edits questions and assigns participants. Participants can also be revised later.

Why: mirrors the existing auto-save model across signal filters / pass criteria / etc. Hard enforcement at pipeline-builder time blocks legitimate workflows. Natural enforcement point is session-start in Phase 3, where missing staffing is a real blocker.

### 8.2 Option-A role gating on the picker

The pool per slot uses existing system roles (`Interviewer`, `Hiring Manager`, `Observer`, `Recruiter`). Ignoring these here would drift the product — suddenly a Recruiter could be a panel Interviewer. If role membership is missing in practice, super admins fix it in the existing Settings → Org Units → Members UI.

### 8.3 Participants live on instances only, not templates

Templates are org-unit-wide and outlive team members. Storing user IDs on a template bit-rots the moment someone rotates teams. Counts (1+, etc.) are implicit from the stage type via `participantSlotsFor` — no template-level count config either.

### 8.4 Collapse `recruiter` + `human_interview` + `panel_interview` → single `human_interview`

Phone-screen stays separate: different in kind (short availability/CTC/compatibility check) and has its own LLM prompt. Panel vs 1-on-1 is captured by the number of assigned interviewers. Collapsing lets us drop `question_bank_panel_interview.txt` — one less prompt to keep synced.

### 8.5 Hard-delete `offer` rows on migration

MVP, one user (the author), no production data. Deleting keeps the data model clean rather than keeping a dead enum value for historical rows. Downgrade is lossy — accepted.

### 8.6 Normalised `pipeline_stage_participants` table, not JSONB columns

Better FK story (user deactivation cascades cleanly). RLS completeness check treats it as a tenant-scoped table like every other one. Future "my assignments" queries become indexed joins rather than JSONB scans.

## 9. Tests

### 9.1 Backend (pytest)

- `app/modules/pipelines/tests/test_participants.py` — round-trip, role/type validation, eligibility, PATCH `None` vs `[]` semantics, stage-type-change with stale participants, cascade on stage delete, cascade on user deactivation, template response always empty.
- `app/modules/pipelines/tests/test_assignable_users.py` — role gating per slot, inactive users excluded, ancestry-only inclusion, cross-tenant RLS.
- `app/modules/pipelines/tests/test_stage_type_migration.py` — legacy rows rename + deletion outcomes, new CHECK rejects old values.
- Existing tests updated: anywhere `panel_interview` / `recruiter` / `ai_interview` / `offer` appears.

### 9.2 Frontend (vitest)

- `lib/pipelines/categories.test.ts` — exhaustive `stageCategory` + `participantSlotsFor` assertions.
- `components/dashboard/pipeline/StageParticipantsEditor.test.tsx` — chip add/remove, combobox filtering, loading + error states.
- `components/dashboard/pipeline/StageConfigDrawer.test.tsx` — participants section hidden when `jobId` absent; category change zeroes participants in the onChange payload.

### 9.3 Migration + RLS validation

- Startup `_assert_rls_completeness` extended to include `pipeline_stage_participants`. Boot fails fast on policy drift.
- Integration test asserts app boot succeeds with the new table in the list.
- Runbook verification: `alembic upgrade head` + `\d pipeline_stage_participants` + `SELECT policyname FROM pg_policies WHERE tablename='pipeline_stage_participants'`.

## 10. Rollout

One PR. Backend migration, backend code, frontend code, starter pack, prompt renames, CLAUDE.md updates, tests — all together. No feature flag; no real users; split-rollout adds coordination cost for no gain.

Order within the PR / migration:

1. Alembic migration + rollback script.
2. Backend code changes.
3. Frontend code changes.
4. Prompt file renames + starter-pack updates + `STAGE_TYPE_TO_PROMPT` update.
5. `_assert_rls_completeness` enumerated list update.
6. Tests.

## 11. Out of scope / future

- Phase 3 scheduler picking specific interviewers from the assigned pool (first-available vs round-robin).
- Per-user "my assignments" landing page.
- `take_home` as a real UI flow.
- `intake` ATS integration.
- Pipeline-level "staffing complete" dashboard summary card.
