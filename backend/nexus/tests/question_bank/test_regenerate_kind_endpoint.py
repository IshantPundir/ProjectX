"""Integration tests for POST /banks/regenerate-kind.

Reuses the test harness pattern from tests/test_question_banks_router.py
(direct dependency-override, faked auth, stubbed Dramatiq sends) so no
new fixtures are introduced. The endpoint, schema validation, and 404
paths are exercised end-to-end through ASGITransport + AsyncClient.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from tests.test_question_banks_router import (
    _build_full_setup,
    _setup_test_context,
    _stub_actor_sends,
    _setup_tenant_user_unit,
    _make_job_with_signals,
    _make_pipeline_and_stage,
    _signal,
)


def _stub_regenerate_kind_send(monkeypatch) -> None:
    """Stub regenerate_kind_actor.send so the request doesn't enqueue."""
    monkeypatch.setattr(
        "app.modules.question_bank.actors.regenerate_kind_actor.send",
        lambda *a, **k: None,
    )


@pytest.mark.asyncio
async def test_regenerate_kind_endpoint_accepts_behavioral(
    db: AsyncSession, monkeypatch,
):
    """POST with kind=behavioral returns 202 and dispatches the actor."""
    _stub_actor_sends(monkeypatch)
    _stub_regenerate_kind_send(monkeypatch)
    setup = await _build_full_setup(db, bank_status="reviewing")
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/"
                f"{setup['stage'].id}/banks/regenerate-kind",
                json={"kind": "behavioral"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "generating"
    assert body["bank_id"] == str(setup["bank"].id)

    # Re-load to confirm the bank flipped to generating
    await db.refresh(setup["bank"])
    assert setup["bank"].status == "generating"


@pytest.mark.parametrize("bad_kind", ["behavioral_star", "technical_depth", "garbage"])
@pytest.mark.asyncio
async def test_regenerate_kind_endpoint_rejects_invalid_kind(
    db: AsyncSession, monkeypatch, bad_kind: str,
):
    """Invalid kind values are rejected at the Pydantic Literal layer.

    The OLD taxonomy labels (`behavioral_star` / `technical_depth`) must now be
    rejected — the body only accepts the engine-v2 phase labels.
    """
    _stub_actor_sends(monkeypatch)
    _stub_regenerate_kind_send(monkeypatch)
    setup = await _build_full_setup(db, bank_status="reviewing")
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/"
                f"{setup['stage'].id}/banks/regenerate-kind",
                json={"kind": bad_kind},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_regenerate_kind_endpoint_404_when_no_bank(
    db: AsyncSession, monkeypatch,
):
    """Stage without a bank row returns 404."""
    _stub_actor_sends(monkeypatch)
    _stub_regenerate_kind_send(monkeypatch)

    # Build tenant + job + pipeline + stage but DO NOT create a bank.
    tenant, user, unit = await _setup_tenant_user_unit(db)
    signals = [_signal(value="Python")]
    job, _snapshot = await _make_job_with_signals(
        db, tenant.id, unit.id, user.id, signals=signals,
    )
    _instance, stage = await _make_pipeline_and_stage(db, job=job)

    headers, restore = _setup_test_context(db, user, tenant.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job.id}/pipeline/stages/{stage.id}"
                f"/banks/regenerate-kind",
                json={"kind": "behavioral"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_regenerate_kind_endpoint_404_for_unknown_stage(
    db: AsyncSession, monkeypatch,
):
    """Unknown stage_id returns 404 (require_bank_access_by_stage path)."""
    _stub_actor_sends(monkeypatch)
    _stub_regenerate_kind_send(monkeypatch)
    setup = await _build_full_setup(db, bank_status="reviewing")
    headers, restore = _setup_test_context(db, setup["user"], setup["tenant"].id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{setup['job'].id}/pipeline/stages/{uuid.uuid4()}"
                f"/banks/regenerate-kind",
                json={"kind": "behavioral"},
                headers=headers,
            )
    finally:
        restore()

    assert resp.status_code == 404, resp.text
