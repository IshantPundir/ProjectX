"""Unit tests for candidates service layer — identity CRUD.

Follows the repo convention: inline row construction + _make_ctx helpers.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models import AuditLog, Candidate
from app.modules.auth.context import UserContext
from app.modules.candidates import service
from app.modules.candidates.errors import (
    CandidateNotFoundError,
    DuplicateEmailError,
)
from app.modules.candidates.schemas import (
    CandidateCreateRequest,
    CandidateUpdateRequest,
)
from app.modules.candidates.sources import ManualSource
from tests.conftest import create_test_client, create_test_user


def _make_ctx(user, assignments=None, is_super=False):
    return UserContext(
        user=user, is_super_admin=is_super, assignments=assignments or []
    )


@pytest.mark.asyncio
async def test_create_candidate_persists_row_and_logs_audit(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)

    req = CandidateCreateRequest(name="Alice", email="alice@example.com")
    created = await service.create_candidate(db, req, ManualSource(), ctx, tenant.id)

    assert created.name == "Alice"
    assert created.email == "alice@example.com"
    assert created.source == "manual"
    assert created.created_by == user.id
    assert created.tenant_id == tenant.id

    # Persistence check
    loaded = (
        await db.execute(select(Candidate).where(Candidate.id == created.id))
    ).scalar_one()
    assert loaded.id == created.id

    # Audit log row written
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "candidate.created",
                AuditLog.resource_id == created.id,
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].payload == {"source": "manual", "has_resume": False}


@pytest.mark.asyncio
async def test_create_candidate_rejects_duplicate_email(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)
    req = CandidateCreateRequest(name="Alice", email="dup@example.com")
    await service.create_candidate(db, req, ManualSource(), ctx, tenant.id)

    with pytest.raises(DuplicateEmailError) as exc:
        await service.create_candidate(db, req, ManualSource(), ctx, tenant.id)
    assert exc.value.email == "dup@example.com"


@pytest.mark.asyncio
async def test_get_candidate_returns_row(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Bob",
        email="bob@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    loaded = await service.get_candidate(db, candidate.id)
    assert loaded.id == candidate.id


@pytest.mark.asyncio
async def test_get_candidate_missing_raises(db):
    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(db, uuid.uuid4())


@pytest.mark.asyncio
async def test_update_candidate_patches_fields(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Alice",
        email="alice@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    req = CandidateUpdateRequest(name="Alice Updated", phone="+15551234567")
    updated = await service.update_candidate(db, candidate.id, req, _make_ctx(user))

    assert updated.name == "Alice Updated"
    assert updated.phone == "+15551234567"
    assert updated.email == "alice@example.com"


@pytest.mark.asyncio
async def test_update_candidate_stringifies_linkedin_url(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Alice",
        email="alice@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    req = CandidateUpdateRequest(linkedin_url="https://linkedin.com/in/alice")
    updated = await service.update_candidate(db, candidate.id, req, _make_ctx(user))
    assert isinstance(updated.linkedin_url, str)
    assert updated.linkedin_url.startswith("https://linkedin.com/in/alice")
