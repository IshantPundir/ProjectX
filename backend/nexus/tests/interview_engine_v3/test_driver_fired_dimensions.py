"""
Task 8 — driver uses fired_dimensions ledger (not integer _probes_used).

Verifies that after construction + opener, the driver carries
``_fired_dimensions`` (dict[str, list[str]]) rather than the old
``_probes_used`` (dict[str, list[int]]).
"""
from __future__ import annotations

import pytest
from datetime import UTC, datetime

from app.modules.interview_engine.driver import build_session_driver
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    FollowUpDimension,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SignalMetadata,
    StageConfig,
)


class _Voice:
    def __init__(self) -> None:
        self.last_interrupted = False

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        pass


def _config() -> SessionConfig:
    q = QuestionConfig(
        id="q1",
        position=0,
        text="Assess a messy tenant migration?",
        signal_values=["s"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[
            FollowUpDimension(
                dimension="validate_impact",
                intent="Probe whether the candidate validated business impact",
                seed_probe="How did you validate the impact on downstream tenants?",
                listen_for=["metrics", "validation", "rollback"],
            ),
            FollowUpDimension(
                dimension="stage_safely",
                intent="Probe how the candidate staged the rollout safely",
                seed_probe="How did you stage the migration safely?",
                listen_for=["canary", "blue-green", "phased rollout"],
            ),
        ],
        positive_evidence=["a", "b", "c"],
        red_flags=["r1", "r2"],
        rubric=QuestionRubric(
            excellent="e" * 20,
            meets_bar="m" * 20,
            below_bar="b" * 20,
        ),
        evaluation_hint="h" * 12,
        question_kind="technical_scenario",
        primary_signal="s",
        difficulty="medium",
    )
    return SessionConfig(
        session_id="11111111-1111-1111-1111-111111111111",
        job_id="22222222-2222-2222-2222-222222222222",
        candidate_id="33333333-3333-3333-3333-333333333333",
        job_title="EMM Engineer",
        hiring_company_name="Acme",
        role_summary="r" * 5,
        jd_text="jd" * 5,
        seniority_level="mid",
        company=CompanyContext(about="a" * 5, industry="i" * 5, company_stage="", hiring_bar="hb" * 5),
        candidate=CandidateContext(name="Punar"),
        stage=StageConfig(
            stage_id="44444444-4444-4444-4444-444444444444",
            stage_type="ai_screening",
            name="Screen",
            duration_minutes=15,
            difficulty="medium",
            questions=[q],
            advance_behavior="manual_review",
        ),
        signals=["s"],
        signal_metadata=[
            SignalMetadata(
                value="s",
                type="competency",
                priority="required",
                weight=2,
                knockout=False,
                stage="screen",
                evaluation_method="verbal_response",
            )
        ],
        keyterms=[],
    )


@pytest.mark.asyncio
async def test_driver_has_fired_dimensions_ledger_not_probes_used() -> None:
    """After construction + opener, the driver holds _fired_dimensions not _probes_used."""

    async def persist(ev) -> None:  # type: ignore[no-untyped-def]
        pass

    driver = build_session_driver(
        _config(),
        voice=_Voice(),
        persist=persist,
        started_at=datetime.now(UTC),
    )

    # Right after construction — ledger is empty (no questions asked yet)
    assert driver._fired_dimensions == {}  # type: ignore[attr-defined]
    assert not hasattr(driver, "_probes_used")

    # After opener — ledger is seeded for the first question
    await driver.opener()
    assert driver._fired_dimensions == {"q1": []}  # type: ignore[attr-defined]
    assert not hasattr(driver, "_probes_used")
