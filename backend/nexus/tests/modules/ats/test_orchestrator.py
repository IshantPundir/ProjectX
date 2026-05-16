"""ATSSyncOrchestrator — unit-level coverage for the four hard-to-get-right
parts of the new sync model:

  1. Email-collision matrix (case 1 / 2 / 3 / 4 from the spec).
  2. Client resolution: HIT, MISS-with-fetch, MISS-with-orphan.
  3. JobDiffResult / SubmissionDiffResult — created / updated / unchanged.
  4. Quarantine logic — 3 consecutive failures → import_quarantined_at set.

Uses a synthetic in-memory FakeAdapter implementing the new Protocol so the
HTTP layer is out of scope. The orchestrator is exercised against the
rollback-isolated ``db`` fixture from the project-root conftest.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from app.modules.ats.adapter import ATSAdapterCapabilities
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.constants import ATS_VENDOR_CEIPAL
from app.modules.ats.orchestrator import (
    ATSSyncOrchestrator,
    JobDiffResult,
)
from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientPayload,
    ATSJobPayload,
    ATSJobStatus,
    ATSSubmissionPayload,
    ATSUserPayload,
)


# ────────────────────────── Fake adapter ──────────────────────────


class FakeAdapter:
    """Minimal in-memory ATSAdapter for orchestrator tests."""

    vendor = ATS_VENDOR_CEIPAL
    capabilities = ATSAdapterCapabilities(
        supports_modified_after_cursor=True,
        supports_per_job_submission_cursor=True,
        supports_client_search_by_name=False,
        job_detail_required_for_client_name=True,
        rate_limit_qps=0.5,
    )

    def __init__(
        self,
        state: ATSConnectionState,
        *,
        jobs: list[ATSJobPayload] | None = None,
        clients: list[ATSClientPayload] | None = None,
        users: dict[str, ATSUserPayload] | None = None,
        submissions: dict[str, list[ATSSubmissionPayload]] | None = None,
        applicants: dict[str, ATSApplicantPayload] | None = None,
        enrich_map: dict[str, str] | None = None,
    ) -> None:
        self.state = state
        self._jobs = jobs or []
        self._clients = clients or []
        self._users = users or {}
        self._submissions = submissions or {}
        self._applicants = applicants or {}
        self._enrich_map = enrich_map or {}

    async def ensure_authenticated(self) -> None:
        return

    async def list_job_statuses(self) -> list[ATSJobStatus]:
        return []

    async def iter_jobs(
        self, *, status_external_ids: list[str], modified_after,
    ) -> AsyncIterator[ATSJobPayload]:
        for j in self._jobs:
            yield j

    async def enrich_job(self, job: ATSJobPayload) -> ATSJobPayload:
        name = self._enrich_map.get(job.external_id)
        if name is None:
            return job
        return job.model_copy(update={"client_external_name": name})

    async def iter_clients(self) -> AsyncIterator[ATSClientPayload]:
        for c in self._clients:
            yield c

    async def get_client(self, *, external_id: str) -> ATSClientPayload:
        for c in self._clients:
            if c.external_id == external_id:
                return c
        raise KeyError(external_id)

    async def get_user(self, *, external_id: str) -> ATSUserPayload:
        return self._users[external_id]

    async def iter_submissions(
        self, *, job_external_id: str, modified_after,
    ) -> AsyncIterator[ATSSubmissionPayload]:
        for s in self._submissions.get(job_external_id, []):
            yield s

    async def get_applicant(
        self, *, external_id: str,
    ) -> ATSApplicantPayload:
        return self._applicants[external_id]


# ────────────────────────── Fixtures ──────────────────────────


@pytest.fixture
def now_utc() -> datetime:
    return datetime.now(tz=UTC)


@pytest.fixture
async def seeded(db, now_utc):
    """Insert a tenant + root org_unit + actor user — minimum to run."""
    tenant_id = uuid.uuid4()
    root_unit_id = uuid.uuid4()
    actor_user_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO clients (id, name) VALUES (:t, 'Acme')"),
        {"t": tenant_id},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, auth_user_id, source) "
            "VALUES (:u, :t, 'admin@acme.com', :a, 'native')"
        ),
        {"u": actor_user_id, "t": tenant_id, "a": uuid.uuid4()},
    )
    await db.execute(
        text(
            "INSERT INTO organizational_units "
            "(id, client_id, name, unit_type, is_root, "
            " company_profile_completion_status, source) "
            "VALUES (:o, :t, 'Acme', 'company', true, 'complete', 'native')"
        ),
        {"o": root_unit_id, "t": tenant_id},
    )
    await db.flush()
    return {
        "tenant_id": tenant_id,
        "root_unit_id": root_unit_id,
        "actor_user_id": actor_user_id,
    }


def _state(
    tenant_id: uuid.UUID, *,
    last_synced_at: datetime | None = None,
    job_status_filter: dict[str, Any] | None = None,
) -> ATSConnectionState:
    return ATSConnectionState(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        vendor=ATS_VENDOR_CEIPAL,
        credentials={},
        access_token="t",
        last_synced_at=last_synced_at,
        tenant_timezone="Asia/Kolkata",
        status_sync_mode="advisory",
        job_status_filter=job_status_filter or {"ids": [1], "names": ["Active"]},
    )


def _job(
    *, external_id: str = "J1",
    title: str = "Eng",
    client_external_id: str | None = None,
    client_external_name: str | None = None,
    primary_recruiter: str | None = None,
    assigned_recruiters: list[str] | None = None,
    external_status: str = "Active",
) -> ATSJobPayload:
    now = datetime.now(tz=UTC)
    return ATSJobPayload(
        external_id=external_id,
        title=title,
        description_raw="...",
        external_status=external_status,
        external_status_id="1",
        client_external_id=client_external_id,
        client_external_name=client_external_name,
        primary_recruiter_external_id=primary_recruiter,
        assigned_recruiter_external_ids=assigned_recruiters or [],
        external_created_at=now,
        external_modified_at=now,
        raw={"id": external_id},
    )


def _client_payload(
    external_id: str, name: str,
    *, contacts=None,
) -> ATSClientPayload:
    return ATSClientPayload(
        external_id=external_id,
        name=name,
        contacts=contacts or [],
        raw={"id": external_id, "name": name},
    )


def _user_payload(
    external_id: str, email: str, full_name: str = "Test User",
) -> ATSUserPayload:
    return ATSUserPayload(
        external_id=external_id,
        email=email,
        full_name=full_name,
        external_status="Active",
        raw={"id": external_id, "email_id": email},
    )


def _make_orch(
    adapter: FakeAdapter, tenant_id: uuid.UUID, actor_id: uuid.UUID,
) -> ATSSyncOrchestrator:
    return ATSSyncOrchestrator(
        adapter,
        connection_id=adapter.state.id,
        tenant_id=tenant_id,
        correlation_id=f"corr-{uuid.uuid4()}",
        actor_id=actor_id,
        actor_email="recruiter",
        action_source="manual",
    )


# ─────────────────────── Email-collision matrix ───────────────────────


@pytest.mark.asyncio
async def test_user_case_1_no_collision_inserts_new_ats_user(db, seeded, monkeypatch):
    """Case 1: no existing user matches email → INSERT new (source='ats_ceipal',
    auth_user_id=None, is_active=False)."""
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    user_payload = _user_payload("U-NEW", "newperson@acme.com")
    adapter = FakeAdapter(state, users={"U-NEW": user_payload})
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    user = await orch._resolve_user_by_external_id(db, "U-NEW")
    assert user is not None
    assert user.source == "ats_ceipal"
    assert user.external_id == "U-NEW"
    assert user.auth_user_id is None
    assert user.is_active is False


@pytest.mark.asyncio
async def test_user_case_2_email_collision_native_no_external_id_links(
    db, seeded, monkeypatch,
):
    """Case 2: existing native user, external_id=NULL → UPDATE external_id;
    source stays 'native'."""
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    # Pre-seed a native user with the same email.
    existing_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, auth_user_id, "
            "source, external_id) "
            "VALUES (:u, :t, 'collide@acme.com', :a, 'native', NULL)"
        ),
        {"u": existing_id, "t": seeded["tenant_id"], "a": uuid.uuid4()},
    )
    await db.flush()

    state = _state(seeded["tenant_id"])
    user_payload = _user_payload("U-X", "collide@acme.com")
    adapter = FakeAdapter(state, users={"U-X": user_payload})
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    user = await orch._resolve_user_by_external_id(db, "U-X")
    assert user is not None
    assert user.id == existing_id
    assert user.source == "native"
    assert user.external_id == "U-X"


@pytest.mark.asyncio
async def test_user_case_4_email_collision_external_id_mismatch_skips(
    db, seeded, monkeypatch,
):
    """Case 4: existing user has a different external_id → skip (return None)."""
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    existing_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, auth_user_id, "
            "source, external_id) "
            "VALUES (:u, :t, 'dup@acme.com', :a, 'ats_ceipal', 'OTHER-EXT-ID')"
        ),
        {"u": existing_id, "t": seeded["tenant_id"], "a": uuid.uuid4()},
    )
    await db.flush()

    state = _state(seeded["tenant_id"])
    user_payload = _user_payload("U-COLLIDE", "dup@acme.com")
    adapter = FakeAdapter(state, users={"U-COLLIDE": user_payload})
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    user = await orch._resolve_user_by_external_id(db, "U-COLLIDE")
    assert user is None  # collision-skipped


# ─────────────────────── Client resolution ───────────────────────


@pytest.mark.asyncio
async def test_client_hit_returns_existing_org_unit(db, seeded, monkeypatch):
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    existing_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO organizational_units "
            "(id, client_id, name, unit_type, parent_unit_id, "
            "company_profile_completion_status, source, external_id) "
            "VALUES (:o, :t, 'Oracle', 'client_account', :r, 'pending', "
            "'ats_ceipal', 'C-123')"
        ),
        {
            "o": existing_id,
            "t": seeded["tenant_id"],
            "r": seeded["root_unit_id"],
        },
    )
    await db.flush()

    state = _state(seeded["tenant_id"])
    job = _job(client_external_id="C-123")
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    ou = await orch._resolve_client(db, job)
    assert ou is not None
    assert ou.id == existing_id


@pytest.mark.asyncio
async def test_client_miss_with_name_resolution_inserts_org_unit(
    db, seeded, monkeypatch,
):
    """Job has only client_external_name. iter_clients yields a match
    (case-insensitive). get_client is called for authoritative payload.
    A new org_unit row is created with source='ats_ceipal'."""
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    job = _job(client_external_name="ORACLE")  # uppercase intentional
    client_payload = _client_payload("C-NEW", "Oracle")
    adapter = FakeAdapter(state, clients=[client_payload])
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    ou = await orch._resolve_client(db, job)
    assert ou is not None
    assert ou.name == "Oracle"
    assert ou.source == "ats_ceipal"
    assert ou.external_id == "C-NEW"


@pytest.mark.asyncio
async def test_client_orphan_name_not_in_index_returns_none(
    db, seeded, monkeypatch,
):
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    job = _job(client_external_name="UnknownCorp")
    adapter = FakeAdapter(state, clients=[])  # empty index
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    ou = await orch._resolve_client(db, job)
    assert ou is None


# ─────────────────────── Job diff (created / unchanged) ───────────────────


@pytest.mark.asyncio
async def test_upsert_job_kind_created_when_new(db, seeded, monkeypatch):
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    job = _job(external_id="JOB-NEW", title="SRE")
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    diff: JobDiffResult = await orch._upsert_job(db, job, None, {
        "assigned_recruiter": [],
        "primary_recruiter": [],
        "posted_by": [],
        "created_by": [],
    })
    assert diff.kind == "created"
    assert diff.job.external_id == "JOB-NEW"
    assert diff.job.source == "ats_ceipal"


@pytest.mark.asyncio
async def test_upsert_job_kind_unchanged_when_identical(
    db, seeded, monkeypatch, now_utc,
):
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    job = _job(external_id="JOB-X", title="X")
    await orch._upsert_job(db, job, None, _empty_recruiters())
    # Re-upsert the same payload — should be 'unchanged'.
    diff = await orch._upsert_job(db, job, None, _empty_recruiters())
    assert diff.kind == "unchanged"


@pytest.mark.asyncio
async def test_upsert_job_status_change_recorded_in_diff(
    db, seeded, monkeypatch,
):
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    job_v1 = _job(external_id="JOB-S", external_status="Active")
    await orch._upsert_job(db, job_v1, None, _empty_recruiters())

    job_v2 = _job(external_id="JOB-S", external_status="Hold by Client")
    diff = await orch._upsert_job(db, job_v2, None, _empty_recruiters())
    assert diff.kind == "updated"
    assert diff.status_transition == ("Active", "Hold by Client")
    assert "external_status" in diff.changed_fields


# ─────────────────────── Quarantine logic ───────────────────


@pytest.mark.asyncio
async def test_quarantine_after_three_consecutive_failures(
    db, seeded, monkeypatch,
):
    """3 consecutive _mark_job_errored calls set import_quarantined_at."""
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    # Pre-create the job row so _mark_job_errored can update it.
    job = _job(external_id="JOB-FAIL")
    await orch._upsert_job(db, job, None, _empty_recruiters())

    for _ in range(3):
        await orch._mark_job_errored(job, RuntimeError("synthetic"))

    row = await db.execute(
        text(
            "SELECT import_retry_count, import_quarantined_at, import_last_error "
            "FROM job_postings WHERE external_id = :e"
        ),
        {"e": "JOB-FAIL"},
    )
    r = row.one()
    assert r.import_retry_count == 3
    assert r.import_quarantined_at is not None
    assert "synthetic" in (r.import_last_error or "")


@pytest.mark.asyncio
async def test_two_failures_dont_quarantine_yet(db, seeded, monkeypatch):
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    state = _state(seeded["tenant_id"])
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, seeded["tenant_id"], seeded["actor_user_id"])

    job = _job(external_id="JOB-RETRY")
    await orch._upsert_job(db, job, None, _empty_recruiters())

    for _ in range(2):
        await orch._mark_job_errored(job, RuntimeError("transient"))

    row = await db.execute(
        text(
            "SELECT import_retry_count, import_quarantined_at "
            "FROM job_postings WHERE external_id = :e"
        ),
        {"e": "JOB-RETRY"},
    )
    r = row.one()
    assert r.import_retry_count == 2
    assert r.import_quarantined_at is None


# ──────────────────────────── helpers ────────────────────────────


def _empty_recruiters() -> dict[str, list]:
    return {
        "assigned_recruiter": [],
        "primary_recruiter": [],
        "posted_by": [],
        "created_by": [],
    }


def _patch_orch_session(monkeypatch, orch_mod, test_db):
    """Patch the orchestrator's `_open_db` helper to yield the test db
    session and stub out tenant scope binding."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_open(self):
        # We can't actually commit (would break rollback isolation).
        original_commit = test_db.commit
        test_db.commit = test_db.flush  # type: ignore[method-assign]
        try:
            yield test_db
        finally:
            test_db.commit = original_commit  # type: ignore[method-assign]

    monkeypatch.setattr(
        orch_mod.ATSSyncOrchestrator, "_open_db", _fake_open,
    )
    # The orchestrator's _is_job_quarantined opens its own session — same
    # patch covers it because _open_db is the single seam.


# ─────────────── _upsert_assignment — identity + idempotency ────────────────
#
# These tests cover the bugfix where _upsert_assignment used to look up
# existing rows by (tenant, source, external_id) — which is provenance
# metadata, not identity. The DB unique constraint is on (candidate, job),
# so a recruiter who manually assigned a candidate to a job, or a re-sync
# of an existing ATS submission with a different external_id, would slip
# past the lookup and crash on UniqueViolationError.


async def _seed_job_with_pipeline(
    db, *, tenant_id: uuid.UUID, root_unit_id: uuid.UUID, actor_id: uuid.UUID,
    title: str = "Test Role",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a job + its bookend pipeline. Returns (job_id, first_stage_id)."""
    job_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    stage_id = uuid.uuid4()
    debrief_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO job_postings "
            "(id, tenant_id, org_unit_id, title, description_raw, status, "
            " source, created_by) "
            "VALUES (:j, :t, :o, :title, '...', 'active', 'manual', :u)"
        ),
        {"j": job_id, "t": tenant_id, "o": root_unit_id, "title": title,
         "u": actor_id},
    )
    await db.execute(
        text(
            "INSERT INTO job_pipeline_instances "
            "(id, tenant_id, job_posting_id, pipeline_version) "
            "VALUES (:i, :t, :j, 1)"
        ),
        {"i": instance_id, "t": tenant_id, "j": job_id},
    )
    await db.execute(
        text(
            "INSERT INTO job_pipeline_stages "
            "(id, tenant_id, instance_id, position, name, stage_type, "
            " advance_behavior, otp_required_default) "
            "VALUES (:s, :t, :i, 0, 'Intake', 'intake', 'auto_advance', false)"
        ),
        {"s": stage_id, "t": tenant_id, "i": instance_id},
    )
    await db.execute(
        text(
            "INSERT INTO job_pipeline_stages "
            "(id, tenant_id, instance_id, position, name, stage_type, "
            " advance_behavior, otp_required_default) "
            "VALUES (:s, :t, :i, 1, 'Debrief', 'debrief', 'manual_review', "
            "false)"
        ),
        {"s": debrief_id, "t": tenant_id, "i": instance_id},
    )
    await db.flush()
    return job_id, stage_id


async def _seed_candidate(
    db, *, tenant_id: uuid.UUID, created_by: uuid.UUID,
    email: str = "cand@acme.com",
) -> uuid.UUID:
    cand_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO candidates "
            "(id, tenant_id, name, email, source, created_by) "
            "VALUES (:c, :t, 'C', :e, 'manual', :u)"
        ),
        {"c": cand_id, "t": tenant_id, "e": email, "u": created_by},
    )
    await db.flush()
    return cand_id


def _submission(
    *, external_id: str = "SUB-1",
    job_external_id: str = "J-1",
    external_status: str = "Submitted",
    pipeline_status: str | None = None,
) -> ATSSubmissionPayload:
    now = datetime.now(tz=UTC)
    return ATSSubmissionPayload(
        external_id=external_id,
        job_external_id=job_external_id,
        applicant_external_id="A-1",
        external_status=external_status,
        pipeline_status=pipeline_status,
        external_submitted_at=now,
        external_modified_at=now,
        raw={"id": external_id, "submission_status": external_status},
    )


@pytest.mark.asyncio
async def test_upsert_assignment_creates_new_with_ats_source(
    db, seeded, monkeypatch,
):
    """No existing (candidate, job) row → INSERT with source='ats_ceipal'."""
    from app.modules.jd.models import JobPosting
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    tenant_id = seeded["tenant_id"]
    actor_id = seeded["actor_user_id"]
    job_id, _stage_id = await _seed_job_with_pipeline(
        db, tenant_id=tenant_id, root_unit_id=seeded["root_unit_id"],
        actor_id=actor_id,
    )
    cand_id = await _seed_candidate(db, tenant_id=tenant_id, created_by=actor_id)
    job = await db.get(JobPosting, job_id)

    state = _state(tenant_id)
    adapter = FakeAdapter(state)
    orch = _make_orch(adapter, tenant_id, actor_id)

    sub = _submission(external_id="ZZ-NEW", external_status="Submitted")
    diff = await orch._upsert_assignment(
        db, sub, candidate_id=cand_id, job=job,
    )

    assert diff.kind == "created"
    assert diff.assignment.source == "ats_ceipal"
    assert diff.assignment.external_id == "ZZ-NEW"
    assert diff.assignment.external_status == "Submitted"
    assert diff.assignment.candidate_id == cand_id
    assert diff.assignment.job_posting_id == job_id


@pytest.mark.asyncio
async def test_upsert_assignment_claims_manual_row_preserving_provenance(
    db, seeded, monkeypatch,
):
    """A recruiter manually assigned candidate→job; later ATS sync brings
    a submission for the same pair. Upsert must:

    - Find the existing row via (tenant, candidate, job) — NOT
      (tenant, source, external_id), which is the bug we fixed.
    - Update external_id / external_status / external_pipeline_status /
      external_last_modified_at / source_metadata to claim the ATS link.
    - Preserve provenance: source stays 'manual', assigned_by/current_stage
      untouched.
    - Return kind='updated' with external_id in changed_fields and a
      status_transition reflecting NULL → 'Submitted'.

    Repro for the original bug: lookup-by-provenance missed this row,
    INSERT branch crashed on candidate_job_assignments_unique_candidate_job.
    """
    from app.modules.jd.models import JobPosting
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    tenant_id = seeded["tenant_id"]
    actor_id = seeded["actor_user_id"]
    job_id, stage_id = await _seed_job_with_pipeline(
        db, tenant_id=tenant_id, root_unit_id=seeded["root_unit_id"],
        actor_id=actor_id,
    )
    cand_id = await _seed_candidate(db, tenant_id=tenant_id, created_by=actor_id)

    # Manual assignment — source='manual', external_id NULL.
    manual_assignment_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO candidate_job_assignments "
            "(id, tenant_id, candidate_id, job_posting_id, source, "
            " current_stage_id, status, assigned_by) "
            "VALUES (:a, :t, :c, :j, 'manual', :s, 'active', :u)"
        ),
        {"a": manual_assignment_id, "t": tenant_id, "c": cand_id,
         "j": job_id, "s": stage_id, "u": actor_id},
    )
    await db.flush()

    job = await db.get(JobPosting, job_id)
    state = _state(tenant_id)
    orch = _make_orch(FakeAdapter(state), tenant_id, actor_id)

    sub = _submission(
        external_id="ATS-CLAIM-1",
        external_status="Submitted",
        pipeline_status="L1 In-Process",
    )
    diff = await orch._upsert_assignment(
        db, sub, candidate_id=cand_id, job=job,
    )

    assert diff.kind == "updated"
    assert diff.assignment.id == manual_assignment_id
    # Provenance preserved.
    assert diff.assignment.source == "manual"
    assert diff.assignment.assigned_by == actor_id
    assert diff.assignment.current_stage_id == stage_id
    # ATS metadata populated.
    assert diff.assignment.external_id == "ATS-CLAIM-1"
    assert diff.assignment.external_status == "Submitted"
    assert diff.assignment.external_pipeline_status == "L1 In-Process"
    # Diff captures the new fields.
    assert "external_id" in diff.changed_fields
    assert "external_status" in diff.changed_fields
    assert "external_pipeline_status" in diff.changed_fields
    assert diff.status_transition == (None, "Submitted")


@pytest.mark.asyncio
async def test_upsert_assignment_idempotent_resync(db, seeded, monkeypatch):
    """Re-syncing the same submission with no field changes → unchanged.

    Regression: the original orchestrator would also crash if Ceipal
    re-issued the same submission ID and the row already existed under a
    prior (source, external_id) lookup miss.
    """
    from app.modules.jd.models import JobPosting
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    tenant_id = seeded["tenant_id"]
    actor_id = seeded["actor_user_id"]
    job_id, _stage_id = await _seed_job_with_pipeline(
        db, tenant_id=tenant_id, root_unit_id=seeded["root_unit_id"],
        actor_id=actor_id,
    )
    cand_id = await _seed_candidate(db, tenant_id=tenant_id, created_by=actor_id)
    job = await db.get(JobPosting, job_id)
    orch = _make_orch(
        FakeAdapter(_state(tenant_id)), tenant_id, actor_id,
    )

    sub = _submission(external_id="SUB-IDEM", external_status="Submitted")

    first = await orch._upsert_assignment(
        db, sub, candidate_id=cand_id, job=job,
    )
    assert first.kind == "created"

    second = await orch._upsert_assignment(
        db, sub, candidate_id=cand_id, job=job,
    )
    assert second.kind == "unchanged"
    assert second.assignment.id == first.assignment.id


@pytest.mark.asyncio
async def test_upsert_assignment_resync_with_changed_external_id(
    db, seeded, monkeypatch,
):
    """Re-sync where Ceipal re-issued the submission ID for the same
    (candidate, job) pair. Old code would miss the existing row in the
    (tenant, source, external_id) lookup and crash on the UNIQUE
    (candidate, job) constraint. New code finds it by canonical identity
    and updates external_id in place.
    """
    from app.modules.jd.models import JobPosting
    from app.modules.ats import orchestrator as orch_mod
    _patch_orch_session(monkeypatch, orch_mod, db)

    tenant_id = seeded["tenant_id"]
    actor_id = seeded["actor_user_id"]
    job_id, _stage_id = await _seed_job_with_pipeline(
        db, tenant_id=tenant_id, root_unit_id=seeded["root_unit_id"],
        actor_id=actor_id,
    )
    cand_id = await _seed_candidate(db, tenant_id=tenant_id, created_by=actor_id)
    job = await db.get(JobPosting, job_id)
    orch = _make_orch(
        FakeAdapter(_state(tenant_id)), tenant_id, actor_id,
    )

    first = await orch._upsert_assignment(
        db,
        _submission(external_id="OLD-ID", external_status="Submitted"),
        candidate_id=cand_id, job=job,
    )
    assert first.kind == "created"

    diff = await orch._upsert_assignment(
        db,
        _submission(external_id="NEW-ID", external_status="Submitted"),
        candidate_id=cand_id, job=job,
    )
    assert diff.kind == "updated"
    assert diff.assignment.external_id == "NEW-ID"
    assert "external_id" in diff.changed_fields
    # Status did not change — no transition recorded.
    assert diff.status_transition is None


# ──────────── trigger_manual_sync — stranded-row recovery ────────────
#
# Recovers the system after a worker crash that left an ats_sync_logs
# row in status='running' without a live actor holding the advisory
# lock. Pre-fix, the in-flight pre-check raised 409 unconditionally,
# making this state unrecoverable from the UI.


async def _seed_ats_connection(
    db, *, tenant_id: uuid.UUID, created_by: uuid.UUID,
) -> uuid.UUID:
    """Insert a minimal active ATSConnection row."""
    conn_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO ats_connections "
            "(id, tenant_id, vendor, credentials_ciphertext, active, "
            " job_status_filter, created_by) "
            "VALUES (:c, :t, 'ats_ceipal', :blob, true, "
            " '{\"ids\": [1], \"names\": [\"Active\"]}'::jsonb, :u)"
        ),
        {"c": conn_id, "t": tenant_id, "blob": b"x", "u": created_by},
    )
    await db.flush()
    return conn_id


async def _seed_running_sync_log(
    db, *, tenant_id: uuid.UUID, connection_id: uuid.UUID,
) -> uuid.UUID:
    """Insert a fake stranded ats_sync_logs row (status='running')."""
    log_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO ats_sync_logs "
            "(id, tenant_id, connection_id, status, started_at, "
            " correlation_id) "
            "VALUES (:l, :t, :c, 'running', now(), :corr)"
        ),
        {"l": log_id, "t": tenant_id, "c": connection_id,
         "corr": f"corr-{log_id}"},
    )
    await db.flush()
    return log_id


@pytest.mark.asyncio
async def test_trigger_cleans_stranded_running_row_and_enqueues(
    db, seeded, monkeypatch,
):
    """A 'running' sync_log with no actor holding the advisory lock is
    stranded from a dead worker. trigger_manual_sync must mark it
    failed (so the polling dialog can flip out of "Syncing…") and
    enqueue the new sync, NOT raise SyncAlreadyRunningError.

    Without this, one crashed worker permanently bricks the
    connection's sync — every subsequent trigger 409s.
    """
    from app.modules.ats import service as ats_service

    tenant_id = seeded["tenant_id"]
    actor_id = seeded["actor_user_id"]
    conn_id = await _seed_ats_connection(
        db, tenant_id=tenant_id, created_by=actor_id,
    )
    stranded_id = await _seed_running_sync_log(
        db, tenant_id=tenant_id, connection_id=conn_id,
    )

    sent_args: list[tuple] = []

    class _FakeActor:
        @staticmethod
        def send(*args, **kwargs):
            sent_args.append((args, kwargs))

    monkeypatch.setattr(
        "app.modules.ats.actors.poll_ats_connection", _FakeActor,
    )

    await ats_service.trigger_manual_sync(
        db, connection_id=conn_id, tenant_id=tenant_id, actor_id=actor_id,
    )

    # Stranded row finalized.
    row = (await db.execute(
        text(
            "SELECT status, error_phase FROM ats_sync_logs WHERE id = :i"
        ).bindparams(i=stranded_id),
    )).one()
    assert row.status == "failed"
    assert row.error_phase == "abandoned"

    # New sync actually enqueued.
    assert len(sent_args) == 1
    assert sent_args[0][0] == (str(conn_id), str(tenant_id), str(actor_id))


@pytest.mark.asyncio
async def test_trigger_rejects_when_actor_holds_advisory_lock(
    db, seeded, monkeypatch,
):
    """When another session (the live actor) holds the per-connection
    advisory lock, trigger_manual_sync must raise SyncAlreadyRunningError
    — the row is NOT stranded; an actor is genuinely processing.

    Simulated by acquiring the lock on a second connection from the
    same pool before calling trigger_manual_sync. The probe in trigger
    uses pg_try_advisory_xact_lock, which respects locks held by any
    other session.
    """
    # Use the test engine (not app.database.engine) so the holder
    # connection lives in the same Postgres database as the test
    # session — advisory locks are per-database, not cluster-wide.
    from tests.conftest import test_engine
    from app.modules.ats import service as ats_service
    from app.modules.ats.actors import _advisory_lock_key
    from app.modules.ats.service import SyncAlreadyRunningError

    tenant_id = seeded["tenant_id"]
    actor_id = seeded["actor_user_id"]
    conn_id = await _seed_ats_connection(
        db, tenant_id=tenant_id, created_by=actor_id,
    )
    await _seed_running_sync_log(
        db, tenant_id=tenant_id, connection_id=conn_id,
    )

    # Patch the actor send so a stray enqueue (would indicate the
    # function didn't raise as expected) fails the test loudly.
    def _should_not_enqueue(*a, **kw):
        raise AssertionError(
            "trigger_manual_sync enqueued a sync while another actor "
            "supposedly held the lock — pre-check is broken",
        )

    class _FakeActor:
        send = staticmethod(_should_not_enqueue)

    monkeypatch.setattr(
        "app.modules.ats.actors.poll_ats_connection", _FakeActor,
    )

    # Hold the advisory lock on a *separate* connection. We use
    # pg_advisory_lock (session-scoped) so it survives until we
    # explicitly release.
    lock_key = _advisory_lock_key(conn_id)
    holder = await test_engine.connect()
    try:
        await holder.execute(
            text("SELECT pg_advisory_lock(:k)").bindparams(k=lock_key),
        )
        with pytest.raises(SyncAlreadyRunningError):
            await ats_service.trigger_manual_sync(
                db, connection_id=conn_id, tenant_id=tenant_id,
                actor_id=actor_id,
            )
    finally:
        await holder.execute(
            text("SELECT pg_advisory_unlock(:k)").bindparams(k=lock_key),
        )
        await holder.close()
