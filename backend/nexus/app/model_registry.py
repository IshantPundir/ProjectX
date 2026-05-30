"""Import every ORM model module, then configure the SQLAlchemy mapper registry.

Phase 4 of the modular-monolith refactor split `app/models.py` per module. Every
model module must be imported so SQLAlchemy can resolve cross-module string FKs
(e.g. `session_proctoring_analysis.tenant_id -> clients.id`) at configure() time
rather than failing at the first query with `NoReferencedTableError`.

Both the API (`app/main.py` lifespan) and the standalone Dramatiq worker
entrypoints that register only a subset of actors (`app/vision_worker.py`) call
this, so every process ends up with a complete, configured registry. The default
`app/worker.py` imports enough actor modules to pull the full model set in
transitively, but calling this is the robust, order-independent way.

When you add a new model module, add it here — the single source of truth.
"""


def configure_all_models() -> None:
    """Import all ORM model modules and run `Base.registry.configure()`."""
    # noqa block: these are side-effect imports (register ORM tables); ordering
    # is irrelevant and isort grouping does not meaningfully apply.
    import app.modules.audit.models  # noqa: F401, I001
    import app.modules.auth.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.reporting.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401
    import app.modules.tenant_settings.models  # noqa: F401
    import app.modules.vision.models  # noqa: F401

    from app.database import Base

    Base.registry.configure()
