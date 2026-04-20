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


from app.models import (
    CandidateStageProgress,
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
)


async def _make_job_with_stages(db, tenant_id, user_id, stage_names=("Screening", "Interview")):
    org_unit = await create_test_org_unit(db, tenant_id)
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit.id,
        title="Engineer",
        description_raw="R" * 60,
        created_by=user_id,
        status="draft",
    )
    db.add(job)
    await db.flush()
    instance = JobPipelineInstance(tenant_id=tenant_id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()
    stages = []
    for i, name in enumerate(stage_names):
        s = JobPipelineStage(
            tenant_id=tenant_id,
            instance_id=instance.id,
            position=i,
            name=name,
            stage_type="ai_interview",
            duration_minutes=30,
            difficulty="medium",
            signal_filter={},
            pass_criteria={},
            advance_behavior="manual",
        )
        db.add(s)
        stages.append(s)
    await db.flush()
    return job, stages


@pytest.mark.asyncio
async def test_create_assignment_defaults_to_first_stage(db):
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)

    candidate = Candidate(
        tenant_id=tenant.id, name="Alice", email="alice@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    job, stages = await _make_job_with_stages(db, tenant.id, user.id)
    req = AssignmentCreateRequest(job_posting_id=job.id)
    a = await create_assignment(db, candidate.id, req, ctx)

    assert a.candidate_id == candidate.id
    assert a.job_posting_id == job.id
    assert a.current_stage_id == stages[0].id
    assert a.status == "active"


@pytest.mark.asyncio
async def test_create_assignment_writes_initial_progress_row(db):
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)
    candidate = Candidate(
        tenant_id=tenant.id, name="Bob", email="bob@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    job, stages = await _make_job_with_stages(db, tenant.id, user.id)
    a = await create_assignment(
        db, candidate.id, AssignmentCreateRequest(job_posting_id=job.id), ctx
    )

    progress_rows = (await db.execute(
        select(CandidateStageProgress).where(CandidateStageProgress.assignment_id == a.id)
    )).scalars().all()
    assert len(progress_rows) == 1
    assert progress_rows[0].stage_id == a.current_stage_id
    assert progress_rows[0].exited_at is None


@pytest.mark.asyncio
async def test_create_assignment_honors_target_stage(db):
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)
    candidate = Candidate(
        tenant_id=tenant.id, name="Cara", email="cara@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    job, stages = await _make_job_with_stages(db, tenant.id, user.id)
    # pick the 2nd stage
    a = await create_assignment(
        db,
        candidate.id,
        AssignmentCreateRequest(job_posting_id=job.id, target_stage_id=stages[1].id),
        ctx,
    )
    assert a.current_stage_id == stages[1].id


@pytest.mark.asyncio
async def test_create_assignment_stage_not_in_pipeline_raises(db):
    from app.modules.candidates.errors import StageNotInPipelineError
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)
    candidate = Candidate(
        tenant_id=tenant.id, name="D", email="d@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    job, _stages = await _make_job_with_stages(db, tenant.id, user.id)
    # Build a stage in a DIFFERENT job's pipeline
    other_job, other_stages = await _make_job_with_stages(db, tenant.id, user.id)

    with pytest.raises(StageNotInPipelineError):
        await create_assignment(
            db,
            candidate.id,
            AssignmentCreateRequest(job_posting_id=job.id, target_stage_id=other_stages[0].id),
            ctx,
        )


@pytest.mark.asyncio
async def test_create_duplicate_assignment_raises(db):
    from app.modules.candidates.errors import AssignmentAlreadyExistsError
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)
    candidate = Candidate(
        tenant_id=tenant.id, name="Dup", email="dup-assign@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()
    job, _ = await _make_job_with_stages(db, tenant.id, user.id)

    await create_assignment(
        db, candidate.id, AssignmentCreateRequest(job_posting_id=job.id), ctx
    )
    with pytest.raises(AssignmentAlreadyExistsError):
        await create_assignment(
            db, candidate.id, AssignmentCreateRequest(job_posting_id=job.id), ctx
        )


@pytest.mark.asyncio
async def test_create_assignment_raises_when_no_pipeline(db):
    """JD has no pipeline instance at all."""
    from app.modules.candidates.errors import StageNotInPipelineError
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)
    org_unit = await create_test_org_unit(db, tenant.id)
    candidate = Candidate(
        tenant_id=tenant.id, name="NoJob", email="nojob@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=org_unit.id, title="T",
        description_raw="R" * 60, created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    with pytest.raises(StageNotInPipelineError):
        await create_assignment(
            db, candidate.id, AssignmentCreateRequest(job_posting_id=job.id), ctx
        )


@pytest.mark.asyncio
async def test_kanban_board_empty_when_job_has_no_pipeline(db):
    from app.modules.candidates.service import get_kanban_board

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(db, tenant.id)
    job = JobPosting(
        tenant_id=tenant.id, org_unit_id=org_unit.id, title="T",
        description_raw="R" * 60, created_by=user.id, status="draft",
    )
    db.add(job)
    await db.flush()

    board = await get_kanban_board(db, job.id)
    assert board.job_posting_id == job.id
    assert board.stages == []


@pytest.mark.asyncio
async def test_kanban_board_returns_all_stages_even_empty(db):
    from app.modules.candidates.service import get_kanban_board

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    job, stages = await _make_job_with_stages(
        db, tenant.id, user.id, stage_names=("A", "B", "C")
    )

    board = await get_kanban_board(db, job.id)
    assert len(board.stages) == 3
    assert [s.position for s in board.stages] == [0, 1, 2]
    assert all(s.candidates == [] for s in board.stages)


@pytest.mark.asyncio
async def test_kanban_board_places_candidate_in_current_stage(db):
    from app.modules.candidates.service import create_assignment, get_kanban_board
    from app.modules.candidates.schemas import AssignmentCreateRequest

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)

    candidate = Candidate(
        tenant_id=tenant.id, name="Ada", email="ada@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    job, stages = await _make_job_with_stages(db, tenant.id, user.id)
    assignment = await create_assignment(
        db, candidate.id,
        AssignmentCreateRequest(job_posting_id=job.id), ctx,
    )

    board = await get_kanban_board(db, job.id)
    stage_0 = next(s for s in board.stages if s.position == 0)
    assert len(stage_0.candidates) == 1
    card = stage_0.candidates[0]
    assert card.candidate_id == candidate.id
    assert card.assignment_id == assignment.id
    assert card.name == "Ada"
    assert card.email == "ada@example.com"
    assert card.latest_session_state is None  # Phase 3C stub


@pytest.mark.asyncio
async def test_kanban_board_excludes_non_active_assignments(db):
    from app.modules.candidates.service import (
        create_assignment,
        get_kanban_board,
        update_assignment_status,
    )
    from app.modules.candidates.schemas import (
        AssignmentCreateRequest,
        AssignmentStatus,
        AssignmentUpdateRequest,
    )

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user)

    job, _stages = await _make_job_with_stages(db, tenant.id, user.id)

    # Active
    active_cand = Candidate(
        tenant_id=tenant.id, name="Active", email="act@example.com",
        source="manual", created_by=user.id,
    )
    db.add(active_cand)
    await db.flush()
    await create_assignment(
        db, active_cand.id,
        AssignmentCreateRequest(job_posting_id=job.id), ctx,
    )

    # Archived
    archived_cand = Candidate(
        tenant_id=tenant.id, name="Arch", email="arch@example.com",
        source="manual", created_by=user.id,
    )
    db.add(archived_cand)
    await db.flush()
    archived_assign = await create_assignment(
        db, archived_cand.id,
        AssignmentCreateRequest(job_posting_id=job.id), ctx,
    )
    await update_assignment_status(
        db, archived_assign.id,
        AssignmentUpdateRequest(status=AssignmentStatus.ARCHIVED), ctx,
    )

    board = await get_kanban_board(db, job.id)
    all_cards = [c for s in board.stages for c in s.candidates]
    card_candidate_ids = {c.candidate_id for c in all_cards}
    assert active_cand.id in card_candidate_ids
    assert archived_cand.id not in card_candidate_ids


@pytest.mark.asyncio
async def test_redact_pii_nulls_personal_fields_and_stamps_audit(db):
    from app.models import AuditLog
    from app.modules.candidates.service import redact_pii

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Alice Redact",
        email="alice-redact@example.com",
        phone="+1-555-0100",
        location="Berlin",
        current_title="SRE",
        linkedin_url="https://linkedin.com/in/alice",
        notes="sensitive notes",
        source="manual",
        external_id="keep-me",
        source_metadata={"pii": "wipe"},
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    await redact_pii(db, candidate.id, ctx)
    await db.refresh(candidate)

    # Nulled:
    assert candidate.name is None
    assert candidate.email is None
    assert candidate.phone is None
    assert candidate.location is None
    assert candidate.current_title is None
    assert candidate.linkedin_url is None
    assert candidate.notes is None
    assert candidate.source_metadata is None
    assert candidate.resume_s3_key is None
    assert candidate.resume_uploaded_at is None

    # Preserved (audit trail):
    assert candidate.id is not None
    assert candidate.tenant_id == tenant.id
    assert candidate.source == "manual"
    assert candidate.external_id == "keep-me"
    assert candidate.created_by == user.id

    # Stamped:
    assert candidate.pii_redacted_at is not None
    assert candidate.pii_redacted_by == user.id

    # Audit event written:
    audit_rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.action == "candidate.pii_redacted",
            AuditLog.resource_id == candidate.id,
        )
    )).scalars().all()
    assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_redact_pii_succeeds_when_no_sessions_exist(db):
    """Phase 3B has no sessions module — redact always succeeds until 3C adds the guard."""
    from app.modules.candidates.service import redact_pii

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)
    candidate = Candidate(
        tenant_id=tenant.id, name="X", email="x@example.com",
        source="manual", created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    await redact_pii(db, candidate.id, ctx)  # does not raise


@pytest.mark.asyncio
async def test_redact_pii_allows_reusing_email_after_redaction(db):
    """Partial unique index on (tenant, email) WHERE pii_redacted_at IS NULL —
    so after redaction, a new candidate can claim that email."""
    from app.modules.candidates.service import create_candidate, redact_pii
    from app.modules.candidates.schemas import CandidateCreateRequest
    from app.modules.candidates.sources import ManualSource

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    ctx = _make_ctx(user, is_super=True)

    req = CandidateCreateRequest(name="Alice 1", email="reclaim@example.com")
    first = await create_candidate(db, req, ManualSource(), ctx, tenant.id)
    await redact_pii(db, first.id, ctx)

    # Second create with same email should succeed now
    second = await create_candidate(
        db,
        CandidateCreateRequest(name="Alice 2", email="reclaim@example.com"),
        ManualSource(),
        ctx,
        tenant.id,
    )
    assert second.id != first.id
