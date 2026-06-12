import uuid
import pytest
from app.modules.question_bank import critic as critic_mod
from app.modules.question_bank.schemas import (
    BankCritiqueOutput, GeneratedQuestion, QuestionRubric, FollowUpDimension,
)

pytestmark = pytest.mark.asyncio


def _q(text="Tell me about a project you drove.", kind="project_deepdive"):
    return GeneratedQuestion(
        position=0, text=text, primary_signal="X", signal_values=["X"],
        estimated_minutes=5.0, is_mandatory=False,
        follow_ups=[FollowUpDimension(dimension="d", intent="i",
                    seed_probe="What did you choose it over?", listen_for=["a tradeoff"])],
        positive_evidence=["a", "b", "c"], red_flags=["says we", "no tradeoff"],
        rubric=QuestionRubric(excellent="x"*20, meets_bar="y"*20, below_bar="z"*20),
        evaluation_hint="tests ownership", question_kind=kind,
    )


async def test_run_bank_critic_returns_corrected_bank(monkeypatch):
    corrected = BankCritiqueOutput(critique="added a knockout question", questions=[_q()])

    async def fake_completion(**kwargs):
        return corrected

    monkeypatch.setattr(critic_mod, "_create_critic_completion", fake_completion)

    out, log = await critic_mod.run_bank_critic(
        draft=[_q()],
        seniority="senior", role_title="Staff Engineer",
        signals=[{"value": "X", "type": "competency", "priority": "required",
                  "weight": 3, "knockout": True, "stage": "interview"}],
        stage_difficulty="hard", stage_duration=20,
        bank_id=uuid.uuid4(), tenant_id=uuid.uuid4(), job_id=uuid.uuid4(),
    )
    assert log == "added a knockout question"
    assert out[0].question_kind == "project_deepdive"


async def test_run_bank_critic_raises_on_llm_failure(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(critic_mod, "_create_critic_completion", boom)

    with pytest.raises(RuntimeError):
        await critic_mod.run_bank_critic(
            draft=[_q()], seniority="senior", role_title="x", signals=[],
            stage_difficulty="hard", stage_duration=20,
            bank_id=uuid.uuid4(), tenant_id=uuid.uuid4(), job_id=uuid.uuid4(),
        )
