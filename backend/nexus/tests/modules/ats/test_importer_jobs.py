"""Phase 3 (jobs):
  - Resolve external_client_id → org_unit via ats_client_mappings.
  - If org_unit.completion_status='pending': status='blocked_pending_client_setup'.
  - If 'complete': status='draft' (caller enqueues extract_and_enhance_jd).
  - assigned_recruiter_external_ids → ats_job_recruiter_assignments rows.
  - Missing client mapping → skip + count in result.skipped.

Test-environment choice: Option (ii) — the ``importer_fixture`` (in
``conftest.py``) monkeypatches ``get_bypass_session`` so the importer's
internally-opened session is the rollback-isolated ``db`` fixture. All seed
and verify queries go through the test ``db`` (NOT async_session_factory).
"""
from __future__ import annotations

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
    # list_jobs now takes job_status_ids kwarg — accept and ignore in tests.
    adapter.list_jobs = lambda since=None, *, job_status_ids=None: _async_iter(jobs)
    # count_jobs seeds the progress denominator — return len(jobs).
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
async def test_job_with_unknown_client_mapping_is_skipped(db, jobs_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    job = ATSJobPayload(
        external_id="j3", external_client_id="not-yet-imported-client",
        title="x", raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _jobs_adapter(tenant_id, [job])
    importer = ATSImporter()
    result = await importer._run_phase("jobs", importer._sync_jobs, adapter)
    assert result.skipped == 1
    assert result.new == 0


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
async def test_job_with_neither_id_nor_name_is_skipped(db, jobs_fixture):
    """A job with empty external_client_id AND empty external_client_name
    can't be linked to any client — skip with a warning, no row inserted."""
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
    assert result.skipped == 1
    assert result.new == 0


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
