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
    adapter.list_jobs = lambda since=None: _async_iter(jobs)
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
