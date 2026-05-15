"""JD module — raw-JD-to-enriched-JD-to-signals pipeline.

Public surface for cross-module callers. Routers and Dramatiq actors are
NOT exported — actors are dispatched only from within this module's own
router. The unified job-creation flow (see docs/superpowers/specs/
2026-05-14-unified-job-creation-flow-design.md) removed the previous
cross-module dispatch site (the unblock cascade in org_units/router.py),
so the actor surface stays internal.
"""
from app.modules.jd.authz import require_job_access
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.schemas import default_evaluation_method
from app.modules.jd.service import delete_job_posting
from app.modules.jd.state_machine import transition


__all__ = [
    "JobPosting",
    "JobPostingSignalSnapshot",
    "default_evaluation_method",
    "delete_job_posting",
    "require_job_access",
    "transition",
]
