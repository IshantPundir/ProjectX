"""ATSAdapter Protocol — the contract every ATS implementation satisfies.

Construction goes through app.modules.ats.registry.get_ats_adapter(state).
The adapter holds a reference to ATSConnectionState; it mutates token fields
during a sync (refresh), and the orchestrator persists those mutations after
the sync completes.

Adapter instances are short-lived (one per sync run) and NOT thread-safe.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import ClassVar, Protocol

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientPayload,
    ATSJobPayload,
    ATSSubmissionPayload,
    ATSUserPayload,
)


class ATSAdapter(Protocol):
    """Per-tenant ATS adapter.

    All list_* methods return AsyncIterators that handle pagination internally.
    All methods may raise:
      - ATSCredentialsInvalidError (permanent; reconnect required)
      - ATSAuthorizationError (permanent; scope insufficient)
      - ATSVendorContractError (permanent; vendor schema drift)
      - ATSRateLimitedError (transient; caller advances next_poll_at)
      - ATSNetworkError (transient; caller retries)
    """

    vendor: ClassVar[str]        # 'ceipal', 'greenhouse', 'workday'
    state: ATSConnectionState    # mutable; orchestrator persists after sync

    async def ensure_authenticated(self) -> None:
        """Refresh tokens if expired or near-expiry (proactive at 80% lifetime).

        Idempotent — safe to call when tokens are already valid. Raises
        ATSCredentialsInvalidError if the stored credentials no longer work.
        """
        ...

    async def list_job_statuses(self) -> list[dict]:
        """Vendor-native available job-status list.

        Shape: ``[{"id": int, "name": str}, ...]`` for Ceipal. Adapters that
        do not have a status concept (Greenhouse uses stages; Workday differs
        again) raise ``NotImplementedError``; the router translates that to
        a 501.
        """
        ...

    async def count_jobs(
        self,
        *,
        since: datetime | None = None,
        job_status_ids: list[int] | None = None,
    ) -> int:
        """Total count of jobs matching the filter, used to seed the
        progress bar's denominator. Adapters with no count endpoint return
        ``-1`` — the frontend renders an indeterminate state.
        """
        ...

    def list_clients(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        """Yield client records. If `since` is None: full sync."""
        ...

    def list_users(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        """Yield user records (recruiters/admins on the tenant's ATS account)."""
        ...

    def list_jobs(
        self,
        since: datetime | None = None,
        *,
        job_status_ids: list[int] | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        """Yield job postings. ``job_status_ids`` filters server-side where
        the vendor supports it (Ceipal); adapters without server-side filter
        MAY ignore the kwarg and filter client-side.
        """
        ...

    def list_applicants(
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        """Yield applicants — the people. Delta sync where supported."""
        ...

    def list_submissions(
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        """Yield submissions for a specific job — the applicant↔job link entity."""
        ...
