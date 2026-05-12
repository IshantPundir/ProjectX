"""Phase 1 (clients) and Phase 2 (users) of ATSImporter:
  - New Ceipal client → auto-create client_account org_unit with stub profile,
    completion_status='pending'.
  - Existing mapping → update last_synced_at + external_client_name, do NOT
    rename the org_unit (recruiter may have customized).
  - User mapping is reference-only (internal_user_id stays NULL on insert).

Test-environment choice: Option (ii) — the ``importer_fixture`` (in
``conftest.py``) monkeypatches ``get_bypass_session`` so the importer's
internally-opened session is the rollback-isolated ``db`` fixture. This
matches the Option B pattern Tasks 9/16/17 used: writes happen on the
test session, are visible to assertions, and roll back automatically.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import ATSClientPayload, ATSUserPayload


def _async_iter(items):
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


def _adapter_with_clients(tenant_id, client_payloads, user_payloads):
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=tenant_id, vendor="ceipal", credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"
    adapter.list_clients = lambda since=None: _async_iter(client_payloads)
    adapter.list_users = lambda since=None: _async_iter(user_payloads)
    return adapter


@pytest.mark.asyncio
async def test_sync_clients_creates_pending_org_unit_for_new_mapping(
    db, importer_fixture,
):
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, _root_unit_id = importer_fixture
    payload = ATSClientPayload(
        external_id="cid-1", name="Oracle", website="www.oracle.com",
        industry="Computer Software", country="India", state="Karnataka",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    assert result.new == 1
    assert result.updated == 0

    # Verify the org unit was created with pending status and stub profile.
    # Querying via the same test session — the monkeypatched bypass session
    # converted .commit() → .flush(), so all rows are visible here.
    row = await db.execute(text(
        "SELECT o.name, o.unit_type, o.company_profile, "
        "o.company_profile_completion_status, m.external_client_id "
        "FROM organizational_units o "
        "JOIN ats_client_mappings m ON m.org_unit_id = o.id "
        "WHERE m.tenant_id = :t"
    ), {"t": tenant_id})
    r = row.one()
    assert r.name == "Oracle"
    assert r.unit_type == "client_account"
    assert r.company_profile_completion_status == "pending"
    assert r.company_profile["name"] == "Oracle"
    assert r.company_profile["website"] == "www.oracle.com"
    assert r.company_profile["industry"] == "Computer Software"
    assert r.external_client_id == "cid-1"


@pytest.mark.asyncio
async def test_sync_clients_existing_mapping_updates_dont_rename_org_unit(
    db, importer_fixture,
):
    """Existing mapping → only refresh metadata; DON'T rename the org_unit.

    Pre-seed an existing org_unit with a recruiter-customized name and a
    matching ATSClientMapping. Re-import the same external_id with Ceipal's
    original name 'Oracle'. Assert:
      - org_unit.name stayed 'Renamed by Recruiter' (NOT renamed)
      - mapping.external_client_name updated to 'Oracle'
      - mapping.last_synced_at advanced
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id = importer_fixture

    # Pre-seed: existing client_account org_unit (renamed by recruiter) and
    # matching ats_client_mappings row pointing at it.
    existing_unit_id = uuid.uuid4()
    existing_mapping_id = uuid.uuid4()
    original_synced_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await db.execute(
        text(
            "INSERT INTO organizational_units "
            "(id, client_id, parent_unit_id, name, unit_type, is_root, "
            "company_profile, company_profile_completion_status) "
            "VALUES (:o, :t, :p, 'Renamed by Recruiter', 'client_account', "
            "false, '{\"name\": \"Renamed by Recruiter\"}', 'complete')"
        ),
        {"o": existing_unit_id, "t": tenant_id, "p": root_unit_id},
    )
    await db.execute(
        text(
            "INSERT INTO ats_client_mappings "
            "(id, tenant_id, ats_vendor, external_client_id, "
            "external_client_name, org_unit_id, last_synced_at) "
            "VALUES (:m, :t, 'ceipal', 'cid-1', 'Oracle Original', :o, :s)"
        ),
        {"m": existing_mapping_id, "t": tenant_id, "o": existing_unit_id,
         "s": original_synced_at},
    )
    await db.flush()

    payload = ATSClientPayload(
        external_id="cid-1", name="Oracle", website="www.oracle.com",
        industry="Computer Software",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    assert result.updated == 1
    assert result.new == 0

    # Verify: org_unit.name stayed unchanged; mapping metadata refreshed.
    row = await db.execute(text(
        "SELECT o.name AS unit_name, m.external_client_name, m.last_synced_at "
        "FROM organizational_units o "
        "JOIN ats_client_mappings m ON m.org_unit_id = o.id "
        "WHERE m.tenant_id = :t AND m.external_client_id = 'cid-1'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.unit_name == "Renamed by Recruiter"
    assert r.external_client_name == "Oracle"
    assert r.last_synced_at > original_synced_at


@pytest.mark.asyncio
async def test_sync_users_inserts_unmapped_rows(db, importer_fixture):
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, _root_unit_id = importer_fixture
    payload = ATSUserPayload(
        external_id="ceipal-uid-1", email="recruiter@x.com",
        display_name="Jane Recruiter", role="Recruiter", status="Active",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [], [payload])

    importer = ATSImporter()
    result = await importer._run_phase("users", importer._sync_users, adapter)
    assert result.new == 1

    row = await db.execute(text(
        "SELECT external_user_id, external_user_email, internal_user_id "
        "FROM ats_user_mappings WHERE tenant_id = :t"
    ), {"t": tenant_id})
    r = row.one()
    assert r.external_user_id == "ceipal-uid-1"
    assert r.external_user_email == "recruiter@x.com"
    assert r.internal_user_id is None  # NOT auto-mapped
