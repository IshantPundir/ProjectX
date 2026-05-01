"""Audit module — append-only event log.

NOTE: ``log_event`` and ``actions`` exports are DEFERRED to Stage E.2
(sub-commit 4d-2). They cannot be eagerly imported here while
``app.models`` is still a re-export shim — ``audit.service`` imports
from ``app.models``, which re-imports per-module models, and the chain
deadlocks via "partially initialized module 'app.models'". Removing
the shim in 4d-2 breaks the cycle.
"""
from app.modules.audit.models import AuditLog

__all__ = ["AuditLog"]
