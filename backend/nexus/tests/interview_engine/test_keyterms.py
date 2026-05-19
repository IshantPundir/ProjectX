"""Unit tests for the engine-side keyterm merger.

Reference spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from app.modules.interview_engine.keyterms import KeytermExtraction, assemble_keyterms
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    SessionConfig,
    StageConfig,
)


def _make_session_config(
    *,
    candidate_name: str = "Ishant",
    keyterms: list[str] | None = None,
) -> SessionConfig:
    """Build a minimal SessionConfig fixture for assemble_keyterms tests."""
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
        candidate=CandidateContext(name=candidate_name),
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


class TestAssembleKeyterms:
    def test_returns_keyterm_extraction(self) -> None:
        result = assemble_keyterms(_make_session_config())
        assert isinstance(result, KeytermExtraction)

    def test_empty_keyterms_falls_back_to_candidate_first_name(self) -> None:
        result = assemble_keyterms(
            _make_session_config(candidate_name="Ishant", keyterms=[]),
        )
        assert result.terms == ["Ishant"]
        assert result.sources == {"candidate_name": 1}

    def test_keyterms_merged_after_candidate_name(self) -> None:
        result = assemble_keyterms(
            _make_session_config(
                candidate_name="Ishant",
                keyterms=["MuleSoft", "TIBCO", "Boomi"],
            )
        )
        assert result.terms == ["Ishant", "MuleSoft", "TIBCO", "Boomi"]
        assert result.sources == {"candidate_name": 1, "bank_cached": 3}

    def test_case_insensitive_dedupe_first_seen_wins(self) -> None:
        # If candidate name happens to collide case-insensitively with a bank term,
        # first-seen casing (candidate) wins; the bank term is dropped.
        result = assemble_keyterms(
            _make_session_config(
                candidate_name="MuleSoft",  # contrived
                keyterms=["mulesoft", "TIBCO"],
            )
        )
        lowered = [t.lower() for t in result.terms]
        assert lowered.count("mulesoft") == 1
        assert "MuleSoft" in result.terms
        assert "mulesoft" not in result.terms
        assert "TIBCO" in result.terms

    def test_cap_at_fifty(self) -> None:
        many = [f"Brand{i}Term" for i in range(100)]
        result = assemble_keyterms(
            _make_session_config(candidate_name="Ishant", keyterms=many),
        )
        assert len(result.terms) == 50
        assert result.terms[0] == "Ishant"  # candidate-name survives at the front

    def test_candidate_first_token_only(self) -> None:
        # build_session_config (Task 8) already trims candidate name to first token,
        # but assemble_keyterms should be defensive — if a full name slips through,
        # only the first token is emitted.
        result = assemble_keyterms(
            _make_session_config(candidate_name="Ishant Pundir Kumar"),
        )
        assert "Ishant" in result.terms
        assert "Pundir" not in result.terms
        assert "Kumar" not in result.terms

    def test_empty_candidate_name_handled(self) -> None:
        result = assemble_keyterms(
            _make_session_config(candidate_name="", keyterms=["MuleSoft"]),
        )
        # No candidate-name term emitted
        assert "candidate_name" not in result.sources
        assert result.terms == ["MuleSoft"]
