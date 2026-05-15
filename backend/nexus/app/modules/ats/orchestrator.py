"""ATSSyncOrchestrator — vendor-blind job-driven sync.

Replaces the legacy 5-phase importer with a single job-driven loop. Every
other entity (org_unit, recruiter user, candidate) is materialized lazily
as a side-effect of importing the job that references it.

Architecture
------------

  for each job returned by adapter.iter_jobs(status_filter, modified_after):
      open a bypass-RLS session with SET LOCAL app.current_tenant
      open a transaction:
          enrich_job to fill client_external_name (per capabilities)
          resolve client → organizational_units row (cached in-memory)
          resolve recruiters → users rows (4-case email-collision matrix)
          upsert job_postings row; diff against prior
          emit audit + notification events for the diff
      close transaction (rolls back on exception)

      for each batch of 50 submissions for that job:
          open a fresh transaction:
              resolve candidate (via candidates.import_candidate)
              upsert candidate_job_assignment row; diff against prior
              emit audit + notification events for the diff

  on completed iteration: advance connection.last_synced_at = sync_started_at.

Cursor advance is single-shot and conservative. last_synced_at is set to
sync_started_at (NOT now()) only after the entire iteration completes
without a fatal error. Records modified during the sync run are caught on
the next pass.

Errored jobs do NOT block cursor advance — they have their own quarantine
state (job_postings.import_quarantined_at, set after 3 consecutive failures).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_bypass_session
from app.modules.ats.adapter import ATSAdapter
from app.modules.ats.connection import (
    ATSConnectionState,
    persist_connection_state,
)
from app.modules.ats.errors import (
    ATSPermanentError,
    ATSRateLimitedError,
)
from app.modules.ats.models import ATSJobAssignment, ATSSyncLog
from app.modules.ats.schemas import (
    ATSClientPayload,
    ATSJobPayload,
    ATSSubmissionPayload,
    ATSUserPayload,
)
from app.modules.audit import log_event
from app.modules.auth.models import User
from app.modules.candidates import (
    CandidateJobAssignment,
    SourcedCandidate,
    import_candidate,
    strip_sensitive_pii,
)
from app.modules.candidates.models import Candidate
from app.modules.jd.models import JobPosting
from app.modules.org_units.models import OrganizationalUnit
from app.modules.pipelines import ensure_minimal_pipeline_for_job

logger = structlog.get_logger()

QUARANTINE_THRESHOLD = 3
SUBMISSION_BATCH_SIZE = 50


# ────────────────────────────── DTOs ──────────────────────────────


@dataclass
class JobDiffResult:
    """Outcome of a single job upsert. Drives event emission."""

    kind: Literal["created", "updated", "unchanged"]
    job: JobPosting
    changed_fields: list[str] = field(default_factory=list)
    status_transition: tuple[str, str] | None = None
    recruiter_assignments_changed: bool = False


@dataclass
class SubmissionDiffResult:
    """Outcome of a single candidate↔job assignment upsert."""

    kind: Literal["created", "updated", "unchanged"]
    assignment: CandidateJobAssignment
    changed_fields: list[str] = field(default_factory=list)
    status_transition: tuple[str | None, str] | None = None


@dataclass
class ATSSyncResult:
    jobs_imported: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    jobs_errored: int = 0
    jobs_quarantined_skipped: int = 0
    submissions_imported: int = 0
    submissions_updated: int = 0
    submissions_unchanged: int = 0
    org_units_imported: int = 0
    users_imported: int = 0
    users_linked: int = 0
    users_collision_skipped: int = 0

    def entity_counts(self) -> dict[str, int]:
        return {
            "jobs_imported": self.jobs_imported,
            "jobs_updated": self.jobs_updated,
            "jobs_unchanged": self.jobs_unchanged,
            "jobs_errored": self.jobs_errored,
            "jobs_quarantined_skipped": self.jobs_quarantined_skipped,
            "submissions_imported": self.submissions_imported,
            "submissions_updated": self.submissions_updated,
            "submissions_unchanged": self.submissions_unchanged,
            "org_units_imported": self.org_units_imported,
            "users_imported": self.users_imported,
            "users_linked": self.users_linked,
            "users_collision_skipped": self.users_collision_skipped,
        }


# ────────────────────────────── Orchestrator ──────────────────────────────


class ATSSyncOrchestrator:
    """Vendor-blind single-mode cursor-based sync.

    Sync-scoped in-memory caches survive between transactions but NOT across
    runs — `run()` clears them at entry. Cache entries that were populated
    inside a failed per-job transaction must be invalidated via
    `_invalidate_cache_for_failed_job` before processing continues.
    """

    def __init__(
        self,
        adapter: ATSAdapter,
        *,
        connection_id: uuid.UUID,
        tenant_id: uuid.UUID,
        correlation_id: str,
        actor_id: uuid.UUID | None,
        actor_email: str | None,
        action_source: Literal["manual", "scheduled", "system"] = "manual",
        sync_log_id: uuid.UUID | None = None,
    ) -> None:
        self.adapter = adapter
        self.connection_id = connection_id
        self.tenant_id = tenant_id
        self.correlation_id = correlation_id
        self.actor_id = actor_id
        self.actor_email = actor_email
        self.action_source = action_source
        # When set, the orchestrator writes incremental progress + entity_counts
        # to the live ats_sync_logs row so the recruiter's progress dialog can
        # render live counters instead of an indeterminate spinner.
        self.sync_log_id = sync_log_id

        # Sync-scoped caches.
        self._client_index: dict[str, ATSClientPayload] | None = None
        self._resolved_orgs: dict[str, OrganizationalUnit] = {}
        self._resolved_users: dict[str, User] = {}

        # Bookkeeping for diff metadata.
        self._root_org_unit_id: uuid.UUID | None = None
        self._tenant_timezone_captured: bool = False

    # ──────────────── public entry point ────────────────

    async def run(self) -> ATSSyncResult:
        state: ATSConnectionState = self.adapter.state
        if not state.job_status_filter or not (
            state.job_status_filter.get("ids") or []
        ):
            # Should not be reachable — service.py 422s before enqueueing.
            raise ATSPermanentError(
                "Cannot run sync: job_status_filter is empty",
            )

        result = ATSSyncResult()
        sync_started_at = datetime.now(tz=UTC)
        modified_after = state.last_synced_at
        status_ids: list[str] = [
            str(i) for i in state.job_status_filter["ids"]
        ]

        async for raw_job in self.adapter.iter_jobs(
            status_external_ids=status_ids,
            modified_after=modified_after,
        ):
            # Skip quarantined jobs before any expensive work.
            if await self._is_job_quarantined(raw_job.external_id):
                result.jobs_quarantined_skipped += 1
                continue

            try:
                job_diff, sub_counts = await self._process_job(raw_job)
            except ATSRateLimitedError:
                # Partial completion; finalize sync_log as 'partial'.
                # last_synced_at is NOT advanced — next run picks up here.
                raise
            except ATSPermanentError as exc:
                # Mark job as errored, continue to next. Push the counter
                # forward so the dialog reflects "we tried this job and
                # moved on" — without this, the spinner would freeze on
                # the row before the error.
                await self._mark_job_errored(raw_job, exc)
                result.jobs_errored += 1
                self._invalidate_cache_for_failed_job(raw_job)
                await self._write_progress(result)
                continue

            if job_diff.kind == "created":
                result.jobs_imported += 1
            elif job_diff.kind == "updated":
                result.jobs_updated += 1
            else:
                result.jobs_unchanged += 1

            result.submissions_imported += sub_counts["imported"]
            result.submissions_updated += sub_counts["updated"]
            result.submissions_unchanged += sub_counts["unchanged"]

            # Write live progress after every processed job so the dialog
            # has a real denominator. Best-effort: a single transaction per
            # job, wrapped so a write failure doesn't tear down the run.
            result.org_units_imported = len(self._resolved_orgs)
            result.users_imported = len(self._resolved_users)
            await self._write_progress(result)

        # Roll up org/user counts from cache state.
        result.org_units_imported = sum(
            1 for ou in self._resolved_orgs.values() if ou is not None
        )
        result.users_imported = len(self._resolved_users)

        # Cursor advance only on full successful iteration.
        state.last_synced_at = sync_started_at
        async with self._open_db() as db:
            await persist_connection_state(db, state)
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.sync.completed",
                resource="ats_connection",
                resource_id=self.connection_id,
                payload={
                    "correlation_id": self.correlation_id,
                    "action_source": self.action_source,
                    "entity_counts": result.entity_counts(),
                },
            )
            await db.commit()
        return result

    # ──────────────── Progress writer ────────────────

    async def _write_progress(self, result: ATSSyncResult) -> None:
        """Push the latest counters into ats_sync_logs.{entity_counts,progress}.

        Best-effort — wrapped in a try/except so a transient DB hiccup never
        kills the run mid-sync. The dialog polls this row every 2s while
        status='running'.
        """
        if self.sync_log_id is None:
            return
        try:
            async with self._open_db() as db:
                counts = result.entity_counts()
                await db.execute(
                    update(ATSSyncLog)
                    .where(ATSSyncLog.id == self.sync_log_id)
                    .values(
                        entity_counts=counts,
                        progress={
                            "jobs": {
                                "processed": (
                                    result.jobs_imported
                                    + result.jobs_updated
                                    + result.jobs_unchanged
                                    + result.jobs_errored
                                    + result.jobs_quarantined_skipped
                                ),
                                # `total` is the recruiter-facing denominator.
                                # We don't know it in advance (Ceipal's
                                # iterator is a server-side stream), so we
                                # publish a sentinel -1 which the frontend
                                # renders as an indeterminate-but-active
                                # progress bar. The processed counter still
                                # advances live.
                                "total": -1,
                            },
                        },
                    ),
                )
                await db.commit()
        except Exception as exc:
            logger.warning(
                "ats.orchestrator.progress_write_failed",
                sync_log_id=str(self.sync_log_id),
                error=str(exc)[:200],
            )

    # ──────────────── DB session helper ────────────────

    def _open_db(self):
        """Open a bypass-RLS session with the tenant scope bound.

        Returns an async context manager — caller is responsible for
        committing or letting the implicit rollback fire on exception.
        """

        class _Ctx:
            def __init__(ctx_self, tenant_id: uuid.UUID) -> None:
                ctx_self._tenant_id = tenant_id
                ctx_self._cm = None

            async def __aenter__(ctx_self) -> AsyncSession:
                ctx_self._cm = get_bypass_session()
                db: AsyncSession = await ctx_self._cm.__aenter__()
                await db.execute(
                    text(
                        f"SET LOCAL app.current_tenant = "
                        f"'{ctx_self._tenant_id}'"
                    )
                )
                return db

            async def __aexit__(ctx_self, exc_type, exc, tb) -> None:
                assert ctx_self._cm is not None
                await ctx_self._cm.__aexit__(exc_type, exc, tb)

        return _Ctx(self.tenant_id)

    # ──────────────── Quarantine ────────────────

    async def _is_job_quarantined(self, external_id: str) -> bool:
        async with self._open_db() as db:
            row = await db.execute(
                select(JobPosting.import_quarantined_at)
                .where(JobPosting.tenant_id == self.tenant_id)
                .where(JobPosting.source == self.adapter.vendor)
                .where(JobPosting.external_id == external_id),
            )
            value = row.scalar_one_or_none()
            return value is not None

    async def _mark_job_errored(
        self, job_payload: ATSJobPayload, exc: Exception,
    ) -> None:
        """Increment retry count; quarantine if threshold reached.

        Runs in its OWN transaction — we never reuse a rolled-back session.
        """
        message = str(exc)[:1000]
        async with self._open_db() as db:
            row = await db.execute(
                select(JobPosting)
                .where(JobPosting.tenant_id == self.tenant_id)
                .where(JobPosting.source == self.adapter.vendor)
                .where(JobPosting.external_id == job_payload.external_id),
            )
            job = row.scalar_one_or_none()
            if job is None:
                # Failure happened before any insert — record as a sync-level
                # audit instead. Nothing to quarantine.
                await log_event(
                    db,
                    tenant_id=self.tenant_id,
                    actor_id=self.actor_id,
                    actor_email=self.actor_email,
                    action="ats.job.import_failed_no_row",
                    resource="ats_job",
                    resource_id=None,
                    payload={
                        "external_id": job_payload.external_id,
                        "error": message,
                        "correlation_id": self.correlation_id,
                    },
                )
                await db.commit()
                return

            new_count = job.import_retry_count + 1
            quarantined = new_count >= QUARANTINE_THRESHOLD
            values: dict[str, Any] = {
                "import_retry_count": new_count,
                "import_last_error": message,
            }
            if quarantined:
                values["import_quarantined_at"] = datetime.now(tz=UTC)
            await db.execute(
                update(JobPosting)
                .where(JobPosting.id == job.id)
                .values(**values),
            )
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action=(
                    "ats.job.import_quarantined"
                    if quarantined
                    else "ats.job.import_errored"
                ),
                resource="job_posting",
                resource_id=job.id,
                payload={
                    "retry_count": new_count,
                    "error": message,
                    "correlation_id": self.correlation_id,
                },
            )
            await db.commit()

    def _invalidate_cache_for_failed_job(
        self, job_payload: ATSJobPayload,
    ) -> None:
        """Drop cache entries that were populated by the failed transaction.

        We don't track per-job cache mutations precisely — instead we clear
        anything that wasn't loaded from a still-committed DB row. The next
        job to reference the same client/recruiter will re-query and
        either re-load the existing row or repopulate the cache.
        """
        # Best-effort: drop any client whose external_id matches this job's
        # client_external_id (if known), and drop all recruiter users this
        # job references. Other cache entries belong to earlier successful
        # jobs and stay.
        if job_payload.client_external_id:
            self._resolved_orgs.pop(job_payload.client_external_id, None)
        recruiter_ids = set(job_payload.assigned_recruiter_external_ids) | {
            x for x in [
                job_payload.primary_recruiter_external_id,
                job_payload.posted_by_external_id,
                job_payload.created_external_id,
            ] if x
        }
        for rid in recruiter_ids:
            self._resolved_users.pop(rid, None)

    # ──────────────── Per-job pipeline ────────────────

    async def _process_job(
        self, raw_job: ATSJobPayload,
    ) -> tuple[JobDiffResult, dict[str, int]]:
        """Resolve+upsert one job, then iterate its submissions in batches.

        Returns the JobDiffResult plus a {imported, updated, unchanged}
        counter for the submissions on this job.
        """
        # Enrich (Ceipal: fetches client_external_name from detail endpoint).
        if self.adapter.capabilities.job_detail_required_for_client_name:
            raw_job = await self.adapter.enrich_job(raw_job)

        # Resolve org_unit + recruiters + upsert + emit events: ONE txn.
        async with self._open_db() as db:
            org_unit = await self._resolve_client(db, raw_job)
            recruiters = await self._resolve_recruiters(db, raw_job)
            diff = await self._upsert_job(db, raw_job, org_unit, recruiters)
            # Every job — new or pre-existing — needs a pipeline so the
            # submission upsert path can attach assignments to a real
            # stage. Idempotent; no-op when the job already has one.
            # Catches existing ATS-imported jobs that were created before
            # this hook landed.
            await ensure_minimal_pipeline_for_job(db, job=diff.job)
            await self._emit_job_events(db, diff)
            await self._on_successful_job_import(db, diff.job)
            await db.commit()

        # Submissions iterated in their own per-batch transactions to keep
        # job-level row locks short.
        sub_counts = {"imported": 0, "updated": 0, "unchanged": 0}
        batch: list[ATSSubmissionPayload] = []
        async for raw_sub in self.adapter.iter_submissions(
            job_external_id=raw_job.external_id,
            modified_after=None,
        ):
            batch.append(raw_sub)
            if len(batch) >= SUBMISSION_BATCH_SIZE:
                await self._process_submission_batch(diff.job, batch, sub_counts)
                batch = []
        if batch:
            await self._process_submission_batch(diff.job, batch, sub_counts)

        return diff, sub_counts

    async def _process_submission_batch(
        self,
        job: JobPosting,
        batch: list[ATSSubmissionPayload],
        sub_counts: dict[str, int],
    ) -> None:
        async with self._open_db() as db:
            for raw_sub in batch:
                candidate = await self._resolve_candidate(db, raw_sub)
                if candidate is None:
                    # No applicant detail / empty payload — skip silently.
                    continue
                sub_diff = await self._upsert_assignment(
                    db, raw_sub, candidate_id=candidate.id, job=job,
                )
                await self._emit_submission_events(db, sub_diff)
                if sub_diff.kind == "created":
                    sub_counts["imported"] += 1
                elif sub_diff.kind == "updated":
                    sub_counts["updated"] += 1
                else:
                    sub_counts["unchanged"] += 1
            await db.commit()

    async def _on_successful_job_import(
        self, db: AsyncSession, job: JobPosting,
    ) -> None:
        """Clear retry/quarantine state on a successful upsert."""
        if job.import_retry_count > 0 or job.import_quarantined_at is not None:
            await db.execute(
                update(JobPosting)
                .where(JobPosting.id == job.id)
                .values(
                    import_retry_count=0,
                    import_quarantined_at=None,
                    import_last_error=None,
                ),
            )

    # ──────────────── Client resolution ────────────────

    async def _root_org_unit(self, db: AsyncSession) -> OrganizationalUnit:
        """Look up the tenant's root org_unit (is_root=true)."""
        if self._root_org_unit_id is not None:
            row = await db.execute(
                select(OrganizationalUnit).where(
                    OrganizationalUnit.id == self._root_org_unit_id,
                ),
            )
            ou = row.scalar_one_or_none()
            if ou is not None:
                return ou

        row = await db.execute(
            select(OrganizationalUnit)
            .where(OrganizationalUnit.client_id == self.tenant_id)
            .where(OrganizationalUnit.is_root.is_(True)),
        )
        root = row.scalar_one_or_none()
        if root is None:
            raise ATSPermanentError(
                f"Tenant {self.tenant_id} has no root org_unit; "
                "onboarding never completed",
            )
        self._root_org_unit_id = root.id
        return root

    async def _resolve_client(
        self, db: AsyncSession, job: ATSJobPayload,
    ) -> OrganizationalUnit | None:
        """Resolve the job's client to an organizational_units row.

        Strategy (see spec section "Client resolution"):
          1. If we already have client_external_id, look up by it.
          2. Otherwise, use client_external_name to consult the
             vendor-side client index (built lazily) to learn the id.
          3. Fetch authoritative payload via adapter.get_client.
          4. INSERT a new org_unit if no row exists for (tenant, source,
             external_id).
          5. Backfill column-level fields only when currently NULL.
          6. Audit event.

        Returns None on orphan_client (no resolvable id from the name).
        """
        # Step 1: do we have a direct external_id (orchestrator cache hit)?
        if job.client_external_id:
            ext_id = job.client_external_id
        elif job.client_external_name:
            ext_id = await self._lookup_client_external_id_by_name(
                job.client_external_name,
            )
            if ext_id is None:
                await log_event(
                    db,
                    tenant_id=self.tenant_id,
                    actor_id=self.actor_id,
                    actor_email=self.actor_email,
                    action="ats.org_unit.orphan_client",
                    resource="job_posting",
                    resource_id=None,
                    payload={
                        "external_id": job.external_id,
                        "external_client_name": job.client_external_name,
                        "correlation_id": self.correlation_id,
                    },
                )
                return None
            job.client_external_id = ext_id
        else:
            # No identifier at all — Ceipal job without a client linkage.
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.job.orphan_client",
                resource="job_posting",
                resource_id=None,
                payload={
                    "external_id": job.external_id,
                    "correlation_id": self.correlation_id,
                },
            )
            return None

        # Cache hit?
        if ext_id in self._resolved_orgs:
            return self._resolved_orgs[ext_id]

        # DB lookup by external identity.
        row = await db.execute(
            select(OrganizationalUnit)
            .where(OrganizationalUnit.client_id == self.tenant_id)
            .where(OrganizationalUnit.source == self.adapter.vendor)
            .where(OrganizationalUnit.external_id == ext_id),
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            self._resolved_orgs[ext_id] = existing
            return existing

        # Miss: fetch authoritative payload from vendor + insert.
        payload = await self.adapter.get_client(external_id=ext_id)
        root = await self._root_org_unit(db)
        ou = OrganizationalUnit(
            client_id=self.tenant_id,
            parent_unit_id=root.id,
            name=payload.name or "(unnamed)",
            unit_type="client_account",
            is_root=False,
            source=self.adapter.vendor,
            external_id=payload.external_id,
            external_source_metadata={
                "website": payload.website,
                "industry": payload.industry,
                "country": payload.country,
                "state": payload.state,
                "city": payload.city,
                "business_unit_id": payload.business_unit_id,
                "contacts": [c.model_dump() for c in payload.contacts],
                "raw": payload.raw,
            },
            company_profile_completion_status="pending",
            created_by=self.actor_id,
        )
        # Backfill columns only when currently NULL — never overwrite
        # recruiter-edited data. The INSERT path sets them from payload.
        _backfill_org_unit_columns(ou, payload)

        db.add(ou)
        await db.flush()
        await log_event(
            db,
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            actor_email=self.actor_email,
            action="ats.org_unit.imported",
            resource="organizational_unit",
            resource_id=ou.id,
            payload={
                "external_id": ou.external_id,
                "name": ou.name,
                "correlation_id": self.correlation_id,
            },
        )
        self._resolved_orgs[ext_id] = ou
        return ou

    async def _lookup_client_external_id_by_name(
        self, name: str,
    ) -> str | None:
        """Build the in-memory client index lazily, then look up by name.

        Index key is `lower(strip(name))`. Case-insensitive match is safe
        under the unified model because identity is enforced by
        (tenant_id, source, external_id) uniqueness — case-folding cannot
        create duplicate org_units.
        """
        if self._client_index is None:
            self._client_index = {}
            async for client_payload in self.adapter.iter_clients():
                key = (client_payload.name or "").strip().lower()
                if key:
                    self._client_index[key] = client_payload
        match = self._client_index.get(name.strip().lower())
        return match.external_id if match else None

    # ──────────────── Recruiter resolution ────────────────

    async def _resolve_recruiters(
        self, db: AsyncSession, job: ATSJobPayload,
    ) -> dict[str, list[User]]:
        """Resolve every recruiter the job references, by role.

        Roles map per spec:
          - assigned_recruiter — assigned_recruiter_external_ids (CSV)
          - primary_recruiter — primary_recruiter_external_id
          - posted_by — posted_by_external_id
          - created_by — created_external_id

        Returns ``{role: [User, ...], ...}`` with empty lists for roles
        with no matching users.
        """
        out: dict[str, list[User]] = {
            "assigned_recruiter": [],
            "primary_recruiter": [],
            "posted_by": [],
            "created_by": [],
        }

        async def resolve_one(ext_id: str) -> User | None:
            return await self._resolve_user_by_external_id(db, ext_id)

        for ext_id in job.assigned_recruiter_external_ids:
            u = await resolve_one(ext_id)
            if u is not None:
                out["assigned_recruiter"].append(u)
        for role, ext_id in [
            ("primary_recruiter", job.primary_recruiter_external_id),
            ("posted_by", job.posted_by_external_id),
            ("created_by", job.created_external_id),
        ]:
            if not ext_id:
                continue
            u = await resolve_one(ext_id)
            if u is not None:
                out[role].append(u)
        return out

    async def _resolve_user_by_external_id(
        self, db: AsyncSession, external_id: str,
    ) -> User | None:
        """Resolve a vendor user_id to a `users` row using the 4-case
        email-collision matrix from the spec.

        Returns None on case 4 (collision skipped) so the caller can drop
        the row from the assignment list (the job is still saved with
        whatever recruiters DID resolve).
        """
        if external_id in self._resolved_users:
            return self._resolved_users[external_id]

        # DB lookup by (tenant, source, external_id).
        row = await db.execute(
            select(User)
            .where(User.tenant_id == self.tenant_id)
            .where(User.source == self.adapter.vendor)
            .where(User.external_id == external_id),
        )
        existing = row.scalar_one_or_none()
        if existing is not None:
            self._resolved_users[external_id] = existing
            return existing

        # Miss: fetch authoritative payload from vendor.
        payload = await self.adapter.get_user(external_id=external_id)

        # Capture tenant_timezone on first observation.
        if (
            not self._tenant_timezone_captured
            and payload.timezone
            and self.adapter.state.tenant_timezone is None
        ):
            self.adapter.state.tenant_timezone = payload.timezone
            self._tenant_timezone_captured = True

        # Email-collision lookup (case-folded).
        normalized_email = (payload.email or "").strip().lower()
        if not normalized_email:
            # Vendor returned a user with no email — cannot dedup. Insert
            # without an email-collision check.
            return await self._insert_ats_user(db, payload)

        row = await db.execute(
            select(User)
            .where(User.tenant_id == self.tenant_id)
            .where(func.lower(User.email) == normalized_email),
        )
        collision = row.scalar_one_or_none()
        if collision is None:
            return await self._insert_ats_user(db, payload)

        # Case 2: existing row has external_id NULL → link.
        if collision.external_id is None:
            collision.external_id = payload.external_id
            # `source` stays 'native' — preserves the audit trail.
            collision.external_source_metadata = (
                _user_metadata_payload(payload)
            )
            await db.flush()
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.user.linked_to_native",
                resource="user",
                resource_id=collision.id,
                payload={
                    "external_id": payload.external_id,
                    "email": normalized_email,
                    "correlation_id": self.correlation_id,
                },
            )
            self._resolved_users[external_id] = collision
            return collision

        # Case 3: external_id matches — refresh metadata only.
        if collision.external_id == payload.external_id:
            collision.external_source_metadata = (
                _user_metadata_payload(payload)
            )
            await db.flush()
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.user.metadata_refreshed",
                resource="user",
                resource_id=collision.id,
                payload={
                    "external_id": payload.external_id,
                    "correlation_id": self.correlation_id,
                },
            )
            self._resolved_users[external_id] = collision
            return collision

        # Case 4: external_id mismatch — skip + audit + notify.
        await log_event(
            db,
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            actor_email=self.actor_email,
            action="ats.user.collision_skipped",
            resource="user",
            resource_id=collision.id,
            payload={
                "external_id_incoming": payload.external_id,
                "external_id_existing": collision.external_id,
                "email": normalized_email,
                "correlation_id": self.correlation_id,
            },
        )
        return None

    async def _insert_ats_user(
        self, db: AsyncSession, payload: ATSUserPayload,
    ) -> User:
        """Case 1: insert a new users row tagged as ATS-imported."""
        user = User(
            tenant_id=self.tenant_id,
            auth_user_id=None,
            email=payload.email,
            full_name=payload.full_name,
            is_active=False,
            source=self.adapter.vendor,
            external_id=payload.external_id,
            external_source_metadata=_user_metadata_payload(payload),
        )
        db.add(user)
        await db.flush()
        await log_event(
            db,
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            actor_email=self.actor_email,
            action="ats.user.imported",
            resource="user",
            resource_id=user.id,
            payload={
                "external_id": payload.external_id,
                "email": payload.email,
                "correlation_id": self.correlation_id,
            },
        )
        self._resolved_users[payload.external_id] = user
        return user

    # ──────────────── Job upsert + diff ────────────────

    async def _upsert_job(
        self,
        db: AsyncSession,
        payload: ATSJobPayload,
        org_unit: OrganizationalUnit | None,
        recruiters: dict[str, list[User]],
    ) -> JobDiffResult:
        """Insert or update the job_postings row + ats_job_assignments rows.

        Returns a JobDiffResult describing what changed.
        """
        row = await db.execute(
            select(JobPosting)
            .where(JobPosting.tenant_id == self.tenant_id)
            .where(JobPosting.source == self.adapter.vendor)
            .where(JobPosting.external_id == payload.external_id),
        )
        existing = row.scalar_one_or_none()

        # The vendor's `created_by` user is the authoritative owner. We
        # resolved it during _resolve_recruiters → use it whenever it's
        # available. Fall back to the recruiter who clicked Resync only
        # when the ATS-side creator couldn't be resolved (Ceipal user
        # missing/unresolvable or email-collision-skipped).
        created_by_user_id = self.actor_id
        for u in recruiters.get("created_by", []):
            created_by_user_id = u.id
            break

        if existing is None:
            # New job. Lands in `draft` regardless of profile completion —
            # the unified job-creation flow (see docs/superpowers/specs/
            # 2026-05-14-unified-job-creation-flow-design.md) treats ATS
            # imports as pre-fills, identical to a manually-created job
            # awaiting the recruiter's explicit enrich + extract clicks.
            if self.actor_id is None:
                raise ATSPermanentError(
                    "Cannot create job: no actor_id (sync was not "
                    "associated with a user)",
                )
            if created_by_user_id is None:
                # Defensive — created_by is NOT NULL on the DB column.
                raise ATSPermanentError(
                    "Cannot create job: no resolvable created_by user",
                )
            job = JobPosting(
                tenant_id=self.tenant_id,
                org_unit_id=org_unit.id if org_unit is not None else None,
                title=payload.title or "(untitled)",
                description_raw=payload.description_raw or "",
                description_enriched=payload.description_enriched,
                status="draft",
                source=self.adapter.vendor,
                external_id=payload.external_id,
                external_status=payload.external_status,
                external_last_modified_at=payload.external_modified_at,
                deadline=payload.deadline,
                location=_compose_location(payload),
                created_by=created_by_user_id,
            )
            db.add(job)
            await db.flush()
            await self._sync_job_assignments(db, job, recruiters)
            return JobDiffResult(kind="created", job=job)

        # Existing — diff against payload.
        changed: list[str] = []
        status_transition: tuple[str, str] | None = None

        if existing.title != payload.title and payload.title:
            existing.title = payload.title
            changed.append("title")
        if existing.description_raw != payload.description_raw:
            existing.description_raw = payload.description_raw
            changed.append("description_raw")
        # Don't overwrite recruiter-edited enriched JD.
        if (
            not existing.enriched_manually_edited
            and existing.description_enriched != payload.description_enriched
        ):
            existing.description_enriched = payload.description_enriched
            changed.append("description_enriched")
        if existing.external_status != payload.external_status:
            status_transition = (
                existing.external_status or "",
                payload.external_status,
            )
            existing.external_status = payload.external_status
            changed.append("external_status")
        if existing.external_last_modified_at != payload.external_modified_at:
            existing.external_last_modified_at = payload.external_modified_at
            # external_last_modified_at change alone is not a "field update"
            # event — it's a freshness marker.
        if existing.deadline != payload.deadline:
            existing.deadline = payload.deadline
            changed.append("deadline")
        new_loc = _compose_location(payload)
        if existing.location != new_loc and new_loc:
            existing.location = new_loc
            changed.append("location")
        # Backfill org_unit_id if it was NULL and we now have one.
        if existing.org_unit_id is None and org_unit is not None:
            existing.org_unit_id = org_unit.id
            changed.append("org_unit_id")

        # ATS sync does NOT change the JD lifecycle status. Once a job is
        # created (in 'draft'), the recruiter owns lifecycle transitions
        # via /enrich and /extract-signals. ATS continues to update
        # content fields (title, description_*, external_*, location,
        # deadline, org_unit_id backfill) regardless of where in the
        # pipeline the recruiter has taken the job.

        # The Ceipal `created_by` user is vendor-authoritative. If we
        # previously stamped this as the syncing super-admin (because the
        # ATS creator hadn't been resolved at the time), correct it now.
        # Don't overwrite a recruiter-set created_by — only touch when
        # the resolved ATS creator differs and we have a fresh value.
        for u in recruiters.get("created_by", []):
            if existing.created_by != u.id:
                existing.created_by = u.id
                changed.append("created_by")
            break

        recruiter_assignments_changed = await self._sync_job_assignments(
            db, existing, recruiters,
        )

        if not changed and not recruiter_assignments_changed and (
            status_transition is None
        ):
            return JobDiffResult(kind="unchanged", job=existing)

        return JobDiffResult(
            kind="updated",
            job=existing,
            changed_fields=changed,
            status_transition=status_transition,
            recruiter_assignments_changed=recruiter_assignments_changed,
        )

    async def _sync_job_assignments(
        self,
        db: AsyncSession,
        job: JobPosting,
        recruiters: dict[str, list[User]],
    ) -> bool:
        """Reconcile ats_job_assignments rows for this job.

        Returns True if any add/remove occurred. Adds rows for new
        (user_id, role) pairs; removes rows for pairs the vendor no
        longer references.
        """
        # Build the desired set.
        desired: set[tuple[uuid.UUID, str]] = set()
        for role, users in recruiters.items():
            for u in users:
                desired.add((u.id, role))

        # Load existing.
        rows = await db.execute(
            select(ATSJobAssignment).where(
                ATSJobAssignment.job_posting_id == job.id,
            ),
        )
        existing_rows = rows.scalars().all()
        existing_set = {(r.user_id, r.role) for r in existing_rows}

        changed = False
        # Inserts.
        for user_id, role in desired - existing_set:
            db.add(ATSJobAssignment(
                tenant_id=self.tenant_id,
                job_posting_id=job.id,
                user_id=user_id,
                role=role,
            ))
            changed = True

        # Deletes.
        for r in existing_rows:
            if (r.user_id, r.role) not in desired:
                await db.delete(r)
                changed = True

        if changed:
            await db.flush()
        return changed

    # ──────────────── Job event emission ────────────────

    async def _emit_job_events(
        self, db: AsyncSession, diff: JobDiffResult,
    ) -> None:
        if diff.kind == "created":
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.job.imported",
                resource="job_posting",
                resource_id=diff.job.id,
                payload={
                    "external_id": diff.job.external_id,
                    "title": diff.job.title,
                    "correlation_id": self.correlation_id,
                },
            )
            return

        if diff.kind == "unchanged":
            return

        if diff.status_transition is not None:
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.job.status_changed",
                resource="job_posting",
                resource_id=diff.job.id,
                payload={
                    "external_id": diff.job.external_id,
                    "old": diff.status_transition[0],
                    "new": diff.status_transition[1],
                    "correlation_id": self.correlation_id,
                },
            )

        non_status_fields = [
            f for f in diff.changed_fields if f != "external_status"
        ]
        if non_status_fields:
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.job.fields_updated",
                resource="job_posting",
                resource_id=diff.job.id,
                payload={
                    "external_id": diff.job.external_id,
                    "fields": non_status_fields,
                    "correlation_id": self.correlation_id,
                },
            )

        if diff.recruiter_assignments_changed:
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.job.recruiter_assignments_changed",
                resource="job_posting",
                resource_id=diff.job.id,
                payload={
                    "external_id": diff.job.external_id,
                    "correlation_id": self.correlation_id,
                },
            )

    # ──────────────── Candidate resolution ────────────────

    async def _resolve_candidate(
        self, db: AsyncSession, sub: ATSSubmissionPayload,
    ):
        """Resolve the applicant to a candidates row via import_candidate.

        Returns the Candidate row or None if the applicant detail call
        failed (skip the submission rather than abort the batch).

        Fast path — DB short-circuit: if a candidate row already exists for
        ``(tenant_id, source='ats_<vendor>', external_id)`` and hasn't been
        PII-redacted, reuse it without calling Ceipal. This is by far the
        biggest perf lever in the sync loop: `getApplicantDetails` is the
        most-called endpoint (one per submission) and a typical re-sync
        re-traverses every existing submission, wasting ~13× the per-job
        call budget on data we've already imported.

        Refreshing the candidate's editable fields (name/phone/location)
        from Ceipal on every sync would be nice, but the per-API-call cost
        far outweighs the staleness risk — Ceipal's applicant profile is
        recruiter-edited, not the source of truth, and our recruiter can
        edit it post-import anyway. The orchestrator already refreshes
        the assignment-side fields (external_status, etc.) via
        `_upsert_assignment` without any extra API call.
        """
        if not sub.applicant_external_id:
            return None

        cached_row = await db.execute(
            select(Candidate)
            .where(Candidate.tenant_id == self.tenant_id)
            .where(Candidate.source == self.adapter.vendor)
            .where(Candidate.external_id == sub.applicant_external_id)
            .where(Candidate.pii_redacted_at.is_(None))
        )
        cached = cached_row.scalar_one_or_none()
        if cached is not None:
            return cached

        try:
            applicant = await self.adapter.get_applicant(
                external_id=sub.applicant_external_id,
            )
        except ATSPermanentError as exc:
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.candidate.fetch_failed",
                resource="candidate",
                resource_id=None,
                payload={
                    "applicant_external_id": sub.applicant_external_id,
                    "error": str(exc)[:300],
                    "correlation_id": self.correlation_id,
                },
            )
            return None

        sanitized_raw = strip_sensitive_pii(applicant.raw)
        sourced = SourcedCandidate(
            name=_compose_applicant_name(applicant),
            email=applicant.email or "",
            phone=applicant.mobile,
            location=_compose_applicant_location(applicant),
            current_title=None,
            linkedin_url=None,
            notes=None,
            source=self.adapter.vendor,
            external_id=applicant.external_id,
            source_metadata=sanitized_raw,
        )
        if self.actor_id is None:
            return None
        return await import_candidate(
            db, sourced,
            tenant_id=self.tenant_id,
            created_by=self.actor_id,
        )

    # ──────────────── Submission upsert + diff ────────────────

    async def _upsert_assignment(
        self,
        db: AsyncSession,
        sub: ATSSubmissionPayload,
        *,
        candidate_id: uuid.UUID,
        job: JobPosting,
    ) -> SubmissionDiffResult:
        """Insert or update the candidate_job_assignments row.

        Returns a SubmissionDiffResult.
        """
        row = await db.execute(
            select(CandidateJobAssignment)
            .where(CandidateJobAssignment.tenant_id == self.tenant_id)
            .where(
                CandidateJobAssignment.source == self.adapter.vendor,
            )
            .where(
                CandidateJobAssignment.external_id == sub.external_id,
            ),
        )
        existing = row.scalar_one_or_none()

        if existing is None:
            # New assignment. We need a current_stage_id — try the job's
            # first pipeline stage; if none exists, skip with a warning.
            stage_id = await _first_pipeline_stage_id(db, job.id)
            if stage_id is None or self.actor_id is None:
                await log_event(
                    db,
                    tenant_id=self.tenant_id,
                    actor_id=self.actor_id,
                    actor_email=self.actor_email,
                    action="ats.submission.skipped_no_pipeline",
                    resource="job_posting",
                    resource_id=job.id,
                    payload={
                        "external_id": sub.external_id,
                        "correlation_id": self.correlation_id,
                    },
                )
                # Synthesize an unchanged placeholder so the counter logic
                # downstream is consistent.
                return SubmissionDiffResult(
                    kind="unchanged",
                    assignment=CandidateJobAssignment(),
                )

            assignment = CandidateJobAssignment(
                tenant_id=self.tenant_id,
                candidate_id=candidate_id,
                job_posting_id=job.id,
                source=self.adapter.vendor,
                external_id=sub.external_id,
                source_metadata=sub.raw,
                current_stage_id=stage_id,
                status="active",
                external_status=sub.external_status,
                external_pipeline_status=sub.pipeline_status,
                external_last_modified_at=sub.external_modified_at,
                assigned_by=self.actor_id,
            )
            db.add(assignment)
            await db.flush()
            return SubmissionDiffResult(
                kind="created", assignment=assignment,
            )

        # Existing — diff.
        changed: list[str] = []
        status_transition: tuple[str | None, str] | None = None

        if existing.external_status != sub.external_status:
            status_transition = (
                existing.external_status, sub.external_status,
            )
            existing.external_status = sub.external_status
            changed.append("external_status")
        if existing.external_pipeline_status != sub.pipeline_status:
            existing.external_pipeline_status = sub.pipeline_status
            changed.append("external_pipeline_status")
        if existing.external_last_modified_at != sub.external_modified_at:
            existing.external_last_modified_at = sub.external_modified_at
            # freshness marker, not a field-update event

        existing.source_metadata = sub.raw

        if not changed and status_transition is None:
            return SubmissionDiffResult(
                kind="unchanged", assignment=existing,
            )
        return SubmissionDiffResult(
            kind="updated",
            assignment=existing,
            changed_fields=changed,
            status_transition=status_transition,
        )

    async def _emit_submission_events(
        self, db: AsyncSession, diff: SubmissionDiffResult,
    ) -> None:
        if diff.kind == "created":
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.submission.created",
                resource="candidate_job_assignment",
                resource_id=diff.assignment.id,
                payload={
                    "external_id": diff.assignment.external_id,
                    "correlation_id": self.correlation_id,
                },
            )
            return
        if diff.kind == "unchanged":
            return

        if diff.status_transition is not None:
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.submission.status_changed",
                resource="candidate_job_assignment",
                resource_id=diff.assignment.id,
                payload={
                    "external_id": diff.assignment.external_id,
                    "old": diff.status_transition[0],
                    "new": diff.status_transition[1],
                    "correlation_id": self.correlation_id,
                },
            )
            # Advisory-action insertion (advisory mode only) is wired by
            # Phase C; the ats_advisory_actions table is empty until then.

        non_status_fields = [
            f for f in diff.changed_fields if f != "external_status"
        ]
        if non_status_fields:
            await log_event(
                db,
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                actor_email=self.actor_email,
                action="ats.submission.fields_updated",
                resource="candidate_job_assignment",
                resource_id=diff.assignment.id,
                payload={
                    "external_id": diff.assignment.external_id,
                    "fields": non_status_fields,
                    "correlation_id": self.correlation_id,
                },
            )


# ────────────────────────────── Helpers ──────────────────────────────


def _user_metadata_payload(payload: ATSUserPayload) -> dict[str, Any]:
    return {
        "role": payload.role,
        "business_unit_id": payload.business_unit_id,
        "timezone": payload.timezone,
        "external_status": payload.external_status,
        "raw": payload.raw,
    }


def _compose_location(payload: ATSJobPayload) -> str | None:
    parts = [
        payload.primary_city,
        payload.primary_state,
        payload.country,
    ]
    composed = ", ".join(p for p in parts if p)
    return composed or None


def _compose_applicant_name(applicant) -> str:
    parts = [applicant.first_name, applicant.last_name]
    name = " ".join(p for p in parts if p).strip()
    return name or "(unknown)"


def _compose_applicant_location(applicant) -> str | None:
    parts = [applicant.city, applicant.state, applicant.country]
    composed = ", ".join(p for p in parts if p)
    return composed or None


def _backfill_org_unit_columns(
    ou: OrganizationalUnit, payload: ATSClientPayload,
) -> None:
    """Set column-level fields from payload only when currently NULL.

    Used at INSERT time so the org_unit reflects the vendor's view by
    default. Subsequent vendor refreshes do not overwrite these columns —
    only `external_source_metadata` is refreshed (separate code path).
    """
    if not getattr(ou, "website", None) and hasattr(ou, "website"):
        ou.website = payload.website
    if not getattr(ou, "industry", None) and hasattr(ou, "industry"):
        ou.industry = payload.industry
    if not getattr(ou, "country", None) and hasattr(ou, "country"):
        ou.country = payload.country
    if not getattr(ou, "state", None) and hasattr(ou, "state"):
        ou.state = payload.state
    if not getattr(ou, "city", None) and hasattr(ou, "city"):
        ou.city = payload.city


async def _first_pipeline_stage_id(
    db: AsyncSession, job_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find the first stage of the job's pipeline instance.

    Returns None if the job has no pipeline instance yet (ATS-imported job
    that hasn't been wired to a template). The orchestrator skips the
    assignment in that case and surfaces a `submission.skipped_no_pipeline`
    audit event.
    """
    row = await db.execute(
        text(
            "SELECT s.id FROM job_pipeline_stages s "
            "JOIN job_pipeline_instances i ON i.id = s.instance_id "
            "WHERE i.job_posting_id = :job_id "
            "ORDER BY s.position ASC LIMIT 1"
        ).bindparams(job_id=job_id),
    )
    return row.scalar_one_or_none()
