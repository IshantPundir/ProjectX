"""Shared fixtures for ATS importer tests.

Test-environment choice: Option (ii) — monkeypatch ``get_bypass_session`` in
``app.modules.ats.importer`` to yield the test's ``db`` fixture session.
Rationale vs the plan's Option (i):
  - The plan's pattern (``async_session_factory``) would write committed rows
    to the dev DB and require explicit ``DELETE FROM clients`` cleanup.
  - The ``db`` fixture (tests/conftest.py) gives per-test connection-level
    transaction rollback for free — no committed data, no cleanup risk.
  - The importer opens its own bypass-RLS session inside ``_run_phase``, so
    we cannot just pass ``db`` in. Patching ``get_bypass_session`` to yield
    the test's ``db`` (and shimming ``commit`` → ``flush``) lets us verify
    writes on the same session before rollback.

Force registration of ATS ORM classes with Base.metadata so the
``_create_tables`` fixture in ``tests/conftest.py`` builds the
``ats_connections`` / ``ats_client_mappings`` / ``ats_user_mappings`` tables
in the test DB before the first ATS test runs.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

# Force registration of ATS ORM classes with Base.metadata so the
# session-scoped _create_tables fixture builds the ats_* tables.
from app.modules.ats import models as _ats_models  # noqa: F401


@pytest.fixture
def patched_bypass_session(db, monkeypatch):
    """Make ``get_bypass_session()`` yield the rollback-isolated ``db`` session
    instead of opening a new dev-DB session.

    Patches BOTH namespaces that re-import the symbol:
      - ``app.modules.ats.importer`` — for the five-phase importer.
      - ``app.modules.ats.actors``    — for the four-phase poll actor.

    The importer's ``_run_phase`` does::

        async with get_bypass_session() as db:
            await db.execute(text("SET LOCAL app.current_tenant = ..."))
            phase_result = await fn(db, adapter)
            await db.commit()

    The actor's ``_do_poll`` opens multiple bypass sessions in sequence
    (Phase A: load + open log → Phase B: persist tokens → Phase C: handle
    sync result → Phase D: finalize). All four resolve to the same test
    ``db`` session via this fixture, preserving rollback isolation.

    We can't actually commit on the test session (would break rollback
    isolation), so the wrapper substitutes ``commit`` with ``flush`` for the
    duration of every patched-session context. Likewise ``SET LOCAL`` is a
    no-op in the test DB (no RLS policies; ``DB_RUNTIME_ROLE`` is
    force-disabled per ``tests/conftest.py`` lines 23-31) but we let it
    through harmlessly.
    """
    from app.modules.ats import importer as importer_mod

    @asynccontextmanager
    async def _fake_bypass_session():
        # Swap the bound method commit -> flush for the duration of the
        # importer's ``_run_phase`` so the test's rollback at teardown still
        # cleans up everything the importer wrote.
        original_commit = db.commit
        db.commit = db.flush  # type: ignore[method-assign]
        try:
            yield db
        finally:
            db.commit = original_commit  # type: ignore[method-assign]

    monkeypatch.setattr(importer_mod, "get_bypass_session", _fake_bypass_session)
    # The actors module imports get_bypass_session at module load time, so we
    # must patch its namespace separately. Lazy import here so the fixture
    # works even before the actors module is imported elsewhere.
    from app.modules.ats import actors as actors_mod
    monkeypatch.setattr(actors_mod, "get_bypass_session", _fake_bypass_session)
    return db


@pytest.fixture
async def jobs_fixture(db, importer_fixture):
    """Add two client_account org_units (pending + complete) + matching mappings.

    Lives in conftest.py (not a test file) so multiple importer tests can
    share it without test-file-level imports. Uses the test ``db`` session
    directly so all rows roll back at teardown.
    """
    tenant_id, user_id, root_unit_id = importer_fixture
    pending_unit_id = uuid.uuid4()
    complete_unit_id = uuid.uuid4()

    await db.execute(text(
        "INSERT INTO organizational_units (id, client_id, name, unit_type, "
        "is_root, parent_unit_id, company_profile, "
        "company_profile_completion_status) VALUES "
        "(:p, :t, 'Pending', 'client_account', false, :r, "
        " '{\"name\":\"P\"}', 'pending'),"
        "(:c, :t, 'Complete', 'client_account', false, :r, "
        " '{\"name\":\"C\"}', 'complete')"
    ), {
        "p": pending_unit_id, "c": complete_unit_id,
        "t": tenant_id, "r": root_unit_id,
    })
    await db.execute(text(
        "INSERT INTO ats_client_mappings (tenant_id, ats_vendor, "
        "external_client_id, external_client_name, org_unit_id) VALUES "
        "(:t, 'ceipal', 'pending-client', 'P', :p),"
        "(:t, 'ceipal', 'complete-client', 'C', :c)"
    ), {
        "t": tenant_id, "p": pending_unit_id, "c": complete_unit_id,
    })
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
    await db.flush()

    yield (
        tenant_id, user_id, root_unit_id,
        str(pending_unit_id), str(complete_unit_id),
    )


@pytest.fixture
async def importer_fixture(db, patched_bypass_session):
    """Seed a tenant + user + root company org_unit + ats_connection.

    Returns ``(tenant_id, user_id, root_unit_id)`` as UUID strings, matching
    the plan's contract. All rows live on the rollback-isolated ``db``
    session and are torn down automatically at test teardown.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    root_unit_id = uuid.uuid4()

    await db.execute(
        text("INSERT INTO clients (id, name) VALUES (:t, 'A')"),
        {"t": tenant_id},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, email, tenant_id, auth_user_id) "
            "VALUES (:u, 'u@x.com', :t, :a)"
        ),
        {"u": user_id, "t": tenant_id, "a": uuid.uuid4()},
    )
    await db.execute(
        text(
            "INSERT INTO organizational_units "
            "(id, client_id, name, unit_type, is_root, company_profile, "
            "company_profile_completion_status) "
            "VALUES (:o, :t, 'Acme', 'company', true, "
            "'{\"name\": \"Acme\"}', 'complete')"
        ),
        {"o": root_unit_id, "t": tenant_id},
    )
    # The importer needs to know who created the connection — seed one.
    await db.execute(
        text(
            "INSERT INTO ats_connections (id, tenant_id, vendor, "
            "credentials_ciphertext, created_by) "
            "VALUES (:c, :t, 'ceipal', :ct, :u)"
        ),
        {"c": uuid.uuid4(), "t": tenant_id, "ct": b"x", "u": user_id},
    )
    await db.flush()

    yield (str(tenant_id), str(user_id), str(root_unit_id))
