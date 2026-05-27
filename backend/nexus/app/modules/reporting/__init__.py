"""reporting module — post-session report compilation and score aggregation.

Public API surface for cross-module consumers.
"""

from app.modules.reporting.models import SessionReport

# actors.py / service.py depend on the old aggregate API (SignalObservation,
# combine_signal) that is being replaced in the Task-4 rework.  Those modules
# are rewritten in a later task; until then we guard this import so that the
# scoring sub-package (and its tests) can be imported without pulling in the
# not-yet-updated callers.
try:
    from app.modules.reporting.actors import score_session_report  # noqa: F401
    __all__ = ["SessionReport", "score_session_report"]
except ImportError:
    __all__ = ["SessionReport"]
