"""create_sync_log inserts a 'running' row; finalize_sync_log_* close it.

Test environment: Option (ii) — uses the per-test rollback-isolated ``db``
fixture (see ``tests/conftest.py``) instead of the plan's
``async_session_factory`` pattern. Rationale is the same as for the importer
tests (see ``tests/modules/ats/conftest.py``): we avoid committed rows in the
dev DB and the need for explicit cleanup.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_create_sync_log_returns_running_row(db, importer_fixture):
    from app.modules.ats.service import create_sync_log_row

    tenant_id, *_ = importer_fixture
    conn_row = await db.execute(text(
        "SELECT id FROM ats_connections WHERE tenant_id = :t LIMIT 1"
    ), {"t": tenant_id})
    connection_id = conn_row.scalar_one()

    log_id = await create_sync_log_row(
        db, connection_id=connection_id,
        tenant_id=uuid.UUID(tenant_id),
        correlation_id="test-corr-1",
    )

    r = await db.execute(text(
        "SELECT status, correlation_id FROM ats_sync_logs WHERE id = :i"
    ), {"i": log_id})
    row = r.one()
    assert row.status == "running"
    assert row.correlation_id == "test-corr-1"


@pytest.mark.asyncio
async def test_finalize_sync_log_success(db, importer_fixture):
    from app.modules.ats.importer import PhaseResult, SyncResult
    from app.modules.ats.service import (
        create_sync_log_row,
        finalize_sync_log_success,
    )

    tenant_id, *_ = importer_fixture
    cid = (await db.execute(text(
        "SELECT id FROM ats_connections WHERE tenant_id = :t LIMIT 1"
    ), {"t": tenant_id})).scalar_one()

    log_id = await create_sync_log_row(
        db, connection_id=cid,
        tenant_id=uuid.UUID(tenant_id), correlation_id="c",
    )

    sync_result = SyncResult()
    sync_result.clients = PhaseResult(
        new=2, updated=30, skipped=0,
        sync_started_at=datetime.now(tz=timezone.utc),
    )
    await finalize_sync_log_success(db, log_id, sync_result)

    r = await db.execute(text(
        "SELECT status, entity_counts, completed_at FROM ats_sync_logs WHERE id = :i"
    ), {"i": log_id})
    row = r.one()
    assert row.status == "success"
    assert row.completed_at is not None
    assert row.entity_counts["clients"]["new"] == 2
    assert row.entity_counts["clients"]["updated"] == 30


@pytest.mark.asyncio
async def test_finalize_sync_log_failure_records_phase_and_error(db, importer_fixture):
    from app.modules.ats.service import (
        create_sync_log_row,
        finalize_sync_log_failure,
    )

    tenant_id, *_ = importer_fixture
    cid = (await db.execute(text(
        "SELECT id FROM ats_connections WHERE tenant_id = :t LIMIT 1"
    ), {"t": tenant_id})).scalar_one()

    log_id = await create_sync_log_row(
        db, connection_id=cid,
        tenant_id=uuid.UUID(tenant_id), correlation_id="c",
    )
    await finalize_sync_log_failure(
        db, log_id, phase="jobs",
        error_summary="ATSRateLimitedError: retry after 60s",
    )

    r = await db.execute(text(
        "SELECT status, error_phase, error_summary FROM ats_sync_logs WHERE id = :i"
    ), {"i": log_id})
    row = r.one()
    assert row.status == "failed"
    assert row.error_phase == "jobs"
    assert "retry after 60s" in row.error_summary
