# ATS — Job-Status Filter + Sync Progress

> Iteration on top of the ATS Adapter system (spec: `2026-05-12-ats-adapter-design.md`).
> Targets the "jobs phase fetches every job sequentially regardless of activity" problem and adds a live progress bar for the long-running phases.

**Author:** Ishant
**Date:** 2026-05-12
**Status:** Approved (design); implementation pending

---

## Problem

The ATS Adapter system currently syncs **every** job from Ceipal regardless of activity state. In the test tenant, that's 662 jobs × 2s pacing ≈ 22 min per first sync. Most jobs are inactive — closed, declined, filled. Wasted calls.

Recruiters can already pre-filter in Ceipal by **job status** (Active, Hold, Filled, …). The Ceipal API exposes:

- `GET /v2/getJobStatusList/` → list of status `{id, name}` (tenant-customizable).
- `GET /v2/getJobPostingsList/?jobStatus=1,8` → server-side filtering on the IDs.

The product needs (a) a UI to pick which statuses to import, and (b) a progress bar so a 22-min sync isn't an opaque "running…" indicator.

## Goals

1. Per-connection persisted job-status filter; status IDs sent on every Ceipal jobs-list call.
2. First-time connect flow: clients + users sync runs in background; the user picks job statuses, then the jobs/applicants/submissions phases run.
3. Live mid-flight progress bar for the jobs phase, polled every 2s while running.
4. Filter editable post-creation; widening it triggers a full re-pull of jobs (cursor reset); narrowing it keeps the cursor.

## Non-Goals

- Filtering applicants or submissions (their phases are not the long pole and Ceipal's submission filter is per-job, not per-status).
- Per-stage SSE progress (poll cadence on the sync-log row is sufficient).
- Re-prompting on every manual sync. The saved filter is reused for cron polls and manual syncs alike.
- Greenhouse / Workday adapters — they implement different filter semantics; this spec is Ceipal-scoped.

---

## UX Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│  /settings/integrations/connect                                        │
│  CeipalConnectionForm: email + password + apiKey → submit              │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │
                               ▼  POST /api/ats/connections
┌────────────────────────────────────────────────────────────────────────┐
│  Backend                                                               │
│  1. ensure_authenticated() on a temporary ATSConnectionState           │
│  2. Persist ATSConnection row (job_status_filter = NULL)               │
│  3. Enqueue poll_ats_connection(phase_filter=["clients", "users"])     │
│  4. Return 201 with ConnectionResponse                                 │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │
                               ▼  router.push("/settings/integrations/{id}")
┌────────────────────────────────────────────────────────────────────────┐
│  /settings/integrations/{connectionId}                                 │
│  Banner: "Configure which job statuses to import" (filter is NULL)     │
│  Modal opens automatically                                             │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │
                               ▼  GET /api/ats/connections/{id}/job-statuses
┌────────────────────────────────────────────────────────────────────────┐
│  Backend: load adapter → ceipal.list_job_statuses() → return raw       │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │
                               ▼  user picks ["Active", "Reactivated"]
┌────────────────────────────────────────────────────────────────────────┐
│  PUT /api/ats/connections/{id}/job-status-filter                       │
│  Body: {status_ids: [1, 8], names: ["Active", "Reactivated"]}          │
│                                                                        │
│  Backend                                                               │
│  1. Detect widen vs prior filter; if widened → drop last_synced cursor │
│     for "jobs"                                                         │
│  2. Persist filter on row                                              │
│  3. Audit: ats.connection.job_status_filter_updated                    │
│  4. Enqueue poll_ats_connection(                                       │
│        phase_filter=["jobs", "applicants", "submissions"]              │
│     )                                                                  │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │
                               ▼  detail page poll loop kicks up to 2s
┌────────────────────────────────────────────────────────────────────────┐
│  Sync log row's progress.jobs = {processed: N, total: M}               │
│  Frontend renders horizontal bar [████░░░░] 245 / 662 (37%)            │
└────────────────────────────────────────────────────────────────────────┘
```

Subsequent visits: banner disappears, "Edit jobs filter" button reopens the same modal.

Cron-driven polls (`/cli/ats_tick.py`) call `poll_ats_connection(phase_filter=None)` — all five phases. Importer's `_sync_jobs` reads the stored filter and either runs filtered, or short-circuits if filter is still NULL.

---

## Schema Changes

### Migration `0032_ats_job_status_filter_and_progress.py`

```sql
-- Per-connection: persisted job-status filter.
-- NULL → "not yet configured"; jobs phase short-circuits with an info log.
ALTER TABLE ats_connections
  ADD COLUMN job_status_filter JSONB NULL;

-- Per-sync-log: mid-flight progress counter.
-- Written by the importer's jobs phase every row; polled by the frontend.
ALTER TABLE ats_sync_logs
  ADD COLUMN progress JSONB NOT NULL DEFAULT '{}'::jsonb;
```

**Shapes:**

```json5
// ats_connections.job_status_filter
{
  "ids":   [1, 8],
  "names": ["Active", "Reactivated"]
}

// ats_sync_logs.progress  — keyed by phase name; only "jobs" populated today
{
  "jobs": { "processed": 245, "total": 662 }
}
```

**Rationale:**

- *Filter on the row, not encrypted.* Status IDs are not secrets; they're returned on a public Ceipal endpoint per tenant. Encrypting them would prevent cron polls from reading them.
- *Names persisted alongside IDs.* IDs are the wire identifier; names are needed for display. Cheap. If Ceipal renames a status, names drift — surface "Edit filter" to refresh.
- *`progress` separate from `entity_counts`.* `entity_counts` is the *terminal* per-phase tally written at phase end. `progress` is the *live* counter written mid-flight. Mixing them muddies the contract (e.g. `entity_counts.jobs.new` would race with `entity_counts.jobs.processed`).

**Backfill:** no DML in the migration. Existing connections (test dev: 1 row) get `job_status_filter = NULL` → frontend banner.

**Rollback:** `DROP COLUMN` both. Importer code paths default-safe on NULL filter; rollback before code-deploy would mean `_sync_jobs` reading `connection.job_status_filter` would fail. Rollback order: code first, schema after.

---

## Adapter Contract

### Protocol additions (`app/modules/ats/adapter.py`)

```python
class ATSAdapter(Protocol):
    ...
    async def list_job_statuses(self) -> list[dict]:
        """Return vendor-native list of available job statuses.

        Shape: [{"id": int, "name": str}, ...] for Ceipal. Other vendors may
        not have a status concept — those adapters raise NotImplementedError;
        the router endpoint translates that to 501.
        """
        ...

    async def count_jobs(
        self,
        *,
        since: datetime | None = None,
        job_status_ids: list[int] | None = None,
    ) -> int:
        """One-shot count of jobs matching the filter combination. Used by
        the importer to seed the progress bar's denominator before list_jobs
        starts streaming. Vendors that don't expose a count endpoint return
        ``-1`` — frontend renders an indeterminate spinner instead of a bar.
        """
        ...

    def list_jobs(
        self,
        since: datetime | None = None,
        *,
        job_status_ids: list[int] | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        """Existing method gains a status-id filter. Adapters that do not
        support server-side filtering MAY ignore the kwarg; the importer
        then filters client-side after yielding.
        """
        ...
```

### Ceipal implementation (`app/modules/ats/adapters/ceipal.py`)

```python
async def list_job_statuses(self) -> list[dict]:
    """GET /getJobStatusList/

    Response is a bare JSON array (not the paginated envelope used by other
    list endpoints). Returned verbatim — UI maps id+name.
    """
    response = await self._request("GET", "/getJobStatusList/")
    body = response.json()
    if not isinstance(body, list):
        raise ATSVendorContractError(
            f"/getJobStatusList/ returned {type(body).__name__}, expected list"
        )
    return body

async def count_jobs(self, *, since=None, job_status_ids=None) -> int:
    """GET /getJobPostingsList/?limit=1&jobStatus=...

    Reads ``envelope.count`` from the first-page response. One HTTP call —
    consumes a pacing slot.
    """
    params = {"limit": 1, **self._format_since(since)}
    if job_status_ids:
        params["jobStatus"] = ",".join(str(i) for i in job_status_ids)
    response = await self._request("GET", "/getJobPostingsList/", params=params)
    if response.status_code == 404:
        return 0
    envelope = response.json()
    return int(envelope.get("count", 0))

async def list_jobs(
    self, since=None, *, job_status_ids=None,
) -> AsyncIterator[ATSJobPayload]:
    params = {"limit": 50, **self._format_since(since)}
    if job_status_ids:
        params["jobStatus"] = ",".join(str(i) for i in job_status_ids)
    # ...existing _paginate + _fetch_job_details merge unchanged...
```

**Pacing impact:** `count_jobs` consumes one pacing slot (~2s). Negligible vs the 22-min total of the jobs phase. Cron syncs that hit `count_jobs` returning 0 still consume the slot — acceptable.

---

## Importer Changes (`app/modules/ats/importer.py`)

### Phase filter on `sync_tenant`

```python
ALL_PHASES: ClassVar[tuple[str, ...]] = (
    "clients", "users", "jobs", "applicants", "submissions",
)

async def sync_tenant(
    self,
    adapter: ATSAdapter,
    *,
    phase_filter: set[str] | None = None,
    sync_log_id: UUID | None = None,
) -> SyncResult:
    phase_table = {
        "clients":     self._sync_clients,
        "users":       self._sync_users,
        "jobs":        self._sync_jobs,
        "applicants":  self._sync_applicants,
        "submissions": self._sync_submissions,
    }
    phases_to_run = tuple(
        (name, phase_table[name])
        for name in self.ALL_PHASES
        if phase_filter is None or name in phase_filter
    )
    result = SyncResult()
    for name, fn in phases_to_run:
        try:
            phase_result = await self._run_phase(name, fn, adapter, sync_log_id)
        except Exception as exc:
            exc.partial_result = result
            raise
        setattr(result, name, phase_result)
    return result

async def _run_phase(self, name, fn, adapter, sync_log_id):
    # ...span+session unchanged, fn called with extra sync_log_id arg...
    phase_result = await fn(db, adapter, sync_log_id)
    ...
```

All five `_sync_*` methods gain `sync_log_id` in their signature (most ignore it). Only `_sync_jobs` uses it for progress writes today.

### `_sync_jobs` — filter read + progress writes

```python
async def _sync_jobs(self, db, adapter, sync_log_id):
    from app.modules.ats.models import (
        ATSClientMapping, ATSConnection, ATSJobRecruiterAssignment,
    )

    result = PhaseResult()
    tenant_id = adapter.state.tenant_id

    connection = await db.scalar(
        select(ATSConnection).where(
            ATSConnection.tenant_id == tenant_id,
            ATSConnection.vendor == adapter.vendor,
        )
    )

    filter_blob = connection.job_status_filter
    if not filter_blob or not filter_blob.get("ids"):
        # Filter not configured — record the skip so the recruiter can see
        # WHY entity_counts.jobs shows zero new/updated. Not an error.
        result.errors.append("filter_not_configured")
        logger.info(
            "ats.sync.jobs.skipped_no_filter",
            connection_id=str(connection.id), tenant_id=str(tenant_id),
        )
        return result

    status_ids: list[int] = filter_blob["ids"]
    created_by = connection.created_by
    since = self._cursor_or_none(adapter.state, "jobs")

    # Seed the progress denominator. count_jobs is one Ceipal call.
    try:
        total = await adapter.count_jobs(since=since, job_status_ids=status_ids)
    except Exception as exc:
        logger.warning("ats.sync.jobs.count_failed", error=str(exc)[:200])
        total = -1  # signal "unknown" to the frontend

    # Separate session for progress writes. Main `db` keeps its long phase
    # transaction; prog_db commits per row so the frontend polls see live data.
    async with get_bypass_session() as prog_db:
        await prog_db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        await self._write_jobs_progress(prog_db, sync_log_id, 0, total)

        processed = 0
        async for payload in adapter.list_jobs(since=since, job_status_ids=status_ids):
            # ...existing upsert logic (client mapping resolve, JobPosting
            # upsert, ATSJobRecruiterAssignment replace-all) unchanged...
            processed += 1
            await self._write_jobs_progress(prog_db, sync_log_id, processed, total)
    return result

@staticmethod
async def _write_jobs_progress(prog_db, sync_log_id, processed, total):
    if sync_log_id is None:
        return  # test paths that don't care
    payload = json.dumps({"processed": processed, "total": total})
    await prog_db.execute(
        text(
            "UPDATE ats_sync_logs "
            "SET progress = jsonb_set(progress, '{jobs}', :p::jsonb) "
            "WHERE id = :id"
        ),
        {"p": payload, "id": sync_log_id},
    )
    await prog_db.commit()
```

**Why two sessions:** the main `db` holds the long-running phase transaction (one commit per phase end — current pattern, keeps the rollback-on-failure semantics). `prog_db` commits per row to publish progress mid-flight. The progress writes go to a different row (`ats_sync_logs`) than the phase writes (`job_postings`, `ats_job_recruiter_assignments`), so no lock contention.

**Connection lookup is done once at top.** Filter changes mid-sync are ignored by the running sync (it loaded `state` at start). The next sync picks up the new filter. Acceptable; filter widens already trigger cursor reset so no rows are missed.

**`count_jobs` failure path:** logged + `total = -1`. Importer still streams the jobs phase normally. Frontend renders an indeterminate state (`?  / ?` or pulsing bar) instead of `0 / 0`.

---

## Service Layer (`app/modules/ats/service.py`)

### `update_job_status_filter`

```python
async def update_job_status_filter(
    db: AsyncSession,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    status_ids: list[int],
    names: list[str],
) -> None:
    """Persist the filter, drop the jobs cursor if widened, audit.

    - Empty status_ids → ValueError (router translates to 422).
    - len(status_ids) != len(names) → ValueError (UI bug guard).
    - Widen detection: any id in new_ids not in prior_ids.
    """
    if not status_ids:
        raise ValueError("status_ids must be non-empty")
    if len(status_ids) != len(names):
        raise ValueError("status_ids and names length mismatch")

    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return

    prior = row.job_status_filter or {}
    prior_ids = set(prior.get("ids", []))
    new_ids = set(status_ids)
    widened = bool(new_ids - prior_ids)

    row.job_status_filter = {"ids": status_ids, "names": names}
    if widened:
        cursors = dict(row.last_synced_cursors or {})
        cursors.pop("jobs", None)
        row.last_synced_cursors = cursors

    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.connection.job_status_filter_updated",
        resource="ats_connection", resource_id=connection_id,
        payload={
            "prior_ids": sorted(prior_ids),
            "new_ids":   sorted(new_ids),
            "widened":   widened,
        },
    )
    await db.flush()
```

### `trigger_manual_sync` — phase_filter parameter

```python
async def trigger_manual_sync(
    db, connection_id, tenant_id, actor_id,
    *, phase_filter: list[str] | None = None,
) -> None:
    from app.modules.ats.actors import poll_ats_connection
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != tenant_id:
        return
    await log_event(
        db, tenant_id=tenant_id, actor_id=actor_id,
        actor_email="recruiter",
        action="ats.sync.manually_triggered",
        resource="ats_connection", resource_id=connection_id,
        payload={"vendor": row.vendor, "phase_filter": phase_filter},
    )
    poll_ats_connection.send(str(connection_id), str(tenant_id), phase_filter)
```

**Audit payload now includes the phase filter** so the audit trail tells the story of an initial limited sync vs a full one.

---

## Actor (`app/modules/ats/actors.py`)

```python
@dramatiq.actor(...)
async def poll_ats_connection(
    connection_id: str,
    tenant_id: str,
    phase_filter: list[str] | None = None,
) -> None:
    await _run_poll(connection_id, tenant_id, phase_filter)


async def _do_poll(connection_id, tenant_id, correlation_id, safe_tenant, phase_filter):
    # ...phase A, B unchanged...
    try:
        sync_result = await ATSImporter().sync_tenant(
            adapter,
            phase_filter=set(phase_filter) if phase_filter else None,
            sync_log_id=sync_log_id,
        )
    except ATSRateLimitedError as exc:
        # ...partial-result handler unchanged...
```

**Why a list on the wire, set inside:** Dramatiq serializes args as JSON. `set` is not JSON. List in, set out at the boundary.

---

## Router (`app/modules/ats/router.py`)

### Modified `POST /api/ats/connections`

```python
await trigger_manual_sync(
    db, conn_id, user.user.tenant_id, user.user.id,
    phase_filter=["clients", "users"],
)
```

Everything else unchanged.

### New `GET /api/ats/connections/{id}/job-statuses`

```python
@router.get(
    "/connections/{connection_id}/job-statuses",
    response_model=list[CeipalJobStatusResponse],
)
async def list_connection_job_statuses(
    connection_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[CeipalJobStatusResponse]:
    """Live fetch from Ceipal. Not cached. Modal calls on open."""
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")

    # Bypass-RLS session for the adapter (load_connection_state needs to decrypt
    # credentials). Tenant binding via SET LOCAL.
    async with get_bypass_session() as bypass_db:
        await bypass_db.execute(
            text(f"SET LOCAL app.current_tenant = '{user.user.tenant_id}'")
        )
        state = await load_connection_state(bypass_db, connection_id)

    adapter = get_ats_adapter(state)
    try:
        raw = await adapter.list_job_statuses()
    except ATSCredentialsInvalidError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "ATS_CREDENTIALS_INVALID", "message": str(exc)[:200]},
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="vendor_no_status_endpoint")
    finally:
        await adapter.aclose()

    return [CeipalJobStatusResponse(id=int(s["id"]), name=str(s["name"])) for s in raw]
```

**Auth:** `get_current_user_roles` (read-only — no state change). Matches `/sync-logs`.

**Rate limit:** Inherits the authenticated bucket (600/min per-IP, 10k/min per-tenant from root CLAUDE.md). Modal calls it at most a handful of times per session. No per-endpoint additional cap warranted.

### New `PUT /api/ats/connections/{id}/job-status-filter`

```python
class JobStatusFilterRequest(BaseModel):
    status_ids: list[int] = Field(..., min_length=1)
    names: list[str] = Field(..., min_length=1)


@router.put(
    "/connections/{connection_id}/job-status-filter",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_job_status_filter(
    connection_id: UUID,
    body: JobStatusFilterRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(require_ats_admin),
) -> None:
    try:
        await update_job_status_filter(
            db,
            connection_id=connection_id,
            tenant_id=user.user.tenant_id,
            actor_id=user.user.id,
            status_ids=body.status_ids,
            names=body.names,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "JOB_STATUS_FILTER_INVALID", "message": str(exc)},
        )
    await db.flush()
    await trigger_manual_sync(
        db, connection_id, user.user.tenant_id, user.user.id,
        phase_filter=["jobs", "applicants", "submissions"],
    )
```

**Auth:** `require_ats_admin` (write).

**Why not run all five phases:** clients + users were already done by the initial sync, are idempotent, but re-syncing burns 30–60s of pacing slots unnecessarily. Explicit skip on the post-filter trigger.

**Cron polls (`ats_tick.py`):** continue to call `poll_ats_connection(...)` with `phase_filter=None` → all five phases. The jobs phase short-circuits if filter is still NULL (banner stays).

### Response models — two additions

```python
class CeipalJobStatusResponse(BaseModel):
    id: int
    name: str


class JobStatusFilterShape(BaseModel):
    ids: list[int]
    names: list[str]


# ConnectionResponse gains:
class ConnectionResponse(BaseModel):
    ...
    job_status_filter: JobStatusFilterShape | None = None

    @classmethod
    def from_row(cls, row):
        return cls(
            ...,
            job_status_filter=(
                JobStatusFilterShape(**row.job_status_filter)
                if row.job_status_filter else None
            ),
        )

# SyncLogResponse gains:
class SyncLogResponse(BaseModel):
    ...
    progress: dict = Field(default_factory=dict)
```

---

## Frontend (`frontend/app/`)

### `lib/api/ats.ts`

```typescript
export interface CeipalJobStatus { id: number; name: string }
export interface JobStatusFilter { ids: number[]; names: string[] }

export interface ATSConnection {
  /* ... existing fields ... */
  job_status_filter: JobStatusFilter | null
}

export interface ATSSyncLog {
  /* ... existing fields ... */
  progress: { jobs?: { processed: number; total: number } }
}

export async function listJobStatuses(
  token: string, connectionId: string,
): Promise<CeipalJobStatus[]>

export async function updateJobStatusFilter(
  token: string, connectionId: string, body: JobStatusFilter,
): Promise<void>
```

### New `components/settings/integrations/JobStatusFilterDialog.tsx`

- Uses `Dialog` from `components/px/`.
- Fetches via `useQuery` keyed `["ats", "connection", id, "job-statuses"]` on `open === true`. Empty cache time so the list re-fetches on reopen.
- Default selection: if `connection.job_status_filter` exists, restore its ids; else preselect any status with `name === "Active"`.
- Multi-select via labeled `<input type="checkbox">` on a new `Checkbox` primitive in `components/px/` (native input + design tokens; exported from `components/px/index.ts`). Adding the primitive is in scope — the existing `px/` library has no checkbox.
- Submit disabled when zero statuses selected.
- On submit: `updateJobStatusFilter` → invalidate `["ats", "connection", id]` and `["ats", "connection", id, "sync-logs"]` → toast "Filter saved. Jobs sync started."
- On 422 with `JOB_STATUS_FILTER_INVALID` → inline error.

### New `components/settings/integrations/SyncProgressBar.tsx`

- Props: `processed`, `total`.
- Hides when `total === 0`.
- Indeterminate (pulsing) state when `total === -1`.
- Renders `processed / total (XX%)` with horizontal bar fill at `processed / total`.

### Modified `app/(dashboard)/settings/integrations/[connectionId]/page.tsx`

- Banner block when `connection.job_status_filter === null`:
  > "Configure which Ceipal job statuses to import before the jobs sync starts."
  >
  > `[Configure jobs filter]` button.
- Auto-open the dialog on initial mount if `job_status_filter === null` and the dialog has not been dismissed this session (use component state, no localStorage).
- "Edit jobs filter" button always available when filter is set.
- `useQuery` for sync-logs sets `refetchInterval: (query) => query.state.data?.some(l => l.status === 'running') ? 2000 : 10000`.
- Render `SyncProgressBar` inside the running row of `SyncLogTable`.

### Modified `SyncLogTable.tsx`

For each row, if `status === 'running'` and `progress.jobs && progress.jobs.total !== 0`, render `<SyncProgressBar processed={progress.jobs.processed} total={progress.jobs.total} />` directly below the row's status badge.

---

## Tests

Each commit ships with its own tests in the same delta.

| Layer | Test file | Cases |
|---|---|---|
| Migration | (manual dev upgrade/downgrade) | n/a |
| Adapter | `tests/modules/ats/adapters/test_ceipal_lists.py` | `list_job_statuses` returns parsed list; raises `ATSVendorContractError` if non-list; `count_jobs` reads `envelope.count`; returns 0 on 404; passes `jobStatus` param when filter set |
| Adapter | `tests/modules/ats/adapters/test_ceipal_paging.py` | `list_jobs` includes `jobStatus` query param when filter set; omits when None |
| Importer | `tests/modules/ats/test_importer_jobs.py` | Filter=NULL → returns early with `errors=["filter_not_configured"]`; filter set → adapter called with `job_status_ids`; progress writes happen N+1 times (1 seed + N rows); cursor not advanced when filter NULL |
| Service | `tests/modules/ats/test_connection_service.py` | Widen → `last_synced_cursors.jobs` dropped; narrow → cursor kept; no-change → cursor kept; empty `status_ids` raises `ValueError`; length mismatch raises `ValueError`; audit row written with correct payload |
| Actor | `tests/modules/ats/test_actors.py` | `phase_filter` arg propagated as set into `sync_tenant`; None on the wire = None into importer; `sync_log_id` propagated |
| Router | `tests/modules/ats/test_router.py` | `GET /job-statuses` returns the adapter list; 422 on creds invalid; `PUT /job-status-filter` calls `update_job_status_filter` + triggers sync with `phase_filter=["jobs","applicants","submissions"]`; 422 on empty status_ids; `POST /connections` triggers with `phase_filter=["clients","users"]`; `ConnectionResponse` exposes filter; `SyncLogResponse` exposes progress |
| Frontend | `tests/lib/api/ats.test.ts` | `listJobStatuses` and `updateJobStatusFilter` wire through `apiFetch` correctly |
| Frontend | `tests/components/JobStatusFilterDialog.test.tsx` | Open → fetches statuses; preselects "Active" on first open; preselects prior filter on edit; submit disabled when zero selected; submit fires `updateJobStatusFilter`; 422 surfaces inline error |
| Frontend | `tests/components/SyncProgressBar.test.tsx` | Renders bar at `processed/total`; hides when `total === 0`; pulses when `total === -1` |

**Negative control where it matters:** Importer's "filter=NULL → skip phase" case. Reintroduce the unconditional iteration and assert the test fails. Catches a future regression that "fixes" the skip.

---

## Commit Sequence

1. `feat(ats): migration 0032 — job_status_filter + sync_log progress columns`
   - Migration file + ORM column additions on `ATSConnection`, `ATSSyncLog`.
2. `feat(ats/ceipal): list_job_statuses + count_jobs + jobStatus filter on list_jobs`
   - Protocol additions + Ceipal impl + adapter tests.
3. `feat(ats/importer): jobs phase reads filter; progress written per-row via second session`
   - `sync_tenant` phase_filter + sync_log_id plumbing; `_sync_jobs` rewrite; `_write_jobs_progress` helper; importer tests.
4. `feat(ats/service): update_job_status_filter with widen-detect cursor reset`
   - Service fn + `trigger_manual_sync` phase_filter kwarg; service tests.
5. `feat(ats/actor): phase_filter + sync_log_id plumbed through poll_ats_connection`
   - Actor signature + call-site updates; actor tests.
6. `feat(ats/router): GET job-statuses, PUT job-status-filter, split initial sync to clients+users`
   - Endpoints + response models + router tests.
7. `feat(frontend/ats): types + API client wrappers for job-status filter and progress`
   - `lib/api/ats.ts` deltas + frontend API tests.
8. `feat(frontend/ats): JobStatusFilterDialog, SyncProgressBar, detail page banner + dialog wiring`
   - New components + detail page edits + `Checkbox` primitive in `px/`; component tests.

---

## Open Questions

None. All four design forks were settled during brainstorming:

- Picker placement → "Background clients+users, then modal."
- Progress transport → "Poll sync-log row, mid-flight updates."
- Update cadence → "Every job (~1 write per 2s)."
- Filter behavior → "Persist on connection; cron reuses; editable from detail page."
- Cursor-on-widen → "Drop jobs cursor on filter change, full re-pull."

## Future Work

- **Status names refresh on edit.** If Ceipal renames a status, our stored `names` array drifts. The "Edit jobs filter" modal already re-fetches live statuses, so the names auto-correct on next save. No separate work needed.
- **Applicants / submissions progress.** Same `_write_progress` pattern, different phase key. Out of scope for this iteration — jobs is the long pole.
- **Cancel a running sync.** Today there's no way to interrupt a 22-min sync. Listed for visibility; tracked separately.
- **Greenhouse / Workday status semantics.** Different vendor model; `list_job_statuses` would return their stage names. Spec-out when adapter lands.
