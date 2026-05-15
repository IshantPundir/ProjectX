"""ATSAdapter Protocol — the contract every ATS implementation satisfies.

Construction goes through `app.modules.ats.registry.get_ats_adapter(state)`.
The adapter holds a reference to ATSConnectionState; it mutates token fields
during a sync (refresh) and may capture `tenant_timezone` once observed. The
orchestrator persists those mutations after the sync completes.

Adapter instances are short-lived (one per sync run) and NOT thread-safe.

Plugin contract — every vendor-specific concern lives behind this Protocol:

  - field-name normalization (Ceipal's `email_id` vs `email`)
  - encoded-ID URL-escaping (`/`, `+`, `=` in opaque IDs)
  - timezone-naive → UTC normalization using `state.tenant_timezone`
  - HTML entity decoding (`&nbsp;`, `&#39;`, `<br />`)
  - sentinel-value handling (`industry_exp == "0"` → None,
    `closing_date == "Open Until Filled"` → None)
  - per-vendor pagination

The orchestrator never sees a vendor field name, a vendor timestamp string,
or a vendor sentinel value. Everything crossing the Protocol boundary uses
ProjectX-canonical names defined in `app/modules/ats/schemas.py`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientPayload,
    ATSJobPayload,
    ATSJobStatus,
    ATSSubmissionPayload,
    ATSUserPayload,
)


@dataclass(frozen=True)
class ATSAdapterCapabilities:
    """Vendor-agnostic descriptor of what this adapter can do.

    The orchestrator branches on these flags to skip optimizations a vendor
    doesn't support. Adding a new flag is additive; existing adapters that
    don't set it keep working — the orchestrator must handle absence as the
    conservative path.
    """

    # If False, the orchestrator must ignore modified_after and walk the full
    # filter on every sync.
    supports_modified_after_cursor: bool
    # Reserved for future per-job submission cursors. Not used by Ceipal MVP.
    supports_per_job_submission_cursor: bool
    # If True, the orchestrator skips the iter_clients walk and resolves the
    # client by name via a vendor-side search endpoint. Ceipal: False.
    supports_client_search_by_name: bool
    # If True, the orchestrator must call enrich_job() to fetch the
    # client_external_name (Ceipal: only available on the detail endpoint).
    job_detail_required_for_client_name: bool
    # Soft pacing target. Adapter respects this internally for inter-request
    # gaps; orchestrator does not consult it directly.
    rate_limit_qps: float


class ATSAdapter(Protocol):
    """A vendor adapter for one specific ATS (Ceipal, Greenhouse, Workday, …).

    All datetimes returned by adapter methods MUST be timezone-aware UTC.
    All string fields MUST be trimmed; empty strings MUST be returned as None
    when they semantically mean "absent".

    Errors raised by adapter methods MUST be one of:
      - ATSCredentialsInvalidError  (permanent; reconnect required)
      - ATSAuthorizationError       (permanent; scope insufficient)
      - ATSVendorContractError      (permanent; vendor schema drift)
      - ATSRateLimitedError         (transient; orchestrator finalizes
                                     sync_log as 'partial')
      - ATSNetworkError             (transient; orchestrator may retry)
    """

    vendor: ClassVar[str]                       # e.g. 'ats_ceipal'
    capabilities: ClassVar[ATSAdapterCapabilities]
    state: ATSConnectionState                    # mutable; orchestrator persists

    async def ensure_authenticated(self) -> None:
        """Idempotent. Refresh tokens at ≥80% of access-token lifetime."""
        ...

    async def list_job_statuses(self) -> list[ATSJobStatus]:
        """For the filter-config UI. Should be cheap; one call typically."""
        ...

    def iter_jobs(
        self,
        *,
        status_external_ids: list[str],
        modified_after: datetime | None,
    ) -> AsyncIterator[ATSJobPayload]:
        """Yield jobs whose `external_status_id` is in `status_external_ids`
        and whose vendor-side `modified` timestamp is strictly after
        `modified_after` (None → full filter walk, used for first-sync and
        post-reset-cursor).

        For Ceipal, this is the full getJobPostingsList row. The
        `client_external_name` field is left None — the orchestrator calls
        `enrich_job` on each new-or-changed job to fetch it.
        """
        ...

    async def enrich_job(self, job: ATSJobPayload) -> ATSJobPayload:
        """Fill in fields not available on the list endpoint.

        For Ceipal: calls getJobPostingDetails to populate
        `client_external_name`. For vendors where capabilities
        `job_detail_required_for_client_name=False`, this is a no-op return.

        Called once per new-or-changed job by the orchestrator.
        """
        ...

    def iter_clients(self) -> AsyncIterator[ATSClientPayload]:
        """Yield all clients. Used to build the in-memory name→external_id
        index when capabilities.supports_client_search_by_name is False.

        Adapter MUST handle pagination internally.
        """
        ...

    async def get_client(self, *, external_id: str) -> ATSClientPayload:
        """Source of truth for organizational_units field values.

        For Ceipal: getClientDetails. Returns the client record including
        `contacts[]` (client-side HR personnel — NOT vendor users).
        """
        ...

    async def get_user(self, *, external_id: str) -> ATSUserPayload:
        """Source of truth for users field values.

        For Ceipal: getUserDetails. Returns the recruiter/admin user record.
        """
        ...

    def iter_submissions(
        self,
        *,
        job_external_id: str,
        modified_after: datetime | None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        """Yield submissions for a specific job.

        `modified_after` is a hint — vendors that don't support per-job
        submission cursors (capabilities.supports_per_job_submission_cursor
        is False) MAY ignore it and yield all submissions for the job.
        Ceipal: ignored — all submissions are yielded whenever the parent
        job is touched.
        """
        ...

    async def get_applicant(
        self, *, external_id: str,
    ) -> ATSApplicantPayload:
        """PII-bearing. Orchestrator strips sensitive fields via
        `app.modules.candidates.pii.strip_sensitive_pii` before persistence.

        For Ceipal: getApplicantDetails. Raw payload may contain
        `aadhar_number`, resume tokens, etc. — these MUST be absent from
        the `raw` field on the returned payload (adapter strips at the
        wire boundary; the orchestrator's strip is a second defence layer).
        """
        ...
