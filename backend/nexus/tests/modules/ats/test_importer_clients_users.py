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
from sqlalchemy import select, text

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
async def test_sync_users_inserts_unlinked_when_no_email_match(db, importer_fixture):
    """Sync inserts ats_user_mappings with internal_user_id=NULL when the
    payload email doesn't match any existing User in the tenant. The
    fixture's only user is 'u@x.com'; the payload below is a different
    address, so auto-link does not fire."""
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
    assert r.internal_user_id is None


@pytest.mark.asyncio
async def test_sync_users_autolinks_to_existing_user_on_insert(db, importer_fixture):
    """When the payload email matches an existing tenant user (case-insensitive),
    the new mapping row is inserted with internal_user_id already set. Closes
    the loop for the out-of-band-join case."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, user_id, _root_unit_id = importer_fixture
    payload = ATSUserPayload(
        external_id="ceipal-uid-2",
        # Existing user in the fixture is 'u@x.com'; verify case-insensitive match.
        email="U@X.COM",
        display_name="U", role="Recruiter", status="Active",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [], [payload])

    importer = ATSImporter()
    result = await importer._run_phase("users", importer._sync_users, adapter)
    assert result.new == 1

    row = await db.execute(text(
        "SELECT internal_user_id, mapped_at, mapped_by "
        "FROM ats_user_mappings WHERE tenant_id = :t "
        "AND external_user_id = 'ceipal-uid-2'"
    ), {"t": tenant_id})
    r = row.one()
    assert str(r.internal_user_id) == user_id
    assert r.mapped_at is not None
    assert str(r.mapped_by) == user_id


@pytest.mark.asyncio
async def test_sync_users_autolinks_on_email_update(db, importer_fixture):
    """When an existing mapping has NULL internal_user_id and a later sync
    re-emits the row with an email that now matches a real user, link it."""
    from app.modules.ats.importer import ATSImporter
    from app.modules.ats.models import ATSUserMapping

    tenant_id, user_id, _root_unit_id = importer_fixture
    # First sync: email doesn't match.
    payload1 = ATSUserPayload(
        external_id="ceipal-uid-3", email="old-address@example.com",
        display_name="X", role=None, status="Active",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [], [payload1])
    importer = ATSImporter()
    await importer._run_phase("users", importer._sync_users, adapter)

    # Second sync: same external id, email now matches the fixture user.
    payload2 = ATSUserPayload(
        external_id="ceipal-uid-3", email="u@x.com",
        display_name="X", role=None, status="Active",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [], [payload2])
    await importer._run_phase("users", importer._sync_users, adapter)

    row = await db.scalar(
        select(ATSUserMapping).where(
            ATSUserMapping.tenant_id == uuid.UUID(tenant_id),
            ATSUserMapping.external_user_id == "ceipal-uid-3",
        )
    )
    assert row is not None
    assert str(row.internal_user_id) == user_id
    assert row.mapped_at is not None


@pytest.mark.asyncio
async def test_sync_clients_promotes_stub_to_real_external_id(
    db, importer_fixture,
):
    """When _sync_clients later returns a real Ceipal id for a client that
    already exists as a stub (created by an earlier _sync_jobs run), the
    stub mapping is PROMOTED in place: external_client_id is rewritten to
    the real id, source_metadata is replaced with the Ceipal payload's
    contacts+raw, last_synced_at advances. The org_unit is untouched —
    same row, same id, same completion_status='pending', same
    company_profile (the recruiter's in-flight profile work survives).

    Audit row 'ats.client_mapping.promoted' is written with from/to ids.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, _user_id, root_unit_id = importer_fixture

    # Pre-seed: a stub created by an earlier jobs-phase run for "Oracle".
    stub_unit_id = uuid.uuid4()
    original_synced_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await db.execute(
        text(
            "INSERT INTO organizational_units "
            "(id, client_id, parent_unit_id, name, unit_type, is_root, "
            "company_profile, company_profile_completion_status) "
            "VALUES (:o, :t, :p, 'Oracle', 'client_account', false, "
            "'{\"name\": \"Oracle\"}', 'pending')"
        ),
        {"o": stub_unit_id, "t": tenant_id, "p": root_unit_id},
    )
    await db.execute(
        text(
            "INSERT INTO ats_client_mappings "
            "(tenant_id, ats_vendor, external_client_id, external_client_name, "
            " org_unit_id, source_metadata, last_synced_at) "
            "VALUES (:t, 'ceipal', 'name:Oracle', 'Oracle', :o, "
            " :sm, :s)"
        ),
        {
            "t": tenant_id, "o": stub_unit_id, "s": original_synced_at,
            "sm": '{"stub": true, "origin": "jobs_phase"}',
        },
    )
    await db.flush()

    # _sync_clients now returns Oracle with its real Ceipal id.
    payload = ATSClientPayload(
        external_id="ABC123",
        name="Oracle",
        website="www.oracle.com",
        industry="Computer Software",
        contacts=[{"email": "ops@oracle.com"}],
        raw={"id": "ABC123", "name": "Oracle"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _adapter_with_clients(uuid.UUID(tenant_id), [payload], [])

    importer = ATSImporter()
    result = await importer._run_phase("clients", importer._sync_clients, adapter)
    assert result.updated == 1
    assert result.new == 0

    # Mapping promoted: synthetic id replaced by real id; metadata refreshed.
    mapping_row = await db.execute(text(
        "SELECT external_client_id, external_client_name, source_metadata, "
        "last_synced_at, org_unit_id::text AS org_unit_id "
        "FROM ats_client_mappings "
        "WHERE tenant_id = :t AND external_client_name = 'Oracle'"
    ), {"t": tenant_id})
    rows = mapping_row.all()
    assert len(rows) == 1  # exactly one mapping (no duplicate created)
    m = rows[0]
    assert m.external_client_id == "ABC123"
    assert m.source_metadata == {
        "contacts": [{"email": "ops@oracle.com"}],
        "raw": {"id": "ABC123", "name": "Oracle"},
    }
    assert m.last_synced_at > original_synced_at
    assert m.org_unit_id == str(stub_unit_id)

    # Org_unit untouched: same row, completion_status still pending.
    unit_row = await db.execute(text(
        "SELECT id::text AS id, name, company_profile_completion_status, "
        "company_profile "
        "FROM organizational_units WHERE id = :o"
    ), {"o": stub_unit_id})
    u = unit_row.one()
    assert u.id == str(stub_unit_id)
    assert u.name == "Oracle"
    assert u.company_profile_completion_status == "pending"
    assert u.company_profile == {"name": "Oracle"}

    # Promotion audit row written.
    audit_row = await db.execute(text(
        "SELECT action, payload FROM audit_log "
        "WHERE tenant_id = :t AND action = 'ats.client_mapping.promoted' "
        "ORDER BY created_at DESC LIMIT 1"
    ), {"t": tenant_id})
    a = audit_row.one()
    assert a.payload["from_external_client_id"] == "name:Oracle"
    assert a.payload["to_external_client_id"] == "ABC123"
    assert a.payload["vendor"] == "ceipal"
