"""import_candidate: upsert by (tenant_id, source, external_id); on
duplicate-email collision with an existing manual candidate, link external_id
+ source_metadata without overwriting editable fields.

Test-environment choice: Option B (per Task 9 decision).
Uses the standard ``db`` fixture from ``tests/conftest.py`` for per-test
connection-level transaction rollback, rather than the plan's
``async_session_factory`` (which would commit rows to the dev DB).
The SUT (``import_candidate``) takes an ``AsyncSession`` — the fixture
binding is purely a test-side choice.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.auth.context import UserContext
from app.modules.candidates.schemas import CandidateCreateRequest
from app.modules.candidates.sources import ManualSource, SourcedCandidate
from tests.conftest import create_test_client, create_test_user


def _make_ctx(user) -> UserContext:
    return UserContext(user=user, is_super_admin=False, assignments=[])


@pytest.mark.asyncio
async def test_import_creates_new_candidate(db):
    from app.modules.candidates.service import import_candidate

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    sourced = SourcedCandidate(
        name="Jane Doe",
        email="new-jane@x.com",
        phone="555-0100",
        location=None,
        current_title=None,
        linkedin_url=None,
        notes=None,
        source="ats_ceipal",
        external_id="ext-1",
        source_metadata={"foo": "bar"},
    )
    cand = await import_candidate(db, sourced, tenant.id, user.id)

    assert cand.name == "Jane Doe"
    assert cand.email == "new-jane@x.com"
    assert cand.source == "ats_ceipal"
    assert cand.external_id == "ext-1"
    assert cand.source_metadata == {"foo": "bar"}
    assert cand.tenant_id == tenant.id
    assert cand.created_by == user.id

    # Audit row written: candidate.imported
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "candidate.imported",
                AuditLog.resource_id == cand.id,
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_import_is_idempotent_on_external_id(db):
    """Re-running import with the same external_id updates, doesn't duplicate."""
    from app.modules.candidates.service import import_candidate

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    def _src(**overrides) -> SourcedCandidate:
        base = dict(
            name="Jane",
            email="dup@x.com",
            phone=None,
            location=None,
            current_title=None,
            linkedin_url=None,
            notes=None,
            source="ats_ceipal",
            external_id="same-id",
            source_metadata=None,
        )
        base.update(overrides)
        return SourcedCandidate(**base)

    c1 = await import_candidate(db, _src(), tenant.id, user.id)
    c2 = await import_candidate(
        db, _src(name="Jane (updated)"), tenant.id, user.id
    )

    assert c1.id == c2.id  # same row
    assert c2.name == "Jane (updated)"


@pytest.mark.asyncio
async def test_email_collision_with_manual_links_external_id(db):
    """A manual candidate already exists with the same email. Import should
    link external_id + source_metadata onto the existing row, NOT overwrite
    editable fields (name, phone) that the recruiter may have edited."""
    from app.modules.candidates.service import create_candidate, import_candidate

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)

    # Manual candidate first (recruiter typed it in)
    manual_req = CandidateCreateRequest(
        name="Manual Name (recruiter-edited)",
        email="collide@x.com",
    )
    manual = await create_candidate(db, manual_req, ManualSource(), ctx, tenant.id)
    assert manual.source == "manual"
    assert manual.external_id is None

    # Now ATS import for same email
    sourced = SourcedCandidate(
        name="ATS Name",
        email="collide@x.com",
        phone="555-9999",
        location=None,
        current_title=None,
        linkedin_url=None,
        notes=None,
        source="ats_ceipal",
        external_id="ats-1",
        source_metadata={"vendor_field": "x"},
    )
    linked = await import_candidate(db, sourced, tenant.id, user.id)

    assert linked.id == manual.id                              # same row, linked
    assert linked.name == "Manual Name (recruiter-edited)"     # NOT overwritten
    assert linked.phone is None                                # NOT overwritten
    assert linked.source == "manual"                           # preserved
    assert linked.external_id == "ats-1"                       # linked
    assert linked.source_metadata == {"vendor_field": "x"}     # linked

    # Audit row written: candidate.linked_to_external (because external_id was None)
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "candidate.linked_to_external",
                AuditLog.resource_id == linked.id,
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
