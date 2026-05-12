"""Phase 4 (applicants) + Phase 5 (submissions) of ATSImporter.

Phase 4 reuses ``candidates.service.import_candidate`` — same idempotent
upsert + manual-collision-link semantics — so the importer is thin glue.

Phase 5 is the join between applicants (Phase 4) and jobs (Phase 3). It
upserts ``candidate_job_assignments`` keyed by
``(tenant_id, source, external_id)`` (matches migration 0031's partial
unique index ``candidate_job_assignments_external_idx``).

Test-environment choice: Option (ii) — the ``importer_fixture`` (in
``conftest.py``) monkeypatches ``get_bypass_session`` so the importer's
internally-opened session is the rollback-isolated ``db`` fixture. All
seed and verify queries go through the test ``db``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.schemas import ATSApplicantPayload, ATSSubmissionPayload


def _async_iter(items):
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


def _ceipal_adapter(tenant_id, *, applicants=None, submissions=None):
    state = ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), vendor="ceipal",
        credentials={},
    )
    adapter = AsyncMock()
    adapter.state = state
    adapter.vendor = "ceipal"
    if applicants is not None:
        adapter.list_applicants = lambda since=None: _async_iter(applicants)
    if submissions is not None:
        # The importer calls ``adapter.list_submissions(job_external_id=..., since=...)``
        # — accept both kwargs and return the provided iterable regardless of job.
        adapter.list_submissions = lambda job_external_id, since=None: _async_iter(
            submissions
        )
    return adapter


@pytest.mark.asyncio
async def test_sync_applicants_imports_via_import_candidate(db, jobs_fixture):
    """A fresh applicant payload becomes a new candidates row tagged
    ``source='ats_ceipal'`` and ``external_id='aid-1'``.
    """
    from app.modules.ats.importer import ATSImporter

    tenant_id, *_ = jobs_fixture
    payload = ATSApplicantPayload(
        external_id="aid-1", name="Jane Doe", email="jane@x.com",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _ceipal_adapter(tenant_id, applicants=[payload])

    importer = ATSImporter()
    result = await importer._run_phase("applicants", importer._sync_applicants, adapter)
    assert result.new >= 1

    # Verify the candidate row landed via the test session — the
    # patched bypass session aliases commit -> flush, so writes are visible.
    row = await db.execute(text(
        "SELECT source, external_id, email FROM candidates WHERE tenant_id = :t"
    ), {"t": tenant_id})
    r = row.one()
    assert r.source == "ats_ceipal"
    assert r.external_id == "aid-1"
    assert r.email == "jane@x.com"


@pytest.mark.asyncio
async def test_sync_submissions_creates_assignment_linking_candidate_to_job(
    db, jobs_fixture,
):
    """A submission for a known (applicant, job) pair produces a
    ``candidate_job_assignments`` row tagged with the vendor source and
    submission external_id, with ``source_metadata`` carrying the full
    submission payload."""
    from app.modules.ats.importer import ATSImporter

    tenant_id, user_id, _root_unit_id, _pending_unit, complete_unit = jobs_fixture

    # Seed an ATS-origin job under the complete client_account org_unit.
    job_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO job_postings (id, tenant_id, org_unit_id, title, "
        "description_raw, status, source, external_id, created_by) "
        "VALUES (:j, :t, :o, 'Java Engineer', 'JD body', 'draft', "
        "'ats_ceipal', 'jid-1', :u)"
    ), {"j": job_id, "t": tenant_id, "o": complete_unit, "u": user_id})

    # Seed a pipeline instance + first stage so the importer has somewhere
    # to land the assignment's current_stage_id (NOT NULL on the column).
    instance_id = uuid.uuid4()
    stage_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO job_pipeline_instances (id, tenant_id, job_posting_id) "
        "VALUES (:i, :t, :j)"
    ), {"i": instance_id, "t": tenant_id, "j": job_id})
    await db.execute(text(
        "INSERT INTO job_pipeline_stages (id, tenant_id, instance_id, "
        "position, name, stage_type, duration_minutes, difficulty, "
        "signal_filter, pass_criteria, advance_behavior) "
        "VALUES (:s, :t, :i, 0, 'Phone Screen', 'phone_screen', 30, "
        "'medium', '{}', '{}', 'manual')"
    ), {"s": stage_id, "t": tenant_id, "i": instance_id})

    # Seed an ATS-origin candidate that the submission will resolve to.
    candidate_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO candidates (id, tenant_id, name, email, source, "
        "external_id, created_by) "
        "VALUES (:c, :t, 'Jane Doe', 'jane@x.com', 'ats_ceipal', "
        "'aid-1', :u)"
    ), {"c": candidate_id, "t": tenant_id, "u": user_id})
    await db.flush()

    submission = ATSSubmissionPayload(
        external_id="sid-1",
        applicant_external_id="aid-1",
        job_external_id="jid-1",
        submission_status="Submitted",
        pipeline_status="Submitted to Client",
        source="LinkedIn",
        submitted_on=datetime(2026, 5, 1, tzinfo=timezone.utc),
        raw={"key": "val"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    adapter = _ceipal_adapter(tenant_id, submissions=[submission])

    importer = ATSImporter()
    result = await importer._run_phase(
        "submissions", importer._sync_submissions, adapter,
    )
    assert result.new == 1
    assert result.skipped == 0

    # Verify the candidate_job_assignments row.
    row = await db.execute(text(
        "SELECT candidate_id::text AS candidate_id, "
        "job_posting_id::text AS job_posting_id, "
        "source, external_id, source_metadata "
        "FROM candidate_job_assignments "
        "WHERE tenant_id = :t AND source = 'ats_ceipal'"
    ), {"t": tenant_id})
    r = row.one()
    assert r.candidate_id == str(candidate_id)
    assert r.job_posting_id == str(job_id)
    assert r.source == "ats_ceipal"
    assert r.external_id == "sid-1"
    assert r.source_metadata["submission_status"] == "Submitted"
    assert r.source_metadata["pipeline_status"] == "Submitted to Client"
    assert r.source_metadata["source"] == "LinkedIn"
    assert r.source_metadata["submitted_on"].startswith("2026-05-01")
    assert r.source_metadata["raw"] == {"key": "val"}
