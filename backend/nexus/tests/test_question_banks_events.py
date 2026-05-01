"""Integration tests for pub/sub event emission from question-bank mutations.

Each test hits the real handler path but replaces pubsub.publish with
the `capture_publishes` fixture stub, asserting on the captured envelope.

URL patterns (actual, from router.py):
  POST   /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions          → create
  PATCH  /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{q_id}  → update
  DELETE /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{q_id}  → delete
  PATCH  /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/reorder → reorder
  POST   /api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/confirm → confirm
"""
from __future__ import annotations

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient

from app import pubsub
from app.main import app
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.database import get_tenant_db
from app.modules.question_bank.service import (
    create_recruiter_question,
    ensure_bank_exists,
)
from app.modules.question_bank.schemas import CreateQuestionBody, QuestionRubric
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)
from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.question_bank.models import StageQuestionBank
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch

pytestmark = pytest.mark.asyncio

_TEST_BEARER = "test-events-token"

_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


# ---------------------------------------------------------------------------
# Shared helpers (mirrored from test_question_banks_router.py)
# ---------------------------------------------------------------------------

async def _set_tenant_ctx(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))


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


def _signal(*, value: str, knockout: bool = False) -> dict:
    return {
        "value": value,
        "type": "competency",
        "priority": "required",
        "weight": 2,
        "knockout": knockout,
        "stage": "screen",
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
) -> tuple[JobPosting, JobPostingSignalSnapshot]:
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Events Test Job",
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
    stage_type: str = "phone_screen",
    duration_minutes: int = 30,
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
        stage_type=stage_type,
        duration_minutes=duration_minutes,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "experience", "credential", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()
    return instance, stage


def _valid_rubric_dict() -> dict:
    return QuestionRubric(
        excellent="A strong answer names specific tools.",
        meets_bar="An acceptable answer mentions at least one tool.",
        below_bar="A weak answer is vague with no tools.",
    ).model_dump()


async def _make_recruiter_question(
    db: AsyncSession,
    *,
    bank: StageQuestionBank,
    snapshot: JobPostingSignalSnapshot,
    user_id: UUID,
    text: str = "Test question text.",
    signal_values: list[str] | None = None,
    estimated_minutes: float = 5.0,
    is_mandatory: bool = False,
):
    body = CreateQuestionBody(
        text=text,
        signal_values=signal_values or ["Python"],
        estimated_minutes=estimated_minutes,
        is_mandatory=is_mandatory,
        follow_ups=[],
        positive_evidence=[],
        red_flags=[],
        rubric=QuestionRubric(
            excellent="A strong answer names specific tools.",
            meets_bar="An acceptable answer mentions at least one tool.",
            below_bar="A weak answer is vague with no tools.",
        ),
        evaluation_hint="Strong answer names tools, describes structured approach.",
        position=None,
    )
    return await create_recruiter_question(
        db,
        bank=bank,
        body=body,
        user_id=user_id,
        user_email="r@test.com",
        snapshot=snapshot,
        allowed_types=["competency", "experience", "credential", "behavioral"],
    )


def _setup_test_context(db: AsyncSession, user, tenant_id: UUID):
    """Install fake auth + DB overrides. Returns (headers, restore_fn)."""
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
        await db.execute(sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        yield db

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override

    verify_patch = patch("app.middleware.auth.verify_access_token", side_effect=_fake_verify)
    verify_patch.start()

    headers = {"Authorization": f"Bearer {_TEST_BEARER}"}

    def restore():
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    return headers, restore


# ---------------------------------------------------------------------------
# T11: create_question publishes bank.question_updated with mutation="create"
# ---------------------------------------------------------------------------

async def test_create_question_publishes_event(db: AsyncSession, monkeypatch, capture_publishes):
    """POST create question emits bank.question_updated with mutation='create'."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    await db.flush()

    payload = {
        "text": "Tell me about a challenging project.",
        "signal_values": ["Python"],
        "estimated_minutes": 5.0,
        "is_mandatory": False,
        "follow_ups": [],
        "positive_evidence": [],
        "red_flags": [],
        "rubric": _valid_rubric_dict(),
        "evaluation_hint": "Strong answer names tools.",
    }

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions",
                json=payload,
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 201, resp.text

    assert len(capture_publishes) == 1, f"expected 1 publish, got {len(capture_publishes)}"
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
    assert pub.payload["bank_id"] == str(bank.id)
    assert pub.payload["job_id"] == str(job.id)
    assert pub.payload["stage_id"] == str(stage.id)
    assert pub.payload["mutation"] == "create"
    assert pub.correlation_id


# ---------------------------------------------------------------------------
# T12: update_question publishes bank.question_updated with mutation="update"
# ---------------------------------------------------------------------------

async def test_update_question_publishes_event(db: AsyncSession, monkeypatch, capture_publishes):
    """PATCH question emits bank.question_updated with mutation='update'."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    question = await _make_recruiter_question(db, bank=bank, snapshot=snapshot, user_id=user.id)
    # create_recruiter_question auto-reverts status to reviewing; reset to reviewing for test
    bank.status = "reviewing"
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions/{question.id}",
                json={"text": "Revised question text."},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text

    assert len(capture_publishes) == 1, f"expected 1 publish, got {len(capture_publishes)}"
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
    assert pub.payload["question_id"] == str(question.id)
    assert pub.payload["bank_id"] == str(bank.id)
    assert pub.payload["mutation"] == "update"
    assert pub.correlation_id


# ---------------------------------------------------------------------------
# T13: delete_question publishes bank.question_updated with mutation="delete"
# ---------------------------------------------------------------------------

async def test_delete_question_publishes_event(db: AsyncSession, monkeypatch, capture_publishes):
    """DELETE question emits bank.question_updated with mutation='delete'."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    question = await _make_recruiter_question(db, bank=bank, snapshot=snapshot, user_id=user.id)
    bank.status = "reviewing"
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions/{question.id}",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 204, resp.text

    assert len(capture_publishes) == 1, f"expected 1 publish, got {len(capture_publishes)}"
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
    assert pub.payload["question_id"] == str(question.id)
    assert pub.payload["bank_id"] == str(bank.id)
    assert pub.payload["mutation"] == "delete"
    assert pub.correlation_id


# ---------------------------------------------------------------------------
# T14: reorder_questions publishes bank.question_updated with mutation="reorder"
# ---------------------------------------------------------------------------

async def test_reorder_questions_publishes_event(db: AsyncSession, monkeypatch, capture_publishes):
    """PATCH reorder emits bank.question_updated with mutation='reorder'."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    q1 = await _make_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id, text="Question one.",
    )
    q2 = await _make_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id, text="Question two.",
    )
    q3 = await _make_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id, text="Question three.",
    )
    bank.status = "reviewing"
    await db.flush()

    # Reverse the order
    new_order = [str(q3.id), str(q2.id), str(q1.id)]
    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions/reorder",
                json={"question_ids": new_order},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text

    assert len(capture_publishes) == 1, f"expected 1 publish, got {len(capture_publishes)}"
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
    assert pub.payload["bank_id"] == str(bank.id)
    assert pub.payload["mutation"] == "reorder"
    assert pub.payload.get("question_id") is None
    assert pub.correlation_id


# ---------------------------------------------------------------------------
# T15: confirm_bank publishes bank.status_changed with new_status="confirmed"
# ---------------------------------------------------------------------------

async def test_confirm_bank_publishes_status_changed(
    db: AsyncSession, monkeypatch, capture_publishes
):
    """POST confirm emits bank.status_changed with new_status='confirmed'."""
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job, duration_minutes=30)
    bank = await ensure_bank_exists(db, stage=stage, job=job)

    # Add enough non-mandatory questions (4 × 5min = 20min, within 15–45 budget)
    for i in range(4):
        await _make_recruiter_question(
            db,
            bank=bank,
            snapshot=snapshot,
            user_id=user.id,
            text=f"Test confirm question {i}.",
            estimated_minutes=5.0,
            is_mandatory=False,
        )
    # Force bank into reviewing (create_recruiter_question auto-reverts, so set explicitly)
    bank.status = "reviewing"
    await db.flush()

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}/questions/confirm",
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    assert len(capture_publishes) == 1, f"expected 1 publish, got {len(capture_publishes)}"
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.BANK_STATUS_CHANGED
    assert pub.payload["bank_id"] == str(bank.id)
    assert pub.payload["new_status"] == "confirmed"
    assert pub.correlation_id


# ---------------------------------------------------------------------------
# T16: regenerate_question actor publishes bank.question_updated post-commit
# ---------------------------------------------------------------------------

async def test_regenerate_question_actor_publishes_event(
    db: AsyncSession, capture_publishes, monkeypatch
):
    """The regenerate_question actor publishes bank.question_updated inline
    after its own commit. Actors don't have FastAPI BackgroundTasks so publish
    is called directly after the session.begin() context exits."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from app import pubsub
    from app.modules.question_bank import actors
    from app.modules.question_bank.schemas import (
        GeneratedQuestion,
        QuestionRubric,
        SingleQuestionOutput,
    )
    from app.modules.question_bank.service import (
        ensure_bank_exists,
        write_generated_questions,
    )

    # ---- Seed data using the test DB session --------------------------------
    tenant, user, unit = await _setup_tenant_user_unit(db)
    job, snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=[_signal(value="Python")],
    )
    instance, stage = await _make_pipeline_and_stage(db, job=job)
    bank = await ensure_bank_exists(db, stage=stage, job=job)
    bank.status = "reviewing"
    await db.flush()

    # Insert one question so the actor has something to regenerate.
    from app.modules.question_bank.schemas import CreateQuestionBody

    question = await _make_recruiter_question(
        db, bank=bank, snapshot=snapshot, user_id=user.id,
        text="Original question text about Python development.",
    )
    await db.flush()

    # ---- Mock get_bypass_session to return the test db session --------------
    # The actor creates its own session via get_bypass_session. Monkeypatching
    # it to yield the test session keeps everything in the per-test rollback
    # transaction so nothing persists.
    @asynccontextmanager
    async def _fake_bypass_session():
        yield db

    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_bypass_session",
        _fake_bypass_session,
    )

    # ---- Mock the LLM call in _regenerate_one_question ----------------------
    regen_question = GeneratedQuestion(
        position=0,
        text="A brand-new Python question about async programming in production.",
        signal_values=["Python"],
        estimated_minutes=5.0,
        is_mandatory=False,
        follow_ups=["What was the biggest async challenge?"],
        positive_evidence=[
            "Names specific async libraries",
            "Describes production usage",
            "Mentions performance impact",
        ],
        red_flags=["No async experience", "Only toy-project experience"],
        rubric=QuestionRubric(
            excellent="Strong async experience in production with specific examples.",
            meets_bar="Basic async usage in at least one production system.",
            below_bar="Only tutorial-level async knowledge with no production use.",
        ),
        evaluation_hint="Strong answer names specific async patterns used in production.",
    )
    llm_output = SingleQuestionOutput(
        question=regen_question,
        reasoning="Replacing with a more targeted async-focused Python question.",
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=llm_output)
    monkeypatch.setattr(
        "app.modules.question_bank.actors.get_openai_client",
        lambda: fake_client,
    )

    # ---- Invoke the actor function directly (bypass Dramatiq broker/middleware)
    # actors.regenerate_question is a decorated Dramatiq Actor. Dramatiq's
    # AsyncIO wrapper makes .fn(...) a sync wrapper — the actual async
    # coroutine lives at .fn.__wrapped__. Calling __wrapped__ directly
    # bypasses the event-loop-thread requirement and lets us await it normally.
    await actors.regenerate_question.fn.__wrapped__(
        str(question.id),
        str(tenant.id),
        str(user.id),
        None,  # replace_signal_values
        "test-corr-regen",  # correlation_id
    )

    # ---- Assert publish was called post-commit ------------------------------
    assert len(capture_publishes) == 1, (
        f"expected 1 publish, got {len(capture_publishes)}"
    )
    pub = capture_publishes[0]
    assert pub.channel == f"job:{job.id}"
    assert pub.event == pubsub.Events.BANK_QUESTION_UPDATED
    assert pub.payload["mutation"] == "regenerate"
    assert pub.payload["job_id"] == str(job.id)
    assert pub.payload["bank_id"] == str(bank.id)
    assert pub.payload["stage_id"] == str(stage.id)
    assert pub.payload["question_id"] == str(question.id)
    assert pub.correlation_id == "test-corr-regen"
