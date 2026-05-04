"""JD module — raw-JD-to-enriched-JD-to-signals pipeline.

Public surface for cross-module callers. Routers + Dramatiq actors
are NOT exported (per the modular-monolith public-API discipline).
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
