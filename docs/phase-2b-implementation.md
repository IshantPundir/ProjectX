# Phase 2B Implementation — Developer Documentation

**Scope:** Signal editing with snapshot versioning, FOR UPDATE row-lock on save, confirmation workflow, Call 2 re-enrichment, company profile ancestry walk, signal schema v2 + job metadata
**Status:** Complete and functional
**Last updated:** 2026-04-15

See also:
- Design spec: `docs/superpowers/specs/2026-04-10-phase-2b-signal-editing-design.md`
- Signal schema v2 design: `docs/superpowers/specs/2026-04-11-signal-schema-v2-job-metadata-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-10-phase-2b-signal-editing.md`
- Schema v2 plan: `docs/superpowers/plans/2026-04-11-signal-schema-v2-job-metadata.md`
- Phase 2A walkthrough: `docs/phase-2a-implementation.md`

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema (migrations 0001–0003)](#2-database-schema-migrations-00010003)
3. [Signal Editing Flow](#3-signal-editing-flow)
4. [Confirmation Flow](#4-confirmation-flow)
5. [Call 2 Re-enrichment](#5-call-2-re-enrichment)
6. [Company Profile Ancestry Walk](#6-company-profile-ancestry-walk)
7. [API Reference](#7-api-reference)
8. [Frontend Architecture](#8-frontend-architecture)
9. [Known Gaps](#9-known-gaps)

---

## 1. Architecture Overview

Phase 2A shipped a read-only pipeline: upload → Call 1 extracts signals → display. Phase 2B turns that review surface into an interactive editing experience and adds the recruiter-driven actions the question-bank pipeline needs downstream:

1. **Signal chip editing** — recruiters with `jobs.manage` toggle the Signals panel into edit mode, add/remove/retype chips, and save. Each save writes a new immutable snapshot row; old versions are preserved.
2. **Version ordering under concurrency** — `save_signals` takes a `SELECT … FOR UPDATE` row lock on the parent `job_postings` row, then computes `MAX(version)` and inserts `version + 1`. The lock serialises concurrent saves through the same parent job so the unique constraint `(job_posting_id, version)` never trips.
3. **Confirmation workflow** — a recruiter-only `Confirm Signals` button transitions the job from `signals_extracted → signals_confirmed`, stamps `confirmed_by`/`confirmed_at` on the latest snapshot, and auto-applies a starter pipeline via the Phase 2C.1 `auto_apply_pipeline_on_confirmation` helper. Re-confirming an already-piped job is idempotent — see commit `a7ba2ea`.
4. **Call 2 re-enrichment** — an explicit `Re-enrich JD` button kicks off the `reenrich_jd` Dramatiq actor, which re-runs the enriched-prose generation using the edited snapshot as input. Enrichment lifecycle tracks on a side field (`enrichment_status`) so it doesn't pollute the main state machine.
5. **Company profile ancestry as a gate** — `find_company_profile_in_ancestry` (introduced in Phase 2A) becomes the shared gate for JD creation and for both Call 1 and Call 2 actors, and is reused by the Phase 2C.1 pipeline builder for template scoping.
6. **Signal schema v2 + job metadata** — migration `0003_signal_schema_v2` collapses the 4 legacy signal arrays into a single flat `signals` JSONB column with 10 fields per item (type, priority, weight, knockout, stage, evaluation_method, etc.) and adds 8 optional job metadata columns to `job_postings` (employment type, work arrangement, salary range, etc.). The UI panels, actors, schemas, and prompts were all rewritten to match.

Phase 2A's SSE stream, Dramatiq actor + `_safe_dispatch_*` pattern, `get_bypass_session` / `SET LOCAL app.current_tenant` worker idiom, and `require_job_access` ancestry-walking authz are all reused unchanged. What Phase 2B adds is new state, new endpoints, a second actor, and a substantially larger frontend surface built on the newly installed Zustand / TanStack Query / Vitest stack.

### State Machine (extended)

Phase 2A's 4 states became 5 after Phase 2B:

```
draft → signals_extracting
signals_extracting → signals_extracted | signals_extraction_failed
signals_extraction_failed → signals_extracting            (retry)
signals_extracted → signals_confirmed                     (NEW)
signals_confirmed → signals_extracted                     (NEW: edit-after-confirm)
```

Canonical set in `app/modules/jd/state_machine.py`. Every transition goes through `transition()` which writes a `job_posting.status_changed` audit row with `{from, to, correlation_id}` before flushing the new state.

### Enrichment — side field, not state machine

Re-enrichment lifecycle is tracked on a dedicated `enrichment_status` column so the main state machine stays small and Call 2 can run concurrently with the `signals_extracted` / `signals_confirmed` terminal states:

```
idle → streaming      (recruiter clicks Re-enrich JD)
streaming → completed (reenrich_jd actor succeeds)
streaming → failed    (actor exhausts retries, or Redis dispatch fails)
failed → streaming    (retry)
completed → streaming (re-enrich again after further edits)
```

The SSE stream (`app/modules/jd/sse.py`) emits an event whenever either `status` or `enrichment_status` changes, and only closes the connection when `status` is terminal **and** `enrichment_status != 'streaming'` — otherwise the UI would miss the completion event.

---

## 2. Database Schema (migrations 0001–0003)

Phase 2B ships three Alembic migrations. `0001` is the Phase 2B spec's column additions; `0002` adds audit trail wiring for the last editor; `0003` (technically the "Phase 2B+" signal schema v2 pass) replaces the Phase 2A signal columns with a single flat `signals` JSONB and adds the job metadata block. All three are part of the 2B delivery and are covered here.

Head after Phase 2B: `0003_signal_schema_v2`. Phase 2C.1 continues from there.

### `0001_phase_2b_columns`

Down revision: `None` (first Alembic migration — Phase 2A's `job_postings` and `job_posting_signal_snapshots` tables ship from the Supabase initial schema).

| Table | Column | Type | Default | Notes |
|---|---|---|---|---|
| `job_postings` | `enrichment_status` | TEXT NOT NULL | `'idle'` | Side-field Call 2 lifecycle tracker |
| `job_postings` | `enrichment_error` | TEXT NULL | — | Sanitized user-facing message when failed |
| `job_posting_signal_snapshots` | `prompt_version` | TEXT NULL | — | Which prompt produced this snapshot (e.g. `"v1"`). Stamped by actors only; `save_signals` leaves it NULL. |

No new tables, no RLS changes — the existing tenant_isolation + service_bypass pair on these tables already covers the added columns.

### `0002_add_updated_by`

Down revision: `0001_phase_2b_columns`.

| Table | Column | Type | Notes |
|---|---|---|---|
| `job_postings` | `updated_by` | UUID NULL, FK → `users.id` | Stamped by `save_signals`, `confirm_signals`, and `trigger_reenrichment` on every recruiter-visible mutation |

`updated_by` surfaces in `GET /api/jobs` as `updated_by_email` — the list view resolves the FK via a batch `User.id.in_(...)` lookup alongside the existing `created_by` resolution.

### `0003_signal_schema_v2`

Down revision: `0002_add_updated_by`. Clean-slate migration — at the time of delivery there was no production data in `job_posting_signal_snapshots`, so the migration drops the 5 legacy columns without a backfill.

**Dropped from `job_posting_signal_snapshots`:** `required_skills`, `preferred_skills`, `must_haves`, `good_to_haves`, `min_experience_years`.

**Added to `job_posting_signal_snapshots`:**

| Column | Type | Default | Notes |
|---|---|---|---|
| `signals` | JSONB NOT NULL | `'[]'::jsonb` | Flat list of signal items. Each item has 10 fields — see schema table below. |

**Added to `job_postings` (8 metadata columns, all nullable):**

| Column | Type | Allowed values (Pydantic Literal) |
|---|---|---|
| `employment_type` | TEXT NULL | `full_time`, `part_time`, `contract`, `contract_to_hire`, `internship` |
| `work_arrangement` | TEXT NULL | `onsite`, `remote`, `hybrid` |
| `location` | TEXT NULL | free text |
| `salary_range_min` | INTEGER NULL | annual, in smallest currency unit |
| `salary_range_max` | INTEGER NULL | annual, in smallest currency unit |
| `salary_currency` | TEXT NULL | `USD`, `EUR`, `GBP`, `INR`, `CAD`, `AUD` |
| `travel_required` | TEXT NULL | `none`, `occasional`, `moderate`, `extensive` |
| `start_date_pref` | TEXT NULL | `immediate`, `within_30_days`, `within_60_days`, `flexible` |

Enum enforcement lives in Pydantic `Literal` types on `JobPostingCreate` (`app/modules/jd/schemas.py`) — the DB columns are plain TEXT without CHECK constraints, matching the rest of the codebase's "Pydantic validates, DB is a safety net" convention.

**Spec drift — signal schema v2:** The spec (`2026-04-11-signal-schema-v2-job-metadata-design.md`) calls for `TEXT + CHECK` constraints on the metadata columns and a narrower enum set (`full_time/part_time/contract/internship`, `remote/hybrid/onsite`, `INR/USD/EUR`, `none/occasional/frequent`, `immediate/within_30_days/within_90_days/flexible`). The shipped migration uses plain TEXT with no CHECK, and the shipped Pydantic `Literal`s use a wider set (`contract_to_hire`, `GBP/CAD/AUD`, `moderate/extensive`, `within_60_days`). Behaviourally equivalent — validation is enforced at the API boundary — but the enum vocabularies differ from the spec.

### Signal item schema (v2)

Each entry in `signals` is a JSON object with the following fields. The Pydantic models in `app/modules/jd/schemas.py` are the source of truth. `SignalItemInput` (PATCH body) defaults `weight=2`, `knockout=false`, and `evaluation_method=None` so the frontend only needs to send the fields the recruiter actually changed. `SignalItemResponse` (GET body) resolves `evaluation_method` via `default_evaluation_method(type, stage)` when the stored value is null.

| Field | Type | Notes |
|---|---|---|
| `value` | string (≥1 char) | The signal text ("Python", "5+ years backend") |
| `type` | enum | `competency`, `experience`, `credential`, `behavioral` |
| `priority` | enum | `required`, `preferred` |
| `weight` | int | `1` / `2` / `3` — relative importance |
| `knockout` | bool | If true, failing = auto-reject in screen |
| `stage` | enum | `screen`, `interview` — which round probes this |
| `evaluation_method` | enum | `verbal_response`, `code_exercise`, `scenario_walkthrough`, `credential_verify`, `behavioral_question`. Derived from `(type, stage)` by `default_evaluation_method()` when stored as null. |
| `evaluation_hint` | string / null | "What good looks like" — populated by Call 3 in a later phase; null in 2B |
| `source` | enum | `ai_extracted`, `ai_inferred`, `recruiter` |
| `inference_basis` | string / null | Required when `source='ai_inferred'`, must be null otherwise — enforced by `SignalItemInput.check_provenance` model validator |

**Spec drift — evaluation method enum:** The design spec lists `depth_probe`, `verification`, `situational`, `case_study`. The shipped enum is `verbal_response`, `code_exercise`, `scenario_walkthrough`, `credential_verify`, `behavioral_question`. The shipped set is a slightly richer vocabulary and maps cleanly to the same (type, stage) default table; no downstream code depends on the spec names.

### What stays unchanged

`seniority_level` and `role_summary` remain separate TEXT columns on the snapshot — they are classification / context, not probeable signals. `confirmed_by`, `confirmed_at`, `prompt_version`, `version`, `created_at`, `tenant_id`, `job_posting_id`, and the `tenant_isolation` + `service_bypass` RLS policy pair are all preserved from Phase 2A.

---

## 3. Signal Editing Flow

Editing is a recruiter-only action (`jobs.manage` permission, enforced by `require_job_access(db, job_id, user, "manage")` walking the job's org unit ancestry). It is the only path that mutates a snapshot once Call 1 has written it — snapshots are immutable, and edits create new rows.

### Endpoint

`PATCH /api/jobs/{job_id}/signals` — body is `SaveSignalsRequest` (`signals: list[SignalItemInput]`, `seniority_level`, `role_summary`). Defined in `app/modules/jd/router.py::update_signals`.

### Precondition

The handler rejects with **409** if `job.status not in ('signals_extracted', 'signals_confirmed')`. Editing while Call 1 is still running, or after a failed extraction, is disallowed — the recruiter must wait for extraction to resolve or retry.

### Save pipeline (service.py::save_signals)

1. **Auto-clear confirmation.** If the job was in `signals_confirmed`, call `transition(db, job, to_state='signals_extracted', ...)`. The state machine writes an audit row for this transition. The recruiter will have to re-confirm after the edit lands — this is the soft-approval invariant from the design spec.
2. **Clear stale enrichment state.** Set `job.enrichment_status = 'idle'` and `job.enrichment_error = None`. The enriched JD was generated from the *previous* snapshot, so it is now stale until Call 2 runs again. Without this clear, the UI could show "completed" next to the stale prose with no cue that the signals had moved on.
3. **Stamp the editor.** `job.updated_by = actor_id`.
4. **Acquire a row lock.** Execute `SELECT job_postings.id WHERE id = :job_id FOR UPDATE`. This serialises concurrent `save_signals` calls on the same parent job so they can't race on the `MAX(version)` read.
5. **Compute the next version.** `SELECT max(version) FROM job_posting_signal_snapshots WHERE job_posting_id = :job_id`. Start at 0 for an empty set; next version = `current_max + 1`.
6. **Insert the new snapshot row.** `signals = [item.model_dump() for item in body.signals]`, `seniority_level` and `role_summary` from the body, `confirmed_by = None`, `confirmed_at = None`. The insert leaves `prompt_version` unset (the column is nullable and defaults to NULL); only Call 1's `_persist_enriched` stamps it. The unique constraint `(job_posting_id, version)` backstops the version pick even if the FOR UPDATE lock were bypassed.
7. **Flush and log.** `db.flush()` stages the write; the commit happens when the `get_tenant_db` dependency's transaction exits. `jd.service.signals_saved` is logged with `{job_posting_id, snapshot_version, correlation_id}`.

### What the FOR UPDATE lock actually does

The lock is purely a **concurrency serializer** on the parent row. Two saves that race through `save_signals` for the same job will block each other at the SELECT … FOR UPDATE point, so the second call's `MAX(version)` read sees the first call's insert and picks the next integer. It does **not** do optimistic concurrency control — there is no `expected_version` parameter on the request body, and clients do not need to carry a version forward to save. If the frontend has stale data, it just writes the next version with whatever the user typed; reviewers see a monotonically increasing version history in the audit trail.

**Spec drift — "version conflict detection":** `backend/nexus/CLAUDE.md` describes `save_signals` as raising a `VersionConflictError` when the client's base version is stale. No such exception exists in the codebase (`grep VersionConflictError` returns only `CLAUDE.md`). The shipped behaviour is simpler: the FOR UPDATE lock serialises writers so there is no conflict to detect; the unique index on `(job_posting_id, version)` guarantees no two snapshots can share a version regardless. The CLAUDE.md description is aspirational and does not match shipped code — documented here so readers do not waste time grepping for a phantom class.

### Response

The router returns `SignalSnapshotResponse` built from the newly inserted snapshot via `_snapshot_to_response`, which resolves any null `evaluation_method` fields via `default_evaluation_method(type, stage)` before returning.

---

## 4. Confirmation Flow

Confirmation is the recruiter's explicit sign-off that the current snapshot is ready to drive the downstream pipeline (Phase 2C.1 auto-apply, Phase 2C.2 question bank generation).

### Endpoint

`POST /api/jobs/{job_id}/signals/confirm` — no body. Defined in `app/modules/jd/router.py::confirm_signals_endpoint`.

### Precondition

Router rejects with **409** if `job.status != 'signals_extracted'`. You cannot confirm while extraction is in-flight or failed, and the "re-confirm a confirmed job" case never reaches confirm because `save_signals` automatically walks it back to `signals_extracted` first.

### Service pipeline (service.py::confirm_signals)

1. **Load the latest snapshot** ordered by `version DESC, LIMIT 1`. Raises `ValueError` — mapped to 409 at the router — if no snapshot exists (this should be unreachable given the status precondition, but the guard is load-bearing for defensive callers like tests).
2. **Stamp confirmation fields** on the latest snapshot: `confirmed_by = actor_id`, `confirmed_at = datetime.now(UTC)`. Also `job.updated_by = actor_id`.
3. **Transition** `signals_extracted → signals_confirmed` via `state_machine.transition()` (audit row written). `db.flush()`.
4. **Auto-apply a starter pipeline** via `app.modules.pipelines.service.auto_apply_pipeline_on_confirmation(db, job=job, actor_id=actor_id)`. This helper resolves a template via (1) last-used template in the org unit, (2) org unit's default template, (3) system starter pack fallback, then inserts a `job_pipeline_instances` row and its stages. See `docs/phase-2c1-implementation.md` for the template resolution rules.
5. **Handle auto-apply failures idempotently.** Two special cases matter here:
   - **`PipelineAlreadyExistsError`** → caught separately and demoted to a `debug`-level log `jd.pipeline_auto_apply_skipped_existing`. This is the expected path when a recruiter re-confirms a job that was previously confirmed (and thus already had a pipeline created) — without this carve-out every re-confirm would log at `error` and write a misleading audit event. Shipped in commit `a7ba2ea`.
   - **Any other exception** → logged at `error` with `exc_info`, and a `job_pipeline.auto_apply_failed` audit event is written. A nested try/except around the audit write makes sure an audit-log failure never cascades back into the confirmation path. The confirmation itself is never rolled back — the job is already flipped to `signals_confirmed` before auto-apply runs, and that invariant is deliberate: pipeline auto-apply is a convenience, not a precondition.

### Response

`JobPostingSummary` via `_job_to_summary(job)`. The frontend's TanStack Query hook invalidates `['jobs', jobId]` on success, which refetches `GET /api/jobs/{id}` and picks up the new `is_confirmed` flag.

---

## 5. Call 2 Re-enrichment

Call 2 re-runs the enriched-prose generation using the current (edited) snapshot as input. It is explicit (the recruiter clicks `Re-enrich JD`), separate from the save step (so recruiters can batch edits before spending tokens), and does not run automatically after a save.

### Endpoint

`POST /api/jobs/{job_id}/enrich` — no body, returns `202 Accepted` with `{"status": "accepted"}`. Defined in `app/modules/jd/router.py::enrich_job`.

### Precondition

Router rejects with **409** if `job.status not in ('signals_extracted', 'signals_confirmed')` — you cannot re-enrich during extraction or after an extraction failure. Within the service, `trigger_reenrichment` additionally rejects when `enrichment_status == 'streaming'` by raising `IllegalTransitionError(from_state='enrichment:streaming', to_state='enrichment:streaming')` — that maps to 409 via the existing exception handler.

### Dispatch pipeline (router.py::enrich_job + service.py::trigger_reenrichment)

1. `require_job_access(db, job_id, user, "manage")` — same ancestry walk as editing.
2. `trigger_reenrichment(db, job=job, actor_id=user.user.id)` sets `enrichment_status='streaming'`, clears any prior `enrichment_error`, and stamps `updated_by`.
3. The router schedules `_safe_dispatch_reenrichment` via FastAPI `BackgroundTasks`. That wrapper does the `reenrich_jd.send()` after the request transaction has committed — same post-commit pattern as `_safe_dispatch_extraction` in Phase 2A.
4. If the Dramatiq `send()` raises (Redis unreachable, broker misconfigured), `_safe_dispatch_reenrichment` opens a new tenant-scoped session, sets `enrichment_status='failed'`, sets `enrichment_error='Failed to dispatch re-enrichment job — please retry. If this persists, contact support.'`, and writes a `job_posting.reenrich_dispatch_failed` audit row. This is the only path where the audit log captures a dispatch-layer failure (the actor's own failures are captured via Langfuse + structlog).

### Actor: `reenrich_jd` (app/modules/jd/actors.py)

Registered via `@dramatiq.actor(max_retries=1, min_backoff=2_000, max_backoff=30_000, queue_name="jd_reenrichment")`. Note the **`max_retries=1`** (Call 1 uses `max_retries=2` — see `docs/phase-2a-implementation.md`). Re-enrichment is user-initiated and should fail fast so the recruiter sees the error and retries manually rather than waiting on an exponential backoff.

The actor's outer wrapper is the same pattern as Call 1:

```
async with get_bypass_session() as db:
    safe_tenant_id = str(UUID(tenant_id))  # defensive round-trip, no injection
    await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'"))
    try:
        await _run_reenrichment(db, ...)
        await db.commit()
    except Exception:
        if retries_so_far >= 1:
            await db.commit()   # final retry — persist failure
        else:
            await db.rollback() # mid-retry — leave state unchanged
        raise
    finally:
        if langfuse_enabled():
            await asyncio.to_thread(flush_langfuse)
```

### `_run_reenrichment` internals

Decorated with `@observe(name="jd_reenrichment_call2")` so each invocation becomes a Langfuse trace with the OpenAI call captured as a nested generation span.

1. **Load the job.** Return early with a warn log if missing.
2. **Idempotency guard.** If `job.enrichment_status != 'streaming'`, log `jd.reenrich.skip_unexpected_status` and return. Prevents double-processing if a duplicate message is delivered.
3. **Load the latest snapshot** (ordered by `version DESC LIMIT 1`). No snapshot → set `enrichment_status='failed'`, `enrichment_error='No signal snapshot found — cannot re-enrich'`, return.
4. **Load the company profile via ancestry walk** — same `find_company_profile_in_ancestry` used at job creation and by Call 1. Missing profile → set failed + error, return.
5. **Attach trace metadata.** `session_id=job_posting_id` groups retries; `tags=['jd_reenrichment', f'retry:{retries_so_far}']`; metadata includes `correlation_id`, `prompt_version`, `model`, `reasoning_effort`.
6. **Build the user message** via `_build_reenrich_user_message`. Order is mandatory (context before document, per user-memory preference): `company profile → original raw JD → current enriched JD → updated signal snapshot (JSON)`. The snapshot is serialised as pretty-printed JSON inside a fenced code block so the model treats it as structured data and doesn't paraphrase chip values.
7. **Call OpenAI** through `get_openai_client()` with `response_model=ReEnrichmentOutput` and `name="jd_reenrichment_call2"`. Latency and token accounting are auto-captured by `langfuse.openai`.
8. **Error handling.** Same `_PERMANENT_EXCEPTIONS` tuple as Call 1 (`BadRequestError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `InstructorRetryException`). Permanent errors — or any error on the final retry (`retries_so_far >= 1`) — write `enrichment_status='failed'` + `enrichment_error=sanitize_error_for_user(exc)`. Transient errors on the first attempt raise so Dramatiq backs off and retries.
9. **Success path.** Set `job.description_enriched = reenriched.enriched_jd`, `job.enrichment_status = 'completed'`, `job.enriched_manually_edited = True`. Log `jd.reenrich.completed`.

### Prompt

`backend/nexus/prompts/v1/jd_reenrichment.txt`. Stamped with `prompt_version='v1'` on the trace but — unlike Call 1 — `reenrich_jd` does **not** write a new signal snapshot, so `job_posting_signal_snapshots.prompt_version` is never filled from Call 2. Only Call 1's `_persist_enriched` stamps snapshot rows.

### What the SSE stream does during re-enrichment

The SSE generator (`sse.py::job_status_event_generator`) emits on any change to either `status` or `enrichment_status`. It only closes when `status` is terminal **and** `enrichment_status != 'streaming'`. So during Call 2 the stream stays open through the full actor lifecycle, emits once when `streaming → completed`, then closes. This is what drives the `StaleBanner → blue "Re-enriching JD..." pill → fresh prose + banner disappears` UX transition.

---

## 6. Company Profile Ancestry Walk

`find_company_profile_in_ancestry(db, org_unit_id)` lives in `app/modules/org_units/service.py` (introduced in Phase 2A). It walks `parent_unit_id` upwards from the given org unit, returning the first non-null `company_profile` dict it finds — or `None` if nothing in the chain has one.

```python
async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict | None:
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

The profile itself conforms to the 4-field `CompanyProfile` Pydantic model in `app/modules/org_units/company_profile.py`: `about`, `industry` (enum), `company_stage` (enum), `hiring_bar`. Validation is enforced in `_validate_and_normalize_company_profile` at org unit create/update time, so anything returned by the walk is already shape-checked.

### Where the walk runs in Phase 2B

The helper is called in four places:

| Caller | File | Purpose |
|---|---|---|
| `create_job_posting` | `app/modules/jd/service.py:58` | Gate: refuses to insert a job row if no ancestor has a profile. Raises `CompanyProfileIncompleteError` with the target `org_unit_id` so the frontend can deep-link to the Company Profile tab (mapped to 422 at the router). |
| `_run_extraction` (Call 1) | `app/modules/jd/actors.py:139` | Defensive re-check inside the actor. This should never fail given the creation-time gate, but if it does the actor marks the job as `signals_extraction_failed` instead of crashing. |
| `_run_reenrichment` (Call 2) | `app/modules/jd/actors.py:378` | Same defensive re-check for Call 2. Failure sets `enrichment_status='failed'` with a sanitised error message. |
| `generate_question_bank_stage` | `app/modules/question_bank/actors.py:285` | Loads the profile as context for the question bank generation prompt (passed into `_build_user_message` alongside the JD snapshot and pipeline stages). Not part of Phase 2B's scope — this caller ships with Phase 2C.2 — but listed here because it reuses the same helper the ancestry walk introduced. |

The walk is cycle-safe via a `seen` set — `parent_unit_id` is supposed to be acyclic but corrupted data should not hang a request.

---

## 7. API Reference

All Phase 2B endpoints live under `/api/jobs` (router prefix in `app/modules/jd/router.py`). Auth is `Bearer` (Supabase JWT). RBAC is enforced by `require_job_access(db, job_id, user, action)` walking the job's org unit ancestry — super admins short-circuit.

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `PATCH` | `/api/jobs/{job_id}/signals` | `jobs.manage` in ancestry | Save edited signals as new snapshot version (auto-clears `signals_confirmed` back to `signals_extracted`) |
| `POST` | `/api/jobs/{job_id}/signals/confirm` | `jobs.manage` in ancestry | Stamp `confirmed_by/at` on latest snapshot, transition to `signals_confirmed`, auto-apply pipeline |
| `POST` | `/api/jobs/{job_id}/enrich` | `jobs.manage` in ancestry | Kick off Call 2 re-enrichment Dramatiq actor |

Existing Phase 2A endpoints had response shape changes too:

| Method | Path | Response changes |
|---|---|---|
| `GET` | `/api/jobs` | `JobPostingSummary` gained `updated_by_email` |
| `GET` | `/api/jobs/{job_id}` | `JobPostingWithSnapshot` gained `enrichment_status`, `enrichment_error`, `is_confirmed`, `can_manage`, and the 8 job metadata fields; `latest_snapshot.signals` changed from 4 arrays to a single flat list |
| `GET` | `/api/jobs/{job_id}/status/stream` | `JobStatusEvent` gained `enrichment_status`, `is_confirmed`. Stream close condition now requires `enrichment_status != 'streaming'` alongside the terminal status check |

### Error shapes

| Error | HTTP | Body |
|---|---|---|
| Editing in wrong state | 409 | `{"detail": "Cannot edit signals in status '<state>'"}` |
| Confirming in wrong state | 409 | `{"detail": "Cannot confirm signals in status '<state>'"}` |
| Enriching in wrong state | 409 | `{"detail": "Cannot trigger re-enrichment in status '<state>'"}` |
| Double-dispatch re-enrichment | 409 | `{"detail": "Cannot transition job from enrichment:streaming to enrichment:streaming"}` — formatted by the generic `f'Cannot transition job from {from_state} to {to_state}'` fallback in `app/main.py`, since `(enrichment:streaming, enrichment:streaming)` is not a keyed entry in `_ILLEGAL_TRANSITION_MESSAGES` |
| Already-confirmed re-confirm | 409 | `{"detail": "Signals are already confirmed"}` — keyed entry in `_ILLEGAL_TRANSITION_MESSAGES` |
| Missing `jobs.manage` | 403 | `{"detail": "Missing jobs.manage in job's org unit ancestry"}` |
| Cross-tenant or missing job | 404 | `{"detail": "Job not found"}` |

### `PATCH /api/jobs/{job_id}/signals`

**Body (`SaveSignalsRequest`):**

```json
{
  "signals": [
    {
      "value": "Python",
      "type": "competency",
      "priority": "required",
      "weight": 3,
      "knockout": false,
      "stage": "interview",
      "evaluation_method": null,
      "evaluation_hint": null,
      "source": "ai_extracted",
      "inference_basis": null
    }
  ],
  "seniority_level": "senior",
  "role_summary": "Senior backend engineer owning the billing service..."
}
```

**Response (`SignalSnapshotResponse`):** the newly inserted snapshot, with `version` incremented, `evaluation_method` fields resolved to defaults where the body left them null, and `confirmed_by` / `confirmed_at` set to null.

**Validation rules (Pydantic):** every item with `source='ai_inferred'` must have non-null `inference_basis`; items with `source in {'ai_extracted','recruiter'}` must have `inference_basis=null`. `role_summary` must be 10–2000 chars.

### `POST /api/jobs/{job_id}/signals/confirm`

No body. Returns `JobPostingSummary`. Side effects: latest snapshot's `confirmed_by/at` stamped, job status → `signals_confirmed`, pipeline auto-applied (idempotent against `PipelineAlreadyExistsError`).

### `POST /api/jobs/{job_id}/enrich`

No body. Returns `202` with `{"status": "accepted"}`. Side effects: `enrichment_status` set to `streaming`, `reenrich_jd` actor enqueued via post-commit `BackgroundTasks` → `_safe_dispatch_reenrichment`. Poll for completion via `GET /api/jobs/{id}/status/stream` or `GET /api/jobs/{id}`.

---

## 8. Frontend Architecture

Phase 2B is the first frontend surface where the full post-Phase 2A stack (Zustand, TanStack Query v5, Vitest, sonner toasts, shadcn + Base UI primitives) is actively used (shadcn v4 ships on Base UI, not Radix — see `frontend/app/CLAUDE.md` for the ecosystem gotchas). All files below are under `frontend/app/`.

### Entry point

| File | Role |
|---|---|
| `app/(dashboard)/jobs/[jobId]/page.tsx` | Three-panel JD review page. Renders `OriginalJdPanel`, `EnrichedJdPanel` (with `StaleBanner` as its `banner` slot), and `SignalsPanelWrapper`. Also drives the "auto-redirect to pipeline tab" behaviour when a confirmed job already has a pipeline (gated on `job.status === 'signals_confirmed'` to prevent the redirect loop that would otherwise trap users on re-extracted jobs). |

### Component tree

| Component | File | Role |
|---|---|---|
| `SignalsPanelWrapper` | `components/dashboard/jd-panels/SignalsPanelWrapper.tsx` | View ↔ edit toggle orchestrator. Reads `isEditing`, `draft`, `isDirty` from Zustand. Renders `SignalsPanel` in view mode, `EditableSignalsPanel` in edit mode. Cleans up editing state on job navigation via a `useEffect` that calls `stopEditing()` in the cleanup phase. Only shows the `Edit Signals` button when `canManage === true`. |
| `SignalsPanel` | `components/dashboard/jd-panels/SignalsPanel.tsx` | Read-only display. Groups by stage (Screen / Interview), then by type within each stage. |
| `EditableSignalsPanel` | `components/dashboard/jd-panels/EditableSignalsPanel.tsx` | Edit-mode panel. Per-chip inline controls: weight select (W1/W2/W3), knockout toggle, type select, stage select, priority select. Per-type `AddSignalInput` at the bottom of each group. Role summary becomes a `Textarea`, seniority becomes a `Select`. `EditableChipRow` uses a composite key `${realIndex}-${item.value}` (commit `9dac616`) so that removing or reordering a chip cannot alias state from a different row — the signal schema has no server-assigned UID, so the composite is the best stable identity available. |
| `ConfirmBar` | `components/dashboard/jd-panels/ConfirmBar.tsx` | Bottom bar. Three states: edit mode → blue `Save Signals` button; unconfirmed → green `Confirm Signals` button; confirmed → static green `Signals Confirmed` badge with a checkmark. Only rendered when `canManage === true`. |
| `StaleBanner` | `components/dashboard/jd-panels/StaleBanner.tsx` | Banner rendered into `EnrichedJdPanel`'s `banner` slot. Four states: error (red + Retry), enriching (blue animated pill, no action), stale (amber + Re-enrich JD button), hidden. Stale = `enrichment_status` is not `completed` and not `streaming` **and** a snapshot exists. |

### Zustand store

`stores/job-edit.ts` — a single store, created with `zustand`'s `create<JobEditState>()`. Global singleton (the store is module-level), so `SignalsPanelWrapper` explicitly calls `stopEditing()` in a `useEffect` cleanup keyed on `jobId` to prevent edit state bleeding across job navigations.

| Field / action | Purpose |
|---|---|
| `isEditing: boolean` | View ↔ edit toggle |
| `draft: { signals, seniority_level, role_summary } \| null` | Working copy populated from the latest snapshot by `startEditing(snapshot)` |
| `isDirty: boolean` | Set true on any draft mutation; drives the "discard unsaved changes?" `window.confirm` in `handleToggleEdit` |
| `startEditing(snapshot)` | Hydrate draft from snapshot (deep-copies signals to avoid mutating query cache), set `isEditing=true`, `isDirty=false` |
| `stopEditing()` | Clear draft, `isEditing=false`, `isDirty=false` |
| `updateDraft(partial)` | Merge into draft, set dirty |
| `addChip(value, type, stage, priority)` | Append a new `recruiter`-sourced chip with `weight=1`, `knockout=false`, `evaluation_method='verbal_response'`, `inference_basis=null` (The Pydantic default is `weight=2` — see Section 3's signal item schema. The frontend intentionally starts new chips at `weight=1` so recruiter-added chips appear as lowest-priority until explicitly raised.) |
| `removeChip(index)` | `filter((_, i) => i !== index)` |
| `updateSignal(index, partial)` | Merge partial into the indexed signal |
| `markClean()` | `isDirty=false` — called by the save hook on success before `stopEditing()` |

Server state (`job`, snapshot, status stream) lives in TanStack Query (`useJob(jobId)`). The store never caches server data — only the transient editing buffer.

### API client (`lib/api/jobs.ts`)

Typed wrapper over `apiFetch`. Methods for Phase 2B:

- `jobsApi.saveSignals(token, id, body)` → `PATCH /api/jobs/{id}/signals`
- `jobsApi.confirmSignals(token, id)` → `POST /api/jobs/{id}/signals/confirm`
- `jobsApi.triggerEnrich(token, id)` → `POST /api/jobs/{id}/enrich`

Full `SignalItem`, `SignalSnapshot`, `SaveSignalsBody`, `JobPostingWithSnapshot`, and `JobStatusEvent` types are defined here and re-exported from the hooks.

### Hooks

All three mutation hooks follow the same pattern: call `getFreshSupabaseToken()` → call the API method → toast success / failure via `sonner` → invalidate `['jobs', jobId]` (TanStack Query key shape matches `useJob`).

| Hook | File | Invalidation |
|---|---|---|
| `useSaveSignals(jobId)` | `lib/hooks/use-save-signals.ts` | `['jobs', jobId]` |
| `useConfirmSignals(jobId)` | `lib/hooks/use-confirm-signals.ts` | `['jobs', jobId]` |
| `useTriggerEnrich(jobId)` | `lib/hooks/use-trigger-enrich.ts` | `['jobs', jobId]` |

`useJobStatusStream(jobId)` (Phase 2A) already handles `JobStatusEvent` including the new `enrichment_status` and `is_confirmed` fields, so the panel re-renders automatically when the worker reports progress.

### UX choreography

**Saving signals.** Recruiter clicks `Edit Signals` → `startEditing(snapshot)` hydrates the draft → inline chip edits mutate via `updateSignal` / `addChip` / `removeChip` → `Save Signals` button calls `useSaveSignals.mutate(draft)`. On success: `markClean()`, `stopEditing()`, toast, and TanStack invalidation pulls fresh `job` data. The new snapshot replaces the old, and if the job was confirmed the status reverts to `signals_extracted` so the `ConfirmBar` reverts from the green badge back to a `Confirm Signals` button.

**Confirming signals.** User clicks `Confirm Signals` → `useConfirmSignals.mutate()` → backend stamps the snapshot, transitions, auto-applies pipeline → query invalidation → new `job` data shows `is_confirmed: true`, `status: 'signals_confirmed'` → `ConfirmBar` renders the static badge. The page-level `useEffect` sees `pipeline != null && job.status === 'signals_confirmed'` and redirects to `/jobs/{id}/pipeline` unless `?tab=jd` was set.

**Re-enriching.** Any save marks the enriched JD as stale (`isStale = enrichment_status not in {completed, streaming} && snapshot != null`). `StaleBanner` renders amber with `Re-enrich JD`. Click → `useTriggerEnrich.mutate()` → `enrichment_status='streaming'` → SSE delivers the change → banner re-renders as a blue animated "Re-enriching JD..." pill. When the actor finishes and emits `streaming → completed`, SSE delivers the update, TanStack refetches, `description_enriched` is replaced in the middle panel, `StaleBanner` returns `null` (neither stale nor enriching nor errored). On failure, the banner shows red with `Retry`.

---

## 9. Known Gaps

- **"Version conflict detection" is only a serializer.** As documented in the Signal Editing Flow section, `save_signals` uses a FOR UPDATE row lock and does not implement optimistic concurrency control. Two recruiters editing the same job simultaneously will both succeed — the second save overwrites the first's perception of "current" signals. The audit trail preserves both snapshots, but the UI has no live collaboration indicator. CLAUDE.md's reference to `VersionConflictError` is aspirational and does not match shipped code.
- **Signal schema v2 enum vocab differs from spec.** Both the metadata column enums (`employment_type`, `work_arrangement`, `salary_currency`, `travel_required`, `start_date_pref`) and the `evaluation_method` enum shipped with different values than the design spec. The shipped set is consistent between the Pydantic `Literal`s and the frontend TS types — if the spec values are the ones we ultimately want, a future cleanup pass needs to touch `app/modules/jd/schemas.py`, `frontend/app/lib/api/jobs.ts`, and any prompts that reference them.
- **No DB CHECK on metadata columns.** Migration 0003 ships the metadata columns as plain TEXT with no CHECK constraint. Validation is only enforced at the Pydantic boundary on `POST /api/jobs`. A direct DB write (or a future `PATCH /api/jobs/{id}` update of metadata, which does not exist yet) could bypass the allowlist.
- **Metadata is write-once at creation.** `POST /api/jobs` accepts all 8 metadata fields, but there is no update endpoint — recruiters cannot change employment type, salary, etc. after the job is created. A later phase will need to add a `PATCH /api/jobs/{id}` endpoint and corresponding UI.
- **`description_enriched` is not rolled back on Call 2 failure.** If `reenrich_jd` fails, the previous enriched prose is preserved (it was never overwritten), but `enrichment_status='failed'` + `enrichment_error` are set on the job row. The UI correctly shows a red banner over the still-visible previous prose. No server-side rollback mechanism — the preservation is a side effect of the "only write on success" actor logic.
- **Dispatch-time Redis failure for Call 2 bypasses the state machine.** `_safe_dispatch_reenrichment` writes `enrichment_status='failed'` directly; the main `status` state machine is untouched. That is intentional (enrichment is a side channel), but operators investigating a stuck job should check the `enrichment_status` column as well as `status`.
- **`prompt_version` is only stamped by Call 1.** `reenrich_jd` does not write a snapshot, so `job_posting_signal_snapshots.prompt_version` reflects the prompt that produced the initial extraction, not any subsequent re-enrichment. The re-enrichment prompt version is only visible in Langfuse trace metadata.
- **Edit UI has no drag-to-reorder and no stage drag.** The spec called out drag-between-stage-sections as nice-to-have; shipped UI uses a per-chip stage dropdown. Same for evaluation_method — the design mentioned it, the UI currently exposes weight / knockout / type / stage / priority but not an evaluation_method override.
- **Vitest coverage is thin.** Vitest is installed and configured but the shipped component test suite for 2B is minimal. The backend tests (`tests/test_jd_signals.py`, `tests/test_jd_reenrich.py`) are the primary safety net.
