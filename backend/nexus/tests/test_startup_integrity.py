"""Phase 4 — startup integrity tests.

Asserts that the per-module models.py split (Phase 4a) leaves Base
in a configurable state at boot. If a string-FK target is misspelled
or a model module fails to load, configure() raises here instead of
silently failing on the first cross-module query.
"""

from __future__ import annotations


def test_every_module_models_py_loads_and_registers_tables():
    """Importing each module's models.py must register its tables on
    Base.metadata. Catches typos / missing model migrations.
    """
    # Force load every per-module models file.
    import app.modules.auth.models  # noqa: F401
    import app.modules.audit.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401

    from app.database import Base

    # Every table that app/main.py's _TENANT_SCOPED_TABLES tracks must
    # appear in Base.metadata.tables after the per-module imports above.
    expected = {
        "users",
        "user_role_assignments",
        "user_invites",
        "audit_log",
        "candidates",
        "candidate_job_assignments",
        "candidate_stage_progress",
        "candidate_session_tokens",
        "job_postings",
        "job_posting_signal_snapshots",
        "clients",
        "organizational_units",
        "pipeline_templates",
        "pipeline_template_stages",
        "job_pipeline_instances",
        "job_pipeline_stages",
        "pipeline_stage_participants",
        "stage_question_banks",
        "stage_questions",
        "roles",
        "sessions",
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    assert not missing, f"Tables missing from Base.metadata: {missing}"


def test_base_registry_configure_resolves_all_string_fks():
    """Base.registry.configure() walks every mapper and resolves string
    FK targets. If any FK references a non-existent table name, this
    raises sqlalchemy.exc.InvalidRequestError.
    """
    # Same imports as above — needed in case this test runs in isolation.
    import app.modules.auth.models  # noqa: F401
    import app.modules.audit.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401

    from app.database import Base

    # No exception → all FK strings resolved.
    Base.registry.configure()
