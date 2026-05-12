# ATS — Job-Status Filter + Sync Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unconditional "sync every Ceipal job" loop with a per-tenant job-status filter, and surface a live progress bar for the long-running jobs phase.

**Architecture:** Eight focused commits (TDD-driven). Migration → adapter → importer → service → actor → router → frontend API → frontend UI. Each layer's tests prove only what that layer owns. Existing importer tests are migrated alongside the importer change because the new adapter contract reaches them.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy async / asyncpg / Alembic / Dramatiq / pytest-asyncio / httpx.MockTransport for adapter tests. Frontend: Next.js 16 / TypeScript strict / React Hook Form + Zod / TanStack Query v5 / `components/px/` design system / Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-12-ats-job-status-filter-and-progress-design.md`

**Test command pattern (run from project root):**
```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/<file> -v
```
Frontend: `cd frontend/app && npm run test -- <file>`

---

## File Map

**Backend — create:**
- `backend/nexus/migrations/versions/0032_ats_job_status_filter_and_progress.py`

**Backend — modify:**
- `backend/nexus/app/modules/ats/models.py` — add `job_status_filter` to `ATSConnection`, `progress` to `ATSSyncLog`
- `backend/nexus/app/modules/ats/adapter.py` — Protocol additions
- `backend/nexus/app/modules/ats/adapters/ceipal.py` — `list_job_statuses`, `count_jobs`, `job_status_ids` on `list_jobs`
- `backend/nexus/app/modules/ats/importer.py` — `ALL_PHASES`, `phase_filter`+`sync_log_id` on `sync_tenant`, `_sync_jobs` rewrite, `_write_jobs_progress`
- `backend/nexus/app/modules/ats/service.py` — `update_job_status_filter`, `trigger_manual_sync` gains `phase_filter` kwarg
- `backend/nexus/app/modules/ats/actors.py` — `phase_filter` arg, plumb through to importer
- `backend/nexus/app/modules/ats/router.py` — new endpoints + response model deltas
- `backend/nexus/tests/modules/ats/conftest.py` — `jobs_fixture` seeds non-NULL filter on connection
- `backend/nexus/tests/modules/ats/test_importer_jobs.py` — adapter helper sets `count_jobs`, list_jobs kwarg
- `backend/nexus/tests/modules/ats/adapters/test_ceipal_lists.py` — new tests
- `backend/nexus/tests/modules/ats/adapters/test_ceipal_paging.py` — new test for filter param
- `backend/nexus/tests/modules/ats/test_connection_service.py` — new tests
- `backend/nexus/tests/modules/ats/test_actors.py` — phase_filter test
- `backend/nexus/tests/modules/ats/test_router.py` — new endpoint tests

**Frontend — create:**
- `frontend/app/components/px/Checkbox.tsx`
- `frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx`
- `frontend/app/components/settings/integrations/SyncProgressBar.tsx`
- `frontend/app/tests/components/JobStatusFilterDialog.test.tsx`
- `frontend/app/tests/components/SyncProgressBar.test.tsx`

**Frontend — modify:**
- `frontend/app/lib/api/ats.ts` — types + helpers
- `frontend/app/tests/lib/api/ats.test.ts` — new helper tests
- `frontend/app/components/px/index.ts` — re-export `Checkbox`
- `frontend/app/components/settings/integrations/SyncLogTable.tsx` — inline progress row
- `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx` — banner, auto-open dialog, tighter poll cadence

---

## Task 1: Migration 0032 + ORM column additions

**Files:**
- Create: `backend/nexus/migrations/versions/0032_ats_job_status_filter_and_progress.py`
- Modify: `backend/nexus/app/modules/ats/models.py`

This task ships migration + ORM together. No standalone test — downstream tasks exercise the columns.

- [ ] **Step 1: Write the migration**

Create `backend/nexus/migrations/versions/0032_ats_job_status_filter_and_progress.py`:

```python
"""ats_job_status_filter_and_progress

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-12

Adds:
  * ats_connections.job_status_filter  (JSONB NULL)
    Persists which Ceipal job statuses to fetch; NULL = not yet configured.
    Importer's jobs phase short-circuits when NULL.
  * ats_sync_logs.progress              (JSONB NOT NULL DEFAULT '{}')
    Mid-flight per-phase counter (e.g. {"jobs": {"processed": 245, "total": 662}}).
    Written by the importer every row; polled by the recruiter UI.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ats_connections",
        sa.Column("job_status_filter", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "ats_sync_logs",
        sa.Column(
            "progress",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ats_sync_logs", "progress")
    op.drop_column("ats_connections", "job_status_filter")
```

- [ ] **Step 2: Extend the ORM**

Modify `backend/nexus/app/modules/ats/models.py`. Inside the `ATSConnection` class, add (after `rate_limit_qps`):

```python
    job_status_filter: Mapped[dict | None] = mapped_column(JSONB)
```

Inside the `ATSSyncLog` class, add (after `error_summary`):

```python
    progress: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
```

- [ ] **Step 3: Apply the migration locally**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
```

Expected: `Running upgrade 0031 -> 0032, ats_job_status_filter_and_progress`. Verify with:

```bash
docker compose -f backend/nexus/docker-compose.yml exec nexus \
  psql "$DATABASE_URL" -c "\d ats_connections" \
  | grep job_status_filter
```

Expected line: `job_status_filter | jsonb | | |`.

- [ ] **Step 4: Verify downgrade works (then re-upgrade)**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic downgrade -1
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
```

Expected: both succeed. Re-upgrade leaves head at 0032.

- [ ] **Step 5: Run the existing test suite to confirm nothing broke**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/ -q
```

Expected: all 78 existing tests pass. The test DB rebuilds via `Base.metadata.create_all` and picks up the new columns automatically (they're nullable / have defaults).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/migrations/versions/0032_ats_job_status_filter_and_progress.py \
        backend/nexus/app/modules/ats/models.py
git commit -m "$(cat <<'EOF'
feat(ats): migration 0032 — job_status_filter + sync_log progress columns

ats_connections.job_status_filter JSONB NULL — persists the recruiter-picked
Ceipal job-status IDs + names. NULL means "not yet configured" — the jobs
phase short-circuits.

ats_sync_logs.progress JSONB NOT NULL DEFAULT '{}' — mid-flight per-phase
counter ({"jobs": {"processed": N, "total": M}}), written every row in the
jobs phase; polled by the recruiter UI for the progress bar.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Ceipal adapter — `list_job_statuses`, `count_jobs`, `jobStatus` filter on `list_jobs`

**Files:**
- Modify: `backend/nexus/app/modules/ats/adapter.py` (Protocol)
- Modify: `backend/nexus/app/modules/ats/adapters/ceipal.py`
- Modify: `backend/nexus/tests/modules/ats/adapters/test_ceipal_lists.py`
- Modify: `backend/nexus/tests/modules/ats/adapters/test_ceipal_paging.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/modules/ats/adapters/test_ceipal_lists.py`:

```python
@pytest.mark.asyncio
async def test_list_job_statuses_returns_parsed_list():
    """GET /getJobStatusList/ returns a bare JSON array (not the paginated
    envelope used by other list endpoints)."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getJobStatusList" in str(request.url)
        return httpx.Response(200, json=[
            {"id": 1, "name": "Active"},
            {"id": 4, "name": "Jobs Filled"},
            {"id": 8, "name": "Reactivated"},
        ])

    a = _adapter(handler)
    out = await a.list_job_statuses()
    assert out == [
        {"id": 1, "name": "Active"},
        {"id": 4, "name": "Jobs Filled"},
        {"id": 8, "name": "Reactivated"},
    ]


@pytest.mark.asyncio
async def test_list_job_statuses_raises_on_non_list_body():
    """A non-list body is a vendor-contract violation (Ceipal docs say array)."""
    from app.modules.ats.errors import ATSVendorContractError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    a = _adapter(handler)
    with pytest.raises(ATSVendorContractError):
        await a.list_job_statuses()


@pytest.mark.asyncio
async def test_count_jobs_reads_envelope_count():
    """count_jobs hits /getJobPostingsList/?limit=1 and returns envelope.count."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "count": 662, "num_pages": 1, "page_number": 1, "limit": 1,
            "next": "", "previous": "",
            "results": [{"id": "first"}],
        })

    a = _adapter(handler)
    total = await a.count_jobs(job_status_ids=[1, 8])
    assert total == 662
    assert captured["params"]["limit"] == "1"
    assert captured["params"]["jobStatus"] == "1,8"


@pytest.mark.asyncio
async def test_count_jobs_returns_zero_on_404():
    """List-endpoint 404 = no rows match the filter (per the adapter's
    existing contract); count_jobs reflects that as 0."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "no rows"})

    a = _adapter(handler)
    assert await a.count_jobs(job_status_ids=[1]) == 0


@pytest.mark.asyncio
async def test_count_jobs_omits_jobStatus_when_no_filter():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "count": 5, "num_pages": 1, "page_number": 1, "limit": 1,
            "next": "", "previous": "", "results": [],
        })

    a = _adapter(handler)
    await a.count_jobs()
    assert "jobStatus" not in captured["params"]
```

Append to `backend/nexus/tests/modules/ats/adapters/test_ceipal_paging.py`:

```python
@pytest.mark.asyncio
async def test_list_jobs_passes_jobStatus_when_filter_set():
    """When job_status_ids is provided, list_jobs forwards a comma-joined
    ``jobStatus`` query param on every page request."""
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getJobPostingsList" in url:
            captured.append(dict(request.url.params))
            return httpx.Response(200, json={
                "count": 0, "num_pages": 1, "page_number": 1, "limit": 50,
                "next": "", "previous": "", "results": [],
            })
        return httpx.Response(404, text="unmocked")

    a = _adapter(handler)
    async for _ in a.list_jobs(job_status_ids=[1, 8]):
        pass
    assert captured, "no list calls made"
    assert captured[0]["jobStatus"] == "1,8"


@pytest.mark.asyncio
async def test_list_jobs_omits_jobStatus_when_filter_none():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "getJobPostingsList" in str(request.url):
            captured.append(dict(request.url.params))
            return httpx.Response(200, json={
                "count": 0, "num_pages": 1, "page_number": 1, "limit": 50,
                "next": "", "previous": "", "results": [],
            })
        return httpx.Response(404, text="unmocked")

    a = _adapter(handler)
    async for _ in a.list_jobs():
        pass
    assert "jobStatus" not in captured[0]
```

Note: `_adapter` already exists in both test files — re-use it as is.

- [ ] **Step 2: Run tests; expect failures**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/adapters/test_ceipal_lists.py \
         tests/modules/ats/adapters/test_ceipal_paging.py -v
```

Expected: all five new tests FAIL — `AttributeError: 'CeipalAdapter' object has no attribute 'list_job_statuses'` (and similar for `count_jobs`), plus the `list_jobs` filter tests fail with `TypeError: unexpected keyword argument 'job_status_ids'`.

- [ ] **Step 3: Implement the adapter methods**

Modify `backend/nexus/app/modules/ats/adapters/ceipal.py`. Add the two new methods (place after `_format_since`, before `list_clients`):

```python
    async def list_job_statuses(self) -> list[dict]:
        """GET /getJobStatusList/

        Returns a bare JSON array, NOT the paginated envelope used by other
        list endpoints. Returned verbatim — caller maps id+name.
        """
        response = await self._request("GET", "/getJobStatusList/")
        body = response.json()
        if not isinstance(body, list):
            raise ATSVendorContractError(
                f"/getJobStatusList/ returned {type(body).__name__}, expected list"
            )
        return body

    async def count_jobs(
        self,
        *,
        since: datetime | None = None,
        job_status_ids: list[int] | None = None,
    ) -> int:
        """GET /getJobPostingsList/?limit=1&jobStatus=...

        Reads ``envelope.count`` from the first-page response. One HTTP call,
        consumes one pacing slot. 404 means "no rows match the filter" → 0.
        """
        params: dict = {"limit": 1, **self._format_since(since)}
        if job_status_ids:
            params["jobStatus"] = ",".join(str(i) for i in job_status_ids)
        response = await self._request("GET", "/getJobPostingsList/", params=params)
        if response.status_code == 404:
            return 0
        envelope = response.json()
        return int(envelope.get("count", 0))
```

Replace the existing `list_jobs` signature + first param-building line:

```python
    async def list_jobs(  # type: ignore[override]
        self,
        since: datetime | None = None,
        *,
        job_status_ids: list[int] | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        now = datetime.now(tz=UTC)
        params: dict = {"limit": 50, **self._format_since(since)}
        if job_status_ids:
            params["jobStatus"] = ",".join(str(i) for i in job_status_ids)
        async for list_raw in self._paginate("/getJobPostingsList/", params):
            # ... existing body unchanged ...
```

(Keep the rest of `list_jobs` exactly as is — only signature + params dict change.)

- [ ] **Step 4: Update the Protocol**

Modify `backend/nexus/app/modules/ats/adapter.py`. Inside `class ATSAdapter(Protocol):`, add (after `ensure_authenticated`):

```python
    async def list_job_statuses(self) -> list[dict]:
        """Vendor-native available job-status list.

        Shape: ``[{"id": int, "name": str}, ...]`` for Ceipal. Adapters that
        do not have a status concept (Greenhouse uses stages; Workday differs
        again) raise ``NotImplementedError``; the router translates that to
        a 501.
        """
        ...

    async def count_jobs(
        self,
        *,
        since: datetime | None = None,
        job_status_ids: list[int] | None = None,
    ) -> int:
        """Total count of jobs matching the filter, used to seed the
        progress bar's denominator. Adapters with no count endpoint return
        ``-1`` — the frontend renders an indeterminate state.
        """
        ...
```

And replace the existing `list_jobs` Protocol stub with:

```python
    def list_jobs(
        self,
        since: datetime | None = None,
        *,
        job_status_ids: list[int] | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        """Yield job postings. ``job_status_ids`` filters server-side where
        the vendor supports it (Ceipal); adapters without server-side filter
        MAY ignore the kwarg and filter client-side.
        """
        ...
```

- [ ] **Step 5: Run tests; expect green**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/adapters/ -v
```

Expected: all adapter tests pass (the original 8+ plus the 5 new ones).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/ats/adapter.py \
        backend/nexus/app/modules/ats/adapters/ceipal.py \
        backend/nexus/tests/modules/ats/adapters/test_ceipal_lists.py \
        backend/nexus/tests/modules/ats/adapters/test_ceipal_paging.py
git commit -m "$(cat <<'EOF'
feat(ats/ceipal): list_job_statuses + count_jobs + jobStatus filter on list_jobs

list_job_statuses() hits /getJobStatusList/ (returns a bare array, not the
paginated envelope), validates the shape, and returns it verbatim.

count_jobs() reads envelope.count from /getJobPostingsList/?limit=1 — one
HTTP call to seed the progress bar's denominator.

list_jobs() gains an optional job_status_ids kwarg, forwarded as a
comma-joined jobStatus query param. Omitted when the filter is None for
backwards-compat with paths that don't yet thread the filter through.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Importer — filter read, `phase_filter`, `sync_log_id`, progress writes

**Files:**
- Modify: `backend/nexus/app/modules/ats/importer.py`
- Modify: `backend/nexus/tests/modules/ats/conftest.py` — seed non-NULL filter on `jobs_fixture`'s connection
- Modify: `backend/nexus/tests/modules/ats/test_importer_jobs.py` — add count_jobs to adapter helper; new tests for skip-on-NULL and progress writes

This task includes a fixture update so all five existing `test_importer_jobs.py` tests continue to pass after the new filter-required path goes in.

- [ ] **Step 1: Update the test fixture to seed a non-NULL filter**

Modify `backend/nexus/tests/modules/ats/conftest.py`. In `jobs_fixture`, after the `ats_client_mappings` INSERT, add an UPDATE on the seeded connection:

```python
    # Seed a non-NULL job_status_filter on the connection so existing tests
    # (which exercise the jobs phase) bypass the filter-not-configured skip.
    # Tests that specifically exercise the skip path NULL this out.
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = :f "
        "WHERE tenant_id = :t AND vendor = 'ceipal'"
    ), {
        "f": '{"ids": [1], "names": ["Active"]}',
        "t": tenant_id,
    })
```

- [ ] **Step 2: Update the `_jobs_adapter` helper in the importer test**

Modify `backend/nexus/tests/modules/ats/test_importer_jobs.py`. Replace the `_jobs_adapter` helper:

```python
def _jobs_adapter(tenant_id, jobs):
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), vendor="ceipal",
        credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"
    # list_jobs now takes job_status_ids kwarg — accept and ignore in tests.
    adapter.list_jobs = lambda since=None, *, job_status_ids=None: _async_iter(jobs)
    # count_jobs seeds the progress denominator — return len(jobs).
    adapter.count_jobs = AsyncMock(return_value=len(jobs))
    return adapter
```

- [ ] **Step 3: Write the failing tests for filter-NULL skip and progress writes**

Append to `backend/nexus/tests/modules/ats/test_importer_jobs.py`:

```python
@pytest.mark.asyncio
async def test_jobs_phase_skipped_when_filter_is_null(db, jobs_fixture):
    """When job_status_filter IS NULL on the connection, _sync_jobs returns
    early with an explicit ``filter_not_configured`` sentinel and writes no
    job rows."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    # NULL the filter back out — the fixture seeds it non-NULL for the
    # other tests in this file.
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = NULL "
        "WHERE tenant_id = :t AND vendor = 'ceipal'"
    ), {"t": tenant_id})
    await db.flush()

    job = ATSJobPayload(
        external_id="j-blocked", external_client_id="complete-client",
        title="x", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)

    assert result.new == 0
    assert result.updated == 0
    assert "filter_not_configured" in result.errors
    # No job_postings row was created
    r = await db.execute(text(
        "SELECT COUNT(*) FROM job_postings WHERE tenant_id = :t"
    ), {"t": tenant_id})
    assert r.scalar_one() == 0


@pytest.mark.asyncio
async def test_jobs_phase_passes_filter_ids_to_adapter(db, jobs_fixture):
    """The connection's stored status IDs are forwarded to adapter.list_jobs
    and adapter.count_jobs."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = :f "
        "WHERE tenant_id = :t AND vendor = 'ceipal'"
    ), {
        "f": '{"ids": [1, 8], "names": ["Active", "Reactivated"]}',
        "t": tenant_id,
    })
    await db.flush()

    captured = {}
    def capturing_list_jobs(since=None, *, job_status_ids=None):
        captured["list_status_ids"] = job_status_ids
        async def _aiter():
            return
            yield  # pragma: no cover
        return _aiter()

    adapter = _jobs_adapter(tenant_id, [])
    adapter.list_jobs = capturing_list_jobs
    adapter.count_jobs = AsyncMock(return_value=0)

    importer = ATSImporter()
    await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert captured["list_status_ids"] == [1, 8]
    adapter.count_jobs.assert_awaited_once()
    assert adapter.count_jobs.call_args.kwargs["job_status_ids"] == [1, 8]


@pytest.mark.asyncio
async def test_jobs_phase_writes_progress_per_row(db, jobs_fixture, monkeypatch):
    """Progress is written once at seed (0/N) and once after every yielded
    row. Verified by counting calls to ``_write_jobs_progress``."""
    from app.modules.ats import importer as importer_mod
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    sync_log_id = uuid.uuid4()
    # Insert a sync_log row so the UPDATE has a target.
    await db.execute(text(
        "INSERT INTO ats_sync_logs (id, tenant_id, connection_id, started_at, "
        "status, correlation_id) "
        "SELECT :s, :t, c.id, now(), 'running', 'test-corr' "
        "FROM ats_connections c WHERE c.tenant_id = :t"
    ), {"s": sync_log_id, "t": tenant_id})
    await db.flush()

    jobs = [
        ATSJobPayload(
            external_id=f"j-{i}",
            external_client_id="complete-client",
            title=f"Job {i}", raw={}, fetched_at=datetime.now(tz=timezone.utc),
        )
        for i in range(3)
    ]
    adapter = _jobs_adapter(tenant_id, jobs)
    adapter.count_jobs = AsyncMock(return_value=3)

    calls = []
    original = ATSImporter._write_jobs_progress
    async def spy(prog_db, log_id, processed, total):
        calls.append((processed, total))
        await original(prog_db, log_id, processed, total)
    monkeypatch.setattr(ATSImporter, "_write_jobs_progress", staticmethod(spy))

    importer = ATSImporter()
    result = await importer._run_phase(
        "jobs", importer._sync_jobs, adapter, sync_log_id,
    )
    assert result.new == 3
    # 1 seed call (0, 3) + 3 per-row calls = 4 total
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]

    # Verify the row's progress JSONB reflects the final state
    r = await db.execute(text(
        "SELECT progress FROM ats_sync_logs WHERE id = :s"
    ), {"s": sync_log_id})
    progress = r.scalar_one()
    assert progress == {"jobs": {"processed": 3, "total": 3}}
```

- [ ] **Step 4: Run tests; expect failures**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_jobs.py -v
```

Expected: the three new tests fail (`AttributeError: type object 'ATSImporter' has no attribute '_write_jobs_progress'` and similar). Existing 5 tests in the file likely fail too because `_sync_jobs` now reads `connection.job_status_filter` from a column that doesn't exist on the legacy code path yet — that's expected; we're about to fix.

- [ ] **Step 5: Rewrite `_sync_jobs` and add helpers in `importer.py`**

Modify `backend/nexus/app/modules/ats/importer.py`. Add `json` to imports near the top:

```python
import json
```

Add a class-level constant inside `class ATSImporter:` (place above `sync_tenant`):

```python
    ALL_PHASES: ClassVar[tuple[str, ...]] = (
        "clients", "users", "jobs", "applicants", "submissions",
    )
```

Add `from typing import ClassVar` to imports near the top.

Replace `sync_tenant` with the new signature:

```python
    async def sync_tenant(
        self,
        adapter: ATSAdapter,
        *,
        phase_filter: set[str] | None = None,
        sync_log_id: UUID | None = None,
    ) -> SyncResult:
        """Run the selected phases. ``phase_filter`` defaults to all five.

        On phase failure, attach the partial result accumulated so far to the
        raised exception via ``partial_result`` so the actor's recovery
        handler can write meaningful entity_counts.
        """
        result = SyncResult()
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
        for name, fn in phases_to_run:
            try:
                phase_result = await self._run_phase(name, fn, adapter, sync_log_id)
            except Exception as exc:
                exc.partial_result = result  # type: ignore[attr-defined]
                raise
            setattr(result, name, phase_result)
        return result
```

Add `from uuid import UUID` to imports (if not already present — check first).

Replace `_run_phase` to pass `sync_log_id` to the phase fn:

```python
    async def _run_phase(self, name, fn, adapter, sync_log_id=None) -> PhaseResult:
        tenant_id = adapter.state.tenant_id
        with tracer.start_as_current_span(f"ats.sync.{name}",
                                          attributes={"ats.vendor": adapter.vendor,
                                                      "tenant_id": str(tenant_id)}):
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                phase_result = await fn(db, adapter, sync_log_id)
                await db.commit()
            adapter.state.last_synced_cursors[name] = phase_result.sync_started_at.isoformat()
            logger.info(
                "ats.sync.phase.ok",
                phase=name, vendor=adapter.vendor,
                tenant_id=str(tenant_id),
                **phase_result.as_counts(),
            )
            return phase_result
```

Update each `_sync_*` signature to accept (and most ignore) `sync_log_id`:

```python
    async def _sync_clients(self, db, adapter, sync_log_id=None) -> PhaseResult:
        # ... body unchanged ...

    async def _sync_users(self, db, adapter, sync_log_id=None) -> PhaseResult:
        # ... body unchanged ...

    async def _sync_applicants(self, db, adapter, sync_log_id=None) -> PhaseResult:
        # ... body unchanged ...

    async def _sync_submissions(self, db, adapter, sync_log_id=None) -> PhaseResult:
        # ... body unchanged ...
```

Replace `_sync_jobs` with the filter-aware + progress-writing implementation:

```python
    async def _sync_jobs(self, db, adapter, sync_log_id=None) -> PhaseResult:
        """Phase 3: upsert job_postings; gated by per-connection job_status_filter.

        If ``job_status_filter`` is NULL or empty on the connection, the phase
        returns immediately with ``errors=["filter_not_configured"]`` — no DB
        writes, no Ceipal calls beyond the filter read. The recruiter UI
        surfaces a banner driven by the same condition.

        Otherwise:
          * ``adapter.count_jobs(...)`` seeds the progress denominator.
          * ``adapter.list_jobs(..., job_status_ids=...)`` streams the rows.
          * After every row, ``_write_jobs_progress`` writes to ats_sync_logs.
            A second bypass-RLS session keeps progress commits independent
            of the main phase transaction.
        """
        from app.modules.ats.models import (
            ATSClientMapping,
            ATSConnection,
            ATSJobRecruiterAssignment,
        )
        from app.modules.jd.models import JobPosting
        from app.modules.org_units.models import OrganizationalUnit

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        if connection is None:
            raise RuntimeError(f"tenant {tenant_id} has no ats_connections row")
        created_by = connection.created_by

        filter_blob = connection.job_status_filter
        if not filter_blob or not filter_blob.get("ids"):
            result.errors.append("filter_not_configured")
            logger.info(
                "ats.sync.jobs.skipped_no_filter",
                connection_id=str(connection.id),
                tenant_id=str(tenant_id),
            )
            return result

        status_ids: list[int] = list(filter_blob["ids"])
        since = self._cursor_or_none(adapter.state, "jobs")

        # Seed the progress denominator. Failures here downgrade to -1, which
        # the frontend renders as "indeterminate" (no division by zero).
        try:
            total = await adapter.count_jobs(since=since, job_status_ids=status_ids)
        except Exception as exc:
            logger.warning("ats.sync.jobs.count_failed", error=str(exc)[:200])
            total = -1

        # Second bypass-RLS session strictly for progress writes. Main `db`
        # keeps its long phase transaction so phase rollback is clean.
        async with get_bypass_session() as prog_db:
            await prog_db.execute(
                text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )
            await self._write_jobs_progress(prog_db, sync_log_id, 0, total)

            processed = 0
            async for payload in adapter.list_jobs(since=since, job_status_ids=status_ids):
                mapping = None
                if payload.external_client_id:
                    mapping = await db.scalar(
                        select(ATSClientMapping).where(
                            ATSClientMapping.tenant_id == tenant_id,
                            ATSClientMapping.ats_vendor == adapter.vendor,
                            ATSClientMapping.external_client_id == payload.external_client_id,
                        )
                    )
                if mapping is None and payload.external_client_name:
                    mapping = await db.scalar(
                        select(ATSClientMapping).where(
                            ATSClientMapping.tenant_id == tenant_id,
                            ATSClientMapping.ats_vendor == adapter.vendor,
                            ATSClientMapping.external_client_name == payload.external_client_name,
                        )
                    )
                if mapping is None:
                    logger.warning(
                        "ats.sync.jobs.skipped_missing_client_mapping",
                        external_job_id=payload.external_id,
                        external_client_id=payload.external_client_id,
                        external_client_name=payload.external_client_name,
                    )
                    result.skipped += 1
                    processed += 1
                    await self._write_jobs_progress(prog_db, sync_log_id, processed, total)
                    continue

                org_unit = await db.get(OrganizationalUnit, mapping.org_unit_id)
                target_status = (
                    "blocked_pending_client_setup"
                    if org_unit.company_profile_completion_status == "pending"
                    else "draft"
                )

                existing = await db.scalar(
                    select(JobPosting).where(
                        JobPosting.tenant_id == tenant_id,
                        JobPosting.source == f"ats_{adapter.vendor}",
                        JobPosting.external_id == payload.external_id,
                    )
                )
                if existing is not None:
                    existing.title = payload.title
                    if payload.description:
                        existing.description_raw = payload.description
                    existing.external_status = payload.status
                    if payload.location:
                        existing.location = payload.location
                    if payload.employment_type:
                        existing.employment_type = payload.employment_type
                    if payload.work_arrangement:
                        existing.work_arrangement = payload.work_arrangement
                    existing.salary_range_min = payload.salary_range_min
                    existing.salary_range_max = payload.salary_range_max
                    existing.salary_currency = payload.salary_currency
                    job_id = existing.id
                    result.updated += 1
                else:
                    jp = JobPosting(
                        tenant_id=tenant_id, org_unit_id=org_unit.id,
                        title=payload.title,
                        description_raw=payload.description or "",
                        status=target_status,
                        source=f"ats_{adapter.vendor}",
                        external_id=payload.external_id,
                        external_status=payload.status,
                        location=payload.location,
                        employment_type=payload.employment_type,
                        work_arrangement=payload.work_arrangement,
                        salary_range_min=payload.salary_range_min,
                        salary_range_max=payload.salary_range_max,
                        salary_currency=payload.salary_currency,
                        created_by=created_by,
                    )
                    db.add(jp)
                    await db.flush()
                    job_id = jp.id
                    await log_event(
                        db, tenant_id=tenant_id, actor_id=created_by,
                        actor_email="ats-import",
                        action="jd.imported_from_ats",
                        resource="job_posting", resource_id=jp.id,
                        payload={"vendor": adapter.vendor,
                                 "external_id": payload.external_id,
                                 "target_status": target_status},
                    )
                    result.new += 1

                # Replace-all recruiter assignments
                await db.execute(
                    text("DELETE FROM ats_job_recruiter_assignments "
                         "WHERE tenant_id = :t AND job_posting_id = :j "
                         "AND ats_vendor = :v"),
                    {"t": tenant_id, "j": job_id, "v": adapter.vendor},
                )
                for rid in payload.assigned_recruiter_external_ids:
                    db.add(ATSJobRecruiterAssignment(
                        tenant_id=tenant_id, job_posting_id=job_id,
                        ats_vendor=adapter.vendor, external_user_id=rid,
                    ))

                processed += 1
                await self._write_jobs_progress(prog_db, sync_log_id, processed, total)
        return result

    @staticmethod
    async def _write_jobs_progress(prog_db, sync_log_id, processed, total):
        """Update ats_sync_logs.progress with the jobs-phase counter.

        No-op when ``sync_log_id`` is None (test paths that don't care about
        progress). Commits immediately so the recruiter UI's poll loop sees
        live data.
        """
        if sync_log_id is None:
            return
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

- [ ] **Step 6: Run tests; expect green**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_importer_jobs.py \
         tests/modules/ats/test_importer_clients_users.py \
         tests/modules/ats/test_importer_applicants_submissions.py -v
```

Expected: all importer tests pass — the 5 existing job tests (fixture provides filter), the new 3 (skip-NULL, status_ids passed, progress written), plus the other importer tests (skipped/no-impact for non-jobs phases).

- [ ] **Step 7: Negative-control check**

To prove the new tests would catch a regression, temporarily comment out the early-return inside `_sync_jobs`:

```python
        # if not filter_blob or not filter_blob.get("ids"):
        #     result.errors.append("filter_not_configured")
        #     ...
        #     return result
```

Re-run `test_jobs_phase_skipped_when_filter_is_null`. Expected: FAIL (the phase no longer short-circuits → assertion `"filter_not_configured" in result.errors` breaks). Restore the early-return; re-run; expect PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/nexus/app/modules/ats/importer.py \
        backend/nexus/tests/modules/ats/conftest.py \
        backend/nexus/tests/modules/ats/test_importer_jobs.py
git commit -m "$(cat <<'EOF'
feat(ats/importer): jobs phase reads filter; progress written per-row

sync_tenant gains phase_filter (set of phase names; None = all five) and
sync_log_id (UUID or None). _run_phase forwards both to the phase fn.

_sync_jobs short-circuits when ats_connections.job_status_filter is NULL
or has no ids — appends "filter_not_configured" to PhaseResult.errors for
visibility in entity_counts. Otherwise calls adapter.count_jobs to seed
the progress denominator, passes status_ids to adapter.list_jobs, and
writes ats_sync_logs.progress every row via a second bypass-RLS session.

The progress session is independent so commit-per-row doesn't fragment
the main phase transaction (which still rolls back cleanly on failure).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Service — `update_job_status_filter` + `trigger_manual_sync(phase_filter=)`

**Files:**
- Modify: `backend/nexus/app/modules/ats/service.py`
- Modify: `backend/nexus/tests/modules/ats/test_connection_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/modules/ats/test_connection_service.py`:

```python
# ---- update_job_status_filter ----

@pytest.fixture
async def connection_for_filter_test(db, basic_tenant):
    """Insert a ats_connections row with NULL job_status_filter and a stale
    jobs cursor so widen-vs-keep can be observed."""
    import json as _json
    tenant_id, user_id = basic_tenant
    conn_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO ats_connections (id, tenant_id, vendor, "
        "credentials_ciphertext, created_by, last_synced_cursors) "
        "VALUES (:c, :t, 'ceipal', :ct, :u, :lc)"
    ), {
        "c": conn_id, "t": tenant_id, "ct": b"x", "u": user_id,
        "lc": _json.dumps({"jobs": "2026-05-10T00:00:00+00:00"}),
    })
    await db.flush()
    return (str(tenant_id), str(user_id), str(conn_id))


@pytest.mark.asyncio
async def test_update_job_status_filter_widen_drops_jobs_cursor(
    db, connection_for_filter_test,
):
    """Widening (any new id) clears last_synced_cursors.jobs so the next
    sync re-pulls from scratch."""
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    # First save: filter from NULL → [1] (Active). NULL→non-empty counts as widen.
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1], names=["Active"],
    )
    r = await db.execute(text(
        "SELECT job_status_filter, last_synced_cursors FROM ats_connections "
        "WHERE id = :c"
    ), {"c": conn_id})
    row = r.one()
    assert row.job_status_filter == {"ids": [1], "names": ["Active"]}
    assert "jobs" not in row.last_synced_cursors


@pytest.mark.asyncio
async def test_update_job_status_filter_narrow_keeps_jobs_cursor(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    # Seed an existing filter [1, 8].
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = :f "
        "WHERE id = :c"
    ), {
        "f": '{"ids": [1, 8], "names": ["Active", "Reactivated"]}',
        "c": conn_id,
    })
    await db.flush()
    # Narrow to [1] — no new ids → cursor stays.
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1], names=["Active"],
    )
    r = await db.execute(text(
        "SELECT last_synced_cursors FROM ats_connections WHERE id = :c"
    ), {"c": conn_id})
    cursors = r.scalar_one()
    assert cursors.get("jobs") == "2026-05-10T00:00:00+00:00"


@pytest.mark.asyncio
async def test_update_job_status_filter_no_change_keeps_cursor(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    await db.execute(text(
        "UPDATE ats_connections SET job_status_filter = :f WHERE id = :c"
    ), {"f": '{"ids": [1], "names": ["Active"]}', "c": conn_id})
    await db.flush()
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1], names=["Active"],
    )
    r = await db.execute(text(
        "SELECT last_synced_cursors FROM ats_connections WHERE id = :c"
    ), {"c": conn_id})
    cursors = r.scalar_one()
    assert cursors.get("jobs") == "2026-05-10T00:00:00+00:00"


@pytest.mark.asyncio
async def test_update_job_status_filter_empty_ids_raises(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    with pytest.raises(ValueError, match="non-empty"):
        await update_job_status_filter(
            db, connection_id=uuid.UUID(conn_id),
            tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
            status_ids=[], names=[],
        )


@pytest.mark.asyncio
async def test_update_job_status_filter_length_mismatch_raises(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    with pytest.raises(ValueError, match="length mismatch"):
        await update_job_status_filter(
            db, connection_id=uuid.UUID(conn_id),
            tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
            status_ids=[1, 8], names=["Active"],
        )


@pytest.mark.asyncio
async def test_update_job_status_filter_writes_audit_row(
    db, connection_for_filter_test,
):
    from app.modules.ats.service import update_job_status_filter

    tenant_id, user_id, conn_id = connection_for_filter_test
    await update_job_status_filter(
        db, connection_id=uuid.UUID(conn_id),
        tenant_id=uuid.UUID(tenant_id), actor_id=uuid.UUID(user_id),
        status_ids=[1, 8], names=["Active", "Reactivated"],
    )
    r = await db.execute(text(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.connection.job_status_filter_updated'"
    ), {"t": tenant_id})
    row = r.one()
    assert row.action == "ats.connection.job_status_filter_updated"
    assert row.payload["new_ids"] == [1, 8]
    assert row.payload["widened"] is True


# ---- trigger_manual_sync phase_filter ----

@pytest.mark.asyncio
async def test_trigger_manual_sync_passes_phase_filter_to_actor(
    db, connection_for_filter_test, monkeypatch,
):
    from app.modules.ats import service as service_mod

    tenant_id, user_id, conn_id = connection_for_filter_test
    captured = {}

    class _FakeActor:
        def send(self, *args):
            captured["args"] = args

    monkeypatch.setattr(
        "app.modules.ats.actors.poll_ats_connection", _FakeActor(),
    )

    await service_mod.trigger_manual_sync(
        db,
        uuid.UUID(conn_id),
        uuid.UUID(tenant_id),
        uuid.UUID(user_id),
        phase_filter=["clients", "users"],
    )
    assert captured["args"][2] == ["clients", "users"]
```

- [ ] **Step 2: Run tests; expect failures**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_connection_service.py -v
```

Expected: all new tests fail (`ImportError: cannot import name 'update_job_status_filter'`, and `TypeError: trigger_manual_sync() got an unexpected keyword argument 'phase_filter'`).

- [ ] **Step 3: Implement `update_job_status_filter`**

Modify `backend/nexus/app/modules/ats/service.py`. Add the new function before the closing of the file (next to `map_ats_user_to_internal`):

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
    """Persist the job-status filter on a connection; drop jobs cursor if widened.

    Widen-detection: any id in ``status_ids`` not present in the prior
    ``job_status_filter.ids`` triggers a reset of
    ``last_synced_cursors.jobs``. Narrowing (only removing ids) keeps the
    cursor — re-pulling previously-included rows would be wasted work.

    Always writes an ``ats.connection.job_status_filter_updated`` audit row.
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

    row.job_status_filter = {"ids": list(status_ids), "names": list(names)}
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

- [ ] **Step 4: Extend `trigger_manual_sync`**

Modify `backend/nexus/app/modules/ats/service.py`. Replace the existing `trigger_manual_sync`:

```python
async def trigger_manual_sync(
    db: AsyncSession,
    connection_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    *,
    phase_filter: list[str] | None = None,
) -> None:
    """Enqueue a poll_ats_connection actor immediately, bypassing next_poll_at.

    ``phase_filter`` — optional explicit list of phase names. Forwarded
    verbatim to the actor; the importer maps it to a set. ``None`` means
    "run all five phases" (the cron default).
    """
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

- [ ] **Step 5: Run tests; expect green**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_connection_service.py -v
```

Expected: all new + existing service tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/ats/service.py \
        backend/nexus/tests/modules/ats/test_connection_service.py
git commit -m "$(cat <<'EOF'
feat(ats/service): update_job_status_filter with widen-detect cursor reset

update_job_status_filter persists the recruiter-picked status ids+names on
ats_connections. Widen detection (any new id) drops last_synced_cursors.jobs
so the next sync re-pulls from scratch — newly-included status rows would
otherwise be missed forever past the modifiedAfter cursor. Narrowing keeps
the cursor (no-op for already-imported data).

trigger_manual_sync now takes an optional phase_filter kwarg forwarded
verbatim to the poll_ats_connection actor. Callers: POST /connections will
trigger {clients,users}; PUT /job-status-filter will trigger
{jobs,applicants,submissions}; cron tick stays None (all five phases).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Actor — `phase_filter` + `sync_log_id` plumbing

**Files:**
- Modify: `backend/nexus/app/modules/ats/actors.py`
- Modify: `backend/nexus/tests/modules/ats/test_actors.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/modules/ats/test_actors.py`:

```python
@pytest.mark.asyncio
async def test_actor_passes_phase_filter_and_sync_log_id_to_importer(
    db, actor_fixture,
):
    """phase_filter (list on the wire) is converted to a set and passed
    to ATSImporter.sync_tenant, along with the sync_log_id created by
    Phase A."""
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()
    fake_adapter.list_clients = lambda since=None: _empty_aiter()
    fake_adapter.list_users   = lambda since=None: _empty_aiter()
    fake_adapter.list_jobs    = lambda since=None, *, job_status_ids=None: _empty_aiter()
    fake_adapter.list_applicants = lambda since=None: _empty_aiter()
    fake_adapter.list_submissions = lambda job_external_id, since=None: _empty_aiter()

    captured = {}
    async def fake_sync_tenant(self, adapter, *, phase_filter=None, sync_log_id=None):
        from app.modules.ats.importer import SyncResult
        captured["phase_filter"] = phase_filter
        captured["sync_log_id"] = sync_log_id
        return SyncResult()

    with patch(
        "app.modules.ats.actors.get_ats_adapter",
        return_value=fake_adapter,
    ), patch.object(
        actors.ATSImporter, "sync_tenant", fake_sync_tenant,
    ):
        await actors._run_poll(
            connection_id, tenant_id, phase_filter=["clients", "users"],
        )

    assert captured["phase_filter"] == {"clients", "users"}
    assert captured["sync_log_id"] is not None  # Phase A created the row


@pytest.mark.asyncio
async def test_actor_default_phase_filter_is_none(db, actor_fixture):
    """Calling _run_poll without phase_filter forwards None to the importer."""
    from app.modules.ats import actors

    tenant_id, connection_id = actor_fixture

    fake_adapter = AsyncMock()
    fake_adapter.vendor = "ceipal"
    fake_adapter.ensure_authenticated = AsyncMock()
    fake_adapter.list_clients = lambda since=None: _empty_aiter()
    fake_adapter.list_users = lambda since=None: _empty_aiter()
    fake_adapter.list_jobs = lambda since=None, *, job_status_ids=None: _empty_aiter()
    fake_adapter.list_applicants = lambda since=None: _empty_aiter()
    fake_adapter.list_submissions = lambda job_external_id, since=None: _empty_aiter()

    captured = {}
    async def fake_sync_tenant(self, adapter, *, phase_filter=None, sync_log_id=None):
        from app.modules.ats.importer import SyncResult
        captured["phase_filter"] = phase_filter
        return SyncResult()

    with patch(
        "app.modules.ats.actors.get_ats_adapter",
        return_value=fake_adapter,
    ), patch.object(
        actors.ATSImporter, "sync_tenant", fake_sync_tenant,
    ):
        await actors._run_poll(connection_id, tenant_id)

    assert captured["phase_filter"] is None
```

You'll also need to import `patch` at the top of the test file if not already there:

```python
from unittest.mock import AsyncMock, patch
```

(Inspect existing imports first — `patch` is probably already imported.)

- [ ] **Step 2: Update the existing actor tests to pass through the new arg**

The existing happy-path / error-path tests in `test_actors.py` call `actors._run_poll(connection_id, tenant_id)` and `actors._do_poll(...)`. After we change the signature to accept `phase_filter`, the existing test calls without the kwarg should still work (default is `None`). No edits needed unless a test mocks at the wrong level — re-run after implementation and patch any breakages.

- [ ] **Step 3: Run tests; expect failures**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_actors.py -v
```

Expected: the two new tests fail (`TypeError: _run_poll() got an unexpected keyword argument 'phase_filter'`).

- [ ] **Step 4: Update the actor signatures**

Modify `backend/nexus/app/modules/ats/actors.py`. Replace the `poll_ats_connection` actor:

```python
@dramatiq.actor(
    max_retries=3,
    min_backoff=30_000,
    max_backoff=600_000,
    queue_name="ats_poll",
)
async def poll_ats_connection(
    connection_id: str,
    tenant_id: str,
    phase_filter: list[str] | None = None,
) -> None:
    """Dramatiq entry point. phase_filter is a JSON list on the wire (or None),
    converted to a set inside the importer.
    """
    await _run_poll(connection_id, tenant_id, phase_filter)
```

Replace `_run_poll`:

```python
async def _run_poll(
    connection_id: str,
    tenant_id: str,
    phase_filter: list[str] | None = None,
) -> None:
    safe_tenant = str(uuid.UUID(tenant_id))
    correlation_id = f"ats-{uuid.uuid4()}"

    structlog.contextvars.bind_contextvars(
        connection_id=connection_id,
        tenant_id=safe_tenant,
        correlation_id=correlation_id,
        queue="ats_poll",
    )

    try:
        with tracer.start_as_current_span(
            "ats.poll",
            attributes={
                "connection_id": connection_id,
                "tenant_id": safe_tenant,
                "phase_filter": ",".join(phase_filter) if phase_filter else "*",
            },
        ):
            await _do_poll(
                uuid.UUID(connection_id),
                uuid.UUID(tenant_id),
                correlation_id,
                safe_tenant,
                phase_filter,
            )
    except ATSConnectionNotFoundError:
        logger.info(
            "ats.poll.connection_gone",
            connection_id=connection_id,
            tenant_id=safe_tenant,
            correlation_id=correlation_id,
        )
        return
    finally:
        structlog.contextvars.clear_contextvars()
```

Replace `_do_poll`'s signature + the line where it calls `sync_tenant`:

```python
async def _do_poll(
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: str,
    safe_tenant: str,
    phase_filter: list[str] | None = None,
) -> None:
    # ---- Phase A: load state + open sync_log ----
    # ... unchanged body up through 'adapter = get_ats_adapter(state)' ...

    # ---- Phase C: run sync ----
    try:
        sync_result = await ATSImporter().sync_tenant(
            adapter,
            phase_filter=set(phase_filter) if phase_filter else None,
            sync_log_id=sync_log_id,
        )
    except ATSRateLimitedError as exc:
        # ... existing handler unchanged ...
```

(Keep the rest of `_do_poll` exactly as is — only the signature line and the `sync_tenant(...)` call change.)

- [ ] **Step 5: Run tests; expect green**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_actors.py -v
```

Expected: all actor tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/ats/actors.py \
        backend/nexus/tests/modules/ats/test_actors.py
git commit -m "$(cat <<'EOF'
feat(ats/actor): phase_filter + sync_log_id plumbed through poll_ats_connection

poll_ats_connection accepts an optional phase_filter list (JSON-serializable
on the Dramatiq wire). _run_poll and _do_poll forward it through to
ATSImporter().sync_tenant, converting list→set at the boundary. The
sync_log_id created in Phase A flows into sync_tenant too, so _sync_jobs
can publish progress mid-flight without round-tripping through state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Router — `GET /job-statuses`, `PUT /job-status-filter`, modified `POST /connections`

**Files:**
- Modify: `backend/nexus/app/modules/ats/router.py`
- Modify: `backend/nexus/tests/modules/ats/test_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/modules/ats/test_router.py`. Re-use the test scaffolding (auth overrides, fake adapter) already established in this file — peek at the existing `test_post_connections_201_on_valid_creds` to see the dependency-override pattern, then add:

```python
@pytest.mark.asyncio
async def test_post_connections_triggers_clients_users_only(db, monkeypatch):
    """POST /connections triggers an initial sync limited to clients + users."""
    # Re-use the same dependency-override pattern as
    # test_post_connections_201_on_valid_creds (see top of this file). The
    # key assertion is on the captured Dramatiq actor args.
    captured = {}

    class _FakeActor:
        def send(self, *args):
            captured["args"] = args

    monkeypatch.setattr(
        "app.modules.ats.actors.poll_ats_connection", _FakeActor(),
    )

    # ... set up tenant/user, override get_tenant_db/require_ats_admin,
    # patch get_ats_adapter with a fake that ensure_authenticates ok,
    # then POST /api/ats/connections with valid Ceipal creds (mirror
    # test_post_connections_201_on_valid_creds).
    #
    # After the POST succeeds, assert:
    assert captured["args"][2] == ["clients", "users"]


@pytest.mark.asyncio
async def test_get_job_statuses_returns_adapter_list(db, monkeypatch):
    """GET /api/ats/connections/{id}/job-statuses returns the live Ceipal list."""
    fake_adapter = AsyncMock()
    fake_adapter.list_job_statuses = AsyncMock(return_value=[
        {"id": 1, "name": "Active"},
        {"id": 4, "name": "Jobs Filled"},
    ])
    fake_adapter.aclose = AsyncMock()

    monkeypatch.setattr(
        "app.modules.ats.router.get_ats_adapter",
        lambda state: fake_adapter,
    )

    # ... arrange auth + seeded connection (as in existing router tests) ...
    # call GET, assert:
    assert response.status_code == 200
    body = response.json()
    assert body == [
        {"id": 1, "name": "Active"},
        {"id": 4, "name": "Jobs Filled"},
    ]


@pytest.mark.asyncio
async def test_get_job_statuses_422_on_credentials_invalid(db, monkeypatch):
    from app.modules.ats.errors import ATSCredentialsInvalidError

    fake_adapter = AsyncMock()
    fake_adapter.list_job_statuses = AsyncMock(
        side_effect=ATSCredentialsInvalidError("revoked"),
    )
    fake_adapter.aclose = AsyncMock()

    monkeypatch.setattr(
        "app.modules.ats.router.get_ats_adapter",
        lambda state: fake_adapter,
    )

    # ... arrange + call GET ...
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ATS_CREDENTIALS_INVALID"


@pytest.mark.asyncio
async def test_put_job_status_filter_persists_and_triggers_sync(db, monkeypatch):
    captured = {}

    class _FakeActor:
        def send(self, *args):
            captured["args"] = args

    monkeypatch.setattr(
        "app.modules.ats.actors.poll_ats_connection", _FakeActor(),
    )

    # ... arrange auth + seeded connection ...
    # call PUT /api/ats/connections/{id}/job-status-filter with
    # {"status_ids": [1, 8], "names": ["Active", "Reactivated"]}
    assert response.status_code == 204

    # Assert filter persisted
    r = await db.execute(text(
        "SELECT job_status_filter FROM ats_connections WHERE id = :c"
    ), {"c": connection_id})
    assert r.scalar_one() == {"ids": [1, 8], "names": ["Active", "Reactivated"]}

    # Assert follow-up sync triggered with the right phases
    assert captured["args"][2] == ["jobs", "applicants", "submissions"]


@pytest.mark.asyncio
async def test_put_job_status_filter_422_on_empty_ids(db):
    # ... arrange + call with {"status_ids": [], "names": []}
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sync_log_response_includes_progress(db):
    # Seed a sync_log row with progress = {"jobs": {"processed": 100, "total": 500}}
    # GET /api/ats/connections/{id}/sync-logs
    # Assert body[0]["progress"] == {"jobs": {"processed": 100, "total": 500}}
    ...


@pytest.mark.asyncio
async def test_connection_response_exposes_job_status_filter(db):
    # Seed a connection with job_status_filter set
    # GET /api/ats/connections/{id}
    # Assert body["job_status_filter"] == {"ids": [...], "names": [...]}
    ...
```

> **Note for the implementer:** the existing `test_router.py` file establishes a verbose dependency-override pattern (see the file's docstring at lines 1-32 and the first existing test). Mirror that pattern exactly when fleshing out the `...` ellipses above — copy the auth override + bearer header setup from the closest existing test. Don't re-invent.

- [ ] **Step 2: Run tests; expect failures**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_router.py -v
```

Expected: new tests fail with 404 on the new endpoints, plus 422 vs 201 mismatches for the modified POST test.

- [ ] **Step 3: Implement the router changes**

Modify `backend/nexus/app/modules/ats/router.py`. Add new imports near the top:

```python
from sqlalchemy import text
from app.database import get_bypass_session
from app.modules.ats.connection import load_connection_state
from app.modules.ats.registry import get_ats_adapter
from app.modules.ats.service import update_job_status_filter
```

(Check existing imports — `get_ats_adapter` and `load_connection_state` may not be imported yet.)

Add new Pydantic models (place near the existing `MapUserRequest`):

```python
class CeipalJobStatusResponse(BaseModel):
    id: int
    name: str


class JobStatusFilterShape(BaseModel):
    ids: list[int]
    names: list[str]


class JobStatusFilterRequest(BaseModel):
    status_ids: list[int] = Field(..., min_length=1)
    names: list[str] = Field(..., min_length=1)
```

Extend `ConnectionResponse`:

```python
class ConnectionResponse(BaseModel):
    id: UUID
    vendor: str
    active: bool
    last_synced_at: str | None = None
    next_poll_at: str | None = None
    last_poll_error: str | None = None
    disabled_reason: str | None = None
    created_at: str
    job_status_filter: JobStatusFilterShape | None = None

    @classmethod
    def from_row(cls, row: ATSConnection) -> ConnectionResponse:
        return cls(
            id=row.id,
            vendor=row.vendor,
            active=row.active,
            last_synced_at=row.last_poll_completed_at.isoformat()
            if row.last_poll_completed_at
            else None,
            next_poll_at=row.next_poll_at.isoformat() if row.next_poll_at else None,
            last_poll_error=row.last_poll_error,
            disabled_reason=row.disabled_reason,
            created_at=row.created_at.isoformat(),
            job_status_filter=(
                JobStatusFilterShape(**row.job_status_filter)
                if row.job_status_filter else None
            ),
        )
```

Extend `SyncLogResponse`:

```python
class SyncLogResponse(BaseModel):
    id: UUID
    started_at: str
    completed_at: str | None = None
    status: str
    entity_counts: dict
    progress: dict = Field(default_factory=dict)
    error_phase: str | None = None
    error_summary: str | None = None
```

Update `list_sync_logs` to include `progress`:

```python
    return [
        SyncLogResponse(
            id=r.id,
            started_at=r.started_at.isoformat(),
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            status=r.status,
            entity_counts=r.entity_counts,
            progress=r.progress or {},
            error_phase=r.error_phase,
            error_summary=r.error_summary,
        )
        for r in rows.scalars()
    ]
```

Update `create_connection_endpoint`. Replace the `trigger_manual_sync` call:

```python
    await trigger_manual_sync(
        db, conn_id, user.user.tenant_id, user.user.id,
        phase_filter=["clients", "users"],
    )
```

Add the new endpoints (place after `manual_sync`):

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
    """Live fetch from the vendor. Not cached server-side; the modal calls
    this on every open. Read-only — no state change, so super_admin is not
    required."""
    row = await db.get(ATSConnection, connection_id)
    if row is None or row.tenant_id != user.user.tenant_id:
        raise HTTPException(status_code=404, detail="ATS_CONNECTION_NOT_FOUND")

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

    return [
        CeipalJobStatusResponse(id=int(s["id"]), name=str(s["name"]))
        for s in raw
    ]


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

- [ ] **Step 4: Run tests; expect green**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/test_router.py -v
```

Expected: all router tests pass.

- [ ] **Step 5: Run the full ATS test suite to confirm nothing regressed**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm -e ENVIRONMENT=test nexus \
  pytest tests/modules/ats/ -q
```

Expected: all ATS tests (78 existing + new from Tasks 2/3/4/5/6) pass.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/ats/router.py \
        backend/nexus/tests/modules/ats/test_router.py
git commit -m "$(cat <<'EOF'
feat(ats/router): GET job-statuses, PUT job-status-filter, split initial sync

POST /api/ats/connections now triggers an initial sync limited to clients
and users — the long-running jobs/applicants/submissions phases wait until
the recruiter has picked a job-status filter.

GET /api/ats/connections/{id}/job-statuses fetches the live status list
from the vendor (no server-side cache). 422 on credentials invalid, 501 on
vendors with no status endpoint.

PUT /api/ats/connections/{id}/job-status-filter persists the picker output
via the service layer and triggers a follow-up sync limited to
{jobs, applicants, submissions} (clients + users were already done).

ConnectionResponse gains job_status_filter; SyncLogResponse gains progress.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend — API types and wrappers

**Files:**
- Modify: `frontend/app/lib/api/ats.ts`
- Modify: `frontend/app/tests/lib/api/ats.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/app/tests/lib/api/ats.test.ts` (peek at existing tests for the `apiFetch` mock pattern — re-use the same setup):

```typescript
import {
  listJobStatuses,
  updateJobStatusFilter,
  type CeipalJobStatus,
} from "@/lib/api/ats"

describe("listJobStatuses", () => {
  it("GETs /api/ats/connections/{id}/job-statuses and returns the array", async () => {
    const mock: CeipalJobStatus[] = [
      { id: 1, name: "Active" },
      { id: 4, name: "Jobs Filled" },
    ]
    // mock apiFetch — re-use the existing helper pattern in this file
    mockApiFetchOnce(mock)

    const out = await listJobStatuses("tok", "conn-123")
    expect(out).toEqual(mock)
    expect(lastApiFetchPath()).toBe("/api/ats/connections/conn-123/job-statuses")
  })
})

describe("updateJobStatusFilter", () => {
  it("PUTs to /api/ats/connections/{id}/job-status-filter with the body", async () => {
    mockApiFetchOnce(undefined)
    await updateJobStatusFilter("tok", "conn-123", {
      ids: [1, 8],
      names: ["Active", "Reactivated"],
    })
    expect(lastApiFetchPath()).toBe("/api/ats/connections/conn-123/job-status-filter")
    expect(lastApiFetchMethod()).toBe("PUT")
    expect(JSON.parse(lastApiFetchBody() ?? "")).toEqual({
      status_ids: [1, 8],
      names: ["Active", "Reactivated"],
    })
  })
})
```

(The `mockApiFetchOnce`, `lastApiFetchPath`, etc. helpers should already exist in the test file's setup — re-use; if they don't, follow the existing per-test `vi.mock` setup that calls `mockResolvedValueOnce` on `apiFetch`.)

- [ ] **Step 2: Run tests; expect failures**

```bash
cd frontend/app && npm run test -- tests/lib/api/ats.test.ts
```

Expected: imports fail (`listJobStatuses` and `updateJobStatusFilter` don't exist).

- [ ] **Step 3: Implement the types and wrappers**

Modify `frontend/app/lib/api/ats.ts`. Add new types near the existing interfaces:

```typescript
export interface CeipalJobStatus {
  id: number
  name: string
}

export interface JobStatusFilter {
  ids: number[]
  names: string[]
}
```

Extend `ATSConnection`:

```typescript
export interface ATSConnection {
  id: string
  vendor: string
  active: boolean
  last_synced_at: string | null
  next_poll_at: string | null
  last_poll_error: string | null
  disabled_reason: string | null
  created_at: string
  job_status_filter: JobStatusFilter | null
}
```

Extend `ATSSyncLog`:

```typescript
export interface ATSSyncLog {
  id: string
  started_at: string
  completed_at: string | null
  status: ATSSyncStatus
  entity_counts: Record<string, Record<string, number>>
  progress: { jobs?: { processed: number; total: number } }
  error_phase: string | null
  error_summary: string | null
}
```

Add the two new wrappers near the existing `apiFetch` exports:

```typescript
export async function listJobStatuses(
  token: string,
  connectionId: string,
): Promise<CeipalJobStatus[]> {
  return apiFetch<CeipalJobStatus[]>(
    `/api/ats/connections/${connectionId}/job-statuses`,
    { token },
  )
}

export async function updateJobStatusFilter(
  token: string,
  connectionId: string,
  body: JobStatusFilter,
): Promise<void> {
  await apiFetch<void>(
    `/api/ats/connections/${connectionId}/job-status-filter`,
    {
      token,
      method: "PUT",
      body: JSON.stringify({
        status_ids: body.ids,
        names: body.names,
      }),
    },
  )
}
```

- [ ] **Step 4: Run tests; expect green**

```bash
cd frontend/app && npm run test -- tests/lib/api/ats.test.ts
```

Expected: tests pass.

- [ ] **Step 5: Run type-check and lint**

```bash
cd frontend/app && npm run type-check && npm run lint
```

Expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/api/ats.ts frontend/app/tests/lib/api/ats.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend/ats): types + API client wrappers for job-status filter and progress

ATSConnection.job_status_filter and ATSSyncLog.progress mirror the backend
response shapes added in the corresponding router commit. listJobStatuses
and updateJobStatusFilter wrap the two new endpoints; the PUT body
intentionally renames {ids,names} -> {status_ids,names} to match the
backend Pydantic shape.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Frontend UI — `Checkbox`, `JobStatusFilterDialog`, `SyncProgressBar`, detail-page wiring

**Files:**
- Create: `frontend/app/components/px/Checkbox.tsx`
- Modify: `frontend/app/components/px/index.ts`
- Create: `frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx`
- Create: `frontend/app/components/settings/integrations/SyncProgressBar.tsx`
- Modify: `frontend/app/components/settings/integrations/SyncLogTable.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx`
- Create: `frontend/app/tests/components/JobStatusFilterDialog.test.tsx`
- Create: `frontend/app/tests/components/SyncProgressBar.test.tsx`

- [ ] **Step 1: Build the `Checkbox` primitive (test-first)**

Create `frontend/app/tests/components/Checkbox.test.tsx` first if you want symmetry — but `Checkbox` is a thin wrapper; we'll lean on the integration test in `JobStatusFilterDialog.test.tsx` to cover it. Skip the unit test for `Checkbox`.

Create `frontend/app/components/px/Checkbox.tsx`:

```typescript
"use client";

import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type CheckboxProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  // The native checkbox + label sit on one line; design tokens drive the
  // visuals so this stays consistent with the rest of the px library.
};

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  function Checkbox({ label, className, id, ...props }, ref) {
    const inputId = id ?? `cb-${label.replace(/\s+/g, "-").toLowerCase()}`;
    return (
      <label
        htmlFor={inputId}
        className={cn(
          "flex items-center gap-2 cursor-pointer text-sm",
          className,
        )}
        style={{ color: "var(--px-fg)" }}
      >
        <input
          ref={ref}
          type="checkbox"
          id={inputId}
          className="size-4 accent-current"
          style={{ accentColor: "var(--px-accent)" }}
          {...props}
        />
        <span>{label}</span>
      </label>
    );
  },
);
```

Modify `frontend/app/components/px/index.ts`. Add the re-export:

```typescript
export { Checkbox } from "./Checkbox";
```

- [ ] **Step 2: Write the failing test for `SyncProgressBar`**

Create `frontend/app/tests/components/SyncProgressBar.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SyncProgressBar } from "@/components/settings/integrations/SyncProgressBar";

describe("SyncProgressBar", () => {
  it("renders processed / total with percentage", () => {
    render(<SyncProgressBar processed={245} total={662} />);
    expect(screen.getByText(/245 \/ 662/)).toBeInTheDocument();
    // 245/662 ≈ 37%
    expect(screen.getByText(/37%/)).toBeInTheDocument();
  });

  it("renders nothing when total is 0", () => {
    const { container } = render(<SyncProgressBar processed={0} total={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders indeterminate when total is -1", () => {
    render(<SyncProgressBar processed={0} total={-1} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-busy", "true");
  });

  it("caps fill at 100% when processed > total (defensive)", () => {
    render(<SyncProgressBar processed={700} total={662} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.style.getPropertyValue("--fill")).toBe("100%");
  });
});
```

- [ ] **Step 3: Run; expect failures**

```bash
cd frontend/app && npm run test -- tests/components/SyncProgressBar.test.tsx
```

Expected: module-not-found.

- [ ] **Step 4: Implement `SyncProgressBar`**

Create `frontend/app/components/settings/integrations/SyncProgressBar.tsx`:

```typescript
"use client";

type Props = {
  processed: number;
  total: number;
};

export function SyncProgressBar({ processed, total }: Props) {
  if (total === 0) return null;

  const indeterminate = total < 0;
  const pct = indeterminate
    ? 0
    : Math.min(100, Math.round((processed / total) * 100));

  return (
    <div className="space-y-1">
      <div
        role="progressbar"
        aria-busy={indeterminate}
        aria-valuemin={0}
        aria-valuemax={indeterminate ? undefined : total}
        aria-valuenow={indeterminate ? undefined : processed}
        className="relative h-2 w-full overflow-hidden rounded-full"
        style={
          {
            background: "color-mix(in oklab, var(--px-fg) 8%, transparent)",
            "--fill": indeterminate ? "30%" : `${pct}%`,
          } as React.CSSProperties
        }
      >
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] ${
            indeterminate ? "animate-pulse" : ""
          }`}
          style={{
            width: "var(--fill)",
            background: "var(--px-accent)",
          }}
        />
      </div>
      <div className="text-xs text-zinc-500">
        {indeterminate ? (
          <span>Counting jobs…</span>
        ) : (
          <span>
            {processed} / {total} ({pct}%)
          </span>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run; expect green**

```bash
cd frontend/app && npm run test -- tests/components/SyncProgressBar.test.tsx
```

Expected: all 4 tests pass.

- [ ] **Step 6: Write the failing test for `JobStatusFilterDialog`**

Create `frontend/app/tests/components/JobStatusFilterDialog.test.tsx`:

```typescript
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobStatusFilterDialog } from "@/components/settings/integrations/JobStatusFilterDialog";

vi.mock("@/lib/auth/tokens", () => ({
  getFreshSupabaseToken: vi.fn(async () => "tok"),
}));

const listJobStatusesMock = vi.fn();
const updateJobStatusFilterMock = vi.fn();
vi.mock("@/lib/api/ats", async () => {
  const actual: typeof import("@/lib/api/ats") = await vi.importActual(
    "@/lib/api/ats",
  );
  return {
    ...actual,
    listJobStatuses: (token: string, id: string) =>
      listJobStatusesMock(token, id),
    updateJobStatusFilter: (token: string, id: string, body: unknown) =>
      updateJobStatusFilterMock(token, id, body),
  };
});

function renderWithProviders(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("JobStatusFilterDialog", () => {
  beforeEach(() => {
    listJobStatusesMock.mockReset();
    updateJobStatusFilterMock.mockReset();
  });

  it("fetches statuses on open and preselects 'Active' when no prior filter", async () => {
    listJobStatusesMock.mockResolvedValue([
      { id: 1, name: "Active" },
      { id: 4, name: "Jobs Filled" },
    ]);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={null}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Active")).toBeChecked();
    });
    expect(screen.getByLabelText("Jobs Filled")).not.toBeChecked();
  });

  it("preselects prior filter ids on edit", async () => {
    listJobStatusesMock.mockResolvedValue([
      { id: 1, name: "Active" },
      { id: 4, name: "Jobs Filled" },
      { id: 8, name: "Reactivated" },
    ]);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={{ ids: [4, 8], names: ["Jobs Filled", "Reactivated"] }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Jobs Filled")).toBeChecked();
    });
    expect(screen.getByLabelText("Active")).not.toBeChecked();
    expect(screen.getByLabelText("Reactivated")).toBeChecked();
  });

  it("disables save when zero statuses are selected", async () => {
    listJobStatusesMock.mockResolvedValue([{ id: 1, name: "Active" }]);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={null}
      />,
    );

    await waitFor(() => screen.getByLabelText("Active"));
    fireEvent.click(screen.getByLabelText("Active")); // uncheck the autopick

    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("calls updateJobStatusFilter on save", async () => {
    listJobStatusesMock.mockResolvedValue([
      { id: 1, name: "Active" },
      { id: 8, name: "Reactivated" },
    ]);
    updateJobStatusFilterMock.mockResolvedValue(undefined);

    renderWithProviders(
      <JobStatusFilterDialog
        open
        onClose={() => {}}
        connectionId="conn-1"
        priorFilter={null}
      />,
    );

    await waitFor(() => screen.getByLabelText("Reactivated"));
    fireEvent.click(screen.getByLabelText("Reactivated"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(updateJobStatusFilterMock).toHaveBeenCalledWith("tok", "conn-1", {
        ids: [1, 8],
        names: ["Active", "Reactivated"],
      });
    });
  });
});
```

- [ ] **Step 7: Run; expect failures**

```bash
cd frontend/app && npm run test -- tests/components/JobStatusFilterDialog.test.tsx
```

Expected: module-not-found.

- [ ] **Step 8: Implement `JobStatusFilterDialog`**

Create `frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx`:

```typescript
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Skeleton,
} from "@/components/px";
import {
  listJobStatuses,
  updateJobStatusFilter,
  type CeipalJobStatus,
  type JobStatusFilter,
} from "@/lib/api/ats";
import { ApiError } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

type Props = {
  open: boolean;
  onClose: () => void;
  connectionId: string;
  priorFilter: JobStatusFilter | null;
};

export function JobStatusFilterDialog({
  open,
  onClose,
  connectionId,
  priorFilter,
}: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [serverError, setServerError] = useState<string | null>(null);

  const statuses = useQuery<CeipalJobStatus[]>({
    queryKey: ["ats", "connection", connectionId, "job-statuses"],
    queryFn: async () =>
      listJobStatuses(await getFreshSupabaseToken(), connectionId),
    enabled: open,
    staleTime: 0,
  });

  // Initialize selection when statuses load. Restore prior filter if present;
  // otherwise auto-pick "Active" (id matched by name).
  useEffect(() => {
    if (!statuses.data) return;
    if (priorFilter) {
      setSelected(new Set(priorFilter.ids));
      return;
    }
    const active = statuses.data.find((s) => s.name === "Active");
    setSelected(new Set(active ? [active.id] : []));
  }, [statuses.data, priorFilter]);

  const mutation = useMutation({
    mutationFn: async (body: JobStatusFilter) =>
      updateJobStatusFilter(await getFreshSupabaseToken(), connectionId, body),
    onSuccess: () => {
      toast.success("Filter saved. Jobs sync started.");
      qc.invalidateQueries({ queryKey: ["ats", "connection", connectionId] });
      qc.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "sync-logs"],
      });
      onClose();
    },
    onError: (err) => {
      if (
        err instanceof ApiError &&
        err.status === 422 &&
        err.code === "JOB_STATUS_FILTER_INVALID"
      ) {
        setServerError("Filter rejected by the server. Pick at least one.");
        return;
      }
      toast.error("Could not save filter. Please try again.");
    },
  });

  const orderedSelection = useMemo(() => {
    if (!statuses.data) return { ids: [] as number[], names: [] as string[] };
    const ids: number[] = [];
    const names: string[] = [];
    for (const s of statuses.data) {
      if (selected.has(s.id)) {
        ids.push(s.id);
        names.push(s.name);
      }
    }
    return { ids, names };
  }, [selected, statuses.data]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setServerError(null);
  };

  const onSubmit = () => {
    if (orderedSelection.ids.length === 0) return;
    mutation.mutate(orderedSelection);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Which job statuses should ProjectX import?</DialogTitle>
          <DialogDescription>
            Ceipal lets you pre-filter by status. Pick the ones worth syncing —
            inactive statuses cost up to 22 minutes per full sync.
          </DialogDescription>
        </DialogHeader>

        {statuses.isLoading && <Skeleton className="h-24 w-full" />}
        {statuses.isError && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            Could not load statuses from Ceipal. Check that the credentials are
            still valid.
          </p>
        )}
        {statuses.data && (
          <div className="space-y-2 py-2">
            {statuses.data.map((s) => (
              <Checkbox
                key={s.id}
                id={`status-${s.id}`}
                label={s.name}
                checked={selected.has(s.id)}
                onChange={() => toggle(s.id)}
              />
            ))}
          </div>
        )}

        {serverError && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            {serverError}
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={
              statuses.isLoading ||
              orderedSelection.ids.length === 0 ||
              mutation.isPending
            }
          >
            {mutation.isPending ? "Saving…" : "Save & start sync"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

> **Implementation note:** The exact named exports from `@/components/px` for `Dialog` parts may differ slightly (the existing `px/Dialog.tsx` exports `Dialog`, `DialogContent`, etc — verify by inspecting the file before this step). Adjust the imports to match the actual barrel.

- [ ] **Step 9: Run dialog tests; expect green**

```bash
cd frontend/app && npm run test -- tests/components/JobStatusFilterDialog.test.tsx
```

Expected: all four tests pass.

- [ ] **Step 10: Wire the dialog and progress bar into the detail page**

Modify `frontend/app/components/settings/integrations/SyncLogTable.tsx`. Add a render of the progress bar below the status badge cell for running rows:

```typescript
// Add to imports
import { SyncProgressBar } from "./SyncProgressBar";

// Inside the <tr> body, replace the Status <td> with:
              <td className="px-3 py-2">
                <div className="space-y-1">
                  <Badge variant={STATUS_VARIANT[log.status]}>{log.status}</Badge>
                  {log.status === "running" &&
                    log.progress?.jobs &&
                    log.progress.jobs.total !== 0 && (
                      <SyncProgressBar
                        processed={log.progress.jobs.processed}
                        total={log.progress.jobs.total}
                      />
                    )}
                </div>
              </td>
```

Modify `frontend/app/app/(dashboard)/settings/integrations/[connectionId]/page.tsx`. Add imports and dialog state:

```typescript
import { JobStatusFilterDialog } from "@/components/settings/integrations/JobStatusFilterDialog";
```

Inside the component (after the existing `useState` for `confirmDelete`):

```typescript
  const [filterDialogOpen, setFilterDialogOpen] = useState(false);
  const [dialogAutoOpened, setDialogAutoOpened] = useState(false);

  // Auto-open the dialog once per mount when the filter is null.
  useEffect(() => {
    if (
      !dialogAutoOpened &&
      connection.data &&
      connection.data.job_status_filter === null
    ) {
      setFilterDialogOpen(true);
      setDialogAutoOpened(true);
    }
  }, [connection.data, dialogAutoOpened]);
```

Add `useEffect` to the React import.

Modify `syncLogs` to poll faster when something is running:

```typescript
  const syncLogs = useQuery<ATSSyncLog[]>({
    queryKey: ["ats", "connection", connectionId, "sync-logs"],
    queryFn: async () =>
      listSyncLogs(await getFreshSupabaseToken(), connectionId),
    refetchInterval: (query) =>
      query.state.data?.some((l) => l.status === "running") ? 2000 : 10000,
  });
```

After the existing "Recent syncs" header section, add the banner + button block. Place inside the main `<div className="mx-auto max-w-[1400px] space-y-6 ...">` block, above the action-button row:

```tsx
      {c.job_status_filter === null && (
        <div
          className="rounded-[10px] border p-4 text-sm"
          style={{
            background: "color-mix(in oklab, var(--px-warning) 8%, transparent)",
            borderColor: "var(--px-warning)",
            color: "var(--px-fg)",
          }}
        >
          <p className="font-medium">
            Configure which Ceipal job statuses to import.
          </p>
          <p className="mt-1 text-zinc-600">
            The jobs sync is paused until you pick at least one status.
          </p>
          <Button
            className="mt-3"
            onClick={() => setFilterDialogOpen(true)}
          >
            Configure jobs filter
          </Button>
        </div>
      )}
```

Add an "Edit jobs filter" button into the existing action-button row (next to "Sync now"):

```tsx
        <Button variant="outline" onClick={() => setFilterDialogOpen(true)}>
          {c.job_status_filter ? "Edit jobs filter" : "Configure jobs filter"}
        </Button>
```

Render the dialog at the end of the return block (next to `DangerConfirmDialog`):

```tsx
      <JobStatusFilterDialog
        open={filterDialogOpen}
        onClose={() => setFilterDialogOpen(false)}
        connectionId={connectionId}
        priorFilter={c.job_status_filter}
      />
```

- [ ] **Step 11: Manual smoke test in the browser**

```bash
# Terminal 1 (backend)
cd backend/nexus && docker compose up --build
# Terminal 2 (frontend)
cd frontend/app && npm run dev
```

Steps in the browser:
1. Log in to the recruiter dashboard.
2. Go to `/settings/integrations`. Click the existing Ceipal connection.
3. Expect: banner "Configure which Ceipal job statuses to import" + auto-opened dialog. Dialog shows the live status list with "Active" preselected.
4. Pick `Active` + `Reactivated`. Click "Save & start sync".
5. Expect: toast "Filter saved. Jobs sync started." Dialog closes. Banner disappears. A new sync log row appears with status='running' and a progress bar.
6. Reload the page mid-sync; the progress bar continues to advance (2s poll cadence).
7. Click "Edit jobs filter". Dialog reopens with `Active` and `Reactivated` checked.

Capture any UI bugs and fix in-place before committing.

- [ ] **Step 12: Run full frontend test suite + type-check + lint**

```bash
cd frontend/app && npm run test && npm run type-check && npm run lint
```

Expected: all pass. (The existing dashboard tests should remain green; new tests pass.)

- [ ] **Step 13: Commit**

```bash
git add frontend/app/components/px/Checkbox.tsx \
        frontend/app/components/px/index.ts \
        frontend/app/components/settings/integrations/JobStatusFilterDialog.tsx \
        frontend/app/components/settings/integrations/SyncProgressBar.tsx \
        frontend/app/components/settings/integrations/SyncLogTable.tsx \
        frontend/app/app/\(dashboard\)/settings/integrations/\[connectionId\]/page.tsx \
        frontend/app/tests/components/JobStatusFilterDialog.test.tsx \
        frontend/app/tests/components/SyncProgressBar.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend/ats): JobStatusFilterDialog, SyncProgressBar, detail page wiring

JobStatusFilterDialog fetches live Ceipal statuses on open via TanStack
Query; preselects "Active" on first run or the prior filter on edit;
save is disabled until at least one status is picked. New Checkbox
primitive added to components/px/ (the library had no native checkbox).

SyncProgressBar reads ats_sync_logs.progress.jobs and renders processed /
total with percentage; falls back to an indeterminate pulsing state when
total is -1 (count_jobs failed).

Detail page: auto-opens the dialog when job_status_filter is NULL; banner
explains the paused state; "Edit jobs filter" button always available when
the filter is set. Sync log poll cadence tightens to 2s while any row is
running so the progress bar feels live.

SyncLogTable renders the progress bar inline within the running row's
status cell.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Migration 0032 (two columns) → Task 1. ✓
- Adapter `list_job_statuses` / `count_jobs` / `list_jobs(job_status_ids=)` → Task 2. ✓
- Importer `sync_tenant(phase_filter, sync_log_id)`, `ALL_PHASES`, `_sync_jobs` rewrite, `_write_jobs_progress` → Task 3. ✓
- Service `update_job_status_filter` with widen detection + `trigger_manual_sync(phase_filter=)` → Task 4. ✓
- Actor `poll_ats_connection(phase_filter)` plumbed through → Task 5. ✓
- Router endpoints (GET job-statuses, PUT job-status-filter, modified POST) + ConnectionResponse/SyncLogResponse fields → Task 6. ✓
- Frontend types + API wrappers → Task 7. ✓
- Frontend components + detail page wiring + Checkbox primitive → Task 8. ✓
- Tests: every layer ships its own tests in the same commit. ✓
- Negative-control demonstration in Task 3 (importer skip-on-NULL). ✓

**Placeholder scan:**
- Task 6 step 1 has explicit `...` ellipses inside the new router test bodies — they reference the existing `test_router.py` scaffolding rather than re-writing the verbose auth-override boilerplate. Flagged inline with an implementer note. This is the one acceptable structural reference (the scaffolding spans ~50 lines and re-pasting it would obscure the new behavior). All other `...` instances elsewhere are inside Python triple-dots showing "unchanged code" — flagged with explicit `# ... existing body unchanged ...` comments.

**Type consistency:**
- `JobStatusFilter` shape: `{ids: number[], names: string[]}` — consistent in Task 4 service (`status_ids` arg, `names` arg, stored as `{"ids": ..., "names": ...}` in DB), Task 6 router (Pydantic `JobStatusFilterShape` mirrors), Task 7 frontend type. ✓
- `JobStatusFilterRequest` wire body: `{status_ids, names}` — Task 6 router defines, Task 7 frontend wrapper does the `ids → status_ids` rename in the PUT body. ✓
- `progress` shape: `{"jobs": {"processed": int, "total": int}}` — consistent across migration, importer write, router response, frontend type, SyncProgressBar component. ✓
- `phase_filter` shape: `list[str] | None` on the wire (Dramatiq-serializable), `set[str] | None` inside `sync_tenant`. Mentioned explicitly in actor commit. ✓
- `_write_jobs_progress` signature: `(prog_db, sync_log_id, processed, total)` — consistent between definition (Task 3 step 5) and test spy (Task 3 step 3). ✓
- `_sync_jobs` signature: `(self, db, adapter, sync_log_id=None)` — consistent between definition (Task 3 step 5) and test invocation via `_run_phase` (Task 3 step 3). ✓

No drift detected.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-12-ats-job-status-filter-and-progress.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

Which approach?
