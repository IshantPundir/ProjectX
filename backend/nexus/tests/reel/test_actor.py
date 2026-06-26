"""Reel actor (gen-3) — loads SessionEvidence + computes the recording offset.

The actor's heavy DB/storage/render collaborators are mocked; these tests pin the
gen-3 wiring: it reads ``session_evidence_json`` (transcript + ``meta.started_at``),
computes ``recording_offset_ms`` against ``recording_started_at``, and feeds that
offset (NOT an event-log / anchor) to ``render_reel``.
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.reel import actors


# --- _parse_iso (pure) ----------------------------------------------------

def test_parse_iso_handles_trailing_z():
    dt = actors._parse_iso("2026-06-14T10:00:00.090000Z")
    assert dt == datetime(2026, 6, 14, 10, 0, 0, 90_000, tzinfo=UTC)


def test_parse_iso_handles_offset_form():
    dt = actors._parse_iso("2026-06-14T10:00:00+00:00")
    assert dt == datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)


def test_parse_iso_none_and_garbage_return_none():
    assert actors._parse_iso(None) is None
    assert actors._parse_iso("") is None
    assert actors._parse_iso("not-a-date") is None


# --- _build_and_upload (gen-3 wiring) -------------------------------------

def _evidence(started_at="2026-06-14T10:00:00.500000Z"):
    return {
        "meta": {"started_at": started_at},
        "transcript": [{"speaker": "candidate", "turn_ref": "t1",
                        "span": {"start_ms": 0, "end_ms": 1000},
                        "words": [{"w": "hello", "rel_start_ms": 0, "rel_end_ms": 500}]}],
    }


def _inputs(evidence, *, recording_started_at, recording_s3_key="rec/x.mp4"):
    return {
        "verdict": "advance", "verdict_reason": "strong", "summary": {},
        "question_scorecards": [], "signal_scorecards": [],
        "role_title": "Backend Engineer", "candidate_name": "Asha",
        "recording_s3_key": recording_s3_key,
        "recording_started_at": recording_started_at,
        "session_evidence_json": evidence,
    }


class _FakeStorage:
    def __init__(self):
        self.downloaded = None
        self.uploaded = None

    async def download_to_path(self, key, path):
        self.downloaded = (key, path)

    async def upload_bytes(self, key, data, content_type=None):
        self.uploaded = (key, data, content_type)


@pytest.fixture
def _patched(monkeypatch):
    """Stub every collaborator; return a captures dict the tests assert on."""
    captured: dict = {}

    async def _fake_load_inputs(db, session_id, tenant_id):
        return captured["inputs"]

    async def _fake_generate_edl(**kwargs):
        captured["generate_edl_kwargs"] = kwargs
        return SimpleNamespace(beats=[])  # raw edl (validate_edl is also stubbed)

    def _fake_validate_edl(raw, transcript):
        captured["validate_transcript"] = transcript
        return SimpleNamespace(beats=["b0", "b1"], duration_ms=42_000)

    async def _fake_render_reel(**kwargs):
        captured["render_kwargs"] = kwargs
        # The actor reads `out_path` bytes off disk → create the file.
        with open(kwargs["out_path"], "wb") as fh:
            fh.write(b"\x00mp4")
        return (kwargs["out_path"], [{"kind": "point", "start_ms": 0}])

    async def _fake_probe_duration_ms(path):
        return 42_000

    storage = _FakeStorage()

    monkeypatch.setattr(actors, "_load_inputs", _fake_load_inputs)
    monkeypatch.setattr(actors, "generate_edl", _fake_generate_edl)
    monkeypatch.setattr(actors, "validate_edl", _fake_validate_edl)
    monkeypatch.setattr(actors.render, "render_reel", _fake_render_reel)
    monkeypatch.setattr(actors.render, "probe_duration_ms", _fake_probe_duration_ms)
    monkeypatch.setattr(actors, "edl_to_dict", lambda v: {"beats": len(v.beats)})
    monkeypatch.setattr(actors, "get_object_storage", lambda: storage)
    monkeypatch.setattr(actors, "_model_versions", lambda: {"director_model": "x"})

    # No real DB: stub the bypass session context manager.
    class _FakeSession:
        async def execute(self, *a, **k):
            return None

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(actors, "get_bypass_session", lambda: _FakeCtx())

    captured["storage"] = storage
    return captured


@pytest.mark.asyncio
async def test_build_computes_offset_and_passes_to_render(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, 0, tzinfo=UTC)
    # meta.started_at is +500ms vs the recording start → offset = 500
    _patched["inputs"] = _inputs(_evidence("2026-06-14T10:00:00.500000Z"),
                                 recording_started_at=rec_start)

    sid, tid = uuid4(), uuid4()
    payload = await actors._build_and_upload(sid, tid, "corr-1",
                                             actors.logger.bind())

    rk = _patched["render_kwargs"]
    assert rk["offset_ms"] == 500
    assert rk["beats"] == ["b0", "b1"]
    # gen-3 render takes NO event-log / speaking / anchor
    assert "events" not in rk and "speaking" not in rk and "anchor" not in rk
    # the gen-3 transcript list reaches the director + validator
    assert _patched["generate_edl_kwargs"]["transcript"] == \
        _patched["inputs"]["session_evidence_json"]["transcript"]
    assert payload["r2_key"] == f"reels/{tid}/{sid}.mp4"
    assert payload["duration_seconds"] == 42.0
    assert _patched["storage"].uploaded[0] == payload["r2_key"]


@pytest.mark.asyncio
async def test_build_raises_when_evidence_missing(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    _patched["inputs"] = _inputs(None, recording_started_at=rec_start)
    with pytest.raises(RuntimeError, match="session evidence not ready"):
        await actors._build_and_upload(uuid4(), uuid4(), "c", actors.logger.bind())


@pytest.mark.asyncio
async def test_build_raises_when_transcript_empty(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    ev = {"meta": {"started_at": "2026-06-14T10:00:00Z"}, "transcript": []}
    _patched["inputs"] = _inputs(ev, recording_started_at=rec_start)
    with pytest.raises(RuntimeError, match="session evidence not ready"):
        await actors._build_and_upload(uuid4(), uuid4(), "c", actors.logger.bind())


@pytest.mark.asyncio
async def test_build_raises_when_started_at_missing(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    ev = {"meta": {}, "transcript": _evidence()["transcript"]}
    _patched["inputs"] = _inputs(ev, recording_started_at=rec_start)
    with pytest.raises(RuntimeError, match="session evidence not ready"):
        await actors._build_and_upload(uuid4(), uuid4(), "c", actors.logger.bind())


@pytest.mark.asyncio
async def test_identity_tag_passed_to_render(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, 0, tzinfo=UTC)
    _patched["inputs"] = _inputs(_evidence("2026-06-14T10:00:00.500000Z"),
                                 recording_started_at=rec_start)
    await actors._build_and_upload(uuid4(), uuid4(), "corr-1", actors.logger.bind())
    assert _patched["render_kwargs"]["identity_tag"] == "Asha · Backend Engineer"


@pytest.mark.asyncio
async def test_build_raises_when_recording_missing(_patched):
    rec_start = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    _patched["inputs"] = _inputs(_evidence(), recording_started_at=rec_start,
                                 recording_s3_key=None)
    with pytest.raises(RuntimeError, match="recording not ready"):
        await actors._build_and_upload(uuid4(), uuid4(), "c", actors.logger.bind())
