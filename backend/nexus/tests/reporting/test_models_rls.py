"""RLS coverage gate for the session_reports table.

This repo cannot live-test RLS in pytest: conftest runs against a separate
`projectx_test` DB built via create_all (no policies) with DB_RUNTIME_ROLE=""
(superuser → RLS bypassed). The real cross-tenant guarantee is enforced at
boot by `_assert_rls_completeness`, which refuses to start if any table in
`_TENANT_SCOPED_TABLES` is missing its `tenant_isolation` + `service_bypass`
policies. This test asserts session_reports is wired into that gate and that
its model is registered, so the boot assertion (and migration 0047's policy
pair) guard it the same as every other tenant-scoped table.
"""
from __future__ import annotations


def test_session_reports_in_tenant_scoped_rls_gate():
    """session_reports must be in _TENANT_SCOPED_TABLES so the startup RLS
    completeness assertion verifies its tenant_isolation + service_bypass
    policies on every boot (fail-closed)."""
    from app.main import _TENANT_SCOPED_TABLES

    assert "session_reports" in _TENANT_SCOPED_TABLES


def test_session_reports_model_registered_and_tenant_scoped():
    """The SessionReport ORM model registers the table on Base.metadata and
    carries a tenant_id column (the column the tenant_isolation policy filters)."""
    import app.modules.reporting.models  # noqa: F401  (side-effect: register table)
    from app.database import Base

    assert "session_reports" in Base.metadata.tables
    table = Base.metadata.tables["session_reports"]
    assert "tenant_id" in table.columns
    # session_id is unique (one current report per session)
    assert table.columns["session_id"].unique is True
