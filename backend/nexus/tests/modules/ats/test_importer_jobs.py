"""Phase 3 (jobs):
  - Resolve external_client_id → org_unit via ats_client_mappings.
  - If org_unit.completion_status='pending': status='blocked_pending_client_setup'.
  - If 'complete': status='draft' (caller enqueues extract_and_enhance_jd).
  - assigned_recruiter_external_ids → ats_job_recruiter_assignments rows.
  - Missing client mapping → insert with org_unit_id=NULL +
    status='blocked_pending_client_setup' (counted as `new`). Frontend
    surfaces these on /jobs with a 'Not set up' chip until a recruiter
    links them to an org_unit.

Test-environment choice: Option (ii) — the ``importer_fixture`` (in
``conftest.py``) monkeypatches ``get_bypass_session`` so the importer's
internally-opened session is the rollback-isolated ``db`` fixture. All seed
and verify queries go through the test ``db`` (NOT async_session_factory).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import ATSJobPayload


def _async_iter(items):
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


def _jobs_adapter(tenant_id, jobs):
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), vendor="ceipal",
        credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"

    # The real CeipalAdapter filters by ``skip_external_ids`` before its
    # per-job details fetch; the mock has to mirror that contract or the
    # importer's Pass-2 missing-detect loop will see jobs it already
    # upserted in Pass 1 and double-count them.
    def list_jobs_fn(
        since=None,
        *,
        job_status_ids=None,
        skip_external_ids=None,
    ):
        async def _aiter():
            for job in jobs:
                if skip_external_ids and job.external_id in skip_external_ids:
                    continue
                yield job
        return _aiter()

    adapter.list_jobs = list_jobs_fn
    # count_jobs seeds the progress denominator. The importer calls it
    # twice now (once with cursor, once without) — same return value for
    # both is fine for the existing tests, which all start from a clean
    # local DB so the missing-detect arithmetic resolves correctly.
    adapter.count_jobs = AsyncMock(return_value=len(jobs))
    return adapter


@pytest.mark.asyncio
async def test_job_for_complete_client_lands_in_draft(db, jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, _root_unit_id, _pending_unit, complete_unit = jobs_fixture
    job = ATSJobPayload(
        external_id="jid", external_client_id="complete-client",
        title="Java Engineer", description="JD body",
        status="Active", raw={}, fetched_at=datetime.now(tz=timezone.utc),
        assigned_recruiter_external_ids=["rid-1"],
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.new == 1
    assert result.updated == 0
    assert result.skipped == 0

    row = await db.execute(text(
        "SELECT status, org_unit_id::text AS org_unit_id, external_status, "
        "source, external_id "
        "FROM job_postings WHERE tenant_id = :t"
    ), {"t": tenant_id})
    r = row.one()
    assert r.status == "draft"
    assert r.org_unit_id == complete_unit
    assert r.external_status == "Active"
    assert r.source == "ats_ceipal"
    assert r.external_id == "jid"

    recruiters = await db.execute(text(
        "SELECT external_user_id FROM ats_job_recruiter_assignments "
        "WHERE tenant_id = :t"
    ), {"t": tenant_id})
    assert {rr.external_user_id for rr in recruiters} == {"rid-1"}


@pytest.mark.asyncio
async def test_job_for_pending_client_lands_in_blocked_state(db, jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, _root_unit_id, _pending_unit, _complete_unit = jobs_fixture
    job = ATSJobPayload(
        external_id="j2", external_client_id="pending-client",
        title="x", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    await importer._run_phase("jobs", importer._sync_jobs, adapter)

    r = await db.execute(text(
        "SELECT status FROM job_postings "
        "WHERE tenant_id = :t AND external_id = 'j2'"
    ), {"t": tenant_id})
    assert r.scalar_one() == "blocked_pending_client_setup"


@pytest.mark.asyncio
async def test_job_with_unknown_client_mapping_is_imported_unlinked(db, jobs_fixture):
    """No matching mapping AND no external_client_name → insert the
    job_posting with org_unit_id=NULL + status='blocked_pending_client_setup'.

    The 'Not set up' chip on /jobs renders for this case. A job with a
    name but no matching mapping takes the stub-creation path instead
    (covered by test_sync_jobs_creates_stub_for_unknown_client_name)."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j3", external_client_id="not-yet-imported-client",
        title="x", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.new == 1
    assert result.skipped == 0

    row = await db.execute(text(
        "SELECT status, org_unit_id, source, external_id "
        "FROM job_postings WHERE tenant_id = :t AND external_id = 'j3'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.status == "blocked_pending_client_setup"
    assert r.org_unit_id is None
    assert r.source == "ats_ceipal"


@pytest.mark.asyncio
async def test_job_links_by_client_name_when_id_is_empty(db, jobs_fixture):
    """Ceipal pattern: list endpoint has no client id; details endpoint
    returns client by NAME. Importer must fall back to name-based lookup
    against ats_client_mappings.external_client_name."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, _, _, _, complete_unit_id = jobs_fixture

    # The complete-client mapping was seeded with external_client_name="C"
    # (see conftest.jobs_fixture). The Ceipal adapter would yield a job
    # with external_client_id="" + external_client_name="C" for jobs
    # linked to that client.
    job = ATSJobPayload(
        external_id="j-by-name",
        external_client_id="",  # Ceipal: id-based linkage unavailable
        external_client_name="C",  # name match against the seeded mapping
        title="Name-Linked Job",
        status="Active",
        raw={"client": "C"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.new == 1
    assert result.skipped == 0

    row = await db.execute(text(
        "SELECT status, org_unit_id::text, external_id, source "
        "FROM job_postings WHERE tenant_id = :t AND external_id = 'j-by-name'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.status == "draft"  # complete_unit's profile is complete
    assert r.org_unit_id == complete_unit_id  # linked correctly via name
    assert r.source == "ats_ceipal"


@pytest.mark.asyncio
async def test_job_with_neither_id_nor_name_is_imported_unlinked(db, jobs_fixture):
    """A job with empty external_client_id AND empty external_client_name
    has nothing to link against — inserted with org_unit_id=NULL +
    status='blocked_pending_client_setup'. The stub-creation path requires
    a non-empty external_client_name; without one we cannot fabricate a
    sensible org_unit name, so the row stays unlinked and the recruiter
    handles it via the /jobs 'Not set up' chip."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j-orphan",
        external_client_id="",
        external_client_name=None,
        title="Orphan",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.new == 1
    assert result.skipped == 0

    row = await db.execute(text(
        "SELECT status, org_unit_id FROM job_postings "
        "WHERE tenant_id = :t AND external_id = 'j-orphan'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.status == "blocked_pending_client_setup"
    assert r.org_unit_id is None


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
    # count_jobs is called twice: once to seed the Pass 1 progress
    # denominator (with the cursor), once for the Pass 2 missing-detect
    # comparison (without cursor). Both must pin the same status filter.
    assert adapter.count_jobs.await_count == 2
    for call in adapter.count_jobs.await_args_list:
        assert call.kwargs["job_status_ids"] == [1, 8]


@pytest.mark.asyncio
async def test_missing_detect_inserts_locally_deleted_job(db, jobs_fixture):
    """Pass 2: cursor-based sync misses jobs the recruiter deleted locally,
    so the importer compares local count vs Ceipal total and walks the
    full list (skipping known external_ids) to re-fetch the gap.

    Setup: Ceipal has 2 jobs that match the filter. We seed 1 of them
    locally (simulating that the recruiter previously synced both and
    then deleted one). Cursor is in the future so the cursor-based pass
    returns zero rows. Pass 2 must detect the drift (local=1 < ceipal=2)
    and re-insert the missing job.
    """
    from app.modules.ats.importer import ATSImporter
    from app.modules.ats.schemas import ATSJobPayload

    tenant_id, _user_id, _root_unit_id, _pending_unit, complete_unit = jobs_fixture

    # Pre-seed: one of the two jobs is already in the local DB. The
    # other is the "deleted locally, still in Ceipal" case.
    await db.execute(text(
        "INSERT INTO job_postings (tenant_id, org_unit_id, title, "
        "description_raw, status, source, external_id, created_by) "
        "SELECT :t, :ou, 'Existing Job', 'desc', 'draft', "
        "'ats_ceipal', 'jid-existing', users.id "
        "FROM users WHERE tenant_id = :t LIMIT 1"
    ), {"t": tenant_id, "ou": complete_unit})
    await db.flush()

    # The importer reads the cursor from the adapter's in-memory
    # ATSConnectionState (not the DB row directly), so we set it on the
    # adapter state below. The test asserts the importer's two-pass
    # control flow — Pass 1 with this cursor, Pass 2 without.
    future = datetime.now(tz=timezone.utc).isoformat()

    # Ceipal "has" both jobs. Pass 1 with cursor=future returns []. Pass 2
    # with cursor=None should yield 'jid-missing' (since 'jid-existing'
    # is in the skip set built from the local DB).
    missing_job = ATSJobPayload(
        external_id="jid-missing", external_client_id="complete-client",
        title="Missing Job", description="restored",
        status="Active", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    existing_job = ATSJobPayload(
        external_id="jid-existing", external_client_id="complete-client",
        title="Existing Job", description="...",
        status="Active", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )

    pass_calls: list[dict] = []

    def list_jobs_fn(since=None, *, job_status_ids=None, skip_external_ids=None):
        pass_calls.append({
            "since": since,
            "skip_external_ids": skip_external_ids,
        })
        async def _aiter():
            if since is not None:
                # Pass 1 (cursor-based): nothing modified since.
                return
                yield  # pragma: no cover
            # Pass 2 (no cursor, skip known): the adapter normally drops
            # known IDs before its details fetch; we model that here.
            for job in (existing_job, missing_job):
                if skip_external_ids and job.external_id in skip_external_ids:
                    continue
                yield job
        return _aiter()

    adapter = _jobs_adapter(tenant_id, [])
    adapter.list_jobs = list_jobs_fn
    # Seed the in-memory cursor so the importer's Pass 1 calls
    # list_jobs(since=<future>) — see _cursor_or_none.
    adapter.state.last_synced_cursors = {"jobs": future}
    # count_jobs returns 0 with cursor (no-op pass), 2 without (Pass 2 trigger).
    async def count_jobs_fn(*, since=None, job_status_ids=None):
        return 0 if since is not None else 2
    adapter.count_jobs = count_jobs_fn

    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)

    # Pass 2 ran: list_jobs called twice (once cursor, once no-cursor).
    assert len(pass_calls) == 2
    assert pass_calls[0]["since"] is not None  # Pass 1 cursor-based
    assert pass_calls[0]["skip_external_ids"] is None
    assert pass_calls[1]["since"] is None  # Pass 2 full list
    assert pass_calls[1]["skip_external_ids"] == {"jid-existing"}

    # The missing job was inserted as new; the existing one untouched.
    assert result.new == 1
    assert result.updated == 0

    row = await db.execute(text(
        "SELECT external_id, title FROM job_postings "
        "WHERE tenant_id = :t ORDER BY external_id"
    ), {"t": tenant_id})
    rows = row.all()
    assert [r.external_id for r in rows] == ["jid-existing", "jid-missing"]


@pytest.mark.asyncio
async def test_missing_detect_recovers_progress_when_pass1_count_failed(
    db, jobs_fixture,
):
    """Production case: Pass 1's count_jobs raises (transient Ceipal flake
    or bad envelope). The importer falls back to total=-1, which the
    frontend renders as the indeterminate 'Counting jobs…' state. If
    Pass 2 then finds missing rows, it must re-seed total to a concrete
    number so the progress bar actually moves; otherwise the bar stays
    indeterminate for the entire ~2-3 minute Pass 2 walk."""
    from app.modules.ats.importer import ATSImporter
    from app.modules.ats.schemas import ATSJobPayload

    tenant_id, _user_id, _root_unit_id, _pending_unit, _complete_unit = jobs_fixture

    missing_job = ATSJobPayload(
        external_id="jid-missing",
        external_client_id="complete-client",
        title="Missing Job", description="restored",
        status="Active", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )

    adapter = _jobs_adapter(tenant_id, [missing_job])
    # Pass 1 cursor-based count raises; pass 2 count succeeds with 1.
    call_count = 0
    async def count_jobs_fn(*, since=None, job_status_ids=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("ceipal returned malformed envelope")
        return 1
    adapter.count_jobs = count_jobs_fn

    sync_log_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO ats_sync_logs (id, tenant_id, connection_id, started_at, "
        "status, correlation_id) "
        "SELECT :s, :t, c.id, now(), 'running', 'test-corr' "
        "FROM ats_connections c WHERE c.tenant_id = :t"
    ), {"s": sync_log_id, "t": tenant_id})
    await db.flush()

    importer = ATSImporter()
    result = await importer._run_phase(
        "jobs", importer._sync_jobs, adapter, sync_log_id,
    )
    assert result.new == 1

    # Final progress: total MUST be a concrete positive number, not -1.
    # If the bug regresses, total would stay at -1 and the frontend bar
    # would never leave the indeterminate state.
    r = await db.execute(text(
        "SELECT progress FROM ats_sync_logs WHERE id = :s"
    ), {"s": sync_log_id})
    progress = r.scalar_one()
    assert progress["jobs"]["total"] == 1
    assert progress["jobs"]["processed"] == 1


@pytest.mark.asyncio
async def test_missing_detect_skipped_when_local_matches_ceipal(db, jobs_fixture):
    """Pass 2 must NOT call list_jobs a second time when local count
    already equals Ceipal total — that's wasted bandwidth in the common
    steady-state case."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture

    list_call_count = 0
    def list_jobs_fn(since=None, *, job_status_ids=None, skip_external_ids=None):
        nonlocal list_call_count
        list_call_count += 1
        async def _aiter():
            return
            yield  # pragma: no cover
        return _aiter()

    adapter = _jobs_adapter(tenant_id, [])
    adapter.list_jobs = list_jobs_fn
    # Both passes (cursor / no-cursor) report zero. local_count == 0 == ceipal,
    # so Pass 2 must short-circuit.
    async def count_jobs_fn(*, since=None, job_status_ids=None):
        return 0
    adapter.count_jobs = count_jobs_fn

    importer = ATSImporter()
    await importer._run_phase("jobs", importer._sync_jobs, adapter)

    # Pass 1 (cursor list_jobs) ran. Pass 2 list_jobs did NOT run.
    assert list_call_count == 1


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
    async def spy(log_id, processed, total, tenant_id):
        calls.append((processed, total))
        await original(log_id, processed, total, tenant_id)
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


@pytest.mark.asyncio
async def test_write_jobs_progress_opens_fresh_session_per_call(monkeypatch):
    """Production bug guard: each call must open its OWN ``get_bypass_session``
    rather than reusing a long-lived one. A shared session would close its
    ``begin()`` context after the first ``commit()``, causing the next
    write to raise ``InvalidRequestError: Can't operate on closed
    transaction inside context manager.`` (Reproduces the production crash
    we saw on commit 27dc72f.)

    The test asserts:
      1. ``get_bypass_session`` is entered exactly once per call.
      2. Within that session, SET LOCAL fires before the UPDATE.
      3. The function does NOT call ``commit()`` explicitly — the outer
         ``session.begin()`` inside ``get_bypass_session`` is responsible.
    """
    from contextlib import asynccontextmanager

    from app.modules.ats import importer as importer_mod
    from app.modules.ats.importer import ATSImporter

    sessions_opened = 0
    calls: list = []

    class FakeSession:
        async def execute(self, stmt, params=None):
            calls.append((str(stmt), params))

        async def commit(self):
            calls.append(("COMMIT", None))

    @asynccontextmanager
    async def fake_get_bypass_session():
        nonlocal sessions_opened
        sessions_opened += 1
        yield FakeSession()

    monkeypatch.setattr(
        importer_mod, "get_bypass_session", fake_get_bypass_session,
    )

    tenant_id = uuid.uuid4()
    sync_log_id = uuid.uuid4()

    # Two calls — proves the per-call session pattern under repeated use.
    await ATSImporter._write_jobs_progress(
        sync_log_id, processed=0, total=10, tenant_id=tenant_id,
    )
    await ATSImporter._write_jobs_progress(
        sync_log_id, processed=1, total=10, tenant_id=tenant_id,
    )

    # Two sessions opened, one per call.
    assert sessions_opened == 2

    # No explicit COMMIT — the outer get_bypass_session()'s session.begin()
    # commits at context exit. If we ever called commit() here, it would
    # close the begin-context and the next execute() would raise.
    assert not any(c[0] == "COMMIT" for c in calls)

    # Per call: SET LOCAL app.current_tenant -> UPDATE ats_sync_logs, in
    # that order, with the right tenant_id interpolated.
    assert "SET LOCAL app.current_tenant" in calls[0][0]
    assert str(tenant_id) in calls[0][0]
    assert "UPDATE ats_sync_logs" in calls[1][0]
    assert "SET LOCAL app.current_tenant" in calls[2][0]
    assert "UPDATE ats_sync_logs" in calls[3][0]


@pytest.mark.asyncio
async def test_sync_jobs_creates_stub_for_unknown_client_name(db, jobs_fixture):
    """A Ceipal job whose `client` name has no matching ats_client_mappings
    row triggers auto-creation of a stub client_account org_unit + paired
    stub mapping. The job is linked to the new stub with
    status='blocked_pending_client_setup'.

    Synthetic external_client_id format: 'name:' + external_client_name.
    Stub mapping carries source_metadata={"stub": True, "origin": "jobs_phase"}.
    Stub org_unit carries company_profile_completion_status='pending' and
    company_profile={"name": <name>}.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id, _pending_unit, _complete_unit = jobs_fixture
    job = ATSJobPayload(
        external_id="j-stub",
        external_client_id="",  # Ceipal jobs never carry a stable client id
        external_client_name="Oracle",  # not in seeded mappings (P, C)
        title="Java Engineer",
        status="Active",
        raw={"client": "Oracle"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.new == 1
    assert result.skipped == 0

    # Stub org_unit was created under the tenant's root.
    unit_row = await db.execute(text(
        "SELECT id::text AS id, name, unit_type, parent_unit_id::text AS parent_id, "
        "company_profile, company_profile_completion_status "
        "FROM organizational_units "
        "WHERE client_id = :t AND name = 'Oracle'"
    ), {"t": tenant_id})
    u = unit_row.one()
    assert u.unit_type == "client_account"
    assert u.parent_id == root_unit_id
    assert u.company_profile_completion_status == "pending"
    assert u.company_profile == {"name": "Oracle"}

    # Paired stub mapping with synthetic id and origin metadata.
    mapping_row = await db.execute(text(
        "SELECT external_client_id, external_client_name, source_metadata, "
        "org_unit_id::text AS org_unit_id "
        "FROM ats_client_mappings "
        "WHERE tenant_id = :t AND ats_vendor = 'ceipal' "
        "AND external_client_name = 'Oracle'"
    ), {"t": tenant_id})
    m = mapping_row.one()
    assert m.external_client_id == "name:Oracle"
    assert m.source_metadata == {"stub": True, "origin": "jobs_phase"}
    assert m.org_unit_id == u.id

    # Job linked to the stub with the blocked status.
    job_row = await db.execute(text(
        "SELECT status, org_unit_id::text AS org_unit_id "
        "FROM job_postings WHERE tenant_id = :t AND external_id = 'j-stub'"
    ), {"t": tenant_id})
    j = job_row.one()
    assert j.status == "blocked_pending_client_setup"
    assert j.org_unit_id == u.id

    # Audit log row written with stub origin marker.
    audit_row = await db.execute(text(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.client_mapping.created' "
        "ORDER BY created_at DESC LIMIT 1"
    ), {"t": tenant_id})
    a = audit_row.one()
    assert a.payload["stub"] is True
    assert a.payload["origin"] == "jobs_phase"
    assert a.payload["external_client_id"] == "name:Oracle"


@pytest.mark.asyncio
async def test_write_jobs_progress_no_op_when_sync_log_id_none(monkeypatch):
    """When sync_log_id is None, ``_write_jobs_progress`` returns immediately
    without even opening a bypass-RLS session — important so test paths
    that don't care about progress don't pay session-setup cost."""
    from contextlib import asynccontextmanager

    from app.modules.ats import importer as importer_mod
    from app.modules.ats.importer import ATSImporter

    opens = 0

    @asynccontextmanager
    async def fake_get_bypass_session():
        nonlocal opens
        opens += 1
        yield None

    monkeypatch.setattr(
        importer_mod, "get_bypass_session", fake_get_bypass_session,
    )

    await ATSImporter._write_jobs_progress(
        None, processed=5, total=10, tenant_id=uuid.uuid4(),
    )
    assert opens == 0


@pytest.mark.asyncio
async def test_sync_jobs_stub_creation_is_idempotent(db, jobs_fixture):
    """Running _sync_jobs twice for the same unknown-client-name payload
    creates exactly one org_unit + one stub mapping.

    On the second run, _upsert_job_payload's name-based mapping lookup
    (the second of the two lookups in `_upsert_job_payload`) matches
    the stub mapping that was created by the first run and
    short-circuits — the stub-creation branch (and the helper) is not
    re-entered. End-to-end idempotency is what's asserted here; direct
    coverage of the helper's own synthetic-id short-circuit lives in
    test_get_or_create_client_stub_by_name_is_idempotent.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j-idem",
        external_client_id="",
        external_client_name="Acme Corp",
        title="t",
        status="Active",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )

    importer = ATSImporter()
    # First sync — creates stub.
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))
    # Second sync — same payload (Pass 2 missing-detect won't fire because
    # the job already exists locally, so this exercises Pass 1's update path).
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))

    unit_count = await db.execute(text(
        "SELECT COUNT(*) FROM organizational_units "
        "WHERE client_id = :t AND name = 'Acme Corp'"
    ), {"t": tenant_id})
    assert unit_count.scalar_one() == 1

    mapping_count = await db.execute(text(
        "SELECT COUNT(*) FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_id = 'name:Acme Corp'"
    ), {"t": tenant_id})
    assert mapping_count.scalar_one() == 1


@pytest.mark.asyncio
async def test_get_or_create_client_stub_by_name_is_idempotent(db, jobs_fixture):
    """Calling _get_or_create_client_stub_by_name twice with the same
    (tenant, vendor, name) returns the same (org_unit, mapping) pair on
    the second call and creates no duplicates. Exercises the helper's
    synthetic-id short-circuit (the first SELECT inside the helper)
    directly — the broader end-to-end idempotency at the _sync_jobs
    level is covered by test_sync_jobs_stub_creation_is_idempotent."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, user_id, root_unit_id, *_ = jobs_fixture
    importer = ATSImporter()

    org_unit_first, mapping_first = await importer._get_or_create_client_stub_by_name(
        db,
        tenant_id=uuid.UUID(tenant_id),
        vendor="ceipal",
        external_client_name="Globex",
        created_by=uuid.UUID(user_id),
        root_org_unit_id=uuid.UUID(root_unit_id),
    )

    # Second call — must short-circuit on the synthetic-id lookup.
    org_unit_second, mapping_second = await importer._get_or_create_client_stub_by_name(
        db,
        tenant_id=uuid.UUID(tenant_id),
        vendor="ceipal",
        external_client_name="Globex",
        created_by=uuid.UUID(user_id),
        root_org_unit_id=uuid.UUID(root_unit_id),
    )

    assert org_unit_second.id == org_unit_first.id
    assert mapping_second.external_client_id == mapping_first.external_client_id

    # Belt-and-suspenders: confirm exactly one row each.
    unit_count = await db.execute(text(
        "SELECT COUNT(*) FROM organizational_units "
        "WHERE client_id = :t AND name = 'Globex'"
    ), {"t": tenant_id})
    assert unit_count.scalar_one() == 1

    mapping_count = await db.execute(text(
        "SELECT COUNT(*) FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_id = 'name:Globex'"
    ), {"t": tenant_id})
    assert mapping_count.scalar_one() == 1


@pytest.mark.asyncio
async def test_sync_jobs_stub_handles_colon_in_client_name(db, jobs_fixture):
    """A client name that itself contains a colon (e.g. 'Acme: West Region')
    is stored verbatim in external_client_id as 'name:Acme: West Region'.

    The colon-in-name case is structurally trivial today (the synthetic id
    is built by f-string interpolation; nothing strips or normalizes), but
    pinning it down with an explicit test prevents a future refactor — e.g.
    swapping in URL encoding or hash-based synthetic ids — from silently
    breaking the exact-equality re-sync lookup. _sync_clients's promotion
    code (added in a later commit) also pattern-matches the synthetic id
    via LIKE 'name:%'; the colon is fine there too because LIKE only
    treats '%' and '_' as metacharacters.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j-colon",
        external_client_id="",
        external_client_name="Acme: West Region",
        title="t",
        raw={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    importer = ATSImporter()
    await importer._run_phase("jobs", importer._sync_jobs, _jobs_adapter(tenant_id, [job]))

    row = await db.execute(text(
        "SELECT external_client_id, external_client_name FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_name = 'Acme: West Region'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.external_client_id == "name:Acme: West Region"
    assert r.external_client_name == "Acme: West Region"
