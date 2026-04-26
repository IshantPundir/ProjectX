# Pipeline flow & activation — recruiter end-to-end from JD to live job

- **Author:** @IshantPundir (with Claude)
- **Date:** 2026-04-26
- **Status:** Approved for implementation plan
- **Prerequisite:** [`2026-04-22-pipeline-stage-types-design.md`](./2026-04-22-pipeline-stage-types-design.md) (stage type taxonomy v5 + participants table — landed in migration `0016`)

## 1. Problem

The pipeline-builder is half-finished. The 6-type stage taxonomy from the 2026-04-22 spec landed (migration `0016`) and the per-category UI helpers exist (`frontend/app/lib/pipelines/categories.ts`), but the *flow* a recruiter follows from JD-paste to a live job is incoherent:

- **Silent auto-apply.** `auto_apply_pipeline_on_confirmation` runs as a side-effect of `confirm_signals`, so the recruiter lands on `/pipeline` looking at a funnel they didn't choose. There is no UI signal about *which* of the three fallback sources was used (last-used template / org default / system starter), no affordance to swap, and no first-time onboarding.
- **Uniform stage config.** `StageConfigDrawer` and `StageConfigurationTab` render `duration_minutes`, `difficulty`, `signal_filter`, `pass_criteria`, `advance_behavior`, and `sla_days` for *every* stage type, including `intake` and `debrief`. The Pydantic `PipelineStageBase` makes them required regardless of type. The matrix from the 2026-04-22 spec is encoded in the frontend helper but not enforced anywhere.
- **Questions surface shows IO stages.** `app/(dashboard)/jobs/[jobId]/questions/page.tsx` iterates *all* pipeline stages — recruiters see question-bank tabs for `intake` and `debrief` even though `stageSupportsQuestionBank()` already exists in the codebase and would correctly filter them. The bank-list endpoint *does* skip those stages server-side, but the UI doesn't.
- **No notion of "ready to ship."** A pipeline can sit half-staffed, with empty banks, and there's no gate. The job has no `active` state. There's no rule for what happens to a candidate when a recruiter edits a stage on a job that's already screening.
- **Question editing is wrong shape.** The current UI exposes raw text editing of question content. That fights the product's "AI decides, human verifies" posture (already used for JD signals) and lets a recruiter drift question wording out of style/calibration with the rest of the bank.

This spec finalizes the recruiter end-to-end flow from pasting a JD to a live job that's screening candidates.

## 2. Scope and non-goals

### In scope

- The recruiter journey J0 → J6: paste JD → review signals → confirm signals → choose pipeline source → configure stages → generate & refine question banks → activate.
- A new `pipeline_built` state and a new `active` state on `job_postings`. `archived` is added as a value (designed-for Phase 3+) but no transitions in/out are exposed.
- Removal of silent auto-apply on `confirm_signals`.
- A front-door **picker** (recent templates, team default, system starters, blank) as the only way to create a pipeline instance.
- An **activation gate** with an explicit checklist that gates the `pipeline_built → active` transition.
- A formal **edit-category model** (A/B/C/D) for changes on jobs in `pipeline_built` and `active`.
- A **stage pause** lifecycle (`active`/`paused`/removed) so a recruiter can take a stage out of new-candidate traversal without orphaning in-flight candidates.
- **Pipeline versioning** (monotonic `pipeline_version` integer per instance), bumped on every save, used by the activation classifier and Phase 3 candidate-journey reconstruction.
- **LLM-mediated question editing**: per-question Refine and Add operations that take a natural-language instruction and produce a proposed question. Direct controls remain for Mandatory toggle, Reorder, and Delete (no LLM).
- Per-category schema enforcement on both the backend (Pydantic) and the frontend (TypeScript discriminated union).
- One Alembic migration (`0017_pipeline_versioning_and_pause`) with rollback script.
- Tests (backend pytest + frontend vitest) and a one-PR rollout.

### Non-goals (explicit)

- Candidate kanban / candidate management / candidate detail pages — Phase 3.
- LiveKit session execution and the runtime session-start freeze enforcement — Phase 3 (this spec defines the schema and rule; runtime enforcement code lands later).
- ATS auto-intake that fills the `intake` stage — Phase 4.
- Specific interviewer scheduling (which person from the assigned pool runs a session, calendar invites) — Phase 3.
- `take_home` as a real candidate-facing flow — stays disabled "Coming soon".
- Per-question difficulty or per-question duration — explicitly rejected. Difficulty is stage-level only; duration is stage-level only.
- Manual raw-text editing of question content — explicitly rejected.
- Reports, scoring dashboards, analytics — Phase 3+.
- Pipeline deactivation (`active → pipeline_built` or `active → archived`) — out of scope; will be designed when archive/deactivate semantics with live candidates are clearer.
- Multi-pipeline-version A/B testing on a single job — out of scope.
- Pipeline rollback to a prior `pipeline_version` — not a feature; `pipeline_version` is a monotonic counter, not a git-style history.

## 3. Recruiter journey (J0 → J6)

```
J0  POST /api/jd                          {paste JD body, optional org_unit_id}
    └─ job created in state `draft`
       └─ enqueue Dramatiq actor extract_and_enhance_jd

J1  GET /api/jd/{id}  (poll / SSE)
    └─ job: draft → signals_extracting → signals_extracted
       └─ /jobs/{id}?tab=jd renders 3-panel JD review (signals editable)

J2  POST /api/jd/{id}/confirm-signals
    └─ job: signals_extracted → signals_confirmed
       └─ NO auto_apply_pipeline_on_confirmation (CHANGED — see §10)
       └─ "Pipeline" and "Interview Questions" tabs become visible+enabled
       └─ /pipeline shows the front-door picker (no instance exists yet)

J3  Recruiter picks a starting point on /pipeline
    └─ POST /api/jobs/{id}/pipeline {source: "template:<id>" | "starter:<key>" | "scratch"}
    └─ pipeline instance created with pipeline_version = 1
    └─ stages copied from source, no participants attached
    └─ banks NOT auto-generated
    └─ job: signals_confirmed → pipeline_built

J4  Recruiter configures stages on /pipeline
    └─ Add / remove / reorder middle stages   (intake, debrief locked at endpoints)
    └─ Configure per-category fields           (per the matrix in §6)
    └─ Assign participants                      (interviewers / observers / reviewers)
    └─ Each save: PATCH /api/jobs/{id}/pipeline → pipeline_version bumps

J5  Recruiter generates and refines question banks
    └─ "Generate banks" CTA → all bank-eligible stages without banks → fan out actors
    └─ Per-stage "Generate" button       → single actor
    └─ Per-question Refine               → POST .../questions/{q_id}/refine (sync LLM)
    └─ Per-question Add                  → POST .../questions/draft     (sync LLM)
    └─ Per-question Mandatory toggle     → no LLM
    └─ Per-question Reorder              → no LLM
    └─ Per-question Delete               → no LLM
    └─ Stage-level Regenerate            → existing actor
    └─ Bank Confirm                      → optional; not gating per §7

J6  Recruiter clicks "Activate" — only enabled when checklist passes
    └─ POST /api/jobs/{id}/activate
    └─ server re-runs the gate predicates as authoritative check
    └─ job: pipeline_built → active
    └─ Edit warnings now apply per §8
```

## 4. State machines

### 4.1 Job state

```
                                    ┌──────────────┐
                                    │   archived   │   (Phase 3+; no transitions defined here)
                                    └──────────────┘
                                           ▲
                                           │ (out of scope)
        ┌────────┐    ┌────────────────────┐    ┌───────────────────────┐    ┌─────────────────┐    ┌────────┐
J0 →    │ draft  │ →  │ signals_extracting │ →  │  signals_extracted    │ →  │ pipeline_built  │ →  │ active │
        └────────┘    └────────────────────┘    └───────────────────────┘    └─────────────────┘    └────────┘
                            │     ▲                  │ ▲                          ▲
                            ▼     │ (retry)          ▼ │ (revise signals)         │
                  ┌────────────────────────┐    ┌────────────────────┐            │
                  │ signals_extraction_    │    │ signals_confirmed  │ ───────────┘
                  │ failed                 │    └────────────────────┘   (J3: picker creates instance)
                  └────────────────────────┘
```

**New legal transitions to add to `app/modules/jd/state_machine.py:LEGAL_TRANSITIONS`:**

```python
"signals_confirmed": {"signals_extracted", "pipeline_built"},   # picker creates instance
"pipeline_built":    {"active"},                                # activation
"active":            set(),                                     # terminal at MVP (archive in Phase 3+)
"archived":          set(),                                     # terminal
```

The existing `"signals_confirmed": {"signals_extracted"}` reverse transition is preserved (used when the recruiter wants to deeply re-extract the JD body); it gains `pipeline_built` as a second target.

**Existing states retained unchanged:** `draft`, `signals_extracting`, `signals_extraction_failed`, `signals_extracted`, `signals_confirmed`.

**Signal-edit semantics on `pipeline_built` and `active`:** signal edits don't change job state. They bump the existing `signal_snapshot` version (Phase 2B `save_signals` mechanism), which marks affected banks stale via `compute_is_stale`. The job stays where it is.

### 4.2 Pipeline instance lifecycle

```
(no instance) ──── recruiter picks source on /pipeline ──→ created (pipeline_version = 1)
                                                                │
                                                                │  every save (PATCH /pipeline,
                                                                │  stage CRUD, participant change,
                                                                │  bank-question CRUD, etc.)
                                                                ▼
                                                        pipeline_version = N+1
                                                                │
                                                                │  "Reset to source" / "Swap source"
                                                                ▼
                                                  instance replaced; pipeline_version = 1 again
                                                  (Reset/Swap blocked when job is `active` — see §8)
```

- One pipeline instance per job. Created exclusively via the J3 picker — never silently.
- `pipeline_version` is monotonic per instance.
- Activation does not freeze the instance — edits continue. Activation only changes which edit warnings apply.

### 4.3 Stage state

```
                     ┌──────────┐
                     │  active  │ ◄──── unpause ────┐
                     └──────────┘                    │
                       │      │                       │
                       │      │ pause                 │
                       │      ▼                       │
                       │  ┌──────────┐                │
                       │  │  paused  │ ───────────────┘
                       │  └──────────┘
                       │      │
                       │      │ delete (only when in-flight = 0)
                       ▼      ▼
                     [removed from DB; cascade banks/participants]
```

- `active` (default, the absence of `paused_at`): new candidates flow through.
- `paused` (`paused_at IS NOT NULL`): new candidates skip on traversal; in-flight candidates remain pending until manually moved/rejected. Bank stays in DB (read-only). Participants stay assigned.
- (removed): physical deletion. Allowed only when in-flight count = 0. Cascades to bank rows, question rows, participant rows.

**Constraints:**
- `intake` and `debrief` stages **cannot be paused or removed**. Server enforces in pause/delete handlers (409 with `code: stage_pause_forbidden_for_endpoint_type` / `stage_delete_forbidden_for_endpoint_type`).
- Pause is exposed in both `pipeline_built` and `active` for UX consistency. Pre-activation, pause is functionally a no-op (no in-flight candidates exist) but the affordance is the same.
- Stage `paused_at` does not bump `pipeline_version`. Wait — yes it does, because pausing is a save and the activation classifier needs to see it. Pausing bumps `pipeline_version` like any other save.

## 5. Versioning, runtime resolution, and session-start freeze

### 5.1 The unified rule

> **Pipeline shape resolves at advance-time. Everything inside a stage resolves at session-start. Once a session starts, that session is frozen.**

| Field | Resolution | Why |
|---|---|---|
| Pipeline shape (which stages exist, in what order, paused_at) | Runtime — at advance-time | New stages added after a candidate's current stage are traversed; new stages added before are skipped |
| Stage `name`, `duration_minutes`, `difficulty`, `signal_filter`, `pass_criteria`, `advance_behavior`, `sla_days` | Runtime — at session-start | A candidate sitting in a stage but who hasn't started their session sees the latest config when their session opens |
| Stage participants (interviewer / observer / reviewer pool) | Runtime — at session-start | Same |
| Bank questions (text, signal_probed, mandatory, ordering) | Runtime — at session-start | Same — recruiter Refine on a question before the session starts means the candidate gets the latest |
| JD signals referenced by banks (`signal_snapshot_id`) | Snapshot at bank generation | Existing Phase 2C.2 behavior (`compute_is_stale` compares `bank.signal_snapshot_id != latest`) |
| Frozen for the duration of a single session | All of the above, captured into per-session snapshot columns at the moment the LiveKit room opens | Phase 3 enforcement; columns designed-for here |

### 5.2 `pipeline_version`

`pipeline_instances.pipeline_version int NOT NULL DEFAULT 1`. Bumped **atomically inside the same transaction** as any save that touches:

- Stages (CRUD, reorder, pause, unpause)
- Stage participants (add/remove/swap)
- Stage configuration (any matrix-allowed field)
- Bank-question CRUD (Refine accept, Add accept, Delete, Mandatory toggle, Reorder)
- Bank state transitions (`generated`, `confirmed`)

**Not bumped on:**
- JD signal edits (those bump `signal_snapshot.version` — a separate axis)
- Read-only operations (status checks, list, dry-run classification)

### 5.3 Bank pinning + persisted `is_stale`

Today: `compute_is_stale(bank) = (bank.signal_snapshot_id != latest_signal_snapshot.id)`. Computed on every read in `app/modules/question_bank/service.py:150` and called from each router read path.

Change: persist `is_stale` (denormalized) and extend its definition.

Add to `question_banks`:
- `pipeline_version_at_generation int NULL` — set on every generation/regenerate to `instance.pipeline_version` at the time.
- `stage_config_snapshot jsonb NULL` — captures the stage's `signal_filter`, `difficulty`, `duration_minutes`, `pass_criteria`, `advance_behavior` at generation.
- `is_stale bool NOT NULL DEFAULT false` — *new column*, denormalized.

`is_stale` becomes:

```python
is_stale = (
    bank.signal_snapshot_id != current_signal_snapshot.id
 OR bank.stage_config_snapshot != current_stage_config(stage)
)
```

Recomputed **on writes** that could affect it (signal save, stage config save, bank generation/regeneration), and persisted. Read paths read the column directly. The `compute_is_stale` helper is repurposed into a writer (`recompute_and_persist_stale(db, bank)`) called from the relevant write paths. `bank.status` does not change on stale flip; it stays `generated` (or `confirmed`-then-dropped, see §11.5).

### 5.4 Candidate journey snapshot — designed-for, Phase 3

The schema needs to support per-candidate forensic reconstruction. **No columns are added to candidate-journey tables in this spec** because the `candidate_journey` table doesn't exist yet. The Phase 3 migration that creates it must include:

```
entered_at_pipeline_version    int     NOT NULL
current_stage_id               uuid    NOT NULL
completed_stage_ids            uuid[]  NOT NULL DEFAULT '{}'
```

With the advance-time resolution algorithm:

```python
def next_stage_for(journey: CandidateJourney) -> Stage | None:
    current = stages_by_id[journey.current_stage_id]
    return min(
        (s for s in pipeline.stages
         if s.position > current.position
         and s.paused_at is None
         and s.id not in journey.completed_stage_ids),
        key=lambda s: s.position,
        default=None,
    )
```

Reorders that would create a non-monotonic position vs. `completed_stage_ids` are naturally skipped by the `s.id not in completed_stage_ids` clause. Phase 3 may add stricter pre-flight checks if real workflows produce confusion.

### 5.5 Session-start freeze — designed-for, Phase 3

The Phase 3 migration that creates `interview_session` must include four snapshot columns, written atomically with LiveKit room creation:

```
bank_questions_snapshot     jsonb     NOT NULL
participants_snapshot       jsonb     NOT NULL
pass_criteria_snapshot      jsonb     NOT NULL
stage_config_snapshot       jsonb     NOT NULL
```

Edits to underlying entities after session-start have no effect on the running session. Reports run from the snapshots, not the live tables.

## 6. Per-category capability matrix (authoritative)

This is the contract. Both UI and server enforce it. ✓ = configurable; ✗ = absent from UI/schema; **locked: X** = field exists but is server-set and not editable from request bodies.

| Capability | `entry`<br>(intake) | `human_led`<br>(phone_screen, human_interview) | `ai_led`<br>(ai_screening) | `review`<br>(debrief) | `disabled`<br>(take_home) |
|---|---|---|---|---|---|
| Custom name | ✓ | ✓ | ✓ | ✓ | ✓ |
| Position | locked: 0 | ✓ | ✓ | locked: last | ✓ |
| Duration minutes | ✗ | ✓ | ✓ | ✗ | ✗ |
| Difficulty *(stage-level, governs all questions)* | ✗ | ✓ | ✓ | ✗ | ✗ |
| Signal filter | ✗ | ✓ | ✓ | ✗ | ✗ |
| Pass criteria | locked: `all_knockouts_pass` *(no-op, always advances)* | knockouts \| threshold \| manual | knockouts \| threshold \| manual | locked: `manual_review` | ✗ |
| Advance behavior | locked: `auto_advance` | auto \| manual | auto \| manual | locked: `manual_review` *(terminal — outcome is hire/reject/hold)* | ✗ |
| SLA days | ✓ *(acceptance window)* | ✓ *(reach-out → completion)* | ✓ | ✓ *(decision deadline for HM)* | ✗ |
| OTP required (Phase 3) | ✗ | ✓ | ✓ | ✗ | ✗ |
| Participants | ✗ | interviewers (≥1, **required for activation**) | observers (≥0, optional) | reviewers (≥1, **required for activation**) | ✗ |
| Question bank | ✗ | ✓ | ✓ | ✗ | ✓ (Phase later) |

**Enforcement layers** (all four must agree):

1. **Frontend forms** (`StageConfigDrawer`, `StageConfigurationTab`): render only the matrix-✓ inputs. Locked fields render as disabled chips with explanatory tooltips.
2. **Frontend types** (`lib/api/pipelines.ts`): `PipelineStageInput` becomes a TypeScript discriminated union by `stage_type`. Compile-time error to construct an invalid combination.
3. **Backend Pydantic** (`app/modules/pipelines/schemas.py`): a sibling `_validate_fields_for_stage_type` model_validator alongside the existing `_validate_participants_role_for_type`. On request: reject `Forbidden` fields (422), require `Required` fields (422), server-stamp `LockedTo` values regardless of request body content.
4. **Backend service**: write paths assume the validator has run. No defensive duplicates.

## 7. Activation gate

`POST /api/jobs/{id}/activate` runs the predicates server-side. UI mirrors them as affordances, never as the source of truth.

### 7.1 Required predicates (hard gate)

| # | Predicate | Failure copy |
|---|---|---|
| 1 | A pipeline instance exists | (auto-true once `pipeline_built` is reached) |
| 2 | At least one **middle stage** in `{phone_screen, ai_screening, human_interview}` *(`take_home` excluded — disabled)* | "Add at least one screening stage between Intake and Debrief." |
| 3 | Every `human_led` stage has ≥1 interviewer assigned | "Assign an interviewer to *Phone Screen*." |
| 4 | The `debrief` stage has ≥1 reviewer assigned | "Assign a reviewer to *Debrief*." |
| 5 | Every bank-eligible stage has a bank in state `generated` or `confirmed` (non-empty, no failed banks) | "Generate a question bank for *AI Screening*." |
| 6 | Every stage has a non-empty trimmed `name` | "Stage at position 2 has no name." |
| 7 | Every stage has positions sequential `0..N-1` | (system error if violated — defensive check; should always be true) |

### 7.2 Soft warnings (visible, never block)

- `ai_led` stage(s) with zero observers — "AI Screening has no observers; no human will see this session live."
- Any stage with `sla_days` unset — "No SLA configured for *Phone Screen*."
- Pipeline unchanged from source template/starter since picker — "You haven't customized the starter pipeline. Activate anyway?"
- Bank in state `generated` but never confirmed — informational, doesn't block.

### 7.3 The activation surface (UI)

A status strip at the top of `/pipeline`:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⚠  3 things needed before you can activate this job:                    │
│   • Add an interviewer to Phone Screen                                   │
│   • Add a reviewer to Debrief                                            │
│   • Generate a question bank for AI Screening                            │
│                                                          [ Activate ]    │ ← disabled
└──────────────────────────────────────────────────────────────────────────┘
```

When all required pass, the strip flips green and the button enables. Each warning item is clickable — focuses the relevant stage's config drawer.

## 8. Edit categories on jobs in `pipeline_built` and `active`

Every save runs through one of four categories. The category is determined server-side from the diff between the current and proposed pipeline state.

| Category | Triggering changes | Save UX | Server semantics |
|---|---|---|---|
| **A. Forward-only safe** | Edit a question (Refine/Add/Reorder/Delete/Mandatory toggle), edit a signal in the JD, swap a participant in/out, change SLA days, change difficulty, change duration, change pass criteria (where allowed), change advance behavior (where allowed), change signal filter | Soft inline banner: *"This affects new candidates and any candidate whose next session hasn't started yet. Candidates currently mid-session keep the questions and config they started with."* No modal. | `pipeline_version` bumps. `is_stale` recomputed for affected banks. |
| **B. Shape additive** | Add a stage (anywhere except endpoints), reorder middle stages, **unpause** a stage | Modal: *"This changes the pipeline shape. New candidates will see N+1 stages. M candidates currently in flight stay on their entered shape and will not be re-routed through the new stage."* Confirm to save. | `pipeline_version` bumps. New stage created in state `active` (`paused_at = NULL`). |
| **C. Shape subtractive** | Remove a stage, **pause** a stage | If stage's in-flight count = 0: confirm dialog → **delete**. If in-flight count > 0: dialog forces **pause-first** path. After pause, recruiter must drain in-flight candidates before delete. | `pipeline_version` bumps on pause. Delete cascades bank, questions, participants. |
| **D. Identity-changing** | Change a stage's `stage_type` | **Blocked on `active` jobs.** Inline error: *"Stage type can't be changed once the job is active. Remove this stage and add a new one of the desired type."* On `pipeline_built` jobs, freely allowed (no candidates exist). | 409 from server with `code: stage_type_change_forbidden`. |

### 8.1 Pause-first flow for Category C with in-flight candidates

```
Recruiter clicks Remove on a stage with in-flight = K (K > 0)
        │
        ▼
Modal: "K candidates are currently in this stage. Pausing this stage will
       stop new candidates from entering it. You'll need to advance or reject
       the K in-flight candidates manually before it can be fully removed."
       [Pause Stage]  [Cancel]
        │
        ▼ (Pause)
Stage marked paused (paused_at = now()). New candidates skip on traversal. In-flight remain.
        │
        ▼ (recruiter drains via candidate list — Phase 3 UI)
in-flight count → 0
        │
        ▼ (recruiter returns; "Remove" now enabled on the paused stage)
Confirm dialog → DELETE stage (cascades).
```

### 8.2 Reset / Swap source on `active` jobs

`POST /api/jobs/{id}/pipeline/reset` and `.../swap` are blocked when the job is `active`. They return 409 `code: pipeline_replace_forbidden_on_active`. The recruiter must edit the pipeline in place (Categories A/B/C). Adding a "deactivate to re-pick source" affordance is out of scope (see §2 non-goals).

### 8.3 Server-side change classification

Frontend issues a dry-run before applying:

```
POST /api/jobs/{id}/pipeline:dry-run
Body: <proposed PATCH body>
Response: { category: "A"|"B"|"C"|"D", warnings: string[], in_flight: { stage_id: count, ... } }
```

Frontend renders the appropriate banner/modal, then submits the real `PATCH` if the recruiter confirms. **The actual PATCH re-runs classification server-side** as a TOCTOU defense — between dry-run and apply, a candidate may have entered a stage. If category escalated (e.g., from B to C), the PATCH returns 409 `code: pipeline_diff_category_changed` and the frontend re-classifies and re-prompts.

## 9. Front-door picker

When `/jobs/{id}/pipeline` loads with the job in `signals_confirmed` (no instance yet), the funnel does not render. The page renders `PipelineSourcePicker` instead.

### 9.1 Picker layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Choose a starting point for this job's pipeline                            │
├──────────────────────────────────────────────────────────────────────────────┤
│  RECENT TEMPLATES                       (top 3, ranked by recency)          │
│  TEAM DEFAULT                           (only if set and not in Recent)     │
│  SYSTEM STARTERS                        (always shown — 4 cards)            │
│  BLANK                                  (always last — intake + debrief)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Sourcing:**

- **Recent templates**: top 3 templates from the same `org_unit_id` (or any ancestor), ranked by `MAX(pipeline_instances.created_at WHERE source_template_id = template.id) DESC`. De-duplicated against Team Default.
- **Team Default**: a template marked `is_default = true` for the org unit ancestry. Single card, with a star.
- **System starters**: the 4 starter packs in `app/modules/pipelines/starter_pack.py` (`standard_technical`, `fast_track`, `screening_only`, `senior_leadership`).
- **Blank**: creates an instance with only `intake` + `debrief`. Recruiter manually adds middle stages.

Each card on hover shows the stage chain. Clicking creates the instance and replaces the picker with the funnel — same page, no navigation.

### 9.2 Picker endpoint

The existing `POST /api/jobs/{id}/pipeline` is reshaped to a discriminated body:

```python
PipelineSourceTemplate = {"source": "template", "template_id": UUID}
PipelineSourceStarter  = {"source": "starter",  "starter_key": StarterKey}
PipelineSourceScratch  = {"source": "scratch"}
```

`StarterKey = Literal["standard_technical", "fast_track", "screening_only", "senior_leadership"]`.

On success: pipeline instance created, `pipeline_version = 1`, no participants attached, banks NOT auto-generated, job transitions `signals_confirmed → pipeline_built`. 409 with `code: pipeline_already_exists` if an instance already exists (recruiter uses Swap, not re-create).

### 9.3 Source attribution & swap on the funnel header

After a pipeline instance exists, the funnel header shows:

- **Source pill**: "Based on **Eng Default** *(team template)*" / "Based on **Standard Technical** *(system starter)*" / "**Custom** *(no source)*". Pill is clickable; opens the template detail in a side drawer (template only).
- **Diverged indicator**: small "Edited" pill when `pipeline_version > 1`.
- **Kebab menu** with four operations (existing endpoints):

| Operation | Endpoint | When available |
|---|---|---|
| Reset to source | `POST /api/jobs/{id}/pipeline/reset` | Source is non-null AND job is not `active` |
| Swap source | `POST /api/jobs/{id}/pipeline/swap` (re-opens picker) | Job is not `active` |
| Save as new template | `POST /api/jobs/{id}/pipeline/save-as-template` | Always |
| Update source template | `POST /api/jobs/{id}/pipeline/update-source-template` | Source IS a tenant template; job is not `active` |

## 10. Removal of silent auto-apply

`auto_apply_pipeline_on_confirmation` is **no longer called from `confirm_signals`** in `app/modules/jd/service.py` (the import at line 429 and the await call at line 431 are removed). The function itself stays in `app/modules/pipelines/service.py` as a helper; it is still exercised by tests and remains available for any future "fast path" use case but is not wired into the production confirm flow.

**One-time data migration in `0017`:**

```sql
-- Existing jobs that already had auto-apply produce a pipeline instance —
-- bring them to the new state.
UPDATE job_postings
   SET status = 'pipeline_built'
 WHERE status = 'signals_confirmed'
   AND id IN (SELECT job_id FROM pipeline_instances);
```

Existing jobs in `signals_confirmed` *without* an instance see the picker on next visit.

## 11. Question bank lifecycle

### 11.1 Generation triggers (Q6a: C + D)

Two trigger points; nothing else generates banks.

1. **"Generate banks for all stages" CTA** on `/pipeline`. One click → fan out one Dramatiq actor per bank-eligible stage that doesn't already have a bank. Skips stages with existing banks (recruiter uses per-stage Regenerate to overwrite).
2. **"Generate" button per stage** in the stage card / config drawer. Single actor for that one stage's bank.

Pipeline creation does **not** auto-generate banks. Adding a new stage does **not** auto-generate. JD signal re-confirm does **not** auto-generate.

### 11.2 Bank state (existing + small additions)

```
            (no bank row)
                │
                ▼  generate
            ┌───────┐  actor finishes  ┌─────────────┐  recruiter clicks
            │ draft │ ───────────────→ │  generated  │ ─────────────────→ confirmed
            └───────┘                  └─────────────┘  "Confirm bank"
                                          │  ▲
                                          │  │
                  "Regenerate" (existing) │  │
            ┌────────────────────────────────┤  │
            ▼                                   │
        (wipes + re-runs actor)                 │
                                                │ (signal_snapshot OR stage_config changes)
                                                ▼
                                        ┌──────────────────┐
                                        │ generated +      │
                                        │ is_stale = true  │
                                        └──────────────────┘
                                                │
                                        ┌───────┴────────┐
                                        ▼                ▼
                                  (recruiter         (recruiter
                                   regenerates)      ignores; activates)
```

### 11.3 Per-question operations

**Two LLM-mediated operations** (sync HTTP, full pipeline context per Q6b-ii Option C):

```
POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}/refine
Body:     { instruction: string }
Response: { proposed_text: string, proposed_signal_probed: string, proposed_mandatory: bool }

POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/draft
Body:     { instruction: string }
Response: { proposed_text: string, proposed_signal_probed: string, proposed_mandatory: bool, proposed_position: int }
```

Both are **stateless previews** — the response is not persisted. The recruiter sees the proposal, clicks Accept (which makes the actual mutation call) or Refine again (re-call with a new instruction; the previous proposal is discarded). No manual text editing of LLM output.

The acceptance call:

```
PUT  /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}    (Refine accept — replaces text + metadata)
POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions                  (Add accept — creates the question)
```

Both bump `pipeline_version`. Both are Category A (forward-only safe).

**Three direct operations** (no LLM):

```
PATCH  /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}    Body: { mandatory: bool }
PATCH  /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions:reorder         Body: { question_ids: UUID[] }
DELETE /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}
```

All three are Category A.

### 11.4 Stage-level operations

**Regenerate** (existing): wipes the bank, runs the full-bank actor. Treated as Category A on `active` jobs (forward-only — in-flight candidates not in mid-session pick up the new bank at session-start). Strong confirm dialog before fire because it discards prior recruiter refinements.

**Confirm** (new affordance): `POST /api/jobs/{job_id}/pipeline/stages/{stage_id}/bank/confirm` sets `bank.status = 'confirmed'`, stamps `confirmed_at` and `confirmed_by`. Available on banks in `generated` state (not stale, not failed). Optional per §7 (not gating activation).

### 11.5 Stale + confirmation interaction

A confirmed bank that goes stale **drops back to `generated`**:

- `confirmed_at = NULL`
- `confirmed_by = NULL`
- `status = 'generated'`
- `is_stale = true`

Rationale: "what you confirmed is no longer based on the current signals/stage config" → re-review needed. Implemented in the same write path that recomputes `is_stale` (signal save, stage config save).

### 11.6 Prompts

Two new prompt files in `prompts/v1/`:

- `question_refine_single.txt` — input includes the existing question, the recruiter's instruction, and full pipeline context.
- `question_create_single.txt` — input includes the recruiter's instruction and full pipeline context.

`question_bank_regenerate_one.txt` is preserved (used internally if needed; not surfaced in this design's UI).

**"Full pipeline context"** (used by the existing actor and the new prompts):

- JD signals (current snapshot).
- This stage's `name`, `signal_filter`, `difficulty`, `duration_minutes`, `pass_criteria`.
- The full existing bank for this stage.
- Prior stages' banks (so questions don't repeat across stages).

Refactor `build_question_context(stage)` out of the existing actor as a shared loader called from both the actor and the new endpoints.

## 12. Database schema changes

### 12.1 Migration `0017_pipeline_versioning_and_pause`

Single transaction:

```sql
-- pipeline_instances: monotonic version counter
ALTER TABLE pipeline_instances
  ADD COLUMN pipeline_version int NOT NULL DEFAULT 1;

-- job_pipeline_stages: pause state
ALTER TABLE job_pipeline_stages
  ADD COLUMN paused_at timestamptz NULL;
CREATE INDEX ix_job_pipeline_stages_paused_at
  ON job_pipeline_stages (instance_id) WHERE paused_at IS NOT NULL;

-- question_banks: forensic columns + denormalized stale flag
ALTER TABLE question_banks
  ADD COLUMN pipeline_version_at_generation int NULL,
  ADD COLUMN stage_config_snapshot jsonb NULL,
  ADD COLUMN is_stale bool NOT NULL DEFAULT false;

-- Backfill is_stale for existing banks (compare against current state once at migration time)
-- Subsequent recomputation happens on writes via recompute_and_persist_stale.
UPDATE question_banks qb
   SET is_stale = (
       qb.signal_snapshot_id != (
           SELECT id FROM job_posting_signal_snapshots
            WHERE job_posting_id = (
                SELECT instance.job_id
                  FROM job_pipeline_stages stage
                  JOIN pipeline_instances instance ON instance.id = stage.instance_id
                 WHERE stage.id = qb.stage_id
            )
            AND confirmed_at IS NOT NULL
            ORDER BY version DESC LIMIT 1
       )
   );

-- jobs status: add new states
ALTER TABLE job_postings
  DROP CONSTRAINT IF EXISTS ck_job_postings_status;
ALTER TABLE job_postings
  ADD CONSTRAINT ck_job_postings_status
  CHECK (status IN ('draft', 'signals_extracting', 'signals_extraction_failed',
                    'signals_extracted', 'signals_confirmed',
                    'pipeline_built', 'active', 'archived'));

-- One-time data migration: existing jobs that already had auto-apply produce
-- a pipeline instance — bring them to pipeline_built.
UPDATE job_postings
   SET status = 'pipeline_built'
 WHERE status = 'signals_confirmed'
   AND id IN (SELECT job_id FROM pipeline_instances);
```

Notes:

- `is_stale` is added with `DEFAULT false` and backfilled inline. Subsequent writes call a `recompute_and_persist_stale` helper.
- The exact CHECK constraint name (`ck_job_postings_status`) and the exact column on `job_postings` (`status`) are verified against `app/models.py:156` (column) and the existing migration history (constraint name pattern).
- No new tables in this migration; `_assert_rls_completeness` enumerated list in `app/main.py` is unchanged.
- A rollback script lives alongside per the root CLAUDE.md policy. See §15.

### 12.2 ORM model updates (`app/models.py`)

```python
# JobPipelineInstance
pipeline_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

# JobPipelineStage
paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

# QuestionBank
pipeline_version_at_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
stage_config_snapshot:          Mapped[dict | None] = mapped_column(JSONB, nullable=True)
is_stale:                       Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
```

### 12.3 Pydantic schema enforcement

In `app/modules/pipelines/schemas.py`, add a sibling validator to the existing `_validate_participants_role_for_type`:

```python
class FieldRule(StrEnum):
    REQUIRED  = "required"
    FORBIDDEN = "forbidden"
    OPTIONAL  = "optional"

# LockedTo is represented separately: dict[StageType, dict[str, Any]] keyed by field name.
_FIELD_RULES_BY_TYPE: dict[StageType, dict[str, FieldRule]] = {
    "intake":          {"duration_minutes": FORBIDDEN, "difficulty": FORBIDDEN, "signal_filter": FORBIDDEN,
                        "pass_criteria": LOCKED, "advance_behavior": LOCKED, "sla_days": OPTIONAL,
                        "otp_required": FORBIDDEN},
    "phone_screen":    {"duration_minutes": REQUIRED, "difficulty": REQUIRED, "signal_filter": REQUIRED,
                        "pass_criteria": REQUIRED, "advance_behavior": REQUIRED, "sla_days": OPTIONAL,
                        "otp_required": OPTIONAL},
    "ai_screening":    {...same as phone_screen...},
    "human_interview": {...same as phone_screen...},
    "debrief":         {...mostly forbidden, pass_criteria/advance_behavior LOCKED...},
    "take_home":       {...mostly forbidden — disabled stage...},
}

_LOCKED_VALUES: dict[StageType, dict[str, Any]] = {
    "intake":  {"pass_criteria": {"type": "all_knockouts_pass"}, "advance_behavior": "auto_advance"},
    "debrief": {"pass_criteria": {"type": "manual_review"},      "advance_behavior": "manual_review"},
}
```

Validator behavior:
- `FORBIDDEN`: 422 if request includes a non-null value (intake with difficulty rejected).
- `LOCKED`: server stamps the canonical value; request body's value is silently replaced.
- `REQUIRED`: 422 if absent.
- `OPTIONAL`: pass-through.

## 13. Endpoints — full inventory

### 13.1 New

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/jobs/{id}/activate` | Run gate; transition `pipeline_built → active`; 422 with `predicates_failed` array on failure |
| POST | `/api/jobs/{id}/pipeline:dry-run` | Classify proposed PATCH → `{category, warnings, in_flight}` |
| POST | `/api/jobs/{id}/pipeline/stages/{stage_id}/pause` | Set `paused_at = now()`. 409 on intake/debrief |
| POST | `/api/jobs/{id}/pipeline/stages/{stage_id}/unpause` | Clear `paused_at` |
| POST | `/api/jobs/{id}/pipeline/stages/{stage_id}/bank/confirm` | Set `bank.status = 'confirmed'` |
| POST | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions/{q_id}/refine` | Sync LLM call; returns proposal; **no DB write** |
| POST | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions/draft` | Sync LLM call; returns proposal; **no DB write** |
| PUT | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions/{q_id}` | Replace question (refine accept) |
| POST | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions` | Create question (add accept) |
| PATCH | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions/{q_id}` | Toggle `mandatory` |
| PATCH | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions:reorder` | New positions for the stage's questions |
| DELETE | `/api/jobs/{id}/pipeline/stages/{stage_id}/questions/{q_id}` | Delete question |

### 13.2 Modified

| Path | Change |
|---|---|
| `POST /api/jd/{id}/confirm-signals` | **Remove** call to `auto_apply_pipeline_on_confirmation`. Job transitions `signals_extracted → signals_confirmed` only. |
| `POST /api/jobs/{id}/pipeline` | Body becomes discriminated union by `source` (template / starter / scratch). On success: pipeline created AND job transitions `signals_confirmed → pipeline_built`. |
| `PATCH /api/jobs/{id}/pipeline` | Runs change classifier internally (re-runs it as a TOCTOU defense vs the dry-run); bumps `pipeline_version`; rejects Category D on `active` jobs with 409. |
| `POST /api/jobs/{id}/pipeline/reset` | Blocked when job is `active` (409 `code: pipeline_replace_forbidden_on_active`). Otherwise unchanged. |
| `POST /api/jobs/{id}/pipeline/swap` | Same as reset. Unchanged otherwise. |
| Bank read paths in `app/modules/question_bank/router.py` | Read persisted `is_stale` instead of calling `compute_is_stale()`. |
| Bank write paths (signal save, stage config save, generation, regeneration) | Call `recompute_and_persist_stale(db, bank)` before commit. |

### 13.3 Unchanged

- `POST /api/jobs/{id}/pipeline/save-as-template`
- `POST /api/jobs/{id}/pipeline/update-source-template`
- `GET /api/jobs/{id}/pipeline/assignable-users?role=`
- All `question_bank` SSE streams
- All JD SSE / status / list endpoints
- All template / starter / org-default endpoints

## 14. Frontend file inventory

### 14.1 New files

| Path | Responsibility |
|---|---|
| `components/dashboard/pipeline/PipelineSourcePicker.tsx` | §9 picker UI |
| `components/dashboard/pipeline/ActivationGate.tsx` | §7 status strip + Activate button |
| `components/dashboard/pipeline/SourcePill.tsx` | §9.3 funnel header source pill + kebab |
| `components/dashboard/pipeline/EditCategoryWarningModal.tsx` | §8 Category B/C confirm dialogs |
| `components/dashboard/question-bank/RefineQuestionDialog.tsx` | §11.3 Refine modal |
| `components/dashboard/question-bank/AddQuestionDialog.tsx` | §11.3 Add modal |
| `lib/api/questions.ts` | Typed namespace for refine/draft/CRUD |
| `lib/hooks/use-pipeline-classify.ts` | Wrapper around `:dry-run` |
| `lib/hooks/use-activate-job.ts` | Wrapper around `POST /activate` |
| `lib/hooks/use-refine-question.ts`, `use-draft-question.ts` | Sync LLM call wrappers |

### 14.2 Modified files

| Path | Change |
|---|---|
| `components/dashboard/pipeline/StageConfigDrawer.tsx` | Render fields per matrix; lock locked fields; hide Advanced on intake/debrief |
| `components/dashboard/pipeline/StageConfigurationTab.tsx` | Same |
| `components/dashboard/pipeline/JobPipelineFunnel.tsx` | Header with SourcePill; ActivationGate above funnel; "Generate banks" CTA in toolbar; integrate dry-run before save |
| `app/(dashboard)/jobs/[jobId]/pipeline/page.tsx` | Branch: render PipelineSourcePicker if no instance, else funnel |
| `app/(dashboard)/jobs/[jobId]/questions/page.tsx` | Filter stage pills with `stageSupportsQuestionBank()`; remove raw-text editing; wire Refine/Add dialogs |
| `lib/api/pipelines.ts` | Discriminated-union `PipelineStageInput`; new types for picker, activation, classification |

### 14.3 Removed (or rendered as no-op)

- Any inline raw `<textarea>` for question editing in the questions page (replaced by RefineQuestionDialog).
- Any silent auto-apply behavior on the JD confirm flow.

**Build note (per `frontend/app/AGENTS.md`):** Next.js 16 has breaking changes from training data. Before writing any new route/layout/API handler, consult `node_modules/next/dist/docs/`. The implementation plan calls this out per task.

## 15. Rollback

- **DB:** rollback script reverses migration `0017`:
  - Drop `pipeline_version`, `paused_at`, `pipeline_version_at_generation`, `stage_config_snapshot`, `is_stale` columns.
  - Drop `ix_job_pipeline_stages_paused_at`.
  - Restore `ck_job_postings_status` with the prior value set (no `pipeline_built`, no `archived`).
  - For any rows with `status = 'pipeline_built'`: downgrade to `signals_confirmed`. For any with `status = 'active'`: downgrade to `signals_confirmed`. Lossy on the active period; MVP, no production data, accepted.
- **Code:** standard Git revert. New endpoints are additive; reverting drops them. The `auto_apply_pipeline_on_confirmation` removal is the one *behavior-changing* revert — restoring it puts confirmation back to silent auto-apply.
- **Frontend:** revert removes the new components. The discriminated-union schema change is the only TypeScript-breaking revert — old code expected the unified shape. Easy to revert because we're not removing fields, just narrowing them.

## 16. Tests

### 16.1 Backend (pytest)

| Area | New test file |
|---|---|
| Per-category schema validators | `app/modules/pipelines/tests/test_stage_field_rules.py` — every (stage_type, field) cell of the matrix; reject Forbidden, server-stamp Locked, require Required |
| Activation gate | `app/modules/jd/tests/test_activate.py` — happy path; each predicate failure surfaces structured error; can't activate from non-`pipeline_built`; idempotent on `active` (409 not 422) |
| Picker (POST /pipeline discriminator) | extends `test_pipeline_create.py` (or equivalent) — template/starter/scratch each path; 409 on existing instance; transition to `pipeline_built` |
| Confirm-signals no longer auto-applies | extends `test_jd_confirm.py` — assert no pipeline instance after confirm; `pipeline_built` reached only by explicit picker call |
| Pipeline versioning | `app/modules/pipelines/tests/test_versioning.py` — every PATCH bumps; bank-question CRUD bumps; signal-edit does NOT bump pipeline_version (bumps signal_snapshot.version) |
| Pause / unpause | `app/modules/pipelines/tests/test_pause.py` — pause intake/debrief → 409; pause non-IO → succeeds; unpause; in-flight count guard for delete |
| Edit-category classifier (dry-run) | `app/modules/pipelines/tests/test_classify_diff.py` — every transition type maps to A/B/C/D; warnings surface; in-flight counts accurate |
| Active-job edit guards | `app/modules/jd/tests/test_active_edits.py` — Category D blocked with `stage_type_change_forbidden`; Category C with in-flight=K forces pause-first; Category A bumps version; classifier re-runs on PATCH |
| Refine / Add LLM endpoints | `app/modules/question_bank/tests/test_refine.py`, `test_draft.py` — full context loader called; structured response shape validated; mocked LLM (no real API hits in tests) |
| Persisted stale flag | `app/modules/question_bank/tests/test_stale_persisted.py` — signal edit flips `is_stale`; stage `signal_filter`/`difficulty` edit flips `is_stale`; confirmation drops back to `generated` on stale |

### 16.2 Frontend (vitest)

| Area | New test file |
|---|---|
| Matrix-driven config rendering | `components/dashboard/pipeline/StageConfigDrawer.test.tsx` — for each stage_type, only matrix-✓ fields render; locked fields render disabled with tooltips |
| Per-category schema | `lib/api/pipelines.test.ts` — TypeScript discriminated union compiles correctly; runtime validation rejects mismatched shapes |
| Picker | `components/dashboard/pipeline/PipelineSourcePicker.test.tsx` — recent templates ranked correctly; team default deduped; system starters always shown; click → POST with right body |
| Activation gate | `components/dashboard/pipeline/ActivationGate.test.tsx` — predicate failures rendered; clicking a failure focuses target stage; button enabled only when all required pass |
| Edit warnings | `components/dashboard/pipeline/EditCategoryWarningModal.test.tsx` — Category A inline banner; Category B confirm modal; Category C pause-first dialog; Category D inline error |
| Question dialogs | `components/dashboard/question-bank/RefineQuestionDialog.test.tsx`, `AddQuestionDialog.test.tsx` — instruction → preview → accept; refine-again loop; cancel discards |
| Questions page filtering | `app/(dashboard)/jobs/[jobId]/questions/page.test.tsx` — intake/debrief never appear in pill row; bank-eligible stages always do |

### 16.3 Integration smoke test

One end-to-end happy-path test (backend pytest with httpx + DB fixture):

1. Create JD → extract → confirm signals → assert no pipeline instance exists.
2. POST /pipeline (starter) → assert job in `pipeline_built`.
3. PATCH a stage → assert `pipeline_version` bumps.
4. Generate banks → wait for actor → assert banks `generated`.
5. Refine a question → accept → assert question replaced, `pipeline_version` bumps.
6. POST /activate → assert job in `active`.
7. PATCH a participant on a stage → assert Category A; succeeds.
8. Attempt to change a stage's `stage_type` → assert 409 Category D.

## 17. Rollout

- **One PR, no feature flag.** No production data; this is pre-launch. Splitting a coordinated frontend + backend change behind a flag adds churn for no gain.
- **Branch name:** `feature/pipeline-flow-and-activation` (or current convention).
- **Reviewers:** root CLAUDE.md flags "RLS / migration / auth" as human-review-required areas. This design touches none of those critically — no new tables, no auth changes, no RLS policy changes. Standard human review.
- **Deploy:** Alembic `0017` runs as the pre-deployment migration step (existing process). Defaults handle column backfill; the inline UPDATE handles the `pipeline_built` data migration for existing jobs.
- **Order within the PR** (matches §1 in the implementation plan, to be authored next):
  1. Migration `0017` + rollback + ORM model updates.
  2. Pydantic schemas + per-category validators (with backend tests).
  3. Modified backend endpoints (drop auto-apply, expand source discriminator, classifier, version bumping).
  4. New backend endpoints (activate, dry-run, pause/unpause, bank confirm, refine/draft, question CRUD).
  5. New prompt files + `build_question_context` refactor.
  6. Frontend types + API namespaces + hooks.
  7. Frontend component rewrites — config gating first (visual smoke).
  8. Frontend new components — picker, ActivationGate, SourcePill, dialogs.
  9. Frontend page-level wiring — `/pipeline` branch, `/questions` filter, dry-run integration.

## 18. Risk register

| Risk | Mitigation |
|---|---|
| Existing jobs in `signals_confirmed` with auto-applied pipeline don't migrate to `pipeline_built` | One-time inline UPDATE in migration `0017` (§12.1). |
| Existing jobs in `signals_confirmed` without a pipeline lose silent auto-apply and now show the picker | Acceptable — picker is one click; users have to choose anyway. Documented in PR description. |
| Refine/Add LLM call fails or times out | Frontend hook surfaces the error inline; recruiter retries or cancels. No partial state because Refine/Add are stateless preview. |
| `pipeline_version` overflow (theoretical — int max is 2^31) | Not realistic at MVP scale. |
| Classifier disagrees between dry-run and apply (TOCTOU) | Server re-classifies on PATCH and rejects with structured error if category escalated; frontend re-renders right warning. |
| Stale-bank cascade when recruiter does multi-field stage edit | Acceptable — stale is informational, not blocking. Recruiter regenerates if they care. |
| `take_home` still shows in the type dropdown but is disabled | UI shouldn't let recruiters select it (existing behavior — `<option disabled>`). Predicate excludes it from middle-stage check. |
| Persisting `is_stale` introduces drift if a write path forgets to call `recompute_and_persist_stale` | Centralize the helper; add backend test that asserts every write path that mutates signal/stage-config calls it (audit-style test). |

## 19. Decisions

### 19.1 Explicit picker over silent auto-apply

Silent auto-apply works for repeat power users but confuses first-timers and obscures the choice from auditability. The picker is one click in the happy path and removes the "where did this come from?" question entirely.

### 19.2 Single `pipeline_version` counter (not separate axes for stage / participants / questions)

A single monotonic counter simplifies the activation classifier, audit log, and Phase 3 candidate-journey reconstruction. Edits that don't bump the counter (signal-snapshot writes) live on a different axis already (`signal_snapshot.version`); they don't need to merge here.

### 19.3 Persisted `is_stale` over compute-on-read

The funnel and questions UI both read `is_stale` heavily. Recomputing on every read joins through signal_snapshots and stage configs. Persisting it costs one helper call on infrequent writes (signal save, stage config save, bank generation) and saves on every read. Centralized in `recompute_and_persist_stale`.

### 19.4 LLM-mediated question editing over raw text

Consistent with the JD-signal flow ("AI decides, human verifies"). Preserves bank style, signal alignment, and difficulty calibration. Recruiter doesn't drift one question into a different voice or scope.

### 19.5 Pause-first flow over force-remove for stages with in-flight candidates

The user explicitly added pause to the model so the recruiter can decide candidate-by-candidate what happens to in-flight people, rather than the system auto-advancing or auto-rejecting. Pause + manual drain is more deliberate; the orphan problem doesn't exist because the recruiter handles each candidate explicitly.

### 19.6 No stage-type change on `active` jobs

Stage_type is part of the stage's identity (determines participant roles, prompt template, question-bank eligibility, signal-filter semantics). Changing it on a live job is a cascade of incompatible state. To "change type": delete + add. On `pipeline_built` jobs (no candidates yet), free.

### 19.7 ≥1 middle stage required for activation

A pipeline of `intake → debrief` doesn't earn its existence — it screens no one. Requiring ≥1 middle stage of `phone_screen | ai_screening | human_interview` (excluding disabled `take_home`) keeps the gate honest.

### 19.8 Generation only on explicit user click

Auto-generation on pipeline creation wastes LLM dollars when recruiters swap source. Lazy-on-questions-tab-visit produces a bad first-impression spinner. Explicit C+D (Generate-all CTA + per-stage button) is deliberate and discoverable.

### 19.9 Stateless preview for Refine/Add over persisted proposal table

Simpler to reason about, no extra table, no cleanup of abandoned proposals, no refresh-resilience worry (recruiter can just refine again). Cost: a refresh mid-dialog loses the proposal.

### 19.10 One cohesive spec rather than splitting JD-flow / question-bank / activation

The three are tightly coupled — the activation gate references question-bank state, the picker depends on signal confirmation having happened, the edit-category classifier needs to know about both stage CRUD and question CRUD. Split specs would re-litigate boundaries.

## 20. Out of scope (re-list)

Re-listed from §2 for spec-level discoverability:

- Candidate kanban / candidate management — Phase 3.
- LiveKit session execution and runtime session-start freeze enforcement — Phase 3 (designed-for in §5.5).
- ATS auto-intake — Phase 4.
- Specific interviewer scheduling — Phase 3.
- Take-home stage as a real flow.
- Per-question difficulty / per-question duration.
- Manual raw-text question editing.
- Reports / analytics / scoring dashboards — Phase 3+.
- Pipeline deactivation (`active → pipeline_built` or `active → archived`).
- Multi-pipeline-version A/B testing on a single job.
- Pipeline rollback to a prior `pipeline_version`.
- Cross-stage question dedup at Refine time (designed-for via prompt context, not server-enforced).

## 21. Implementation plan

To be authored next via the writing-plans skill. Filename: `2026-04-26-pipeline-flow-and-activation-plan.md`.
