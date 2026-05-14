"""Edit-category classifier tests — A/B/C/D mapping per spec §8.

Sections:
  - Unit tests (pure function, no DB) — test_no_changes_is_category_a … test_highest_category_wins
  - HTTP integration tests — test_preview_endpoint_* and test_active_job_blocks_*
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.jd.models import JobPosting
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.database import get_tenant_db
from app.modules.pipelines.classifier import classify_pipeline_diff, EditCategory
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_TEST_BEARER = "test-classify-token"

_VALID_PROFILE = {
    "about": "We build enterprise recruiting tools at scale.",
    "industry": "Fintech / Financial Services",
    "hiring_bar": "Engineers who own problems end-to-end.",
}


def _stage(id_, position, stage_type, **overrides):
    base = {
        "id": id_, "position": position, "stage_type": stage_type,
        "name": f"S{position}", "paused_at": None,
        "duration_minutes": 30 if stage_type not in ("intake", "debrief") else None,
        "difficulty": "medium" if stage_type not in ("intake", "debrief") else None,
        "signal_filter": {"include_types": ["competency"]} if stage_type not in ("intake", "debrief") else None,
        "pass_criteria": {"type": "all_knockouts_pass"},
        "advance_behavior": "auto_advance",
        "sla_days": None,
    }
    base.update(overrides)
    return base


def test_no_changes_is_category_a():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.A


def test_duration_change_is_category_a():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen", duration_minutes=45), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.A


def test_add_stage_is_category_b():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"),
                _stage("new", 2, "ai_screening"), _stage("s2", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B


def test_reorder_is_category_b():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"),
               _stage("s2", 2, "ai_screening"), _stage("s3", 3, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "ai_screening"),
                _stage("s1", 2, "phone_screen"), _stage("s3", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B


def test_remove_stage_with_zero_in_flight_is_category_c():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={"s1": 0})
    assert result.category == EditCategory.C
    assert result.in_flight.get("s1", 0) == 0


def test_remove_stage_with_in_flight_is_category_c_with_in_flight_count():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={"s1": 3})
    assert result.category == EditCategory.C
    assert result.in_flight["s1"] == 3


def test_pause_is_category_c():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen", paused_at="2026-04-26T10:00:00Z"),
                _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.C


def test_stage_type_change_is_category_d():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "ai_screening"), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.D


def test_highest_category_wins():
    """If a diff contains both A and B changes, B wins."""
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"),
                _stage("s1", 1, "phone_screen", duration_minutes=45),
                _stage("new", 2, "ai_screening"),
                _stage("s2", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B  # B wins over A


# ---------------------------------------------------------------------------
# HTTP integration helpers
# ---------------------------------------------------------------------------


def _setup_http_context(
    db: AsyncSession,
    user,
    tenant_id: uuid.UUID,
    is_super_admin: bool = True,
):
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
        is_super_admin=is_super_admin,
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


async def _make_job_with_pipeline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    org_unit_id: uuid.UUID,
    user_id: uuid.UUID,
    job_status: str = "pipeline_built",
) -> tuple[JobPosting, JobPipelineInstance, list[JobPipelineStage]]:
    """Create a job with a 3-stage pipeline: intake → phone_screen → debrief."""
    job = JobPosting(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        title="Classify Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched description for classify tests.",
        status=job_status,
        source="native",
        created_by=user_id,
    )
    db.add(job)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    stages = []
    stage_defs = [
        {"position": 0, "name": "Intake", "stage_type": "intake", "duration_minutes": None, "difficulty": None},
        {"position": 1, "name": "Phone Screen", "stage_type": "phone_screen", "duration_minutes": 30, "difficulty": "easy"},
        {"position": 2, "name": "Debrief", "stage_type": "debrief", "duration_minutes": None, "difficulty": None},
    ]
    for s in stage_defs:
        stage = JobPipelineStage(
            tenant_id=tenant_id,
            instance_id=instance.id,
            position=s["position"],
            name=s["name"],
            stage_type=s["stage_type"],
            duration_minutes=s["duration_minutes"],
            difficulty=s["difficulty"],
            signal_filter={"include_types": ["competency"]} if s["stage_type"] not in ("intake", "debrief") else None,
            pass_criteria={"type": "all_knockouts_pass"} if s["stage_type"] not in ("intake", "debrief") else None,
            advance_behavior="auto_advance",
        )
        db.add(stage)
        stages.append(stage)

    await db.flush()
    return job, instance, stages


# ---------------------------------------------------------------------------
# HTTP test 1 — preview-changes returns category A for no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_endpoint_returns_category_A_for_no_op(db: AsyncSession):
    """POST /pipeline/preview-changes on an unchanged stage list returns category A."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job, instance, stages = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
    )
    await db.commit()

    headers, restore = _setup_http_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r0.status_code == 200, r0.text

            # Build a no-op body — same stage list, no changes
            body = {
                "stages": [
                    {
                        "id": s["id"],
                        "position": s["position"],
                        "name": s["name"],
                        "stage_type": s["stage_type"],
                        "duration_minutes": s["duration_minutes"],
                        "difficulty": s["difficulty"],
                        "signal_filter": s["signal_filter"],
                        "pass_criteria": s["pass_criteria"],
                        "advance_behavior": s["advance_behavior"],
                    }
                    for s in r0.json()["stages"]
                ]
            }

            r = await ac.post(
                f"/api/jobs/{job.id}/pipeline/preview-changes",
                json=body,
                headers=headers,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["category"] == "A"
            assert isinstance(data["warnings"], list)
            assert isinstance(data["in_flight"], dict)
    finally:
        restore()


# ---------------------------------------------------------------------------
# HTTP test 2 — active job blocks stage_type change with 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_job_blocks_stage_type_change_with_409(db: AsyncSession):
    """PATCH /pipeline on an active job rejects Category D with 409.

    The job status is set directly in the DB (bypassing the activation gate,
    which lands in Task 12). The PATCH guard only inspects job.status — it
    does not validate how the job got there.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    # Create pipeline_built job + pipeline, then flip status to active directly
    job, instance, stages = await _make_job_with_pipeline(
        db, tenant_id=tenant.id, org_unit_id=company.id, user_id=user.id,
        job_status="pipeline_built",
    )
    job.status = "active"
    await db.flush()
    await db.commit()

    headers, restore = _setup_http_context(db, user, tenant.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r0 = await ac.get(f"/api/jobs/{job.id}/pipeline", headers=headers)
            assert r0.status_code == 200, r0.text
            current_stages = r0.json()["stages"]

            # Find the phone_screen stage (non-IO) and flip its type to ai_screening
            middle = next(
                (s for s in current_stages if s["stage_type"] == "phone_screen"),
                None,
            )
            assert middle is not None, "Fixture must have a phone_screen stage"

            payload = {
                "stages": [
                    {
                        "id": s["id"],
                        "position": s["position"],
                        "name": s["name"],
                        "stage_type": "ai_screening" if s["id"] == middle["id"] else s["stage_type"],
                        "duration_minutes": s["duration_minutes"],
                        "difficulty": s["difficulty"],
                        "signal_filter": s["signal_filter"],
                        "pass_criteria": s["pass_criteria"],
                        "advance_behavior": s["advance_behavior"],
                    }
                    for s in current_stages
                ]
            }

            r = await ac.patch(
                f"/api/jobs/{job.id}/pipeline",
                json=payload,
                headers=headers,
            )
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "stage_type_change_forbidden"
    finally:
        restore()
