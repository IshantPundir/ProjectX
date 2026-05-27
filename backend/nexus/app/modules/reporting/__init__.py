"""reporting module — post-session report compilation and score aggregation.

Public API surface for cross-module consumers.
"""

from app.modules.reporting.actors import score_session_report
from app.modules.reporting.models import SessionReport

__all__ = ["SessionReport", "score_session_report"]
