"""ATSImporter — five-phase orchestrator translating ATS DTOs to ProjectX rows.

Each phase opens its OWN bypass-RLS DB session, sets `app.current_tenant`,
runs inside an OTel span, and commits independently. Partial-failure tolerance:
a failure in phase N leaves phases 1..N-1 durable and their cursors advanced.

Phase ordering is sequential by data dependency:
  1. clients     → client_account org_units (auto-create with stub profile)
  2. users       → ats_user_mappings (reference data; recruiter maps later)
  3. jobs        → job_postings (blocked_pending_client_setup if profile=pending)
  4. applicants  → candidates (via candidates.service.import_candidate)
  5. submissions → candidate_job_assignments (per-job; uses jobs touched in phase 3)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
from opentelemetry import trace
from sqlalchemy import text

from app.database import get_bypass_session
from app.modules.ats.adapter import ATSAdapter


logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


@dataclass
class PhaseResult:
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    sync_started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def as_counts(self) -> dict:
        """Compact JSON-able form for ats_sync_logs.entity_counts."""
        return {"new": self.new, "updated": self.updated, "skipped": self.skipped,
                "errors": len(self.errors)}


@dataclass
class SyncResult:
    clients: PhaseResult | None = None
    users: PhaseResult | None = None
    jobs: PhaseResult | None = None
    applicants: PhaseResult | None = None
    submissions: PhaseResult | None = None

    def entity_counts(self) -> dict:
        return {
            name: getattr(self, name).as_counts() if getattr(self, name) else None
            for name in ("clients", "users", "jobs", "applicants", "submissions")
        }


class ATSImporter:
    async def sync_tenant(self, adapter: ATSAdapter) -> SyncResult:
        result = SyncResult()
        result.clients     = await self._run_phase("clients",     self._sync_clients,     adapter)
        result.users       = await self._run_phase("users",       self._sync_users,       adapter)
        result.jobs        = await self._run_phase("jobs",        self._sync_jobs,        adapter)
        result.applicants  = await self._run_phase("applicants",  self._sync_applicants,  adapter)
        result.submissions = await self._run_phase("submissions", self._sync_submissions, adapter)
        return result

    async def _run_phase(self, name, fn, adapter) -> PhaseResult:
        tenant_id = adapter.state.tenant_id
        with tracer.start_as_current_span(f"ats.sync.{name}",
                                          attributes={"ats.vendor": adapter.vendor,
                                                      "tenant_id": str(tenant_id)}):
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                phase_result = await fn(db, adapter)
                await db.commit()
            adapter.state.last_synced_cursors[name] = phase_result.sync_started_at.isoformat()
            logger.info(
                "ats.sync.phase.ok",
                phase=name, vendor=adapter.vendor,
                tenant_id=str(tenant_id),
                **phase_result.as_counts(),
            )
            return phase_result

    # Phase methods — implementations land in Tasks 19–22.
    async def _sync_clients(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 19")

    async def _sync_users(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 19")

    async def _sync_jobs(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 20")

    async def _sync_applicants(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 21")

    async def _sync_submissions(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 22")
