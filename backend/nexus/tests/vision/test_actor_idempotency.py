# tests/vision/test_actor_idempotency.py
import uuid

import pytest

from app.modules.vision import actors as vision_actors


def _never_analyze(*a, **k):
    raise AssertionError("must not analyze on a skip/none action")


@pytest.mark.asyncio
async def test_actor_skips_when_already_done(monkeypatch):
    # Existing ready/unscorable row → _load_state returns ("skip", None) → no work.
    async def _fake(db, session_id, tenant_id):
        return "skip", None

    monkeypatch.setattr(vision_actors, "_load_state", _fake)
    monkeypatch.setattr(vision_actors, "run_analysis", _never_analyze)
    await vision_actors._run(str(uuid.uuid4()), str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_actor_skips_when_no_recording(monkeypatch):
    # No usable recording → _load_state returns ("none", None) → no work.
    async def _fake(db, session_id, tenant_id):
        return "none", None

    monkeypatch.setattr(vision_actors, "_load_state", _fake)
    monkeypatch.setattr(vision_actors, "run_analysis", _never_analyze)
    await vision_actors._run(str(uuid.uuid4()), str(uuid.uuid4()))
