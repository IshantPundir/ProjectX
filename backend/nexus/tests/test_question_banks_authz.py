"""Authz tests for question_bank.

Verifies require_bank_access / require_bank_access_by_stage /
require_question_access / require_pipeline_access against the org-unit
ancestry permission model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy
from fastapi import HTTPException

from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.question_bank.models import (
    StageQuestion,
    StageQuestionBank,
)
from app.modules.auth.context import RoleAssignment, UserContext
from app.modules.question_bank.authz import (
    require_bank_access,
    require_bank_access_by_stage,
    require_pipeline_access,
    require_question_access,
)
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db, tenant_id) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


def _build_user_context(
    user,
    *,
    is_super_admin: bool = False,
    assignments: list[RoleAssignment] | None = None,
) -> UserContext:
    return UserContext(
        user=user,
        is_super_admin=is_super_admin,
        assignments=assignments or [],
    )


def _assignment(unit, *, permissions: list[str], role_name: str = "Recruiter") -> RoleAssignment:
    return RoleAssignment(
        org_unit_id=unit.id,
        org_unit_name=unit.name,
        role_id=uuid.uuid4(),
        role_name=role_name,
        permissions=permissions,
    )


async def _make_company_only(db):
    """Create a tenant + a non-super-admin user + a company org unit."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


async def _make_full_chain(
    db,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
) -> tuple[
    JobPosting,
    JobPostingSignalSnapshot,
    JobPipelineInstance,
    JobPipelineStage,
    StageQuestionBank,
    StageQuestion,
]:
    """Create job → snapshot → pipeline instance → stage → bank → one question."""
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "Python",
                "type": "competency",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "screen",
                "evaluation_method": "verification",
                "evaluation_hint": None,
                "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant_id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={
            "include_types": ["competency", "experience", "credential", "behavioral"],
        },
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant_id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="reviewing",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    question = StageQuestion(
        tenant_id=tenant_id,
        bank_id=bank.id,
        position=0,
        source="ai_generated",
        text="Tell me about a Python project you've shipped.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric={
            "excellent": "A strong answer names specific tools and shows structure.",
            "meets_bar": "An acceptable answer mentions at least one specific tool.",
            "below_bar": "A weak answer is vague with no tools and no structure.",
        },
        evaluation_hint="Strong answer names tools and structure.",
        edited_by_recruiter=False,
    )
    db.add(question)
    await db.flush()

    return job, snapshot, instance, stage, bank, question


# ---------------------------------------------------------------------------
# 1. Permitted access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_returns_bank_when_permitted(db):
    tenant, user, company = await _make_company_only(db)
    _job, _snap, _inst, _stage, bank, _q = await _make_full_chain(
        db, tenant.id, company.id, user.id,
    )

    ctx = _build_user_context(
        user,
        assignments=[_assignment(company, permissions=["jobs.view"])],
    )
    out_bank, _stage, _job = await require_bank_access(db, bank.id, ctx, "view")
    assert out_bank.id == bank.id


# ---------------------------------------------------------------------------
# 2. Nonexistent bank → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_raises_404_for_nonexistent_bank(db):
    _tenant, user, _company = await _make_company_only(db)
    ctx = _build_user_context(user, is_super_admin=True)

    with pytest.raises(HTTPException) as exc:
        await require_bank_access(db, uuid.uuid4(), ctx, "view")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 3. Missing permission → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_raises_403_when_user_lacks_permission(db):
    tenant, user, company = await _make_company_only(db)
    _job, _snap, _inst, _stage, bank, _q = await _make_full_chain(
        db, tenant.id, company.id, user.id,
    )

    # No assignments + not super admin
    ctx = _build_user_context(user, assignments=[])
    with pytest.raises(HTTPException) as exc:
        await require_bank_access(db, bank.id, ctx, "view")
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 4. Walk ancestry (parent grant → child access)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_walks_ancestry(db):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    division = await create_test_org_unit(
        db, tenant.id, unit_type="division",
        parent_unit_id=company.id, name="Division A",
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team",
        parent_unit_id=division.id, name="Team A1",
    )
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)

    _job, _snap, _inst, _stage, bank, _q = await _make_full_chain(
        db, tenant.id, team.id, user.id,
    )

    # Grant on company (top of ancestry) — should reach the team-level job
    ctx = _build_user_context(
        user,
        assignments=[_assignment(company, permissions=["jobs.view"])],
    )
    out_bank, _stage, _job = await require_bank_access(db, bank.id, ctx, "view")
    assert out_bank.id == bank.id


# ---------------------------------------------------------------------------
# 5. Cross-tenant returns 404 (RLS hides the bank)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_cross_tenant_returns_404_not_403(db):
    """In production, RLS hides another tenant's bank so the SELECT returns
    no row and require_bank_access raises 404 (NOT 403 — the auth check
    never even runs because there's no row to authorize).

    The test database connects as a Postgres role with rolbypassrls=true,
    so we cannot rely on RLS to filter rows. To exercise the same code path,
    we create a bank in tenant A, then call require_bank_access from a user
    in tenant B against an id that has been deleted (simulating the
    "row invisible under RLS" outcome). What matters: the function MUST
    return 404 — not 403 — whenever the SELECT comes back empty.
    """
    # Tenant A: create a bank
    tenant_a, user_a, company_a = await _make_company_only(db)
    _job, _snap, _inst, _stage, bank_a, _q = await _make_full_chain(
        db, tenant_a.id, company_a.id, user_a.id,
    )
    bank_a_id = bank_a.id

    # Tenant B: separate user, no permissions on tenant A's company
    tenant_b = await create_test_client(db)
    user_b = await create_test_user(db, tenant_b.id)
    company_b = await create_test_org_unit(
        db, tenant_b.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    await db.flush()

    # Simulate the "RLS hides this row from tenant B" outcome by removing
    # the bank from the visible result set. We delete the bank row directly
    # — the require_bank_access SELECT will return no row, the same as if
    # RLS had filtered it out in production.
    await db.execute(
        sqlalchemy.text(f"DELETE FROM stage_question_banks WHERE id = '{bank_a_id}'")
    )
    await db.flush()

    # Tenant B context — even granting super_admin + full perms, the
    # missing-row path must produce 404, not a 403 from the auth check.
    await _set_tenant_ctx(db, tenant_b.id)
    ctx_b = _build_user_context(
        user_b,
        is_super_admin=True,
        assignments=[_assignment(company_b, permissions=["jobs.view", "jobs.manage"])],
    )
    with pytest.raises(HTTPException) as exc:
        await require_bank_access(db, bank_a_id, ctx_b, "view")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 6. require_question_access walks question → bank → stage → job → org_unit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_question_access_walks_up_through_bank(db):
    tenant, user, company = await _make_company_only(db)
    _job, _snap, _inst, _stage, bank, question = await _make_full_chain(
        db, tenant.id, company.id, user.id,
    )

    ctx = _build_user_context(
        user,
        assignments=[_assignment(company, permissions=["jobs.manage"])],
    )
    out_q, out_bank, _stage, _job = await require_question_access(
        db, question.id, ctx, "manage",
    )
    assert out_q.id == question.id
    assert out_bank.id == bank.id


# ---------------------------------------------------------------------------
# 7. require_pipeline_access happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_pipeline_access_returns_instance_when_permitted(db):
    tenant, user, company = await _make_company_only(db)
    job, _snap, instance, _stage, _bank, _q = await _make_full_chain(
        db, tenant.id, company.id, user.id,
    )

    ctx = _build_user_context(
        user,
        assignments=[_assignment(company, permissions=["jobs.view"])],
    )
    out_instance, out_job = await require_pipeline_access(db, job.id, ctx, "view")
    assert out_instance.id == instance.id
    assert out_job.id == job.id


# ---------------------------------------------------------------------------
# 8. require_pipeline_access — no instance → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_pipeline_access_raises_404_when_no_instance(db):
    tenant, user, company = await _make_company_only(db)
    # Create a job but no pipeline instance
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    ctx = _build_user_context(user, is_super_admin=True)
    with pytest.raises(HTTPException) as exc:
        await require_pipeline_access(db, job.id, ctx, "view")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 9. View vs manage — view permission cannot manage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_view_vs_manage(db):
    tenant, user, company = await _make_company_only(db)
    _job, _snap, _inst, _stage, bank, _q = await _make_full_chain(
        db, tenant.id, company.id, user.id,
    )

    ctx_view_only = _build_user_context(
        user,
        assignments=[_assignment(company, permissions=["jobs.view"])],
    )
    # view: ok
    out_bank, _stage, _job = await require_bank_access(
        db, bank.id, ctx_view_only, "view",
    )
    assert out_bank.id == bank.id

    # manage: 403
    with pytest.raises(HTTPException) as exc:
        await require_bank_access(db, bank.id, ctx_view_only, "manage")
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 10. require_bank_access_by_stage with no bank yet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_bank_access_by_stage_returns_none_bank_when_not_yet_created(db):
    tenant, user, company = await _make_company_only(db)
    # Build job + pipeline + stage WITHOUT a bank
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={
            "include_types": ["competency", "experience", "credential", "behavioral"],
        },
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()

    ctx = _build_user_context(
        user,
        assignments=[_assignment(company, permissions=["jobs.view"])],
    )
    bank, out_stage, out_job = await require_bank_access_by_stage(
        db, job.id, stage.id, ctx, "view",
    )
    assert bank is None
    assert out_stage.id == stage.id
    assert out_job.id == job.id
