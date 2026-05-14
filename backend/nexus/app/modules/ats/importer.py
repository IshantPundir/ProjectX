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

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

import structlog
from opentelemetry import trace
from sqlalchemy import func, select, text

from app.database import get_bypass_session
from app.modules.ats.adapter import ATSAdapter
from app.modules.audit import log_event

if TYPE_CHECKING:
    from app.modules.ats.models import ATSClientMapping
    from app.modules.org_units.models import OrganizationalUnit

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


def _normalize_payload_text(value: str | None) -> str | None:
    """Mirror the org_units service `_normalize_text` semantics for ATS
    payload string fields: `.strip()`, then empty -> None. Importing
    `_normalize_text` from org_units would cross a private module
    boundary; this two-line duplicate keeps the importer self-contained."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass
class PhaseResult:
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    sync_started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

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
    ALL_PHASES: ClassVar[tuple[str, ...]] = (
        "clients", "users", "jobs", "applicants", "submissions",
    )

    async def sync_tenant(
        self,
        adapter: ATSAdapter,
        *,
        phase_filter: set[str] | None = None,
        sync_log_id: UUID | None = None,
    ) -> SyncResult:
        """Run the selected phases. ``phase_filter`` defaults to all five.

        On phase failure, attach the partial result accumulated so far to the
        raised exception via ``partial_result`` so the actor's recovery
        handler can write meaningful entity_counts.
        """
        result = SyncResult()
        phase_table = {
            "clients":     self._sync_clients,
            "users":       self._sync_users,
            "jobs":        self._sync_jobs,
            "applicants":  self._sync_applicants,
            "submissions": self._sync_submissions,
        }
        phases_to_run = tuple(
            (name, phase_table[name])
            for name in self.ALL_PHASES
            if phase_filter is None or name in phase_filter
        )
        for name, fn in phases_to_run:
            try:
                phase_result = await self._run_phase(name, fn, adapter, sync_log_id)
            except Exception as exc:
                exc.partial_result = result  # type: ignore[attr-defined]
                raise
            setattr(result, name, phase_result)
        return result

    async def _run_phase(self, name, fn, adapter, sync_log_id: UUID | None = None) -> PhaseResult:
        tenant_id = adapter.state.tenant_id
        with tracer.start_as_current_span(f"ats.sync.{name}",
                                          attributes={"ats.vendor": adapter.vendor,
                                                      "tenant_id": str(tenant_id)}):
            async with get_bypass_session() as db:
                await db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
                phase_result = await fn(db, adapter, sync_log_id)
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
    async def _sync_clients(self, db, adapter, sync_log_id=None) -> PhaseResult:
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
                existing.last_synced_at = datetime.now(tz=UTC)

                # Backfill the linked org_unit's column-level fields where
                # currently NULL. Mirrors the promote-path pattern: NULL-only,
                # recruiter edits preserved, about/hiring_bar never touched
                # (Ceipal has no equivalent). This is the self-healing path
                # for client_accounts synced before the column-level
                # refactor — their country/state/city were never stored
                # forward through migration 0034, so the next clients sync
                # fills them in from the fresh Ceipal payload.
                unit = await db.get(OrganizationalUnit, existing.org_unit_id)
                refreshed_fields: list[str] = []
                if unit is not None:
                    for field_name in (
                        "website", "industry", "country", "state", "city",
                    ):
                        if getattr(unit, field_name) is None:
                            normalized = _normalize_payload_text(
                                getattr(payload, field_name)
                            )
                            if normalized is not None:
                                setattr(unit, field_name, normalized)
                                refreshed_fields.append(field_name)
                if refreshed_fields:
                    await log_event(
                        db,
                        tenant_id=tenant_id,
                        actor_id=created_by,
                        actor_email="ats-import",
                        action="ats.client_mapping.org_unit_refreshed",
                        resource="ats_client_mapping",
                        resource_id=existing.org_unit_id,
                        payload={
                            "vendor": adapter.vendor,
                            "external_client_id": payload.external_id,
                            "org_unit_refreshed_fields": refreshed_fields,
                        },
                    )

                result.updated += 1
                continue

            # Promotion: a stub mapping (synthetic id "name:<name>") created
            # by an earlier _sync_jobs run is upgraded in place when Ceipal
            # now returns the real client id. We rewrite external_client_id
            # to the real id and refresh source_metadata, but leave the
            # linked org_unit alone so the recruiter's in-flight profile
            # completion work survives the promotion. See spec
            # docs/superpowers/specs/2026-05-13-ats-job-sync-client-stub-design.md.
            #
            # Known limitation: if Ceipal has two distinct clients sharing
            # the same name, _sync_jobs would have consolidated both onto
            # a single "name:<n>" stub (forced by the mapping unique
            # constraint), and this block promotes the stub to whichever
            # real id arrives first. The other client's jobs would then
            # be misattributed to the first client's org_unit via the
            # name-based lookup in _upsert_job_payload. This is the
            # spec's documented same-name out-of-scope case — recruiter
            # resolves by splitting the org_unit manually.
            promotable = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_name == payload.name,
                    ATSClientMapping.external_client_id.like("name:%"),
                )
            )
            if promotable is not None:
                from_id = promotable.external_client_id
                promotable.external_client_id = payload.external_id
                promotable.source_metadata = {
                    "contacts": payload.contacts,
                    "raw": payload.raw,
                }
                promotable.last_synced_at = datetime.now(tz=UTC)

                # Refresh the linked org_unit's column-level fields ONLY
                # where they're currently NULL. Recruiter edits between
                # stub creation and promotion survive the upgrade.
                # about/hiring_bar are never auto-filled — Ceipal has no
                # equivalent.
                unit = await db.get(OrganizationalUnit, promotable.org_unit_id)
                refreshed_fields: list[str] = []
                if unit is not None:
                    for field_name in (
                        "website", "industry", "country", "state", "city",
                    ):
                        if getattr(unit, field_name) is None:
                            normalized = _normalize_payload_text(
                                getattr(payload, field_name)
                            )
                            if normalized is not None:
                                setattr(unit, field_name, normalized)
                                refreshed_fields.append(field_name)

                await log_event(
                    db,
                    tenant_id=tenant_id,
                    actor_id=created_by,
                    actor_email="ats-import",
                    action="ats.client_mapping.promoted",
                    resource="ats_client_mapping",
                    resource_id=promotable.org_unit_id,
                    payload={
                        "vendor": adapter.vendor,
                        "from_external_client_id": from_id,
                        "to_external_client_id": payload.external_id,
                        "org_unit_refreshed_fields": refreshed_fields,
                    },
                )
                result.updated += 1
                continue

            # Create the org_unit with column-level fields populated from
            # the Ceipal payload. about + hiring_bar stay NULL — recruiter
            # authors those via the inline editor on /settings/org-units/[unitId].
            new_unit = OrganizationalUnit(
                client_id=tenant_id,
                parent_unit_id=root.id,
                name=payload.name,
                unit_type="client_account",
                is_root=False,
                website=_normalize_payload_text(payload.website),
                industry=_normalize_payload_text(payload.industry),
                country=_normalize_payload_text(payload.country),
                state=_normalize_payload_text(payload.state),
                city=_normalize_payload_text(payload.city),
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

    async def _sync_users(self, db, adapter, sync_log_id=None) -> PhaseResult:
        """Phase 2: upsert ats_user_mappings.

        Auto-link: if a ProjectX User exists in this tenant with a matching
        (case-insensitive) email, set ``internal_user_id`` on the mapping
        immediately. Covers the out-of-band case where a ProjectX user was
        created before the ATS sync ever ran. The team-page invite-accept
        handler covers the opposite direction.
        """
        from app.modules.ats.models import ATSUserMapping
        from app.modules.auth.models import User

        result = PhaseResult()
        tenant_id = adapter.state.tenant_id
        now = datetime.now(tz=UTC)

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
                existing.last_synced_at = now
                # If the email changed and now matches a real user, link it.
                # We never overwrite an existing link — that would silently
                # re-point a mapping if the same vendor user changed email.
                if existing.internal_user_id is None:
                    match = await db.scalar(
                        select(User).where(
                            User.tenant_id == tenant_id,
                            func.lower(User.email) == payload.email.lower(),
                            User.is_active.is_(True),
                        )
                    )
                    if match is not None:
                        existing.internal_user_id = match.id
                        existing.mapped_at = now
                        existing.mapped_by = match.id
                result.updated += 1
                continue

            # New row — try to auto-link before insert.
            match = await db.scalar(
                select(User).where(
                    User.tenant_id == tenant_id,
                    func.lower(User.email) == payload.email.lower(),
                    User.is_active.is_(True),
                )
            )
            db.add(ATSUserMapping(
                tenant_id=tenant_id, ats_vendor=adapter.vendor,
                external_user_id=payload.external_id,
                external_user_email=payload.email,
                external_user_display_name=payload.display_name,
                external_user_role=payload.role,
                external_user_status=payload.status,
                external_user_metadata=payload.raw,
                internal_user_id=match.id if match else None,
                mapped_at=now if match else None,
                mapped_by=match.id if match else None,
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

    @staticmethod
    def _empty_partial_result() -> SyncResult:
        """Empty result for the rate-limit case — closes the sync log row
        cleanly. All five phases stay ``None`` (i.e. ``entity_counts`` returns
        the all-None dict), so the partial sync_log payload is unambiguous.
        """
        return SyncResult()

    async def _sync_jobs(self, db, adapter, sync_log_id=None) -> PhaseResult:
        """Phase 3: upsert job_postings; gated by per-connection job_status_filter.

        If ``job_status_filter`` is NULL or empty on the connection, the phase
        returns immediately with ``errors=["filter_not_configured"]`` — no DB
        writes, no Ceipal calls beyond the filter read. The recruiter UI
        surfaces a banner driven by the same condition.

        Otherwise:
          * ``adapter.count_jobs(...)`` seeds the progress denominator.
          * ``adapter.list_jobs(..., job_status_ids=...)`` streams the rows.
          * After every row, ``_write_jobs_progress`` writes to ats_sync_logs.
            A second bypass-RLS session keeps progress commits independent
            of the main phase transaction.
        """
        from app.modules.ats.models import (
            ATSClientMapping,
            ATSConnection,
            ATSJobRecruiterAssignment,
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
        if connection is None:
            raise RuntimeError(f"tenant {tenant_id} has no ats_connections row")
        created_by = connection.created_by

        filter_blob = connection.job_status_filter
        if not filter_blob or not filter_blob.get("ids"):
            result.errors.append("filter_not_configured")
            logger.info(
                "ats.sync.jobs.skipped_no_filter",
                connection_id=str(connection.id),
                tenant_id=str(tenant_id),
            )
            return result

        status_ids: list[int] = list(filter_blob["ids"])

        # Resolve the tenant's root org_unit ONCE here — mirroring
        # _sync_clients (lines 136-143). _upsert_job_payload's stub-creation
        # branch needs it for every unknown-client job; resolving it per-call
        # would issue N redundant SELECTs when N jobs all hit that branch.
        # Failing here (before any rows are written) is strictly better than
        # failing mid-loop inside _upsert_job_payload.
        root = await db.scalar(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == tenant_id,
                OrganizationalUnit.is_root.is_(True),
            )
        )
        if root is None:
            raise RuntimeError(
                f"tenant {tenant_id} has no root company org_unit"
            )

        # Cursor-based incremental: only pull jobs modified since the last
        # successful sync. Cheap when nothing's changed — Ceipal returns
        # zero rows for `modifiedAfter=<last cursor>`. A second pass below
        # detects locally-deleted jobs and re-fetches them, so this cursor
        # never silently misses a job that needs to come back.
        since = self._cursor_or_none(adapter.state, "jobs")

        # Pass 1's denominator is just the cursor-modified count. Pass 2's
        # actual missing count isn't known until Pass 1 finishes (Pass 1
        # might have inserted some of those "missing" rows). We re-seed
        # the progress total between passes — over-estimating upfront
        # would show, e.g. "3/6 done" in the test scenario when actually
        # the work is complete.
        try:
            pass1_total = await adapter.count_jobs(
                since=since, job_status_ids=status_ids,
            )
        except Exception as exc:
            logger.warning("ats.sync.jobs.count_failed", error=str(exc)[:200])
            pass1_total = -1

        total = pass1_total

        # Each call to ``_write_jobs_progress`` opens its own bypass-RLS
        # session — see that method's docstring for why a long-lived shared
        # session doesn't work with per-row commits.
        await self._write_jobs_progress(sync_log_id, 0, total, tenant_id)

        processed = 0
        async for payload in adapter.list_jobs(since=since, job_status_ids=status_ids):
            await self._upsert_job_payload(
                db, adapter, payload, tenant_id, created_by, result,
                root_org_unit_id=root.id,
            )
            processed += 1
            await self._write_jobs_progress(sync_log_id, processed, total, tenant_id)

        # ---- Pass 2: missing-job detection ----
        # If a recruiter deleted a job locally (manually or via the bulk
        # delete on /jobs during dev iteration), Pass 1 above will miss
        # it — Ceipal hasn't modified the row, so it doesn't come back.
        # Compute the actual gap NOW (after Pass 1 has run) using a fresh
        # local count; this is more accurate than estimating before Pass 1.
        try:
            ceipal_total = await adapter.count_jobs(
                since=None, job_status_ids=status_ids,
            )
        except Exception as exc:
            logger.warning(
                "ats.sync.jobs.full_count_failed",
                error=str(exc)[:200],
            )
            return result

        local_count = await db.scalar(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.tenant_id == tenant_id,
                JobPosting.source == f"ats_{adapter.vendor}",
                JobPosting.external_id.is_not(None),
            )
        ) or 0

        pass2_missing = max(0, ceipal_total - local_count)
        if pass2_missing <= 0:
            # No drift — either everything's in sync, or there are MORE
            # local rows than Ceipal returns (status changed out of the
            # filter, etc.). Either way, nothing for Pass 2 to do. If
            # Pass 1's count failed (total=-1) but its loop ran to
            # completion, surface that as a concrete total now — better
            # to show "12/12 done" than leave the bar indeterminate
            # forever.
            if total < 0:
                total = processed
                await self._write_jobs_progress(
                    sync_log_id, processed, total, tenant_id,
                )
            return result

        # Re-seed the denominator. Pass 2 always re-seeds, even when Pass 1's
        # count call failed — Pass 2 gives us a concrete `ceipal_total -
        # local_count`, which we add to whatever work Pass 1 already did.
        # Without this branch the progress bar would stay indeterminate
        # ("Counting jobs…") for the entire run whenever Pass 1's count
        # call raised — Pass 2's per-row writes would still increment
        # `processed` but with a negative denominator the bar can't render.
        total = (processed if total < 0 else total) + pass2_missing
        await self._write_jobs_progress(sync_log_id, processed, total, tenant_id)

        logger.info(
            "ats.sync.jobs.missing_detect_triggered",
            local_count=local_count,
            ceipal_total=ceipal_total,
            missing_estimate=pass2_missing,
        )

        local_ids_result = await db.execute(
            select(JobPosting.external_id).where(
                JobPosting.tenant_id == tenant_id,
                JobPosting.source == f"ats_{adapter.vendor}",
                JobPosting.external_id.is_not(None),
            )
        )
        local_ids: set[str] = {row[0] for row in local_ids_result.all()}

        async for payload in adapter.list_jobs(
            since=None,
            job_status_ids=status_ids,
            skip_external_ids=local_ids,
        ):
            # The adapter filtered known IDs out, so every payload here
            # is a missing-in-local row. _upsert_job_payload will INSERT
            # (existing is None by construction) and bump result.new.
            await self._upsert_job_payload(
                db, adapter, payload, tenant_id, created_by, result,
                root_org_unit_id=root.id,
            )
            processed += 1
            await self._write_jobs_progress(sync_log_id, processed, total, tenant_id)

        return result

    async def _upsert_job_payload(
        self,
        db,
        adapter,
        payload,
        tenant_id: UUID,
        created_by: UUID,
        result: PhaseResult,
        *,
        root_org_unit_id: UUID,
    ) -> None:
        """Upsert one job_postings row from a single ATSJobPayload.

        Shared by the cursor-based pass (Pass 1) and the missing-detect
        pass (Pass 2) in ``_sync_jobs``. Updates ``result.new`` /
        ``result.updated`` in place; replaces the row's
        ats_job_recruiter_assignments to mirror Ceipal exactly.
        """
        from app.modules.ats.models import (
            ATSClientMapping,
            ATSJobRecruiterAssignment,
        )
        from app.modules.jd.models import JobPosting
        from app.modules.org_units.models import OrganizationalUnit

        mapping = None
        if payload.external_client_id:
            mapping = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_id == payload.external_client_id,
                )
            )
        if mapping is None and payload.external_client_name:
            mapping = await db.scalar(
                select(ATSClientMapping).where(
                    ATSClientMapping.tenant_id == tenant_id,
                    ATSClientMapping.ats_vendor == adapter.vendor,
                    ATSClientMapping.external_client_name == payload.external_client_name,
                )
            )

        # Stub-creation step: a job with a client NAME but no matching
        # mapping triggers auto-creation of a stub client_account org_unit
        # so the recruiter has a row to act on (complete the company
        # profile) under /settings/org-units. The job is linked to the
        # stub; status stays 'blocked_pending_client_setup' until the
        # profile is completed.
        if mapping is None and payload.external_client_name:
            org_unit, mapping = await self._get_or_create_client_stub_by_name(
                db,
                tenant_id=tenant_id,
                vendor=adapter.vendor,
                external_client_name=payload.external_client_name,
                created_by=created_by,
                root_org_unit_id=root_org_unit_id,
            )

        # No client info at all (empty/None name) → import unlinked.
        # org_unit_id stays NULL; the /jobs page's 'Not set up' chip
        # renders for this narrow case only. The recruiter wires the
        # job to an org_unit later via a separate flow.
        if mapping is None:
            logger.info(
                "ats.sync.jobs.imported_unlinked",
                external_job_id=payload.external_id,
                external_client_id=payload.external_client_id,
                external_client_name=payload.external_client_name,
            )
            org_unit_id_for_insert: UUID | None = None
            target_status = "blocked_pending_client_setup"
        else:
            org_unit = await db.get(OrganizationalUnit, mapping.org_unit_id)
            target_status = (
                "blocked_pending_client_setup"
                if org_unit.company_profile_completion_status == "pending"
                else "draft"
            )
            org_unit_id_for_insert = org_unit.id

        existing = await db.scalar(
            select(JobPosting).where(
                JobPosting.tenant_id == tenant_id,
                JobPosting.source == f"ats_{adapter.vendor}",
                JobPosting.external_id == payload.external_id,
            )
        )
        if existing is not None:
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

            # Migrate a NULL org_unit_id forward when the current sync now
            # resolves a stub or real mapping for the job's client. This
            # closes the regression where a job imported before the
            # stub-creation feature shipped — and thus landed with
            # org_unit_id=NULL — would stay orphaned even after a later
            # sync produced a stub it could link to. We deliberately do
            # NOT overwrite an existing non-NULL link: recruiters may have
            # manually managed it, and the org_units profile-completion
            # cascade is the authoritative path for status transitions.
            if existing.org_unit_id is None and org_unit_id_for_insert is not None:
                existing.org_unit_id = org_unit_id_for_insert

            job_id = existing.id
            result.updated += 1
        else:
            jp = JobPosting(
                tenant_id=tenant_id, org_unit_id=org_unit_id_for_insert,
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

        # Replace-all recruiter assignments
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

    async def _get_or_create_client_stub_by_name(
        self,
        db,
        *,
        tenant_id: UUID,
        vendor: str,
        external_client_name: str,
        created_by: UUID,
        root_org_unit_id: UUID,
    ) -> tuple[OrganizationalUnit, ATSClientMapping]:
        """Look up or create a stub client_account org_unit + mapping pair
        for a Ceipal job whose ``client`` field is a name with no matching
        ``ats_client_mappings`` row.

        Idempotent: returns the existing stub if one already exists under
        the synthetic id ``"name:" + external_client_name``. Otherwise
        inserts a new ``OrganizationalUnit`` (parented at the tenant's
        root) and a paired ``ATSClientMapping`` and writes an audit row.

        Returns ``(org_unit, mapping)``.

        Called from ``_upsert_job_payload`` when both existing mapping
        lookups miss and the payload carries a non-empty
        ``external_client_name``. ``_sync_clients`` does not use this
        helper — it already has its own create path and follows up with
        a promotion check (see ``_sync_clients``).
        """
        from app.modules.ats.models import ATSClientMapping
        from app.modules.org_units.models import OrganizationalUnit

        synthetic_id = f"name:{external_client_name}"

        existing = await db.scalar(
            select(ATSClientMapping).where(
                ATSClientMapping.tenant_id == tenant_id,
                ATSClientMapping.ats_vendor == vendor,
                ATSClientMapping.external_client_id == synthetic_id,
            )
        )
        if existing is not None:
            org_unit = await db.get(OrganizationalUnit, existing.org_unit_id)
            return org_unit, existing

        org_unit = OrganizationalUnit(
            client_id=tenant_id,
            parent_unit_id=root_org_unit_id,
            name=external_client_name,
            unit_type="client_account",
            is_root=False,
            company_profile_completion_status="pending",
            created_by=created_by,
        )
        db.add(org_unit)
        await db.flush()

        mapping = ATSClientMapping(
            tenant_id=tenant_id,
            ats_vendor=vendor,
            external_client_id=synthetic_id,
            external_client_name=external_client_name,
            org_unit_id=org_unit.id,
            source_metadata={"stub": True, "origin": "jobs_phase"},
        )
        db.add(mapping)

        await log_event(
            db,
            tenant_id=tenant_id,
            actor_id=created_by,
            actor_email="ats-import",
            action="ats.client_mapping.created",
            resource="ats_client_mapping",
            resource_id=org_unit.id,
            payload={
                "vendor": vendor,
                "external_client_id": synthetic_id,
                "org_unit_id": str(org_unit.id),
                "stub": True,
                "origin": "jobs_phase",
            },
        )
        return org_unit, mapping

    @staticmethod
    async def _write_jobs_progress(
        sync_log_id: UUID | None,
        processed: int,
        total: int,
        tenant_id: UUID,
    ) -> None:
        """Update ats_sync_logs.progress with the jobs-phase counter.

        Each call opens its OWN ``get_bypass_session()`` context. We can't
        share a long-lived session here: ``get_bypass_session`` enters
        ``async with session.begin()`` internally, and calling
        ``session.commit()`` on the inner session ends the begin-context the
        first time. The next ``session.execute(...)`` would raise
        ``InvalidRequestError: Can't operate on closed transaction inside
        context manager.`` Per-call sessions sidestep that — each call is a
        complete begin → SET LOCAL → UPDATE → commit cycle, and the context
        manager commits at exit (no manual commit needed).

        ``SET LOCAL app.current_tenant`` is the only RLS gate needed: the
        ``service_bypass`` policy on ``ats_sync_logs`` requires
        ``app.bypass_rls = 'true'`` which ``get_bypass_session`` sets
        itself; the ``tenant_isolation`` policy needs the tenant uuid to
        match the row. Either policy alone admits the UPDATE.

        No-op when ``sync_log_id`` is None (test paths that don't care
        about progress).
        """
        if sync_log_id is None:
            return
        payload = json.dumps({"processed": processed, "total": total})
        async with get_bypass_session() as prog_db:
            await prog_db.execute(
                text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )
            await prog_db.execute(
                text(
                    "UPDATE ats_sync_logs "
                    "SET progress = jsonb_set(progress, '{jobs}', CAST(:p AS jsonb)) "
                    "WHERE id = :id"
                ),
                {"p": payload, "id": sync_log_id},
            )
            # The outer ``session.begin()`` inside get_bypass_session
            # commits on context exit. Do NOT call prog_db.commit() here.

    async def _sync_applicants(self, db, adapter, sync_log_id=None) -> PhaseResult:
        """Phase 4: applicants → candidates via import_candidate.

        Reuses the candidates module's idempotent service function; collisions
        with manual-flow candidates (same email) link external_id without
        overwriting editable fields.
        """
        from app.modules.ats.models import ATSConnection
        from app.modules.ats.sources import ATSImportSource
        from app.modules.candidates import import_candidate

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

    async def _sync_submissions(self, db, adapter, sync_log_id=None) -> PhaseResult:
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
