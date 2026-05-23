"""SHIM (CMI-1, master §3a): models relocated to app.modules.interview_runtime.results.
Re-exported here so v1's internal importers stay byte-stable. Deleted with v1 in M6."""
from app.modules.interview_runtime import (  # noqa: F401
    ClaimEntry,
    ClaimsPoolSnapshot,
)

__all__ = ["ClaimEntry", "ClaimsPoolSnapshot"]
