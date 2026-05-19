"""Tests for the per-session STT plugin factory.

Exercises the tuple-return contract introduced 2026-05-19:
build_stt_plugin_for_session returns (stt, KeytermExtraction).
"""
from __future__ import annotations

from unittest.mock import patch

from app.modules.interview_engine.keyterms import KeytermExtraction
from app.modules.interview_engine.stt_factory import build_stt_plugin_for_session
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    SessionConfig,
    StageConfig,
)


def _make_session_config(*, keyterms: list[str] | None = None) -> SessionConfig:
    return SessionConfig(
        session_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        job_title="Sr. Integration Engineer",
        hiring_company_name="Workato",
        role_summary="x",
        jd_text=None,
        seniority_level="senior",
        company=CompanyContext(about="x", industry="x", hiring_bar="x"),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(
            stage_id="00000000-0000-0000-0000-000000000004",
            stage_type="ai_screening",
            name="Bot Screening",
            duration_minutes=15,
            difficulty="hard",
            questions=[],
            advance_behavior="auto_advance",
        ),
        signals=[],
        signal_metadata=[],
        keyterms=keyterms or [],
    )


def test_factory_returns_tuple_of_stt_and_extraction() -> None:
    """build_stt_plugin_for_session returns (stt, KeytermExtraction)."""
    sentinel_stt = object()
    sc = _make_session_config(keyterms=["MuleSoft", "TIBCO"])

    with patch(
        "app.modules.interview_engine.stt_factory.build_stt_plugin",
        return_value=sentinel_stt,
    ) as mock_build:
        stt, extraction = build_stt_plugin_for_session(session_config=sc)

    assert stt is sentinel_stt
    assert isinstance(extraction, KeytermExtraction)
    # Candidate name + 2 bank keyterms
    assert extraction.terms == ["Ishant", "MuleSoft", "TIBCO"]
    # Verify the underlying factory was called with the assembled keyterms list
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs["keyterms"] == ["Ishant", "MuleSoft", "TIBCO"]


def test_factory_handles_empty_bank_keyterms() -> None:
    """When session_config.keyterms is empty, only candidate name is in the extraction."""
    sentinel_stt = object()
    sc = _make_session_config(keyterms=[])

    with patch(
        "app.modules.interview_engine.stt_factory.build_stt_plugin",
        return_value=sentinel_stt,
    ):
        _, extraction = build_stt_plugin_for_session(session_config=sc)

    assert extraction.terms == ["Ishant"]
    assert extraction.sources == {"candidate_name": 1}
