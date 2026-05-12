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
        """Phase 3: upsert job_postings; resolve client mapping → org_unit;
        gate status by org_unit.company_profile_completion_status.

        Skip semantics: a payload whose external_client_id has no matching
        ats_client_mappings row is logged + counted in ``result.skipped`` and
        does NOT raise — clients are expected to land in phase 1; a missing
        mapping is recoverable on the next poll.

        Update path preserves ``existing.status`` — recruiter state (e.g.
        signals_confirmed, pipeline_built) is never regressed by a re-import.
        Recruiter assignments use replace-all semantics: Ceipal is the source
        of truth for who is assigned to a JD, so the delete-then-insert
        approach drops any recruiter Ceipal no longer lists.
        """
        from app.modules.ats.models import (
            ATSClientMapping, ATSConnection, ATSJobRecruiterAssignment,
        )
        from app.modules.jd.models import JobPosting
        from app.modules.org_units.models import OrganizationalUnit

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        since = self._cursor_or_none(adapter.state, "jobs")
        async for payload in adapter.list_jobs(since=since):
            # Resolve client mapping — missing → skip + log, NOT error.
            mapping = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_id == payload.external_client_id,
                )
            )
            if mapping is None:
                logger.warning(
                    "ats.sync.jobs.skipped_missing_client_mapping",
                    external_job_id=payload.external_id,
                    external_client_id=payload.external_client_id,
                )
                result.skipped += 1
                continue

            org_unit = await db.get(OrganizationalUnit, mapping.org_unit_id)
            target_status = (
                "blocked_pending_client_setup"
                if org_unit.company_profile_completion_status == "pending"
                else "draft"
            )

            existing = await db.scalar(
                select(JobPosting).where(
                    JobPosting.tenant_id == tenant_id,
                    JobPosting.source == f"ats_{adapter.vendor}",
                    JobPosting.external_id == payload.external_id,
                )
            )
            if existing is not None:
                # NOTE: do NOT touch existing.status — preserves recruiter
                # state (signals_confirmed, pipeline_built, etc.). The
                # blocked → draft unblock is driven by
                # _unblock_pending_jobs_for_org_unit (Task 17), not re-import.
                existing.title = payload.title
                if payload.description:
                    existing.description_raw = payload.description
                existing.external_status = payload.status
                if payload.location:
                    existing.location = payload.location
                if payload.employment_type:
                    existing.employment_type = payload.employment_type
                if payload.work_arrangement:
                    existing.work_arrangement = payload.work_arrangement
                existing.salary_range_min = payload.salary_range_min
                existing.salary_range_max = payload.salary_range_max
                existing.salary_currency = payload.salary_currency
                job_id = existing.id
                result.updated += 1
            else:
                jp = JobPosting(
                    tenant_id=tenant_id, org_unit_id=org_unit.id,
                    title=payload.title,
                    description_raw=payload.description or "",
                    status=target_status,
                    source=f"ats_{adapter.vendor}",
                    external_id=payload.external_id,
                    external_status=payload.status,
                    location=payload.location,
                    employment_type=payload.employment_type,
                    work_arrangement=payload.work_arrangement,
                    salary_range_min=payload.salary_range_min,
                    salary_range_max=payload.salary_range_max,
                    salary_currency=payload.salary_currency,
                    created_by=created_by,
                )
                db.add(jp)
                await db.flush()
                job_id = jp.id
                await log_event(
                    db, tenant_id=tenant_id, actor_id=created_by,
                    actor_email="ats-import",
                    action="jd.imported_from_ats",
                    resource="job_posting", resource_id=jp.id,
                    payload={"vendor": adapter.vendor,
                             "external_id": payload.external_id,
                             "target_status": target_status},
                )
                result.new += 1

            # Sync recruiter assignments — replace-all semantics.
            await db.execute(
                text("DELETE FROM ats_job_recruiter_assignments "
                     "WHERE tenant_id = :t AND job_posting_id = :j "
                     "AND ats_vendor = :v"),
                {"t": tenant_id, "j": job_id, "v": adapter.vendor},
            )
            for rid in payload.assigned_recruiter_external_ids:
                db.add(ATSJobRecruiterAssignment(
                    tenant_id=tenant_id, job_posting_id=job_id,
                    ats_vendor=adapter.vendor, external_user_id=rid,
                ))
        return result

    async def _sync_applicants(self, db, adapter) -> PhaseResult:
        """Phase 4: applicants → candidates via import_candidate.

        Reuses the candidates module's idempotent service function; collisions
        with manual-flow candidates (same email) link external_id without
        overwriting editable fields.
        """
        from app.modules.ats.sources import ATSImportSource
        from app.modules.ats.models import ATSConnection
        from app.modules.candidates.service import import_candidate

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        bridge = ATSImportSource(vendor=adapter.vendor)
        since = self._cursor_or_none(adapter.state, "applicants")
        async for payload in adapter.list_applicants(since=since):
            try:
                sourced = bridge.normalize(payload)
                candidate = await import_candidate(db, sourced, tenant_id, created_by)
                # import_candidate writes its own audit row. We just count.
                # created_at == updated_at approximates the new-vs-updated
                # split because import_candidate may both insert and update
                # on different paths.
                if candidate.created_at == candidate.updated_at:
                    result.new += 1
                else:
                    result.updated += 1
            except Exception as exc:
                logger.warning(
                    "ats.sync.applicants.row_failed",
                    external_id=payload.external_id, error=str(exc),
                )
                result.errors.append(payload.external_id)
        return result

    async def _sync_submissions(self, db, adapter) -> PhaseResult:
        """Phase 5: for each known job_posting from this vendor, fetch
        submissions and upsert candidate_job_assignments. The submission
        external_id is the join key on candidate_job_assignments.

        Submission → candidate resolution goes via candidates.external_id
        (set by import_candidate in Phase 4). Submission → job resolution
        goes via job_postings.external_id (set by _sync_jobs in Phase 3).
        Both lookups are scoped to the same (tenant_id, vendor) pair.

        Stage / assignee resolution:
          - ``current_stage_id`` resolves to the job's pipeline-instance first
            stage (lowest ``position``). If the job has no pipeline yet
            (still ``draft`` / ``blocked_pending_client_setup``), the
            submission is skipped — the recruiter can't accept candidates
            into a stage that doesn't exist.
          - ``assigned_by`` is the connection's ``created_by`` — the user
            who installed the integration is the system actor for ATS-origin
            assignments.
        """
        from app.modules.ats.models import ATSConnection
        from app.modules.candidates.models import Candidate, CandidateJobAssignment
        from app.modules.jd.models import JobPosting
        from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id
        vendor_source = f"ats_{adapter.vendor}"

        connection = await db.scalar(
            select(ATSConnection).where(
                ATSConnection.tenant_id == tenant_id,
                ATSConnection.vendor == adapter.vendor,
            )
        )
        created_by = connection.created_by

        # Iterate jobs we know about from this vendor
        jobs = await db.execute(
            select(JobPosting).where(
                JobPosting.tenant_id == tenant_id,
                JobPosting.source == vendor_source,
            )
        )
        since = self._cursor_or_none(adapter.state, "submissions")
        for job in jobs.scalars():
            if not job.external_id:
                continue
            # Resolve first stage for this job (one-shot per job).
            first_stage = await db.scalar(
                select(JobPipelineStage)
                .join(
                    JobPipelineInstance,
                    JobPipelineStage.instance_id == JobPipelineInstance.id,
                )
                .where(JobPipelineInstance.job_posting_id == job.id)
                .order_by(JobPipelineStage.position.asc())
                .limit(1)
            )
            async for sub in adapter.list_submissions(
                job_external_id=job.external_id, since=since,
            ):
                # Resolve candidate by external_id
                candidate = await db.scalar(
                    select(Candidate).where(
                        Candidate.tenant_id == tenant_id,
                        Candidate.source == vendor_source,
                        Candidate.external_id == sub.applicant_external_id,
                        Candidate.pii_redacted_at.is_(None),
                    )
                )
                if candidate is None:
                    logger.warning(
                        "ats.sync.submissions.skip_unknown_applicant",
                        submission_external_id=sub.external_id,
                        applicant_external_id=sub.applicant_external_id,
                    )
                    result.skipped += 1
                    continue

                existing = await db.scalar(
                    select(CandidateJobAssignment).where(
                        CandidateJobAssignment.tenant_id == tenant_id,
                        CandidateJobAssignment.source == vendor_source,
                        CandidateJobAssignment.external_id == sub.external_id,
                    )
                )
                meta = {
                    "submission_status": sub.submission_status,
                    "pipeline_status": sub.pipeline_status,
                    "source": sub.source,
                    "submitted_on": sub.submitted_on.isoformat() if sub.submitted_on else None,
                    "pay_rate": str(sub.pay_rate) if sub.pay_rate else None,
                    "employment_type": sub.employment_type,
                    "raw": sub.raw,
                }
                if existing is not None:
                    # Replace-metadata semantics; do NOT touch candidate_id
                    # or job_posting_id (those are the join keys and are
                    # locked once set).
                    existing.source_metadata = meta
                    result.updated += 1
                else:
                    if first_stage is None:
                        # Job has no pipeline yet — recruiter hasn't built
                        # one (blocked_pending_client_setup or draft). Skip
                        # this submission; next poll will retry.
                        logger.warning(
                            "ats.sync.submissions.skip_no_pipeline",
                            submission_external_id=sub.external_id,
                            job_external_id=job.external_id,
                        )
                        result.skipped += 1
                        continue
                    db.add(CandidateJobAssignment(
                        tenant_id=tenant_id,
                        candidate_id=candidate.id,
                        job_posting_id=job.id,
                        current_stage_id=first_stage.id,
                        assigned_by=created_by,
                        source=vendor_source,
                        external_id=sub.external_id,
                        source_metadata=meta,
                    ))
                    result.new += 1
        return result
