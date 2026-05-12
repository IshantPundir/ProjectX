"""ATSImporter shell: _run_phase opens its own DB session, sets tenant scope,
writes the cursor on success, returns a PhaseResult, advances the OTel span."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.modules.ats.connection import ATSConnectionState


def _fake_adapter():
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor="ceipal",
        credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    return adapter


@pytest.mark.asyncio
async def test_sync_tenant_runs_all_five_phases_in_order():
    from app.modules.ats.importer import ATSImporter, SyncResult

    importer = ATSImporter()
    adapter = _fake_adapter()

    called = []
    importer._sync_clients = AsyncMock(side_effect=lambda *a, **k: called.append("clients") or _empty_phase())
    importer._sync_users = AsyncMock(side_effect=lambda *a, **k: called.append("users") or _empty_phase())
    importer._sync_jobs = AsyncMock(side_effect=lambda *a, **k: called.append("jobs") or _empty_phase())
    importer._sync_applicants = AsyncMock(side_effect=lambda *a, **k: called.append("applicants") or _empty_phase())
    importer._sync_submissions = AsyncMock(side_effect=lambda *a, **k: called.append("submissions") or _empty_phase())

    result = await importer.sync_tenant(adapter)

    assert called == ["clients", "users", "jobs", "applicants", "submissions"]
    assert isinstance(result, SyncResult)


def _empty_phase():
    from app.modules.ats.importer import PhaseResult
    return PhaseResult(new=0, updated=0, skipped=0,
                       sync_started_at=datetime.now(tz=timezone.utc))


@pytest.mark.asyncio
async def test_sync_result_default_counts_zero():
    from app.modules.ats.importer import SyncResult, PhaseResult

    r = SyncResult()
    for phase in ("clients", "users", "jobs", "applicants", "submissions"):
        assert getattr(r, phase) is None  # not run yet
