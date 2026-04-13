"""HTTP/router tests for the question_bank module.

Tests exercise the FastAPI app via AsyncClient + ASGI transport. Auth is
faked the same way the JD router tests do it:

  1. Patch ``app.middleware.auth.verify_access_token`` to accept a sentinel
     bearer and return a TokenPayload.
  2. Override ``get_current_user_roles`` to return a synthesized UserContext.
  3. Override ``get_tenant_db`` to yield the test session (so the rows the
     test set up are visible to the request).

Dramatiq ``.send`` calls are stubbed out via ``monkeypatch.setattr`` so no
broker is needed.

Covers: GET banks overview, GET bank detail, generate (single/all),
regenerate, create question, validation error paths, patch (with
edited_by_recruiter flag), delete, reorder, confirm (success + failure
modes).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestion,
    StageQuestionBank,
    User,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.modules.question_bank.service import (
    create_recruiter_question,
    ensure_bank_exists,
)
from app.modules.question_bank.schemas import (
    CreateQuestionBody,
    QuestionRubric,
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

_TEST_BEARER = "test-question-bank-router-token"


# ---------------------------------------------------------------------------
# Helpers — copied from test_question_banks_service.py (self-contained)
# ---------------------------------------------------------------------------


async def _set_tenant_ctx(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
    )


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


def _signal(
    *,
    value: str,
    signal_type: str = "competency",
    priority: str = "required",
    weight: int = 2,
    knockout: bool = False,
    stage: str = "screen",
) -> dict:
    return {
        "value": value,
        "type": signal_type,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage,
        "evaluation_method": "verification",
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


async def _make_job_with_signals(
    db: AsyncSession,
    tenant_id: UUID,
    org_unit_id: UUID,
    user_id: UUID,
    *,
    signals: list[dict],
    version: int = 1,
    confirm: bool = True,
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        version=version,
        signals=signals,
        seniority_level="senior",
        role_summary="A senior backend engineer.",
        prompt_version="v1",
        confirmed_by=user_id if confirm else None,
        confirmed_at=datetime.now(UTC) if confirm else None,
    )
    db.add(snapshot)
    await db.flush()
    return job, snapshot


async def _make_pipeline_and_stage(
    db: AsyncSession,
    *,
    job: JobPosting,
    stage_type: str = "phone_screen",
    duration_minutes: int = 30,
    signal_filter: dict | None = None,
    pass_criteria: dict | None = None,
    advance_behavior: str = "auto_advance",
    difficulty: str = "medium",
    name: str = "Phone Screen",
    position: int = 0,
    instance: JobPipelineInstance | None = None,
) -> tuple[JobPipelineInstance, JobPipelineStage]:
    if instance is None:
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
        position=position,
        name=name,
        stage_type=stage_type,
        duration_minutes=duration_minutes,
        difficulty=difficulty,
        signal_filter=signal_filter
        or {"include_types": ["competency", "experience", "credential", "behavioral"]},
        pass_criteria=pass_criteria or {"type": "all_knockouts_pass"},
        advance_behavior=advance_behavior,
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _valid_rubric() -> QuestionRubric:
    return QuestionRubric(
        excellent="A strong answer names specific tools and describes hypothesis-verify flow.",
        meets_bar="An acceptable answer mentions at least one tool and shows structure.",
        below_bar="A weak answer is vague with no tools and no structure.",
    )


def _valid_rubric_dict() -> dict:
    return _valid_rubric().model_dump()


def _make_create_body(
    *,
    text: str = "What is your favorite production debugging tool?",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
    position: int | None = None,
) -> CreateQuestionBody:
    return CreateQuestionBody(
        text=text,
        signal_values=signal_values or ["Python"],
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric=_valid_rubric(),
        evaluation_hint="Strong answer names tools, describes structured approach.",
        position=position,
    )


async def _add_recruiter_question(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    snapshot: JobPostingSignalSnapshot,
    user_id: UUID,
    text: str = "Recruiter question: tell me about it.",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
    position: int | None = None,
    allowed_types: list[str] | None = None,
) -> StageQuestion:
    body = _make_create_body(
        text=text,
        signal_values=signal_values,
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        position=position,
    )
    return await create_recruiter_question(
        db,
        bank=bank,
        body=body,
        user_id=user_id,
        user_email="r@test.com",
        snapshot=snapshot,
        allowed_types=allowed_types
        or ["competency", "experience", "credential", "behavioral"],
    )


# ---------------------------------------------------------------------------
# Auth + DB override plumbing — same shape as test_jd_router._setup_test_context
# ---------------------------------------------------------------------------


def _setup_test_context(
    db: AsyncSession,
    user: User,
    tenant_id: UUID,
    is_super_admin: bool = True,
):
    """Install fake auth + DB overrides for one HTTP request.

    Returns (headers, restore_fn).
    """
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
        is_super_admin=is_super_admin,
        workspace_mode="enterprise",
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


def _stub_actor_sends(monkeypatch) -> None:
    """Stub every question_bank actor .send so requests don't try to enqueue."""
    monkeypatch.setattr(
        "app.modules.question_bank.actors.generate_question_bank_stage.send",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.modules.question_bank.actors.generate_question_bank_pipeline.send",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.modules.question_bank.actors.regenerate_question.send",
        lambda *a, **k: None,
    )


# ---------------------------------------------------------------------------
# Composite fixture that builds tenant + user + job + pipeline + stage + bank
# ---------------------------------------------------------------------------


async def _build_full_setup(
    db: AsyncSession,
    *,
    knockout: bool = False,
    duration_minutes: int = 30,
    bank_status: str = "draft",
    add_questions: bool = False,
    question_minutes: float = 5.0,
    question_signal_values: list[str] | None = None,
    question_is_mandatory: bool = False,
    question_count: int = 1,
):
    """Build a tenant + super-admin user + job + pipeline + stage + bank.

    Returns a dict with all the pieces. By default produces a draft empty bank.
    """
    tenant, user, unit = await _setup_tenant_user_unit(db)
    signals = [_signal(value="Python", knockout=knockout)]
    if knockout:
        signals.append(_signal(value="Apigee", knockout=True))
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=signals,
    )
    instance, stage = await _make_pipeline_and_stage(
        db, job=job, duration_minutes=duration_minutes,
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = bank_status
    await db.flush()

    questions: list[StageQuestion] = []
    if add_questions:
        for i in range(question_count):
            q = await _add_recruiter_question(
                db,
                bank=bank,
                snapshot=snapshot,
                user_id=user.id,
                text=f"Recruiter question number {i} please answer.",
                signal_values=question_signal_values,
                estimated_minutes=question_minutes,
                is_mandatory=question_is_mandatory,
            )
            questions.append(q)
        # creating recruiter questions auto-reverts to "reviewing"
        bank.status = bank_status
        await db.flush()

    return {
        "tenant": tenant,
        "user": user,
        "unit": unit,
        "job": job,
        "snapshot": snapshot,
        "instance": instance,
        "stage": stage,
        "bank": bank,
        "questions": questions,
    }


# ---------------------------------------------------------------------------
# 1. GET banks overview — 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_banks_overview_returns_200_with_banks(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db)
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{setup['job'].id}/pipeline/questions",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "banks" in data
    assert len(data["banks"]) == 1
    assert data["banks"][0]["stage_id"] == str(setup["stage"].id)


# ---------------------------------------------------------------------------
# 2. GET bank detail — 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bank_detail_returns_200_with_questions(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(
        db, add_questions=True, question_count=2,
    )
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["stage_id"] == str(setup["stage"].id)
    assert len(data["questions"]) == 2
    assert data["question_count"] == 2


# ---------------------------------------------------------------------------
# 3. GET on nonexistent job — 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bank_nonexistent_job_returns_404(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    tenant, user, _unit = await _setup_tenant_user_unit(db)
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/jobs/{uuid.uuid4()}/pipeline/questions",
                headers=headers,
            )
    finally:
        restore()
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 4. POST generate stage — 202 + status='generating'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_generate_stage_returns_202_and_sets_generating(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db)
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions/generate",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "generating"
    assert body["bank_id"] == str(setup["bank"].id)

    # Re-load to confirm the bank row was updated
    await db.refresh(setup["bank"])
    assert setup["bank"].status == "generating"


# ---------------------------------------------------------------------------
# 5. POST generate stage when already generating — 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_generate_stage_when_already_generating_returns_409(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, bank_status="generating")
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions/generate",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# 6. POST generate-all — 202
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_generate_all_returns_202(db: AsyncSession, monkeypatch):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db)
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/questions/generate-all",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "generating"
    assert body["bank_id"] is None


# ---------------------------------------------------------------------------
# 7. POST generate-all when any bank is generating — 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_generate_all_when_any_generating_returns_409(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, bank_status="generating")
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/questions/generate-all",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# 8. POST regenerate question — 202
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_regenerate_question_returns_202(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, add_questions=True)
    question = setup["questions"][0]
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}"
                f"/questions/{question.id}/regenerate",
                json={"replace_signal_values": None},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "generating"


# ---------------------------------------------------------------------------
# 9. POST create question — 201 with source='recruiter'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_create_question_returns_201_with_recruiter_source(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db)
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)

    payload = {
        "text": "How do you debug a noisy production incident?",
        "signal_values": ["Python"],
        "estimated_minutes": 5.0,
        "is_mandatory": False,
        "follow_ups": [],
        "positive_evidence": [],
        "red_flags": [],
        "rubric": _valid_rubric_dict(),
        "evaluation_hint": "Strong answer names tools, describes structure.",
    }

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "recruiter"
    assert body["text"] == payload["text"]


# ---------------------------------------------------------------------------
# 10. POST create question with invalid signal_value — 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_create_question_with_invalid_signal_value_returns_400(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db)
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)

    payload = {
        "text": "Tell me about a hallucinated signal value.",
        "signal_values": ["NotInSnapshot"],
        "estimated_minutes": 5.0,
        "is_mandatory": False,
        "follow_ups": [],
        "positive_evidence": [],
        "red_flags": [],
        "rubric": _valid_rubric_dict(),
        "evaluation_hint": "Strong answer names tools, describes structure.",
    }

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 11. POST create question with type outside include_types — 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_create_question_with_type_outside_include_types_returns_400(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, _snapshot = await _make_job_with_signals(
        db,
        tenant.id,
        unit.id,
        user.id,
        signals=[_signal(value="Python", signal_type="behavioral")],
    )
    # Stage only allows competency + experience — behavioral is filtered out.
    _instance, stage = await _make_pipeline_and_stage(
        db,
        job=job,
        signal_filter={"include_types": ["competency", "experience"]},
    )
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "draft"
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id)
    payload = {
        "text": "Tell me about a behavioral situation you faced.",
        "signal_values": ["Python"],
        "estimated_minutes": 5.0,
        "is_mandatory": False,
        "follow_ups": [],
        "positive_evidence": [],
        "red_flags": [],
        "rubric": _valid_rubric_dict(),
        "evaluation_hint": "Strong answer names tools, describes structure.",
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 12. PATCH question — sets edited_by_recruiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_question_sets_edited_by_recruiter(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, add_questions=True)
    question = setup["questions"][0]
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)

    payload = {
        "text": "An updated question text from the recruiter side.",
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.patch(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}"
                f"/questions/{question.id}",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["edited_by_recruiter"] is True
    assert data["text"] == payload["text"]


# ---------------------------------------------------------------------------
# 13. PATCH question with extra fields — 422 (Pydantic extra='forbid')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_question_with_extra_fields_returns_422(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, add_questions=True)
    question = setup["questions"][0]
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)

    payload = {
        "text": "An updated question text from the recruiter side.",
        "totally_unknown_field": "boom",
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.patch(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}"
                f"/questions/{question.id}",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 14. DELETE question — 204 + repacks positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_question_returns_204_and_repacks_positions(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, add_questions=True, question_count=3)
    questions = setup["questions"]
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)

    # Delete the middle question
    target = questions[1]
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.delete(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}"
                f"/questions/{target.id}",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 204, resp.text

    # Re-pack: remaining 2 questions should have positions [0, 1]
    rows = (
        await db.execute(
            select(StageQuestion)
            .where(StageQuestion.bank_id == setup["bank"].id)
            .order_by(StageQuestion.position)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert [q.position for q in rows] == [0, 1]


# ---------------------------------------------------------------------------
# 15. PATCH reorder — 200 with new positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_reorder_returns_200_with_new_positions(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    setup = await _build_full_setup(db, add_questions=True, question_count=3)
    questions = setup["questions"]
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)

    # Reverse the order
    reversed_ids = [str(q.id) for q in reversed(questions)]
    payload = {"question_ids": reversed_ids}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.patch(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}"
                f"/questions/reorder",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    returned_ids = [q["id"] for q in data["questions"]]
    assert returned_ids == reversed_ids


# ---------------------------------------------------------------------------
# 16. POST confirm — reviewing → confirmed → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_confirm_reviewing_bank_returns_200(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    # Need a bank in reviewing with valid duration budget (15-45min for 30min stage).
    # 4 * 5min questions = 20min — within range.
    setup = await _build_full_setup(
        db,
        bank_status="reviewing",
        add_questions=True,
        question_count=4,
        question_minutes=5.0,
    )
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "confirmed"
    assert data["confirmed_at"] is not None


# ---------------------------------------------------------------------------
# 17. POST confirm draft bank — 409 (not in reviewing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_confirm_draft_bank_returns_409_not_in_reviewing(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    # Add questions to satisfy budget so we hit the state-machine check first.
    setup = await _build_full_setup(
        db,
        bank_status="draft",
        add_questions=False,
    )
    # We need an empty draft bank for this test (default state).
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{setup['stage'].id}/questions/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# 18. POST confirm with uncovered knockout — 409 (detail mentions 'knockout')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_confirm_uncovered_knockout_returns_409(
    db: AsyncSession, monkeypatch
):
    _stub_actor_sends(monkeypatch)
    tenant, user, unit = await _setup_tenant_user_unit(db)
    # Knockout signal "Apigee" — must be covered by a mandatory question
    # before confirm is allowed.
    job, snapshot = await _make_job_with_signals(
        db,
        tenant.id,
        unit.id,
        user.id,
        signals=[
            _signal(value="Python"),
            _signal(value="Apigee", knockout=True),
        ],
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job, duration_minutes=30)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # Add non-mandatory questions that do NOT cover the knockout — bank
    # ends up in 'reviewing' (auto-revert) with the budget filled.
    # Two 10-minute questions = 20min total (within 15-45 range).
    await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        signal_values=["Python"],
        estimated_minutes=10.0,
        is_mandatory=False,
        text="First non-mandatory python question for this stage.",
    )
    await _add_recruiter_question(
        db,
        bank=bank,
        snapshot=snapshot,
        user_id=user.id,
        signal_values=["Python"],
        estimated_minutes=10.0,
        is_mandatory=False,
        text="Second non-mandatory python question for this stage.",
    )
    bank.status = "reviewing"
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "knockout" in detail.lower()
