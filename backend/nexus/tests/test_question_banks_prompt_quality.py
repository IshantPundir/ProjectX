"""Phase 4 — prompt-quality coverage of question_kind selection.

Real-LLM tests. Opt-in tier: run via
    docker compose exec nexus pytest tests/test_question_banks_prompt_quality.py -m prompt_quality

These tests EXERCISE the live OpenAI client and the actual bank-generator
prompts. They are slow and consume tokens. Do NOT include in the default
test gate.

Three assertions:
  1. Phone screen with a UK-shift knockout signal emits at least one
     `compliance_binary` question (and at most one per binary knockout).
  2. AI screening across N=3 independent runs emits ZERO `compliance_binary`
     questions — the hard-ban assertion.
  3. Regen-one preserves the kind when `replace_signal_values` is None.
"""

from __future__ import annotations

import asyncio
import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import prompt_loader
from app.modules.question_bank.schemas import (
    GeneratedQuestion,
    QuestionRubric,
    SingleQuestionOutput,
    StageQuestionBankOutput,
)


pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


def _phone_screen_user_message_with_uk_shift_knockout() -> str:
    """A minimal-but-realistic phone-screen user message featuring a
    UK-shift knockout signal. Mirrors the shape of
    actors.py::_build_user_message but inlined to keep the test
    self-contained."""
    return """# JOB CONTEXT

Job title: Customer Support Engineer (UK hours)
Role summary: Frontline support for our UK-based enterprise customers.
Seniority: mid

# COMPANY PROFILE

about: B2B SaaS serving Fortune 500 retail clients in the UK and EU.
industry: Technology
company_stage: Series C
hiring_bar: standard

# SIGNALS TO ASSESS (pinned snapshot)

- value: 'Available for UK shift (1pm-9pm UK time)'
  type: experience
  priority: required
  weight: 3
  knockout: true
  stage_tag: screen
- value: 'Python'
  type: competency
  priority: preferred
  weight: 2
  knockout: false
  stage_tag: screen
- value: 'Customer support experience'
  type: experience
  priority: required
  weight: 3
  knockout: false
  stage_tag: screen

# PIPELINE CONTEXT

This pipeline has 1 stages. You are generating questions for STAGE 1.

## Stage 1 — Phone Screen (CURRENT — you are generating this)
  Type: phone_screen, Duration: 15 min, Difficulty: medium

# THIS STAGE'S METADATA

Name: Phone Screen
Type: phone_screen
Duration: 15 min
Difficulty: medium
Signal type filter (include_types): ['competency', 'experience', 'credential']
Advance behavior: manual_review

# BUDGET FOR THIS STAGE (HARD CAPS — server-enforced)

Stage duration: 15 min
Mandatory budget cap: 15 min (sum of estimated_minutes across is_mandatory=true questions)
Total budget cap: 20 min (sum across ALL questions, mandatory + optional combined)
Optional buffer: 5 min (reserved for the screening AI's runtime fallback probes)

Eligible signals (after include_types filter):
  - knockouts: 1 (each gets ONE mandatory question)
  - weight=3 non-knockout: 1 (mandatory only if mandatory budget allows; otherwise optional)
  - weight=2: 1 (optional depth probes)
  - weight=1: 0 (skip unless every higher-weight signal is covered AND buffer remains)

Optimize for SIGNAL DENSITY, not question count. Under-using budget by 1-2 minutes is acceptable; padding shallow questions is rejected.

Now generate the structured question bank output as specified in the system instructions.
"""


def _ai_screening_user_message() -> str:
    """A realistic ai_screening user message featuring competency + experience
    signals with no binary-knockout fits — ai_screening should emit only
    technical_depth."""
    return """# JOB CONTEXT

Job title: Senior Backend Engineer
Role summary: Distributed systems on AWS for a fintech platform.
Seniority: senior

# COMPANY PROFILE

about: Fintech platform processing real-time payments at scale.
industry: Financial services
company_stage: Series D
hiring_bar: high

# SIGNALS TO ASSESS (pinned snapshot)

- value: 'Distributed systems design'
  type: competency
  priority: required
  weight: 3
  knockout: true
  stage_tag: interview
- value: 'AWS production experience'
  type: experience
  priority: required
  weight: 3
  knockout: false
  stage_tag: interview
- value: 'Postgres at scale'
  type: competency
  priority: required
  weight: 2
  knockout: false
  stage_tag: interview

# PIPELINE CONTEXT

This pipeline has 1 stages. You are generating questions for STAGE 1.

## Stage 1 — AI Deep Interview (CURRENT — you are generating this)
  Type: ai_screening, Duration: 30 min, Difficulty: hard

# THIS STAGE'S METADATA

Name: AI Deep Interview
Type: ai_screening
Duration: 30 min
Difficulty: hard
Signal type filter (include_types): ['competency', 'experience']
Advance behavior: manual_review

# BUDGET FOR THIS STAGE (HARD CAPS — server-enforced)

Stage duration: 30 min
Mandatory budget cap: 30 min (sum of estimated_minutes across is_mandatory=true questions)
Total budget cap: 35 min (sum across ALL questions, mandatory + optional combined)
Optional buffer: 5 min (reserved for the screening AI's runtime fallback probes)

Eligible signals (after include_types filter):
  - knockouts: 1
  - weight=3 non-knockout: 1
  - weight=2: 1
  - weight=1: 0

Optimize for SIGNAL DENSITY, not question count.

Now generate the structured question bank output as specified in the system instructions.
"""


async def _call_bank_gen(stage_type: str, user_message: str) -> StageQuestionBankOutput:
    """Hit the live LLM for a stage bank, mirroring actors.py composition."""
    system_prompt = prompt_loader.load_pair(
        "question_bank_common", f"question_bank_{stage_type}"
    )
    client = get_openai_client()
    return await client.chat.completions.create(
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        response_model=StageQuestionBankOutput,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_retries=1,
    )


async def test_phone_screen_emits_compliance_binary_for_uk_shift_knockout():
    """Phone screen with a UK-shift knockout signal must emit at least one
    compliance_binary question."""
    output = await _call_bank_gen("phone_screen", _phone_screen_user_message_with_uk_shift_knockout())
    kinds = [q.question_kind for q in output.questions]
    assert "compliance_binary" in kinds, (
        f"phone_screen with UK-shift knockout produced no compliance_binary "
        f"question; kinds={kinds}"
    )
    assert kinds.count("compliance_binary") <= 1, (
        f"phone_screen with one binary knockout should emit at most one "
        f"compliance_binary question; kinds={kinds}"
    )


async def test_ai_screening_never_emits_compliance_binary():
    """Across N=3 independent runs, ai_screening must emit ZERO
    compliance_binary questions — the hard-ban assertion."""
    runs = await asyncio.gather(*[
        _call_bank_gen("ai_screening", _ai_screening_user_message())
        for _ in range(3)
    ])
    all_kinds: list[str] = []
    for output in runs:
        all_kinds.extend(q.question_kind for q in output.questions)
    assert "compliance_binary" not in all_kinds, (
        f"ai_screening violated the BAN: emitted compliance_binary in N=3 "
        f"runs; kinds across all runs={all_kinds}"
    )


async def test_regenerate_one_preserves_kind_when_signals_unchanged():
    """When replace_signal_values is None, regenerate-one preserves the
    original question's question_kind."""
    system_prompt = prompt_loader.load_pair(
        "question_bank_common", "question_bank_regenerate_one"
    )
    user_parts = [
        "# JOB CONTEXT\n\nJob: Customer Support Engineer (UK hours)\nSeniority: mid\n\n",
        "# SIGNALS (pinned snapshot)\n",
        "- 'Available for UK shift (1pm-9pm UK time)' (type: experience, weight: 3, knockout: True)\n",
        "\n# CURRENT QUESTION BEING REPLACED\n",
        "Text: Can you work the UK shift (1pm-9pm UK time)?\n",
        "Probes: ['Available for UK shift (1pm-9pm UK time)']\n",
        "Rubric meets_bar: Candidate confirms availability with concrete reasoning\n",
        "Estimated minutes: 1.5\n",
        "Original question_kind: compliance_binary\n",
        "\n# TARGET SIGNALS (probe these — same as current)\n",
        "- 'Available for UK shift (1pm-9pm UK time)'\n",
        "\n# OTHER QUESTIONS IN THIS STAGE'S BANK — DO NOT DUPLICATE\n",
        "(none)\n",
        "\n# STAGE METADATA\n",
        "Type: phone_screen, Duration: 15 min, Difficulty: medium\n",
        "\nNow generate ONE replacement question as a SingleQuestionOutput.\n",
    ]
    client = get_openai_client()
    result: SingleQuestionOutput = await client.chat.completions.create(
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
        response_model=SingleQuestionOutput,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "".join(user_parts)},
        ],
        max_retries=1,
    )
    assert result.question.question_kind == "compliance_binary", (
        f"regenerate-one failed to preserve compliance_binary kind; "
        f"got {result.question.question_kind}"
    )
