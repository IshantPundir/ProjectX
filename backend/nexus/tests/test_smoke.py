"""Smoke test — validates test infrastructure works."""

import pytest

from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_factory_helpers_create_rows(db):
    client = await create_test_client(db, name="Acme Corp")
    user = await create_test_user(db, client.id, email="alice@acme.com")
    unit = await create_test_org_unit(db, client.id, name="Engineering")

    assert client.id is not None
    assert user.tenant_id == client.id
    assert unit.client_id == client.id
    assert user.email == "alice@acme.com"
    assert unit.name == "Engineering"


def test_tenant_scoped_tables_includes_candidate_tables():
    from app.main import _TENANT_SCOPED_TABLES
    assert "candidates" in _TENANT_SCOPED_TABLES
    assert "candidate_job_assignments" in _TENANT_SCOPED_TABLES
    assert "candidate_stage_progress" in _TENANT_SCOPED_TABLES


def test_candidates_router_registered_under_candidates_prefix():
    """Sanity: /api/candidates is reachable as a route prefix in the app."""
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any(p.startswith("/api/candidates") for p in paths)


def test_kanban_router_registered_under_jobs_prefix():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any("/candidates/kanban" in p for p in paths)


def test_tenant_scoped_tables_includes_session_tables():
    from app.main import _TENANT_SCOPED_TABLES
    assert "sessions" in _TENANT_SCOPED_TABLES
    assert "candidate_session_tokens" in _TENANT_SCOPED_TABLES


def test_scheduler_router_registered():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any(p.startswith("/api/scheduler/") for p in paths)


def test_candidate_session_router_registered():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any(p.startswith("/api/candidate-session/") for p in paths)
