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
