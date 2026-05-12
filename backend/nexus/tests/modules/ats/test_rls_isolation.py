"""RLS policy presence checks for the 5 ATS tables (Task 3 of the ATS plan).

Why introspection-only (not a live cross-tenant INSERT/SELECT test):

  The test DB (``projectx_test``) is built via ``Base.metadata.create_all``,
  not via Alembic migrations (see ``tests/conftest.py`` lines 23-31). That
  means the test DB has no RLS policies on any table, no ``nexus_app``
  role, and ``DB_RUNTIME_ROLE`` is force-disabled. The default ``postgres``
  role has ``rolbypassrls=true``, so a literal cross-tenant SELECT under
  ``SET LOCAL app.current_tenant = ...`` would silently bypass every
  policy and return rows from both tenants — meaning a "broken isolation"
  is indistinguishable from "RLS works fine" in the test DB.

  ``app/main.py::_assert_rls_completeness`` already runs at every app boot
  against the real (migrated) DB and aborts startup if any tenant-scoped
  table is missing ``tenant_isolation`` (with non-NULL WITH CHECK) or
  ``service_bypass``. The 5 ATS tables are enumerated in
  ``_TENANT_SCOPED_TABLES`` (Task 2, commit 99646dd), so that startup
  check is the live runtime gate.

  What this test adds on top is a **static regression gate** that runs on
  every developer's first ``pytest`` invocation with zero infrastructure
  dependencies. It verifies:

  1. The migration that introduces the 5 tables (``0031_ats_core``) calls
     the canonical RLS helper on every one of them. Forgetting one
     would make the startup check fail at deploy — this catches it
     pre-merge instead.
  2. The canonical RLS helper actually emits the load-bearing SQL
     fragments per backend/CLAUDE.md → "RLS Pattern": ``USING``,
     ``WITH CHECK``, ``NULLIF(...)`` (the empty-string-GUC trap fix from
     migration 0011), ``service_bypass`` keyed on ``app.bypass_rls``,
     and a GRANT to ``nexus_app``.
  3. All 5 tables are registered in ``app/main.py::_TENANT_SCOPED_TABLES``
     so the startup-time live check covers them.

  A future regression that drops the ``NULLIF`` wrap, or rewrites
  ``tenant_isolation`` as ``FOR SELECT USING (...)`` (silently blocking
  writes), or omits a new ATS table from the helper-application list,
  fails this test loudly.

If we eventually wire up a live cross-tenant isolation test, it belongs
behind an ``@pytest.mark.integration`` marker keyed on a probe that the
target DB has migration 0031 applied — that's deferred to a later task.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# The 5 ATS tables introduced by migration 0031_ats_core, in the order
# defined by the plan/spec. The same tuple lives in
# ``migrations/versions/0031_ats_core.py::_NEW_TABLES``. We re-declare it
# here intentionally — this test is the assertion that the migration
# matches the spec, so importing the tuple from the migration would
# make the test self-fulfilling.
ATS_TABLES: tuple[str, ...] = (
    "ats_connections",
    "ats_client_mappings",
    "ats_user_mappings",
    "ats_job_recruiter_assignments",
    "ats_sync_logs",
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Read the text of migration 0031_ats_core.py once for all tests."""
    repo_root = Path(__file__).resolve().parents[3]  # backend/nexus
    migration_path = repo_root / "migrations" / "versions" / "0031_ats_core.py"
    assert migration_path.exists(), f"migration not found at {migration_path}"
    return migration_path.read_text()


def _strip_python_line_comments(source: str) -> str:
    """Strip everything from the first un-quoted ``#`` to end-of-line.

    Naive but adequate for our purpose: the migration file has no string
    literals containing a ``#`` (verified manually). A commented-out
    ``# _apply_canonical_rls("ats_sync_logs")`` line should NOT count
    as a real invocation — otherwise a sloppy reviewer who leaves a
    commented-out call in the migration would silently pass this test.
    """
    out_lines: list[str] = []
    for line in source.splitlines():
        idx = line.find("#")
        if idx == -1:
            out_lines.append(line)
        else:
            out_lines.append(line[:idx])
    return "\n".join(out_lines)


def test_all_five_tables_get_canonical_rls(migration_source: str) -> None:
    """Each ATS table must have ``_apply_canonical_rls("<table>")`` invoked.

    Catches: someone adds a 6th ATS table to ``_NEW_TABLES`` but forgets
    to call the helper on it — the startup check would then fail at
    deploy. This test fails earlier, at pytest time. Commented-out calls
    do not count (we strip line comments before regexing).
    """
    cleaned = _strip_python_line_comments(migration_source)
    missing: list[str] = []
    for table in ATS_TABLES:
        # Match _apply_canonical_rls("<table>") with either quote style.
        pattern = re.compile(rf'_apply_canonical_rls\(["\']{re.escape(table)}["\']\)')
        if not pattern.search(cleaned):
            missing.append(table)
    assert not missing, (
        f"migration 0031 does not call _apply_canonical_rls() for these tables: "
        f"{missing!r}. Every ATS table must have the canonical RLS pair applied "
        f"or tenant isolation will silently break under the nexus_app role."
    )


def test_canonical_rls_helper_emits_tenant_isolation_with_nullif(
    migration_source: str,
) -> None:
    """``_apply_canonical_rls`` must emit ``tenant_isolation`` with both
    ``USING`` and ``WITH CHECK`` wrapped in ``NULLIF(..., '')::uuid``.

    These three properties are load-bearing per backend/CLAUDE.md → "RLS
    Pattern":

    * ``USING`` + ``WITH CHECK`` together (no ``FOR SELECT``) — without
      the matching ``WITH CHECK``, tenant-scoped INSERT/UPDATE/DELETE
      silently fall through to ``service_bypass``, which is false when
      ``app.bypass_rls`` is unset, so every write is blocked. This is
      the "FOR SELECT trap" that broke Phase 1 tables until migration
      0008/0009.
    * ``NULLIF(current_setting('app.current_tenant', true), '')::uuid``
      — without ``NULLIF``, the next pooled request after a
      ``SET LOCAL`` reverts the custom GUC to empty string ``""``, not
      NULL, and the next ``::uuid`` cast crashes the query with
      ``invalid input syntax for type uuid``. Migration 0011 wraps
      every policy with ``NULLIF``.
    """
    # The migration defines a small helper that takes a table name and
    # emits the RLS SQL. We pluck out that helper's body and assert the
    # canonical fragments are all present.
    helper_match = re.search(
        r"def\s+_apply_canonical_rls\s*\(.*?\)\s*->\s*None:(?P<body>.+?)(?=\ndef\s|\Z)",
        migration_source,
        re.DOTALL,
    )
    assert helper_match is not None, (
        "could not locate _apply_canonical_rls() in migration 0031 — "
        "either the helper was renamed or removed. The test below assumes "
        "the canonical helper exists."
    )
    body = helper_match.group("body")

    # Names of the two policies — both must appear.
    assert "tenant_isolation" in body, (
        "_apply_canonical_rls() body missing 'tenant_isolation' policy name. "
        "Tenant isolation policy must be named exactly this so the startup "
        "check (_assert_rls_completeness) finds it in pg_policies."
    )
    assert "service_bypass" in body, (
        "_apply_canonical_rls() body missing 'service_bypass' policy name. "
        "Service-bypass policy must be named exactly this so admin/internal "
        "code paths using get_bypass_db() can write across tenants."
    )

    # USING + WITH CHECK — both clauses must be present on tenant_isolation.
    # We do a generous "both keywords appear in the body" check rather than
    # a strict regex on the exact SQL, so reformatting (e.g. newline shifts)
    # doesn't break the test.
    assert "USING" in body and "WITH CHECK" in body, (
        "_apply_canonical_rls() must emit both USING and WITH CHECK on "
        "tenant_isolation. A FOR SELECT USING (...) policy without a "
        "matching WITH CHECK silently blocks writes from tenant-scoped "
        "sessions (the 'FOR SELECT trap' — see backend/CLAUDE.md)."
    )

    # NULLIF wrap — non-negotiable per migration 0011. We check for the
    # SQL fragment (not just the word "NULLIF") so a docstring mention
    # alone doesn't satisfy the assertion. The canonical pattern is
    # ``NULLIF(current_setting('app.current_tenant', true), '')::uuid``.
    nullif_wrap_pattern = re.compile(
        r"NULLIF\s*\(\s*current_setting\s*\(\s*['\"]app\.current_tenant['\"]"
    )
    assert nullif_wrap_pattern.search(body), (
        "_apply_canonical_rls() must wrap current_setting('app.current_tenant') "
        "in NULLIF(..., '')::uuid. Without NULLIF, the next pooled connection "
        "after SET LOCAL reverts the GUC to '' (empty string, not NULL), and "
        "the ::uuid cast crashes with 'invalid input syntax for type uuid'. "
        "Pattern not matched: NULLIF(current_setting('app.current_tenant', ...))."
    )

    # The current_tenant GUC must be referenced in the SQL.
    assert "current_setting('app.current_tenant'" in body, (
        "_apply_canonical_rls() must read tenant from current_setting"
        "('app.current_tenant', true). Hardcoding the GUC name here is "
        "the contract with get_tenant_db() in app/database.py."
    )

    # The bypass GUC must be referenced on service_bypass.
    assert "current_setting('app.bypass_rls'" in body, (
        "service_bypass must read current_setting('app.bypass_rls', true). "
        "This is the contract with get_bypass_db() in app/database.py."
    )

    # The nexus_app role must be granted SELECT/INSERT/UPDATE/DELETE so the
    # NOBYPASSRLS runtime role can actually exercise the policies. Without
    # this grant, every query crashes with 'permission denied' under
    # SET LOCAL ROLE nexus_app.
    assert "GRANT" in body and "nexus_app" in body, (
        "_apply_canonical_rls() must GRANT DML to nexus_app. Without the "
        "GRANT, the NOBYPASSRLS runtime role used by every request gets "
        "'permission denied' instead of exercising the RLS policy."
    )


def test_all_five_tables_registered_in_tenant_scoped_tables() -> None:
    """The 5 ATS tables must be in ``app/main._TENANT_SCOPED_TABLES``.

    The runtime startup check (``_assert_rls_completeness``) iterates
    that tuple and asserts every entry has both ``tenant_isolation``
    (with non-NULL WITH CHECK) and ``service_bypass`` in ``pg_policies``.
    A table that exists in the DB with policies but isn't enumerated in
    the tuple is invisible to that check.
    """
    # Import lazily so test collection doesn't open a DB engine.
    from app.main import _TENANT_SCOPED_TABLES

    missing: list[str] = [t for t in ATS_TABLES if t not in _TENANT_SCOPED_TABLES]
    assert not missing, (
        f"these ATS tables are not in app/main._TENANT_SCOPED_TABLES: "
        f"{missing!r}. The startup RLS check iterates that tuple — tables "
        f"missing from it are silently skipped by the live check."
    )
