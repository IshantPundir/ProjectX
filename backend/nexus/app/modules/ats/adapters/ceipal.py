"""CeipalAdapter — STUB. Full implementation lands in Task 13+ (Phase 5).

This stub exists so app/modules/ats/registry.py can import it now. The
Protocol methods raise NotImplementedError until the implementation phase.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, ClassVar

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import (
    ATSApplicantPayload, ATSClientPayload, ATSJobPayload,
    ATSSubmissionPayload, ATSUserPayload,
)


class CeipalAdapter:
    vendor: ClassVar[str] = "ceipal"

    def __init__(self, state: ATSConnectionState) -> None:
        self.state = state

    async def ensure_authenticated(self) -> None:
        raise NotImplementedError("Phase 5")

    async def list_clients(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSClientPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover  (makes the function an async generator)

    async def list_users(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSUserPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover

    async def list_jobs(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSJobPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover

    async def list_applicants(  # type: ignore[override]
        self, since: datetime | None = None,
    ) -> AsyncIterator[ATSApplicantPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover

    async def list_submissions(  # type: ignore[override]
        self, job_external_id: str, since: datetime | None = None,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        raise NotImplementedError("Phase 5")
        yield  # pragma: no cover
