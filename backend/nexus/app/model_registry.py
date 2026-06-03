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

When you add a new model module, add it to the body below — the single source of
truth.
"""

from app.database import Base


def configure_all_models() -> None:
    """Import all ORM model modules and run `Base.registry.configure()`.

    These are side-effect imports (each registers its ORM tables on the shared
    declarative `Base`); the local-import style keeps them out of this module's
    public import surface and avoids import cycles at module load.
    """
    import app.modules.audit.models  # noqa: F401
    import app.modules.auth.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.reel.models  # noqa: F401
    import app.modules.reporting.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401
    import app.modules.tenant_settings.models  # noqa: F401
    import app.modules.vision.models  # noqa: F401

    Base.registry.configure()
