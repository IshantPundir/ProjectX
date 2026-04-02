"""ATS adapter protocol.

Every ATS integration (Ceipal, Greenhouse, Workday) implements this interface.
The core pipeline never changes — only adapters are swapped.
"""

from typing import Protocol

from app.modules.ats.schemas import Candidate, InterviewOutcome, Job


class ATSAdapter(Protocol):
    async def fetch_new_jobs(self, tenant_id: str) -> list[Job]: ...
    async def fetch_new_candidates(self, tenant_id: str, job_id: str) -> list[Candidate]: ...
    async def push_interview_outcome(self, tenant_id: str, outcome: InterviewOutcome) -> None: ...
