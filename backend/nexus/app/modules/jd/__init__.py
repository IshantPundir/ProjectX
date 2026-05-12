"""JD module — raw-JD-to-enriched-JD-to-signals pipeline.

Public surface for cross-module callers. Routers are NOT exported.

Dramatiq actors are normally not exported either, but
`extract_and_enhance_jd` is re-exported here because the
profile-completion unblock cascade in `org_units/router.py` legitimately
needs to fire it (an `unblocked → enriching` transition is JD-domain
behavior triggered by an org_units mutation). The actor is the public
contract for "start JD enrichment now"; everything else (loading the
JD row, RLS context, retry policy) is internal to this module.

The actor is exposed lazily via `__getattr__` rather than an eager
top-level import, because `app.modules.jd.actors` reaches into
`jd.service` which transitively imports `candidates` and `pipelines`
— eagerly chaining those during JD package init creates a circular
import (jd ↔ candidates). Lazy access through __getattr__ defers the
chain until first use, by which time the package graph is fully
initialised.
"""
from typing import TYPE_CHECKING, Any

from app.modules.jd.authz import require_job_access
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.schemas import default_evaluation_method
from app.modules.jd.service import delete_job_posting
from app.modules.jd.state_machine import transition

if TYPE_CHECKING:
    from app.modules.jd.actors import extract_and_enhance_jd


def __getattr__(name: str) -> Any:
    if name == "extract_and_enhance_jd":
        from app.modules.jd.actors import extract_and_enhance_jd as _actor
        return _actor
    raise AttributeError(f"module 'app.modules.jd' has no attribute {name!r}")


__all__ = [
    "JobPosting",
    "JobPostingSignalSnapshot",
    "default_evaluation_method",
    "delete_job_posting",
    "extract_and_enhance_jd",
    "require_job_access",
    "transition",
]
