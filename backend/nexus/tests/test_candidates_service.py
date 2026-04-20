"""Unit tests for candidates service layer — identity CRUD.

Follows the repo convention: inline row construction + _make_ctx helpers.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models import AuditLog, Candidate
from app.modules.auth.context import RoleAssignment, UserContext
from app.modules.candidates import service
from app.modules.candidates.errors import (
    CandidateNotFoundError,
    DuplicateEmailError,
)
from app.modules.candidates.schemas import (
    CandidateCreateRequest,
    CandidateUpdateRequest,
)
from app.modules.candidates.service import CandidateListPage, list_candidates
from app.modules.candidates.sources import ManualSource
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


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


@pytest.mark.asyncio
async def test_list_candidates_returns_tenant_candidates_for_super_admin(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Sample",
        email="sample@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    result = await list_candidates(db, ctx, tenant.id, filters={})

    assert isinstance(result, CandidateListPage)
    assert any(c.id == candidate.id for c in result.items)
    assert result.total >= 1


@pytest.mark.asyncio
async def test_list_candidates_search_by_name_matches_ilike(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)

    req = CandidateCreateRequest(name="Zaphod Beeblebrox", email="zaphod@example.com")
    await service.create_candidate(db, req, ManualSource(), ctx, tenant.id)

    # Also create a non-matching row to prove the filter isolates.
    await service.create_candidate(
        db,
        CandidateCreateRequest(name="Arthur Dent", email="arthur@example.com"),
        ManualSource(),
        ctx,
        tenant.id,
    )

    result = await list_candidates(db, ctx, tenant.id, filters={"q": "zaphod"})
    assert result.total == 1
    assert result.items[0].name == "Zaphod Beeblebrox"


@pytest.mark.asyncio
async def test_list_candidates_excludes_redacted(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)

    live = Candidate(
        tenant_id=tenant.id, name="Alive", email="alive@example.com",
        source="manual", created_by=user.id,
    )
    from datetime import datetime, UTC
    redacted = Candidate(
        tenant_id=tenant.id, name="Redacted", email="redacted@example.com",
        source="manual", created_by=user.id,
        pii_redacted_at=datetime.now(UTC), pii_redacted_by=user.id,
    )
    db.add_all([live, redacted])
    await db.flush()

    result = await list_candidates(db, ctx, tenant.id, filters={})
    ids = {c.id for c in result.items}
    assert live.id in ids
    assert redacted.id not in ids


@pytest.mark.asyncio
async def test_list_candidates_non_super_without_permission_sees_nothing(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)  # empty assignments → no candidates.view

    candidate = Candidate(
        tenant_id=tenant.id, name="X", email="x@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    result = await list_candidates(db, ctx, tenant.id, filters={})
    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_candidates_non_super_with_permission_sees_all_mvp(db):
    """MVP: if user has candidates.view anywhere, they see all tenant candidates.
    Ancestry-scoped filtering is deferred — this test pins current behavior."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    ctx = _make_ctx(
        user,
        assignments=[
            RoleAssignment(
                org_unit_id=unit.id,
                org_unit_name=unit.name,
                role_id=uuid.uuid4(),
                role_name="Recruiter",
                permissions=["candidates.view"],
            )
        ],
    )

    candidate = Candidate(
        tenant_id=tenant.id, name="Y", email="y@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    result = await list_candidates(db, ctx, tenant.id, filters={})
    assert result.total == 1
    assert result.items[0].id == candidate.id


@pytest.mark.asyncio
async def test_list_candidates_pagination(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)

    for i in range(5):
        c = Candidate(
            tenant_id=tenant.id,
            name=f"Candidate {i}",
            email=f"c{i}@example.com",
            source="manual",
            created_by=user.id,
        )
        db.add(c)
    await db.flush()

    page1 = await list_candidates(db, ctx, tenant.id, filters={}, offset=0, limit=2)
    page2 = await list_candidates(db, ctx, tenant.id, filters={}, offset=2, limit=2)
    assert page1.total == 5
    assert len(page1.items) == 2
    assert len(page2.items) == 2
    # Disjoint pages
    assert {c.id for c in page1.items}.isdisjoint({c.id for c in page2.items})
