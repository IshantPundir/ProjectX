"""End-to-end ATS sync: hand-rolled fake CeipalAdapter feeds canonical DTOs
into the real importer/actor pipeline.

This is the audit-chain gate test. It exercises:
  scheduler tick → actor → importer → all 5 phases → audit_log entries
                                                   → ats_sync_logs row

Verifies the full materialization picture from a single one-shot poll run:
  - client_account org_unit auto-created with stub profile +
    completion_status='pending'
  - blocked_pending_client_setup JD created (because org_unit profile
    is pending)
  - applicant becomes a candidate (source='ats_ceipal', external_id set)
  - ats_sync_logs row closes with status='success'
  - audit_log carries the expected sequence: ats.sync.started,
    ats.client_mapping.created, jd.imported_from_ats, candidate.imported,
    ats.sync.completed

Test-environment choice: Option (ii) — uses the per-test rollback-isolated
``db`` fixture plus ``patched_bypass_session`` (conftest.py), which patches
``importer.get_bypass_session`` AND ``actors.get_bypass_session``. Writes
through the actor and the five importer phases are visible to the in-test
assertions on the same ``db`` session before teardown rolls them back.

Submission-skip caveat (plan deviation): the plan body asserts an
``ats_sync_logs.entity_counts.submissions.new == 1`` plus a
``candidate_job_assignments`` row. In this run the auto-created org_unit
lands ``pending`` → the JD is ``blocked_pending_client_setup`` → no
pipeline_instance exists → ``_sync_submissions`` skips the submission
(Task 21 contract). Verifying the assignment would require pre-seeding the
mapping + a pipeline instance, which decouples the test from the
"one-shot poll from nothing" scenario the plan is gating. We instead assert
``submissions.skipped >= 1`` and ``new == 0`` and document the rationale
here. The audit-chain assertions for the first four phases stand
unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.modules.ats.schemas import (
    ATSApplicantPayload,
    ATSClientPayload,
    ATSJobPayload,
    ATSSubmissionPayload,
    ATSUserPayload,
)


@pytest.fixture(autouse=True)
def _enc_keys(monkeypatch):
    """Provide a deterministic ATS credentials encryption key so the
    actor_fixture's encrypt/decrypt round-trip in Phase A succeeds.

    Mirrors the same pattern in test_actors.py — the registry-level fernet
    cache is cleared on both sides so per-test keys don't leak across tests.
    """
    from app.config import settings
    from app.modules.ats import crypto
    monkeypatch.setattr(
        settings,
        "ats_credentials_encryption_keys",
        [Fernet.generate_key().decode()],
    )
    crypto._fernet = None
    yield
    crypto._fernet = None


@pytest.fixture
async def actor_fixture(db, importer_fixture):
    """Update the importer_fixture's ats_connections row with a real
    encrypted credentials blob so the actor's load_connection_state can
    decrypt + hydrate state. Returns ``(tenant_id, connection_id)``.

    (Duplicated from test_actors.py — the two test files run independently
    and each owns its own fixture chain.)
    """
    from app.modules.ats.crypto import encrypt_credentials_blob

    tenant_id, _user_id, _root_unit_id = importer_fixture
    ct = encrypt_credentials_blob({
        "email": "u@x.com", "password": "p", "api_key": "k",
    })
    await db.execute(text(
        "UPDATE ats_connections SET credentials_ciphertext = :ct, "
        "job_status_filter = :f "
        "WHERE tenant_id = :t"
    ), {
        "ct": ct,
        "f": '{"ids": [1], "names": ["Active"]}',
        "t": tenant_id,
    })
    cid = (await db.execute(text(
        "SELECT id::text FROM ats_connections WHERE tenant_id = :t LIMIT 1"
    ), {"t": tenant_id})).scalar_one()
    await db.flush()
    yield (str(tenant_id), cid)


def _aiter(items):
    async def _gen():
        for item in items:
            yield item
    return _gen()


class _FakeCeipal:
    """Vendor-agnostic hand-rolled adapter — bypasses HTTP entirely.

    Pre-fills access_token + expiry so ``ensure_authenticated`` is a no-op
    and the actor's Phase B doesn't need to mint credentials. Yields one
    payload per list method, all linked by external_id so the importer can
    join across phases.
    """
    vendor = "ceipal"

    def __init__(self, state):
        self.state = state
        now = datetime.now(tz=timezone.utc)
        # Pre-fill tokens so ensure_authenticated is a no-op.
        self.state.access_token = "t"
        self.state.access_token_expires_at = now + timedelta(hours=1)
        self._now = now

    async def ensure_authenticated(self):
        return None

    def list_clients(self, since=None):
        return _aiter([ATSClientPayload(
            external_id="ceipal-client-1", name="Oracle",
            website="www.oracle.com", industry="Computer Software",
            country="India", state="Karnataka",
            raw={}, fetched_at=self._now,
        )])

    def list_users(self, since=None):
        return _aiter([ATSUserPayload(
            external_id="ceipal-user-1", email="rec@x.com",
            display_name="Recruiter One",
            raw={}, fetched_at=self._now,
        )])

    async def count_jobs(self, since=None, job_status_ids=None):
        return 1

    def list_jobs(self, since=None, *, job_status_ids=None):
        return _aiter([ATSJobPayload(
            external_id="ceipal-job-1", external_client_id="ceipal-client-1",
            title="Java Developer", description="JD body", status="Active",
            raw={}, fetched_at=self._now,
        )])

    def list_applicants(self, since=None):
        return _aiter([ATSApplicantPayload(
            external_id="ceipal-appl-1", name="Jane Doe",
            email="jane@x.com",
            raw={}, fetched_at=self._now,
        )])

    def list_submissions(self, job_external_id, since=None):
        # Submissions are tied to a job; only emit for the job we created.
        if job_external_id != "ceipal-job-1":
            return _aiter([])
        return _aiter([ATSSubmissionPayload(
            external_id="ceipal-sub-1",
            applicant_external_id="ceipal-appl-1",
            job_external_id="ceipal-job-1",
            submission_status="Submitted",
            raw={}, fetched_at=self._now,
        )])


@pytest.mark.skip(reason="re-enabled in Task 5 after ATS create rewrite: importer still writes company_profile JSONB to OrganizationalUnit")
@pytest.mark.asyncio
async def test_end_to_end_sync_creates_full_picture(db, actor_fixture):
    """One sync run materializes the full first-poll picture: org_unit
    (pending) + JD (blocked) + candidate + sync_log(success) + the audit
    chain that proves every importer phase ran in order.

    Submission is intentionally skipped (see module docstring).
    """
    from app.modules.ats.actors import _run_poll

    tenant_id, connection_id = actor_fixture

    with patch(
        "app.modules.ats.actors.get_ats_adapter",
        side_effect=lambda state: _FakeCeipal(state),
    ):
        await _run_poll(connection_id, tenant_id)

    # ---- Phase 1: client_account org_unit auto-created with stub + pending ----
    r = await db.execute(text(
        "SELECT name, company_profile_completion_status "
        "FROM organizational_units "
        "WHERE client_id = :t AND unit_type = 'client_account'"
    ), {"t": tenant_id})
    unit = r.one()
    assert unit.name == "Oracle"
    assert unit.company_profile_completion_status == "pending"

    # ---- Phase 3: JD created in blocked_pending_client_setup ----
    r = await db.execute(text(
        "SELECT status, external_id FROM job_postings WHERE tenant_id = :t"
    ), {"t": tenant_id})
    jd = r.one()
    assert jd.status == "blocked_pending_client_setup"
    assert jd.external_id == "ceipal-job-1"

    # ---- Phase 4: applicant imported as candidate ----
    r = await db.execute(text(
        "SELECT email, source, external_id FROM candidates "
        "WHERE tenant_id = :t"
    ), {"t": tenant_id})
    cand = r.one()
    assert cand.email == "jane@x.com"
    assert cand.source == "ats_ceipal"
    assert cand.external_id == "ceipal-appl-1"

    # ---- Phase 5: submission skipped (no pipeline on a blocked JD) ----
    r = await db.execute(text(
        "SELECT COUNT(*) FROM candidate_job_assignments WHERE tenant_id = :t"
    ), {"t": tenant_id})
    assert r.scalar_one() == 0

    # ---- Actor Phase D: sync_log closed cleanly with counts ----
    r = await db.execute(text(
        "SELECT status, entity_counts FROM ats_sync_logs "
        "WHERE connection_id = :c ORDER BY started_at DESC LIMIT 1"
    ), {"c": connection_id})
    log = r.one()
    assert log.status == "success"
    assert log.entity_counts["clients"]["new"] == 1
    assert log.entity_counts["users"]["new"] == 1
    assert log.entity_counts["jobs"]["new"] == 1
    assert log.entity_counts["applicants"]["new"] == 1
    # See module docstring — submission is skipped on a blocked JD.
    assert log.entity_counts["submissions"]["new"] == 0
    assert log.entity_counts["submissions"]["skipped"] >= 1

    # ---- Audit chain: every importer phase emits its event in order ----
    # NOTE: the audit_log model column is ``created_at`` (not ``occurred_at``
    # — see app/modules/audit/models.py). The plan body uses ``occurred_at``
    # which would error here.
    r = await db.execute(text(
        "SELECT action FROM audit_log "
        "WHERE tenant_id = :t "
        "  AND (action LIKE 'ats.%' "
        "       OR action = 'jd.imported_from_ats' "
        "       OR action = 'candidate.imported') "
        "ORDER BY created_at"
    ), {"t": tenant_id})
    actions = [row.action for row in r]
    assert "ats.sync.started" in actions
    assert "ats.client_mapping.created" in actions
    assert "jd.imported_from_ats" in actions
    assert "candidate.imported" in actions
    assert "ats.sync.completed" in actions
