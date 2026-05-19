"""Verifies hiring_company_name is populated from the closest org_unit.

Spec: docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md §2
"Schema additions" — the intro_brief Speaker turn references the HIRING company
name (e.g., "Workato"), NOT the ProjectX tenant name (e.g., "BinQle" if the
tenant is a staffing agency). The hiring company is the closest org_unit to the
job (depth 0 in `get_org_unit_ancestry`).

This file is a schema-level gate. End-to-end population through
`build_session_config` is covered by the broader interview_runtime/service
integration tests.
"""
from __future__ import annotations

from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)


def _valid_session_config_kwargs(**overrides: object) -> dict[str, object]:
    """Build a minimal-but-valid SessionConfig kwargs dict.

    The SessionConfig wire contract requires job_id, candidate_id,
    role_summary, seniority_level, company, and a fully-populated stage.
    Tests override only the field they care about.
    """
    base: dict[str, object] = dict(
        session_id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-0000000000aa",
        candidate_id="00000000-0000-0000-0000-000000000002",
        job_title="Sr. Integration Engineer",
        role_summary="Build and maintain integrations.",
        seniority_level="senior",
        company=CompanyContext(
            about="ok",
            industry="software",
            hiring_bar="ok",
        ),
        candidate=CandidateContext(name="Punar"),
        stage=StageConfig(
            stage_id="00000000-0000-0000-0000-000000000003",
            stage_type="ai_screening",
            name="AI Screening",
            duration_minutes=15,
            difficulty="medium",
            questions=[],
        ),
        signal_metadata=[],
    )
    base.update(overrides)
    return base


def test_session_config_accepts_hiring_company_name() -> None:
    cfg = SessionConfig(
        **_valid_session_config_kwargs(hiring_company_name="Workato"),
    )
    assert cfg.hiring_company_name == "Workato"


def test_session_config_hiring_company_name_optional() -> None:
    """Field defaults to None for backward-compat with legacy session configs."""
    cfg = SessionConfig(**_valid_session_config_kwargs())
    assert cfg.hiring_company_name is None
