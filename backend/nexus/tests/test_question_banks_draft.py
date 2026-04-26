"""LLM-mediated draft endpoint — stateless preview for adding a new question (Task 16)."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    User,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.modules.question_bank.service import ensure_bank_exists
from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    QuestionRubric,
)
from app.modules.question_bank.service import create_recruiter_question
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

_TEST_BEARER = "test-question-bank-draft-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


def _signal(*, value: str) -> dict:
    return {
        "value": value,
        "type": "competency",
        "priority": "required",
        "weight": 2,
        "knockout": False,
        "stage": "screen",
        "evaluation_method": "verification",
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


async def _setup_tenant_user_unit(db: AsyncSession):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await _set_tenant_ctx(db, tenant.id)
    return tenant, user, company


async def _make_job_with_signals(
    db: AsyncSession,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
    *,
    signals: list[dict],
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Senior Backend Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing purposes.",
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
        signals=signals,
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_and_stage(
    db: AsyncSession,
    *,
    job: JobPosting,
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=job.tenant_id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "experience"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _setup_test_context(
    db: AsyncSession,
    user: User,
    tenant_id: UUID,
) -> tuple[dict, object]:
    """Install fake auth + DB overrides. Returns (headers, restore_fn)."""
    from app.database import get_tenant_db

    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )
    ctx = UserContext(
        user=user,
        is_super_admin=True,
        assignments=[],
    )

    def _fake_verify(token: str):
        if token == _TEST_BEARER:
            return fake_payload
        return None

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def _job_with_generated_bank(db: AsyncSession):
    """Build a tenant + user + confirmed-signals job + pipeline stage + bank
    with one recruiter question in 'reviewing' status.

    Yields (job, stage_id, user, tenant).
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python"), _signal(value="Django")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    rubric = QuestionRubric(
        excellent="A strong answer names specific tools and describes their use in production.",
        meets_bar="An acceptable answer mentions at least one tool and shows basic structure.",
        below_bar="A weak answer is vague with no tools cited and no clear structure.",
    )
    body = CreateQuestionBody(
        text="Tell me about your experience with Python in production systems.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric=rubric,
        evaluation_hint="Strong answer cites specific production systems.",
        position=None,
    )
    await create_recruiter_question(
        db,
        bank=bank,
        body=body,
        user_id=user.id,
        user_email=user.email,
        snapshot=snapshot,
        allowed_types=["competency", "experience"],
    )
    bank.status = "reviewing"
    await db.flush()

    return (job, stage.id, user, tenant)


@pytest.fixture
async def _job_with_pipeline(db: AsyncSession):
    """Build a tenant + user + confirmed-signals job + pipeline stage
    (no bank created yet).

    Yields the job object.
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id,
        signals=[_signal(value="Python")],
    )
    _instance, _stage = await _make_pipeline_and_stage(db, job=job)
    return job, user, tenant


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_returns_proposal_without_persisting(
    db: AsyncSession, _job_with_generated_bank
):
    job, stage_id, user, tenant = _job_with_generated_bank
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Record the current question count
            bank_resp = await ac.get(
                f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions",
                headers=headers,
            )
            assert bank_resp.status_code == 200, bank_resp.text
            initial_count = len(bank_resp.json()["questions"])

            fake = {
                "proposed_text": "New question about handling deadline pressure.",
                "proposed_signal_probed": "behavioral:resilience",
                "proposed_mandatory": False,
                "proposed_position": initial_count,
                "rationale": "Adds behavioral dimension.",
            }
            from app.modules.question_bank.refine import DraftResponse

            async def mock_call(prompt: str) -> DraftResponse:
                return DraftResponse(**fake)

            with patch(
                "app.modules.question_bank.refine._call_llm_draft",
                new=mock_call,
            ):
                r = await ac.post(
                    f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/draft",
                    json={
                        "instruction": "Add a behavioral question about deadline pressure."
                    },
                    headers=headers,
                )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "deadlines" in body["proposed_text"].lower() or "deadline" in body["proposed_text"].lower()
            assert body["proposed_position"] == initial_count

            # Bank unchanged — no new questions persisted
            bank_after = await ac.get(
                f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions",
                headers=headers,
            )
            assert len(bank_after.json()["questions"]) == initial_count
    finally:
        restore()


@pytest.mark.asyncio
async def test_draft_404_on_unknown_stage(db: AsyncSession, _job_with_pipeline):
    job, user, tenant = _job_with_pipeline
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            bogus_stage_id = "00000000-0000-0000-0000-000000000000"
            r = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{bogus_stage_id}/questions/draft",
                json={"instruction": "Add something."},
                headers=headers,
            )
            assert r.status_code == 404, r.text
    finally:
        restore()


@pytest.mark.asyncio
async def test_draft_validates_instruction_length(
    db: AsyncSession, _job_with_generated_bank
):
    job, stage_id, user, tenant = _job_with_generated_bank
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{stage_id}/questions/draft",
                json={"instruction": "ab"},  # < min_length=3
                headers=headers,
            )
            assert r.status_code == 422, r.text
    finally:
        restore()
