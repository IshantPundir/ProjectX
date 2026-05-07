"""Engine test fixtures: SessionConfig factory, JudgeOutput factory."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, ProbePayload, TurnMetadata,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext, CompanyContext, QuestionConfig, QuestionRubric,
    SessionConfig, SignalMetadata, StageConfig,
)


@pytest.fixture
def make_question():
    def _factory(
        qid: str = "q1", position: int = 0, mandatory: bool = True,
        text: str = "Tell me about your work on this topic.",
        signal_values: list[str] | None = None,
        follow_ups: list[str] | None = None,
    ) -> QuestionConfig:
        return QuestionConfig(
            id=qid, position=position, text=text,
            signal_values=signal_values or ["S1"],
            estimated_minutes=2.0, is_mandatory=mandatory,
            follow_ups=follow_ups or [],
            positive_evidence=["a-anchor", "b-anchor", "c-anchor"],
            red_flags=["x-flag", "y-flag"],
            rubric=QuestionRubric(excellent="ex", meets_bar="mb", below_bar="bb"),
            evaluation_hint="hint hint hint hint hint",
            question_kind="technical_depth",
        )
    return _factory


@pytest.fixture
def make_session_config(make_question):
    def _factory(
        questions: list[QuestionConfig] | None = None,
        signals: list[str] | None = None,
        knockout_signal: str | None = None,
        duration_minutes: int = 10,
    ) -> SessionConfig:
        if questions is None:
            questions = [make_question()]
        if signals is None:
            signals = ["S1"]
        signal_metadata = []
        for v in signals:
            signal_metadata.append(SignalMetadata(
                value=v, type="competency", priority="required", weight=3,
                knockout=(v == knockout_signal),
                stage="screen", evaluation_method="verbal_response",
            ))
        return SessionConfig(
            session_id="sess-test", job_id="job-test", candidate_id="cand-test",
            job_title="SRE", role_summary="role role role role role", seniority_level="Senior",
            company=CompanyContext(
                about="A test company that builds great software for SREs everywhere.",
                industry="software",
                company_stage="growth",
                hiring_bar="We hire engineers who can ship and own outcomes.",
            ),
            candidate=CandidateContext(name="Alice"),
            stage=StageConfig(
                stage_id="stg-test", name="AI Screening",
                stage_type="ai_screening",
                difficulty="medium",
                duration_minutes=duration_minutes,
                questions=questions,
            ),
            signals=signals, signal_metadata=signal_metadata,
        )
    return _factory


@pytest.fixture
def make_judge_output():
    def _factory(
        action: NextAction = NextAction.advance,
        target: str = "q1",
        probe_id: str = "0",
        probe_rationale: str = "r",
        observations: list | None = None,
        claims: list | None = None,
    ) -> JudgeOutput:
        if action == NextAction.advance:
            payload = AdvancePayload(target_question_id=target)
        elif action == NextAction.probe:
            payload = ProbePayload(probe_id=probe_id, probe_rationale=probe_rationale)
        else:
            raise ValueError(f"factory does not support {action}; build directly")
        return JudgeOutput(
            thought="t",
            observations=observations or [],
            candidate_claims=claims or [],
            next_action=action,
            next_action_payload=payload,
            turn_metadata=TurnMetadata(),
        )
    return _factory


@pytest.fixture
def sample_session_config_path() -> Path:
    return Path(__file__).parent / "fixtures" / "sample_session_config.json"


@pytest.fixture
def sample_session_config(sample_session_config_path) -> SessionConfig:
    return SessionConfig.model_validate_json(sample_session_config_path.read_text())
