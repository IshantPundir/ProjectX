"""JD module — raw-JD-to-enriched-JD-to-signals pipeline.

Public surface for cross-module callers. Routers + Dramatiq actors
are NOT exported (per the modular-monolith public-API discipline).

NOTE: ``require_job_access``, ``transition``, and ``delete_job_posting``
exports are DEFERRED to Stage E.2 (sub-commit 4d-2). They cannot be
eagerly imported here while ``app/models.py`` is still a re-export shim
— the resulting load chain deadlocks via "partially initialized
module 'app.models'". Removing the shim in 4d-2 breaks the cycle.
"""
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot

__all__ = [
    "JobPosting",
    "JobPostingSignalSnapshot",
]
