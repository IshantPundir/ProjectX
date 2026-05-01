"""Question bank module — per-stage AI-generated question banks.

NOTE: ``recompute_and_persist_stale`` export is DEFERRED to Stage E.2
(sub-commit 4d-2). It cannot be eagerly imported here while
``app/models.py`` is still a re-export shim — see auth/__init__.py for
the cycle explanation. Removing the shim in 4d-2 lets us add it.
"""
from app.modules.question_bank.models import StageQuestion, StageQuestionBank

__all__ = ["StageQuestion", "StageQuestionBank"]
