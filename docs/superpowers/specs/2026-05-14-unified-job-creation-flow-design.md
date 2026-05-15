# Unified job-creation flow — single path, explicit triggers, ATS as a pre-fill

**Date:** 2026-05-14
**Status:** Draft for user review
**Scope:** Backend — `app/modules/jd/`, `app/modules/org_units/`, `app/modules/ats/`, `migrations/`. Frontend — `frontend/app/app/(dashboard)/jobs/`, `frontend/app/lib/api/jobs.ts`.
**Amends:** `2026-05-14-job-scoped-ats-sync-design.md` — Section "ATS `_upsert_job` flow" (status assignment). The ATS spec's "active vs blocked_pending_client_setup" derivation is retired.
**Supersedes:** None outright.

---

## TL;DR

Collapse the two divergent job-creation flows (manual `/jobs/new` wizard auto-running extraction; ATS `_upsert_job` writing parallel states outside the state machine) into **one path**. Every job — manual or ATS — lands in `status='draft'` with whatever pre-fill is available. The recruiter then makes two explicit decisions on `/jobs/{id}`: **(1) "Enrich JD"** rewrites raw → enriched without changing `status`; **(2) "Extract signals"** transitions `draft → signals_extracting` and runs the existing extraction actor. ATS becomes a pure pre-fill source — indistinguishable from a manually-pasted JD from the recruiter's perspective.

`blocked_pending_client_setup` retires entirely. `_unblock_pending_jobs_for_org_unit` and the cascade in `org_units/router.py` are deleted. The `jd.unblocked_by_profile_completion` audit action is dropped. The Phase E unblock-cascade bug we diagnosed (`docs/sessions/...`) becomes unreachable because the code path no longer exists.

**Codebase posture:** pure development stage; no live tenants. One PR per phase, no feature flags, no backward-compatibility shims, no data preservation concerns. The migration migrates the handful of `blocked_pending_client_setup` rows to `draft` and drops the CHECK value.

---

## Motivation

Three load-bearing problems with the current design:

1. **Two divergent flows for the same conceptual action.** Manual `POST /api/jobs` runs `create_job_posting` which validates the company profile, INSERTs in `draft`, immediately transitions to `signals_extracting`, and enqueues `extract_and_enhance_jd`. ATS `_upsert_job` bypasses `create_job_posting` entirely, writes directly to `job_postings` with `status='active'` or `status='blocked_pending_client_setup'`, and never enters the state machine. Two paths, two state shapes, two failure modes. Adding any cross-cutting concern (signal extraction tweaks, audit format, observability) requires touching both — and they drift.

2. **The unblock cascade is structurally broken.** `_unblock_pending_jobs_for_org_unit` (`org_units/service.py:1265-1309`) transitions `blocked_pending_client_setup → draft` and the router (`org_units/router.py:277-285`) enqueues `extract_and_enhance_jd` against jobs in `draft`. The actor's state guard (`jd/actors.py:156`) explicitly refuses anything other than `signals_extracting`, so the actor logs `skip_unexpected_state` and exits. JDs sit in `draft` with `enrichment_status='idle'` forever. The user-visible symptom — "Open roles" badge disappears from the Workato node — is the surface manifestation. The deeper issue is that the cascade was designed under the assumption that "blocked → draft → auto-runs extraction" was a viable handoff, which it isn't given the state machine's current shape.

3. **The 3-tab wizard hides a real recruiter decision.** The current `/jobs/new` Step 2 has a `skip_enrichment` toggle defaulting to "Enrich". For polished JDs that decision matters; for first-drafts of raw JDs the recruiter wants enrichment; for ATS-imported JDs the recruiter never sees the toggle at all because they don't go through the wizard. Surfacing enrichment and extraction as explicit recruiter actions on the JD page (where the JD itself is the central artifact) puts the decision where it belongs.

The structural fix isn't to patch the cascade or fix the toggle — it's to delete both. One flow, one state model, one place where the recruiter decides what runs.

---

## Goals

- **One flow.** Every job created — manual or ATS — lands in `status='draft'`. There is no second insertion path, no parallel state, no derived status logic.
- **Explicit triggers.** Enrichment and signal extraction are recruiter actions on `/jobs/{id}`, not implicit side-effects of creation.
- **ATS as pre-fill, not as a parallel pipeline.** The ATS adapter populates the same columns a manual create would (`title`, `org_unit_id`, `description_raw`, `description_enriched` if vendor-supplied, basics). The recruiter sees an ATS-imported job in `draft` exactly as if they had pasted the JD themselves.
- **Lifecycle `status` vs sub-task `enrichment_status` cleanly separated.** Enrichment is a sub-task that mutates `description_enriched` and toggles `enrichment_status`; it does not change `status`. Only "Extract signals" transitions the lifecycle.
- **Retire the unblock cascade and `blocked_pending_client_setup`.** They were workarounds for the create-time profile gate. With the gate moved to the explicit trigger endpoints, both become unreachable and are deleted.
- **Auditability preserved.** Every transition still writes an audit row via the existing `transition()` helper. Enrichment runs still write the existing `jd.llm_call.*` events. The only audit action removed is `jd.unblocked_by_profile_completion` (no producers after this change).

## Non-goals

- **A new "enriched-only" lifecycle state.** Enrichment continues to be modeled via `enrichment_status` only; `status` stays `draft` through enrichment. Adding a state like `enriched_pending` would be a more invasive state-machine change for marginal gain.
- **Per-field PATCH sentinel discipline on `/api/jobs/{id}`.** The org_units module uses per-field `set_<name>` sentinels for partial updates; for the draft-edit case we use simpler `null = don't change` PATCH semantics. The fields involved are simple text + scalars with no "intentionally clear to NULL" requirement on draft jobs.
- **Editing a job after `signals_extracted`.** PATCH is gated on `status='draft'` only. Editing basics or raw JD after extraction would invalidate the signals snapshot — out of scope.
- **Frontend `pressureForOpenRoles` / canvas `draft` exclusion fixes.** Both are real bugs (`OrgUnitNode.tsx:213`, `page.tsx:120`) but orthogonal to this refactor. Filed as a separate follow-up.
- **`raw_manually_edited` flag to protect recruiter-edited raw JD from ATS overwrite.** Today `description_enriched` is protected via `enriched_manually_edited`; the equivalent for `description_raw` is a meaningful follow-up but not blocking this refactor (recruiters editing raw JD on an active ATS sync is an edge case).
- **Two-way ATS sync.** Already out of scope per the existing ATS spec; carried forward.

---

## Background — what we have today

### Manual `/jobs/new` flow

`frontend/app/app/(dashboard)/jobs/new/page.tsx` is a 3-tab wizard:

| Tab | Fields | Validation |
|---|---|---|
| 1. Basics | `title`, `org_unit_id`, employment/arrangement, location, salary, headcount, travel, start | `title` non-empty, `org_unit_id` UUID |
| 2. JD | `description_raw`, `project_scope_raw`, `skip_enrichment` | `description_raw` min 50 chars |
| 3. Review | summary panel | "Publish role" submits |

Submit → `POST /api/jobs` with the full body. Backend handler (`jd/router.py:284-357`):
1. RBAC check (ancestry walk for `jobs.create`).
2. `create_job_posting()` (`jd/service.py:165-242`):
   - `find_company_profile_in_ancestry(db, org_unit_id)` — raises `CompanyProfileIncompleteError` if no ancestor has a complete profile.
   - INSERT row with `status='draft'`, `source='native'`.
   - `transition(job, to_state='signals_extracting', ...)` — uses `LEGAL_TRANSITIONS["draft"] → {"signals_extracting"}`.
3. `BackgroundTasks.add_task(_safe_dispatch_extraction, ..., skip_enrichment=body.skip_enrichment)` — fires after the dependency's auto-commit, so the actor sees the committed row.
4. `BackgroundTasks` also publishes the initial `jd.status_changed` pubsub event.
5. Return `JobPostingWithSnapshot` (snapshot is None at this point — actor hasn't run yet).

### ATS `_upsert_job` flow

`app/modules/ats/orchestrator.py:909-1050`. For each job pulled from Ceipal:

1. Look up existing row by `(tenant_id, source=vendor, external_id)`.
2. **Derive status** (lines 945-952):
   ```python
   derived_status = (
       "active"
       if (org_unit is not None
           and org_unit.company_profile_completion_status == "complete")
       else "blocked_pending_client_setup"
   )
   ```
3. If new: INSERT with `status=derived_status`, `source=vendor`, `description_raw`, `description_enriched` (if vendor-supplied), `title`, `external_id`, `external_status`, etc. Does NOT go through `create_job_posting`. Does NOT enter the state machine.
4. If existing: diff fields, update changed ones. Status toggle between `active` ↔ `blocked_pending_client_setup` based on profile completion. Never touches `archived` or any "in-pipeline" state.

### The unblock cascade (broken path)

`org_units/router.py:241-285` — when `PUT /api/org-units/{id}` flips `company_profile_completion_status` from `pending` → `complete`:

1. `_unblock_pending_jobs_for_org_unit(db, org_unit_id, tenant_id)` (`org_units/service.py:1265-1309`):
   - SELECT all jobs in this org_unit with `status='blocked_pending_client_setup'`.
   - Direct write: `job.status = 'draft'` (no `transition()` call — bypasses state machine).
   - Write `jd.unblocked_by_profile_completion` audit row per job.
2. For each unblocked id: `extract_and_enhance_jd.send(jid, tenant_id, "unblock-<uuid>")`.

The actor receives the message, opens its own session, loads the job. `job.status == 'draft'`. The guard at `actors.py:156` (`if job.status != "signals_extracting": skip_unexpected_state; return`) fires. The actor exits cleanly with no error and no work done. Audit shows the unblock fired; logs show `skip_unexpected_state`. JDs sit at `draft`, `enrichment_status='idle'`, forever.

### State machine

`app/modules/jd/state_machine.py:24-33`:

```python
LEGAL_TRANSITIONS = {
    "draft":                       {"signals_extracting"},
    "signals_extracting":          {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed":   {"signals_extracting"},  # retry
    "signals_extracted":           {"signals_confirmed"},
    "signals_confirmed":           {"signals_extracted", "pipeline_built"},
    "pipeline_built":              {"active"},
    "active":                      set(),
    "archived":                    set(),
}
```

`blocked_pending_client_setup` is **not** in this map. The cascade's direct write bypasses the state machine entirely. Audit only captures the unblock, not the entry into `blocked_pending_client_setup` (which happens via raw INSERT in `_upsert_job`).

### Affected surface (from the deep audit)

Backend code (~12 files), frontend (~5 files), migrations (1 new), tests (~25 files), specs (1 amendment). Full per-file list in §Detailed Change List below.

---

## Design

### Status model — the lifecycle / sub-task split

The corrected mental model:

```
status            : draft ──────────────────────────────────► signals_extracting ──► signals_extracted ──► signals_confirmed ──► pipeline_built ──► active
                    │                                                  ▲
                    │ "Enrich JD" (sub-task, status unchanged)          │ "Extract signals" (lifecycle transition)
                    │                                                  │
enrichment_status : idle ──► streaming ──► completed/failed ──┘
                    │                                                  │
                    │ (recruiter can re-run enrichment 0..N times       │
                    │  while job is in draft; each run overwrites       │
                    │  description_enriched)                            │
```

Key invariants:

1. **`status` reflects lifecycle phase.** `draft` means "recruiter is preparing the JD." It says nothing about whether enrichment has happened.
2. **`enrichment_status` reflects the sub-task.** It's about whether `description_enriched` is a) absent (`idle`), b) being produced now (`streaming`), c) ready (`completed`), or d) failed last try (`failed`). It is decoupled from `status` — the only constraint is that `enrichment_status='streaming'` requires a worker actively running.
3. **The only transition into `draft` from outside is "row freshly inserted."** No other state goes back to `draft`. This matches the current state machine and is preserved.
4. **Extracting signals is the lifecycle gate.** Clicking "Extract signals" is the recruiter saying "I'm done preparing; turn this into a structured job posting." That action is what transitions `draft → signals_extracting`.

This matches the existing `reenrich_jd` actor's behavior: it touches `enrichment_status` and `description_enriched`, never `status`. We're just generalizing that pattern to the initial enrichment too.

### Endpoint surface

| Endpoint | Method | Today's behavior | After Phase A |
|---|---|---|---|
| `/api/jobs` | POST | INSERT in `draft` → transition to `signals_extracting` → auto-dispatch actor | INSERT in `draft`. **No transition. No actor dispatch.** No profile check. `description_raw` optional (empty string allowed). `skip_enrichment` removed from body. |
| `/api/jobs/{id}` | GET | Unchanged | Unchanged |
| `/api/jobs/{id}` | PATCH | **(does not exist)** | **New.** Updates `description_raw`, `project_scope_raw`, and basics (`title`, `employment_type`, `work_arrangement`, `location`, `salary_range_*`, `salary_currency`, `travel_required`, `start_date_pref`, `target_headcount`, `deadline`). Gated on `status='draft'`. Null-means-don't-change PATCH semantics. RBAC: `require_job_access(db, job_id, user, "manage")`. |
| `/api/jobs/{id}/enrich` | POST | Re-enrichment. Gated on `status ∈ {signals_extracted, signals_confirmed}`. Dispatches `reenrich_jd`. | **Gate extended** to also allow `status='draft'`. **New guards** at endpoint: refuse 422 if `description_raw` is empty (`EmptyRawJDError`); refuse 422 if no profile in ancestry (`CompanyProfileIncompleteError`). Dispatches the same `reenrich_jd` actor — it already only mutates `enrichment_status` + `description_enriched`. |
| `/api/jobs/{id}/extract-signals` | POST | **(does not exist)** | **New.** Gated on `status='draft'`. Same 422 guards as `/enrich` (empty raw JD; missing profile). `transition(job, to_state='signals_extracting', ...)`. Dispatches `extract_and_enhance_jd` with `skip_enrichment=True` (since enrichment is a separate decision now — if the recruiter wants to enrich, they click that button first). |
| `/api/jobs/{id}/retry` | POST | Gated on `status='signals_extraction_failed'`. | Unchanged. |
| `/api/jobs/{id}/signals` | PATCH | Unchanged | Unchanged |
| `/api/jobs/{id}/signals/confirm` | POST | Unchanged | Unchanged |
| `/api/jobs/{id}/activate` | POST | Unchanged | Unchanged |
| `/api/jobs/{id}` | DELETE | Unchanged | Unchanged |
| `/api/jobs/{id}/status/stream` | GET (SSE) | Unchanged | Unchanged. SSE consumers (`use-job-status-stream`) keep working — the events they listen for are still published whenever `status` or `enrichment_status` changes. |

### Errors

Two error types involved, one new:

| Error | HTTP | When raised | Where |
|---|---|---|---|
| `CompanyProfileIncompleteError` | 422 | Profile ancestry walk returns None | `/enrich`, `/extract-signals` endpoint guards. Removed from `create_job_posting`. |
| `EmptyRawJDError` (**new**) | 422 | Raw JD is empty/whitespace-only when enrich or extract is invoked | `/enrich`, `/extract-signals` endpoint guards. Defensive guard also added in the actors. |

`EmptyRawJDError` payload: `{"code": "empty_raw_jd", "message": "Add the job description before enriching or extracting signals."}`. Defined in `app/modules/jd/errors.py` alongside `CompanyProfileIncompleteError`.

Both errors registered in `app/main.py` exception handlers (the existing one already handles `CompanyProfileIncompleteError`).

### ATS unification

`app/modules/ats/orchestrator.py::_upsert_job` simplifies:

**Create path:**
```python
job = JobPosting(
    tenant_id=self.tenant_id,
    org_unit_id=org_unit.id if org_unit is not None else None,
    title=payload.title or "(untitled)",
    description_raw=payload.description_raw or "",
    description_enriched=payload.description_enriched,
    status="draft",                  # ← always draft, no derivation
    source=self.adapter.vendor,
    external_id=payload.external_id,
    external_status=payload.external_status,
    external_last_modified_at=payload.external_modified_at,
    deadline=payload.deadline,
    location=_compose_location(payload),
    created_by=created_by_user_id,
)
```

**Update path:** the `derived_status` recompute and the `active ↔ blocked_pending_client_setup` toggle (lines 1026-1050) are deleted. ATS never changes a job's `status` post-create — it only updates content fields (`title`, `description_raw`, `description_enriched`, `external_status`, `deadline`, `location`, `org_unit_id` backfill). If the recruiter has advanced the job (`signals_extracting` and beyond), the ATS sync still updates content fields but leaves `status` alone — same as today.

The existing `enriched_manually_edited` guard (line 997) is preserved: ATS doesn't overwrite a recruiter-edited enriched copy. Symmetrical protection for `description_raw` is **out of scope** (filed as follow-up).

### Deletions

| What | Where | Why |
|---|---|---|
| `blocked_pending_client_setup` literal | `jd/schemas.py:35`, `frontend/app/lib/api/jobs.ts:67`, `jd/models.py:23` (comment), `org_units/service.py:1291`, `ats/orchestrator.py:951`, `ats/orchestrator.py:1030-1050`, `pipelines/service.py:1068` (comment), `org_units/router.py:244` (comment), migration `0033_job_postings_org_unit_nullable.py:9` (docstring), `tests/modules/org_units/test_unblock_pending_jobs.py` (entire file) | No producers after `_upsert_job` rewrite. |
| `_unblock_pending_jobs_for_org_unit` | `org_units/service.py:1265-1309` (entire helper) | No callers after cascade deletion. |
| Cascade block in `org_units/router.py` | Lines 241-287 (the `if prev_completion_status == "pending" and ... == "complete"` branch and its `extract_and_enhance_jd.send` loop) | Replaces with a no-op — completing the profile no longer triggers any side-effect on jobs. |
| `jd.unblocked_by_profile_completion` audit action | `app/modules/audit/actions.py` (if defined as a constant), `org_units/service.py:1303` (string literal) | No producers. |
| `tests/modules/org_units/test_unblock_pending_jobs.py` | Entire file | Tests the deleted helper. |
| `skip_enrichment` field | `JobPostingCreate` (`jd/schemas.py`), `frontend/app/lib/api/jobs.ts`, wizard form schema | The decision now lives in the recruiter's button choice on `/jobs/{id}`. |
| Wizard tabs 2 + 3 | `frontend/app/app/(dashboard)/jobs/new/page.tsx` | Replaced by single-tab basics form. |

### Migrations

**New: `migrations/versions/0036_drop_blocked_pending_client_setup.py`**

```python
"""Drop blocked_pending_client_setup status

Migrates any existing rows to 'draft'. Drops the value from job_postings.status CHECK constraint.
The unblock cascade and ats orchestrator no longer produce this status — see
docs/superpowers/specs/2026-05-14-unified-job-creation-flow-design.md.
"""

revision = "0036_drop_blocked_pending_client_setup"
down_revision = "<head>"  # whatever the current head is at implementation time

def upgrade():
    # Migrate rows
    op.execute("""
        UPDATE job_postings
        SET status = 'draft'
        WHERE status = 'blocked_pending_client_setup'
    """)
    # Replace the CHECK constraint
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS job_postings_status_check")
    op.execute("""
        ALTER TABLE job_postings ADD CONSTRAINT job_postings_status_check
        CHECK (status IN (
            'draft',
            'signals_extracting',
            'signals_extraction_failed',
            'signals_extracted',
            'signals_confirmed',
            'pipeline_built',
            'active',
            'archived'
        ))
    """)

def downgrade():
    op.execute("ALTER TABLE job_postings DROP CONSTRAINT IF EXISTS job_postings_status_check")
    op.execute("""
        ALTER TABLE job_postings ADD CONSTRAINT job_postings_status_check
        CHECK (status IN (
            'draft', 'signals_extracting', 'signals_extraction_failed',
            'signals_extracted', 'signals_confirmed',
            'pipeline_built', 'active', 'archived',
            'blocked_pending_client_setup'
        ))
    """)
    # Note: rows that were migrated cannot be reversed (we don't know which 'draft'
    # rows used to be blocked). Acceptable in pre-production.
```

`description_raw` stays `NOT NULL` at the ORM/DB level. Empty string `""` is permitted (Postgres treats it as a valid Text value). No DDL change to the column itself.

---

## Detailed Change List

### Backend

#### `app/modules/jd/schemas.py`
- `JobStatus` literal: remove `"blocked_pending_client_setup"` (line 35).
- `JobPostingCreate` Pydantic model: drop `skip_enrichment` field; relax `description_raw` to `Field(default="", max_length=50_000)` (no min).
- Add `JobPostingUpdate` Pydantic model for the new PATCH endpoint — all fields optional, none defaultable (caller sends only what changes).

#### `app/modules/jd/service.py::create_job_posting`
Before (signature):
```python
async def create_job_posting(
    db, *, tenant_id, created_by, org_unit_id, title,
    description_raw, project_scope_raw, ..., correlation_id,
) -> JobPosting:
    profile = await find_company_profile_in_ancestry(db, org_unit_id)
    if profile is None:
        raise CompanyProfileIncompleteError(org_unit_id)
    job = JobPosting(..., status="draft", source="native", ...)
    db.add(job); await db.flush()
    await transition(db, job, to_state="signals_extracting", actor_id=created_by, correlation_id=correlation_id)
    await db.flush()
    return job
```

After:
```python
async def create_job_posting(
    db, *, tenant_id, created_by, org_unit_id, title,
    description_raw="", project_scope_raw=None, ..., correlation_id,
) -> JobPosting:
    job = JobPosting(..., status="draft", source="native", ...)
    db.add(job); await db.flush()
    return job
```

The `correlation_id` arg is preserved (still used for the audit row written by INSERT-side logging if any; consistent with the rest of the module).

#### `app/modules/jd/service.py` (new function): `update_job_posting_draft`
```python
async def update_job_posting_draft(
    db, *, job: JobPosting, updates: dict, actor_id, actor_email, ip_address,
) -> JobPosting:
    """Update editable fields on a draft job. Raises IllegalTransitionError-equivalent
    if job.status != 'draft'."""
    if job.status != "draft":
        raise JobNotEditableError(job.status)
    for field in EDITABLE_DRAFT_FIELDS:
        if field in updates:
            setattr(job, field, updates[field])
    # Audit row via existing log_event with action='job_posting.updated'
    ...
    return job
```

`EDITABLE_DRAFT_FIELDS` is a tuple of column names defined at module scope. Add `JobNotEditableError` to `jd/errors.py` (409 Conflict).

#### `app/modules/jd/router.py`

`POST /api/jobs` (line 284-357):
- Drop the `BackgroundTasks.add_task(_safe_dispatch_extraction, ...)` block (lines 322-334).
- Drop the initial pubsub publish (lines 336-349) — without an actor run, status doesn't change, no event needed.
- Drop the `body.skip_enrichment` reference.

`POST /api/jobs/{id}/enrich` (line 552-590):
- Relax the gate: `if job.status not in ("draft", "signals_extracted", "signals_confirmed"): raise 409`.
- Add `if not (job.description_raw or "").strip(): raise EmptyRawJDError(...)`.
- Add `profile = await find_company_profile_in_ancestry(db, job.org_unit_id); if profile is None: raise CompanyProfileIncompleteError(job.org_unit_id)`.

`POST /api/jobs/{id}/extract-signals` (new, ~30 lines):
- Gate on `job.status == "draft"`.
- Same two 422 guards (empty raw JD, missing profile).
- `await transition(db, job, to_state="signals_extracting", actor_id=user.user.id, correlation_id=correlation_id)`.
- `background_tasks.add_task(_safe_dispatch_extraction, ..., skip_enrichment=True)`.
- Publish initial `jd.status_changed` event.
- Return 202.

`PATCH /api/jobs/{id}` (new, ~30 lines):
- `job = await require_job_access(db, job_id, user, "manage")`.
- Body is `JobPostingUpdate`.
- `await update_job_posting_draft(...)`.
- Return updated `JobPostingWithSnapshot`.

#### `app/modules/jd/actors.py`

`_run_enrichment` (line 125-250):
- Today's guard `if job.status != "signals_extracting": skip_unexpected_state; return` is replaced with `if not (job.description_raw or "").strip(): log + set enrichment_status='failed'; return`. The state guard is unnecessary now — the endpoint already validated the state and either we're being called by the `reenrich_jd` path (which has its own gate) or by `extract_and_enhance_jd` with `skip_enrichment=True` (which doesn't run this function).

Wait — `_run_enrichment` is called from `extract_and_enhance_jd` (line 501). After this refactor, that call path is only reached when `skip_enrichment=False`, which from the new endpoints is **never** (the `extract-signals` endpoint always passes `skip_enrichment=True`). So `_run_enrichment` is now only reachable via `reenrich_jd` actor, which gates on `status ∈ {draft, signals_extracted, signals_confirmed}` — the new draft gate.

Action: keep `_run_enrichment` as a building block but stop calling it from `extract_and_enhance_jd`. Or: leave `extract_and_enhance_jd` as-is and never call it with `skip_enrichment=False` (cleaner; actor stays general). The `extract-signals` endpoint always passes `skip_enrichment=True`.

`_run_signal_extraction` (line ~260-420):
- Guard at line 284 (`if job.status != "signals_extracting": skip`) is still valid — the new `extract-signals` endpoint transitions to `signals_extracting` before dispatching, so the guard sees the expected state.

`reenrich_jd` actor (line ~580+):
- Status guard updates to allow `draft` as a starting state.
- No other change.

#### `app/modules/jd/errors.py`
- Add `EmptyRawJDError(JDError)` — 422.
- Add `JobNotEditableError(JDError)` — 409 (raised by `update_job_posting_draft`).

#### `app/modules/ats/orchestrator.py::_upsert_job` (line 909-1050)
- Replace the `derived_status` block (lines 938-952) with a constant `status="draft"`.
- Remove the update-path `active ↔ blocked_pending_client_setup` toggle block (lines 1026-1050).
- Keep everything else (title, description_raw, description_enriched, external_*, location, deadline, org_unit_id backfill, enriched_manually_edited protection).

#### `app/modules/org_units/router.py` (line 241-287)
- Delete the whole "unblock cascade" block: `prev_completion_status` capture (line 212), the `if pending → complete` branch (lines 247-255), and the `extract_and_enhance_jd.send` loop (lines 277-285). The `update_org_unit` call itself stays.

#### `app/modules/org_units/service.py`
- Delete `_unblock_pending_jobs_for_org_unit` (lines 1265-1309).
- Confirm no other callers (audit closed this — only the router used it).

#### `app/modules/audit/actions.py`
- Remove `JD_UNBLOCKED_BY_PROFILE_COMPLETION` constant if defined. Search for the string `jd.unblocked_by_profile_completion` and remove the constant definition.

### Frontend

#### `frontend/app/lib/api/jobs.ts`
- `JobStatus` union: remove `"blocked_pending_client_setup"`.
- `CreateJobBody`: drop `description_raw` requirement (make optional), drop `project_scope_raw` requirement (already optional), drop `skip_enrichment`.
- New `UpdateJobBody` type matching the PATCH endpoint body.
- New `jobsApi.update(token, id, body): Promise<JobPostingWithSnapshot>` — calls `PATCH /api/jobs/{id}`.
- New `jobsApi.extractSignals(token, id): Promise<{status: string}>` — calls `POST /api/jobs/{id}/extract-signals`.
- Existing `jobsApi.triggerEnrich` stays; gate-extension is server-side only.

#### `frontend/app/app/(dashboard)/jobs/new/page.tsx`
- Strip the wizard wrapper (`WizardProgress`, step state, `goNext`/`goBack`).
- Single form: basics only (title, org_unit_id, employment, arrangement, location, salary, headcount, travel, start).
- Submit → `jobsApi.create()` with the smaller body → `router.push(`/jobs/${job.id}`)`.
- Toast: "Role created — open and add the JD to continue."

#### `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`
- State-aware render based on `job.status`:
  - `draft` → new `<JobDraftEditor>` component: read-only basics (or inline-edit if scope-creep tolerable), large textarea for `description_raw`, smaller for `project_scope_raw`, two buttons ("Enrich JD", "Extract signals"), inline CTA when profile incomplete or raw JD empty.
  - `signals_extracting` / `signals_extraction_failed` → existing `<JDExtractingView>` / `<JDExtractionFailed>` unchanged.
  - `signals_extracted` and beyond → existing three-panel review unchanged.
- ATS-imported jobs hit the same `<JobDraftEditor>` with `description_raw` and `description_enriched` pre-filled. No special branch.

#### `frontend/app/components/dashboard/jd-panels/JobDraftEditor.tsx` (new)
- React Hook Form with zod validation matching the PATCH body shape.
- Save-on-blur PATCH via `useUpdateJobDraft` (new hook in `lib/hooks/`).
- Two button row at the bottom: "Enrich JD" (calls `useTriggerEnrich`, exists), "Extract signals" (calls new `useExtractSignals`).
- Inline error state if endpoint returns 422 (empty raw JD / incomplete profile) — surface as a banner above the buttons with a "Go to org unit settings" link for the profile case.
- Composition test under `frontend/app/tests/components/`: parent + child rendered together, mock at API boundary, negative-control verification.

### Tests

Backend tests to update:

| File | Action |
|---|---|
| `tests/test_jd_service_create.py` | Update happy-path test (no longer transitions to `signals_extracting`). Delete profile-gate failure test (or move to test_jd_router for the new endpoints). |
| `tests/test_jd_events.py` | Drop tests asserting `POST /api/jobs` publishes `signals_extracting`. Add tests for `/extract-signals` event sequence. Re-enrich event tests unchanged. |
| `tests/test_jd_router.py` | Rewrite POST /api/jobs tests to assert `draft` + no extraction. Add tests for new PATCH and `/extract-signals` endpoints. Update `/enrich` tests for the new gate (draft allowed) and the two new 422s. |
| `tests/test_jd_actor.py` | Adjust `extract_and_enhance_jd` tests to use `skip_enrichment=True` for the typical case (called via `/extract-signals`). `reenrich_jd` tests: add coverage for the new `draft` starting state. |
| `tests/modules/org_units/test_unblock_pending_jobs.py` | **Delete.** |
| `tests/modules/org_units/test_update_org_unit.py` (or equivalent) | Add an assertion that completing the profile no longer dispatches anything. |
| `tests/modules/ats/test_orchestrator_upsert_job.py` (or wherever `_upsert_job` is tested) | Update to assert `status='draft'` always; drop `blocked_pending_client_setup` fixtures. |
| `tests/test_jd_state_machine.py` | No code changes, but verify no test references `blocked_pending_client_setup`. |
| `tests/conftest.py` | Update job fixtures to either land in `draft` (default) or whatever the test scenario needs. Drop any fixture with `status='blocked_pending_client_setup'`. |

Frontend tests:

| File | Action |
|---|---|
| `frontend/app/tests/...` for `/jobs/new` | Rewrite for single-tab flow. |
| `frontend/app/tests/components/JobDraftEditor.test.tsx` (new) | Composition test per project convention. |

### Specs to amend

- `docs/superpowers/specs/2026-05-14-job-scoped-ats-sync-design.md` — add a "Superseded by" note in the section about `_upsert_job` status assignment pointing to this spec. The rest of that spec (single-trigger sync, unified storage, plugin contract) is unchanged.

---

## Phasing

Per `feedback_phasing` memory: each phase must be end-to-end testable, no manual fakes for later-phase features.

### Phase A — Backend (one PR)

**Scope:** every backend file in §Detailed Change List + the new migration + the test updates listed above.

**End-to-end check:** purely API-driven (no frontend changes yet — frontend will momentarily be broken for the 3-tab wizard's submit path; that's acceptable for a one-PR cutover since we're solo dev).

1. Run migration: `docker compose run nexus alembic upgrade head`. Verify no `blocked_pending_client_setup` rows remain.
2. `POST /api/jobs` with body `{"org_unit_id": "...", "title": "Test", "description_raw": ""}` → returns `JobPostingWithSnapshot` with `status='draft'`, `enrichment_status='idle'`. Confirm no Dramatiq actor fires.
3. `PATCH /api/jobs/{id}` with `{"description_raw": "...realistic JD..."}` → 200, row updated.
4. `POST /api/jobs/{id}/enrich` → 202; SSE shows `enrichment_status` cycling `idle → streaming → completed`; `description_enriched` populated. `status` unchanged at `draft`.
5. `POST /api/jobs/{id}/extract-signals` → 202; SSE shows `status: draft → signals_extracting → signals_extracted`. Snapshot persisted.
6. Trigger ATS sync against the running stack: verify imported jobs land in `status='draft'`, none in `blocked_pending_client_setup`. Verify the test Workato 3 jobs migrate to `draft` (or stay there if already migrated).
7. Run full backend test suite: `docker compose run nexus pytest` — green.

Phase A's PR is mergeable on its own. After it merges, the frontend's "Publish role" button will POST a body that's still valid (the new schema accepts the old fields and ignores them) — but the auto-extraction it expects won't happen. That's the cutover window between A and B. Solo-dev OK; staged team-of-N would gate behind a flag.

### Phase B — Frontend (one PR)

**Scope:** every frontend file in §Detailed Change List + tests.

**End-to-end check:**
1. `npm run dev` in `frontend/app/`.
2. Click "+ New role". Confirm single-tab form (no Step 2/3).
3. Fill basics, click "Create role". Redirected to `/jobs/{id}`.
4. Confirm the draft editor renders. Paste raw JD. Save (PATCH).
5. Click "Enrich JD". Watch `description_enriched` populate via SSE; `status` stays draft.
6. Click "Extract signals". Watch transition; review panel appears once signals_extracted.
7. From settings → org-units, manually create an ATS-imported-like job (or run a real ATS sync). Open it from `/jobs`. Confirm same draft editor renders with raw JD pre-filled.
8. Run frontend tests: `npm run test` — green.
9. Run lint + type-check: `npm run lint && npm run type-check` — green.

---

## Test plan

| Layer | Files | Coverage gate |
|---|---|---|
| Schema | Pydantic `JobPostingCreate`, `JobPostingUpdate`, `JobStatus` literal | Drop `blocked_pending_client_setup` value; PATCH accepts each editable field nullable; literal-type test asserts the 8 remaining states |
| State machine | `tests/test_jd_state_machine.py` | LEGAL_TRANSITIONS unchanged; just verify `blocked_pending_client_setup` not present |
| Service | `tests/test_jd_service_create.py` | `create_job_posting` returns a `draft` job without raising on missing profile; doesn't call `transition`; doesn't enqueue |
| Service (new) | new `tests/test_jd_service_update_draft.py` | PATCH on `draft` updates fields; PATCH on `signals_extracting+` raises `JobNotEditableError` |
| Router | `tests/test_jd_router.py` | POST returns `draft` + no actor side-effect; PATCH 200 / 409; `/enrich` allows `draft` and 422s on empty raw JD or missing profile; `/extract-signals` transitions and dispatches with `skip_enrichment=True`; both endpoints' RBAC matches existing |
| Actor | `tests/test_jd_actor.py` | `extract_and_enhance_jd(skip_enrichment=True)` path exercised; `reenrich_jd` accepts `draft` |
| ATS orchestrator | `tests/modules/ats/test_orchestrator*` | `_upsert_job` always writes `status='draft'`; the update path no longer toggles status |
| Org units router | `tests/modules/org_units/test_update_org_unit*` | Completing a profile no longer dispatches Dramatiq actors |
| Org units service | (`test_unblock_pending_jobs.py` deleted) | — |
| Events | `tests/test_jd_events.py` | `POST /api/jobs` does NOT publish a status event (because nothing changes); `/extract-signals` publishes the transition; `/enrich` publishes `enrichment_status` changes; existing re-enrich event tests pass |
| Frontend | new `JobDraftEditor.test.tsx` + updated wizard tests | Composition: parent + child with API mocked at boundary; verify negative control by reintroducing the empty-raw-JD bug → 422 surfaces correctly |

Project-wide gate from root `CLAUDE.md` (80% line, 100% branch on auth/RLS/candidate-session): unaffected by this spec since we don't touch those modules.

---

## Open design decisions — resolved

Recorded for traceability of the spec review:

| Question | Decision | Rationale |
|---|---|---|
| Two buttons (Enrich + Extract) or one combined? | **Two.** | 1:1 with the existing Phase 1 / Phase 2 split in `extract_and_enhance_jd`. Lets the recruiter run signal extraction on an already-polished JD without rewriting it (real use case for ATS-imported polished copy). |
| Keep ATS-supplied `description_enriched` on import? | **Keep.** | Mirrors a "pre-filled form" rather than forcing a re-enrichment per imported job. Recruiter can still click "Enrich JD" to overwrite with our prompt's version. |
| Profile-completion check at create vs at enrich/extract? | **At enrich/extract.** | Lets `draft` jobs exist on incomplete profiles. The gate fires only when the recruiter tries an action that actually needs the profile (the prompts read it via ancestry walk). This is what makes the cascade obsolete. |
| Existing `blocked_pending_client_setup` rows? | **Migrate to `draft` in the same migration that drops the value.** | Pre-production, no rollback concern. Cleanest schema posture. |
| Endpoint naming for the new explicit-trigger pair? | `/enrich` (extended gate) + new `/extract-signals`. | Reuses existing endpoint where semantics match (re-enrichment was already enrichment-only); names the new endpoint after the action it performs. |
| Audit action `jd.unblocked_by_profile_completion`? | **Delete.** | Pre-development; no historical query value. |
| `pressureForOpenRoles(0) === null` badge gating and canvas's `draft` exclusion? | **Out of scope; file as follow-up.** | Both bugs reveal themselves only because the cascade left jobs stuck at `draft`. Once Phase A ships, jobs progress normally and these surface less. Worth fixing but not on the critical path. |

---

## Implementation order within Phase A

For my own discipline during execution — not a contract:

1. Migration first (`0036_drop_blocked_pending_client_setup.py`). Schema and the literal change in `jd/schemas.py`. Run migration locally; verify table state.
2. Delete the cascade + helper + audit action + dead test file. No new code yet; clean cut.
3. Update `create_job_posting` and the `POST /api/jobs` handler. Drop `skip_enrichment` from `JobPostingCreate`.
4. Add `EmptyRawJDError`, `JobNotEditableError` to `jd/errors.py`. Register handlers in `app/main.py`.
5. Add `update_job_posting_draft` service function and `PATCH /api/jobs/{id}` route.
6. Extend `POST /api/jobs/{id}/enrich` gate + 422 guards. Update `reenrich_jd` actor's status gate.
7. Add `POST /api/jobs/{id}/extract-signals` route.
8. Simplify `_upsert_job` (drop derived_status, drop the active/blocked toggle).
9. Update tests file-by-file. Delete `test_unblock_pending_jobs.py`. Run pytest. Iterate until green.
10. Smoke E2E from §Phase A end-to-end check.
