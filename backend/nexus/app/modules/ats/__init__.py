"""ATS integration module — public API.

Cross-module callers MUST import from this `__init__.py`, never deep-import.
Two documented exceptions (per backend CLAUDE.md):
  - app/worker.py deep-imports actors to register them with the broker
  - app/main.py deep-imports router to register it with the FastAPI app
"""
from __future__ import annotations

from app.modules.ats.adapter import ATSAdapter, ATSAdapterCapabilities
from app.modules.ats.connection import (
    ATSConnectionState,
    load_connection_state,
    persist_connection_state,
)
from app.modules.ats.constants import (
    ATS_VENDOR_CEIPAL,
    ATS_VENDOR_PREFIX,
    is_ats_source,
)
from app.modules.ats.errors import (
    ATSAuthorizationError,
    ATSConnectionNotFoundError,
    ATSCredentialsInvalidError,
    ATSError,
    ATSNetworkError,
    ATSPermanentError,
    ATSRateLimitedError,
    ATSTransientError,
    ATSUnknownVendorError,
    ATSVendorContractError,
)
from app.modules.ats.registry import SUPPORTED_VENDORS, get_ats_adapter
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientContact,
    ATSClientPayload,
    ATSJobPayload,
    ATSJobStatus,
    ATSSubmissionPayload,
    ATSUserPayload,
)

__all__ = [
    # Protocol + capabilities
    "ATSAdapter",
    "ATSAdapterCapabilities",
    # Connection state lifecycle
    "ATSConnectionState",
    "load_connection_state",
    "persist_connection_state",
    # Constants
    "ATS_VENDOR_CEIPAL",
    "ATS_VENDOR_PREFIX",
    "is_ats_source",
    # DTOs
    "ATSApplicantPayload",
    "ATSClientContact",
    "ATSClientPayload",
    "ATSJobPayload",
    "ATSJobStatus",
    "ATSSubmissionPayload",
    "ATSUserPayload",
    # Errors
    "ATSError",
    "ATSPermanentError",
    "ATSCredentialsInvalidError",
    "ATSAuthorizationError",
    "ATSVendorContractError",
    "ATSUnknownVendorError",
    "ATSConnectionNotFoundError",
    "ATSTransientError",
    "ATSNetworkError",
    "ATSRateLimitedError",
    # Registry
    "SUPPORTED_VENDORS",
    "get_ats_adapter",
]
