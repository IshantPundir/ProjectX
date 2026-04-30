"""nexus_client retry/error coverage with respx.

Mocks Nexus's /api/internal/sessions/{id}/{config,results} endpoints at
the httpx layer. No real network. The module under test imports types
from app.modules.interview_runtime.schemas via the path-installed nexus
package (see Dockerfile + pyproject.toml).
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from nexus_client import (
    ConfigNotFoundError,
    ConfigUnavailableError,
    ResultPostFailedError,
    ResultRejectedError,
    fetch_session_config,
    post_session_result,
)
from app.modules.interview_runtime.schemas import (
    SessionResult,
    TranscriptEntry,
)


BASE = "http://nexus:8000"
SID = "00000000-0000-0000-0000-000000000001"
JWT = "fake-jwt"


def _config_url() -> str:
    return f"{BASE}/api/internal/sessions/{SID}/config"


def _results_url() -> str:
    return f"{BASE}/api/internal/sessions/{SID}/results"


@pytest.fixture
def session_config_payload() -> dict:
    """Minimal valid SessionConfig JSON shape — must parse via the lifted
    schema in app.modules.interview_runtime.schemas."""
    return {
        "session_id": SID,
        "job_title": "Senior Backend Engineer",
        "role_summary": "Owns the platform.",
        "seniority_level": "senior",
        "company": {
            "about": "We build infrastructure for mid-market AI startups today.",
            "industry": "ai_machine_learning",
            "company_stage": "series_a_b",
            "hiring_bar": "Engineers who own problems end-to-end with autonomy.",
        },
        "candidate": {"name": "Alex"},
        "stage": {
            "stage_id": "00000000-0000-0000-0000-000000000099",
            "stage_type": "ai_screening",
            "name": "Phone screen",
            "duration_minutes": 30,
            "difficulty": "medium",
            "questions": [],
            "advance_behavior": "manual_review",
        },
        "signals": [],
    }


@pytest.fixture
def sample_result() -> SessionResult:
    return SessionResult(
        session_id=SID,
        job_title="Senior Backend Engineer",
        stage_id="00000000-0000-0000-0000-000000000099",
        stage_type="ai_screening",
        candidate_name="Alex",
        duration_seconds=600.0,
        questions_asked=8,
        questions_skipped=1,
        total_probes_fired=3,
        question_results=[],
        full_transcript=[
            TranscriptEntry(role="agent", text="Hi", timestamp_ms=0),
        ],
        completed_at="2026-04-29T12:00:00Z",
    )


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    """Drop the asyncio.sleep delays inside the retry loops so tests run fast."""
    import asyncio

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


# ---------------------------------------------------------------------------
# fetch_session_config
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_config_200_happy(session_config_payload):
    respx.get(_config_url()).mock(return_value=Response(200, json=session_config_payload))
    cfg = await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)
    assert cfg.session_id == SID
    assert cfg.candidate.name == "Alex"


@respx.mock
async def test_fetch_config_401_no_retry():
    route = respx.get(_config_url()).mock(
        return_value=Response(401, json={"code": "ENGINE_TOKEN_INVALID"})
    )
    with pytest.raises(PermissionError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)
    assert route.call_count == 1  # NO retry


@respx.mock
async def test_fetch_config_404_raises_not_found():
    respx.get(_config_url()).mock(
        return_value=Response(404, json={"code": "SESSION_NOT_FOUND"})
    )
    with pytest.raises(ConfigNotFoundError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)


@respx.mock
async def test_fetch_config_422_unavailable():
    respx.get(_config_url()).mock(
        return_value=Response(422, json={"code": "STAGE_TYPE_NOT_AI_DRIVEN", "stage_type": "human_interview"})
    )
    with pytest.raises(ConfigUnavailableError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)


@respx.mock
async def test_fetch_config_409_unavailable():
    respx.get(_config_url()).mock(
        return_value=Response(409, json={"code": "BANK_NOT_READY"})
    )
    with pytest.raises(ConfigUnavailableError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)


@respx.mock
async def test_fetch_config_5xx_retries_then_succeeds(session_config_payload):
    route = respx.get(_config_url()).mock(
        side_effect=[
            Response(503),
            Response(503),
            Response(200, json=session_config_payload),
        ]
    )
    cfg = await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)
    assert cfg.session_id == SID
    assert route.call_count == 3


@respx.mock
async def test_fetch_config_5xx_exhausts_retries():
    """3 consecutive 503s — final raise_for_status."""
    import httpx

    route = respx.get(_config_url()).mock(
        side_effect=[Response(503), Response(503), Response(503)]
    )
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_session_config(session_id=SID, jwt=JWT, base_url=BASE)
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# post_session_result
# ---------------------------------------------------------------------------


@respx.mock
async def test_post_result_204_success(sample_result):
    route = respx.post(_results_url()).mock(return_value=Response(204))
    await post_session_result(
        session_id=SID, jwt=JWT, result=sample_result, base_url=BASE
    )
    assert route.call_count == 1


@respx.mock
async def test_post_result_409_idempotent_success(sample_result):
    """409 from /results is the idempotent-retry path — engine treats it as success, NOT a failure."""
    route = respx.post(_results_url()).mock(
        return_value=Response(409, json={"code": "SESSION_NOT_ACTIVE"})
    )
    # Must not raise.
    await post_session_result(
        session_id=SID, jwt=JWT, result=sample_result, base_url=BASE
    )
    assert route.call_count == 1


@respx.mock
async def test_post_result_401_rejected(sample_result):
    route = respx.post(_results_url()).mock(
        return_value=Response(401, json={"code": "ENGINE_TOKEN_INVALID"})
    )
    with pytest.raises(ResultRejectedError):
        await post_session_result(
            session_id=SID, jwt=JWT, result=sample_result, base_url=BASE
        )
    assert route.call_count == 1  # no retry on auth


@respx.mock
async def test_post_result_422_rejected(sample_result):
    route = respx.post(_results_url()).mock(
        return_value=Response(422, json={"code": "INVALID"})
    )
    with pytest.raises(ResultRejectedError):
        await post_session_result(
            session_id=SID, jwt=JWT, result=sample_result, base_url=BASE
        )
    assert route.call_count == 1


@respx.mock
async def test_post_result_5xx_exhausts_retries(sample_result):
    route = respx.post(_results_url()).mock(
        side_effect=[Response(503), Response(503), Response(503)]
    )
    with pytest.raises(ResultPostFailedError):
        await post_session_result(
            session_id=SID, jwt=JWT, result=sample_result, base_url=BASE
        )
    assert route.call_count == 3


@respx.mock
async def test_post_result_5xx_then_204_succeeds(sample_result):
    route = respx.post(_results_url()).mock(
        side_effect=[Response(503), Response(204)]
    )
    await post_session_result(
        session_id=SID, jwt=JWT, result=sample_result, base_url=BASE
    )
    assert route.call_count == 2
