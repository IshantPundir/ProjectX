"""Unit tests for _build_user_message — the prompt-injection seam.

These are PURE tests (no DB, no LLM, no async fixtures). `_build_user_message`
is a deterministic string builder; the only thing we need to verify is which
structural block it renders depending on whether a CoveragePlan is supplied.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.question_bank.actors import _build_user_message
from app.modules.question_bank.coverage_planner import CoveragePlan


# ---------------------------------------------------------------------------
# Minimal stubs — built from exactly the attributes _build_user_message reads.
# We use SimpleNamespace instead of real ORM objects because this is a pure
# string-builder test; no DB session is needed and the test stays synchronous.
# ---------------------------------------------------------------------------

def _make_job(*, title: str = "Senior Backend Engineer") -> SimpleNamespace:
    return SimpleNamespace(title=title, description_enriched=None)


def _make_snapshot(
    *,
    signals: list[dict] | None = None,
    role_summary: str = "Builds high-throughput data pipelines.",
    seniority_level: str = "senior",
) -> SimpleNamespace:
    return SimpleNamespace(
        signals=signals or [],
        role_summary=role_summary,
        seniority_level=seniority_level,
    )


def _make_stage(
    *,
    stage_id: str = "00000000-0000-0000-0000-000000000001",
    name: str = "AI Screening",
    stage_type: str = "ai_screening",
    duration_minutes: int = 30,
    difficulty: str = "medium",
    signal_filter: dict | None = None,
    advance_behavior: str = "auto_advance",
) -> SimpleNamespace:
    from uuid import UUID
    return SimpleNamespace(
        id=UUID(stage_id),
        name=name,
        stage_type=stage_type,
        duration_minutes=duration_minutes,
        difficulty=difficulty,
        signal_filter=signal_filter or {"include_types": ["competency", "experience"]},
        advance_behavior=advance_behavior,
    )


def _make_pipeline_stages(stage_id: str = "00000000-0000-0000-0000-000000000001") -> list[dict]:
    """Minimal pipeline_stages list (one entry matching the stage under test)."""
    return [
        {
            "id": stage_id,
            "name": "AI Screening",
            "stage_type": "ai_screening",
            "duration_minutes": 30,
            "difficulty": "medium",
        }
    ]


# ---------------------------------------------------------------------------
# Test 1: with coverage_plan — renders COVERAGE PLAN block, not BUDGET block
# ---------------------------------------------------------------------------


def test_build_user_message_with_coverage_plan_renders_plan_block():
    """When a CoveragePlan is supplied, the message must contain the
    COVERAGE PLAN block with REQUIRED PRIMARY lines and secondary-only lines,
    and must NOT contain the BUDGET FOR THIS STAGE block.
    """
    stage_id = "00000000-0000-0000-0000-000000000001"
    plan = CoveragePlan(
        required_primaries=["Skill A", "Skill B"],
        secondary_only=["Skill C"],
        bundle_eligible=["Skill C"],
        slot_budget=6,
        must_cover_count=3,
        feasible=False,
        recommended_minutes=9,
    )

    msg = _build_user_message(
        job=_make_job(),
        snapshot=_make_snapshot(),
        company_profile=None,
        stage=_make_stage(stage_id=stage_id),
        pipeline_stages=_make_pipeline_stages(stage_id),
        prior_stages_questions=[],
        coverage_plan=plan,
    )

    # Must contain the COVERAGE PLAN header
    assert "# COVERAGE PLAN FOR THIS STAGE" in msg, (
        "Expected '# COVERAGE PLAN FOR THIS STAGE' block when coverage_plan is provided"
    )
    # Each required primary must appear with the REQUIRED PRIMARY: prefix
    assert "REQUIRED PRIMARY: 'Skill A'" in msg, (
        "Expected REQUIRED PRIMARY line for 'Skill A'"
    )
    assert "REQUIRED PRIMARY: 'Skill B'" in msg, (
        "Expected REQUIRED PRIMARY line for 'Skill B'"
    )
    # Secondary-only skill must appear as a secondary-only line
    assert "secondary-only: 'Skill C'" in msg, (
        "Expected secondary-only line for 'Skill C'"
    )
    # Must NOT contain the budget block
    assert "# BUDGET FOR THIS STAGE" not in msg, (
        "BUDGET FOR THIS STAGE block must NOT appear when coverage_plan is provided"
    )


# ---------------------------------------------------------------------------
# Test 2: without coverage_plan (None) — renders BUDGET block, not PLAN block
# ---------------------------------------------------------------------------


def test_build_user_message_without_coverage_plan_renders_budget_block():
    """When coverage_plan=None, the message must contain the BUDGET FOR THIS
    STAGE block and must NOT contain the COVERAGE PLAN FOR THIS STAGE block.
    """
    stage_id = "00000000-0000-0000-0000-000000000001"

    msg = _build_user_message(
        job=_make_job(),
        snapshot=_make_snapshot(),
        company_profile=None,
        stage=_make_stage(stage_id=stage_id),
        pipeline_stages=_make_pipeline_stages(stage_id),
        prior_stages_questions=[],
        coverage_plan=None,
    )

    # Must contain the BUDGET header
    assert "# BUDGET FOR THIS STAGE" in msg, (
        "Expected '# BUDGET FOR THIS STAGE' block when coverage_plan is None"
    )
    # Must NOT contain the COVERAGE PLAN header
    assert "# COVERAGE PLAN FOR THIS STAGE" not in msg, (
        "COVERAGE PLAN FOR THIS STAGE block must NOT appear when coverage_plan is None"
    )
