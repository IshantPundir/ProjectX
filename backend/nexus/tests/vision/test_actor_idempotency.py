# tests/vision/test_actor_idempotency.py
import uuid

import pytest

from app.modules.vision import actors as vision_actors


@pytest.mark.asyncio
async def test_actor_skips_when_row_already_ready(monkeypatch):
    calls = {"analyzed": 0}

    async def _fake_load_or_create(db, session_id, tenant_id):
        # Simulate an existing terminal row → actor must short-circuit.
        return "ready", None  # (status, recording_key)

    def _fake_run_analysis(*a, **k):
        calls["analyzed"] += 1
        raise AssertionError("must not analyze when already ready")

    monkeypatch.setattr(vision_actors, "_load_state", _fake_load_or_create)
    monkeypatch.setattr(vision_actors, "run_analysis", _fake_run_analysis)
    await vision_actors._run(str(uuid.uuid4()), str(uuid.uuid4()))
    assert calls["analyzed"] == 0
