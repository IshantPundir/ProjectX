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
from sqlalchemy import select, text

from app.database import get_bypass_session
from app.modules.ats.adapter import ATSAdapter
from app.modules.audit import log_event


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
        """Phase 1: upsert ats_client_mappings; auto-create client_account
        org_units (with stub profile + completion_status='pending') for new clients."""
        # Function-local model imports — keeps the module-level import graph
        # minimal and avoids touching ORM modules before _create_tables runs
        # in tests.
        from app.modules.ats.models import ATSClientMapping, ATSConnection
        from app.modules.org_units.models import OrganizationalUnit

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        # Look up root company unit (for parent_unit_id) and the connection's created_by
        root = await db.scalar(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == tenant_id,
                OrganizationalUnit.is_root.is_(True),
            )
        )
        if root is None:
            raise RuntimeError(f"tenant {tenant_id} has no root company org_unit")

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        since = self._cursor_or_none(adapter.state, "clients")
        async for payload in adapter.list_clients(since=since):
            existing = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_id == payload.external_id,
                )
            )
            if existing is not None:
                # Update mapping metadata; do NOT rename the org_unit.
                existing.external_client_name = payload.name
                existing.source_metadata = {"contacts": payload.contacts, "raw": payload.raw}
                existing.last_synced_at = datetime.now(tz=timezone.utc)
                result.updated += 1
                continue

            # Create the org_unit with stub profile
            stub = {
                "name": payload.name,
                "website": payload.website,
                "industry": payload.industry,
                "country": payload.country,
                "state": payload.state,
                "city": payload.city,
                "address": payload.address,
            }
            stub = {k: v for k, v in stub.items() if v is not None}
            new_unit = OrganizationalUnit(
                client_id=tenant_id, parent_unit_id=root.id,
                name=payload.name, unit_type="client_account",
                is_root=False, company_profile=stub,
                company_profile_completion_status="pending",
                created_by=created_by,
            )
            db.add(new_unit)
            await db.flush()

            db.add(ATSClientMapping(
                tenant_id=tenant_id, ats_vendor=adapter.vendor,
                external_client_id=payload.external_id,
                external_client_name=payload.name,
                org_unit_id=new_unit.id,
                source_metadata={"contacts": payload.contacts, "raw": payload.raw},
            ))
            await log_event(
                db, tenant_id=tenant_id, actor_id=created_by,
                actor_email="ats-import",
                action="ats.client_mapping.created",
                resource="ats_client_mapping",
                resource_id=new_unit.id,
                payload={"vendor": adapter.vendor,
                         "external_client_id": payload.external_id,
                         "org_unit_id": str(new_unit.id)},
            )
            result.new += 1
        return result

    async def _sync_users(self, db, adapter) -> PhaseResult:
        """Phase 2: upsert ats_user_mappings. internal_user_id stays NULL —
        recruiter explicitly maps via UI later."""
        from app.modules.ats.models import ATSUserMapping

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        async for payload in adapter.list_users(since=None):
            existing = await db.scalar(
                select(ATSUserMapping).where(
                    ATSUserMapping.tenant_id == tenant_id,
                    ATSUserMapping.ats_vendor == adapter.vendor,
                    ATSUserMapping.external_user_id == payload.external_id,
                )
            )
            if existing is not None:
                existing.external_user_email = payload.email
                existing.external_user_display_name = payload.display_name
                existing.external_user_role = payload.role
                existing.external_user_status = payload.status
                existing.external_user_metadata = payload.raw
                existing.last_synced_at = datetime.now(tz=timezone.utc)
                result.updated += 1
                continue

            db.add(ATSUserMapping(
                tenant_id=tenant_id, ats_vendor=adapter.vendor,
                external_user_id=payload.external_id,
                external_user_email=payload.email,
                external_user_display_name=payload.display_name,
                external_user_role=payload.role,
                external_user_status=payload.status,
                external_user_metadata=payload.raw,
                internal_user_id=None,
            ))
            result.new += 1
        return result

    @staticmethod
    def _cursor_or_none(state, phase_name: str) -> datetime | None:
        raw = state.last_synced_cursors.get(phase_name)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    async def _sync_jobs(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 20")

    async def _sync_applicants(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 21")

    async def _sync_submissions(self, db, adapter) -> PhaseResult:
        raise NotImplementedError("Task 22")
