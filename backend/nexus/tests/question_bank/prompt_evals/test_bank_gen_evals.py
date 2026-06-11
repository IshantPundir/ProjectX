"""Bank-gen prompt-quality eval suite for the v2 one-call prompts.

Opt-in tier: run via
    docker compose exec nexus pytest tests/question_bank/prompt_evals -m prompt_quality

These tests hit the REAL OpenAI API (and are therefore SLOW and CONSUME TOKENS).
Do NOT include in the default test gate. The default addopts in pyproject.toml
already excludes them via ``-m 'not prompt_quality'``.

The generator authors the WHOLE bank in ONE streamed call (the behavioral/technical
phase split is gone), so each case generates against the single unified
``question_bank_ai_screening`` prompt.

Assertions:
  1. Every generated question is SPOKEN (≤240 chars), single-focus, and has a valid
     primary_signal, difficulty, and question_kind.
  2. Within one bank, every follow-up `dimension` slug is distinct (the live engine
     fires each dimension at most once — duplicates re-ask the candidate).
  3. No evaluator-only phrasing leaks into the spoken fields (text / seed_probe).
  4. Compliance-knockout cases produce ≥1 compliance_binary question.
  5. Adversarial multi-part-tempting case: lead questions stay single-focus.
  6. Underspecified-role case: signal_values never contain hallucinated strings.
  7. One-of / OR-requirement case: no question collapses a multi-option requirement
     (e.g. "Java, Python, or Ruby") to a single option.
  8. One-call balance: a bank with behavioral-TYPE signals still emits ≥1 true STAR
     `behavioral` question (depth probes must not crowd out behavioral coverage).
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.modules.question_bank.schemas import GeneratedQuestion


pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Test case definition
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class BankGenCase:
    """One eval scenario — all fields for a self-contained one-call bank-gen run."""

    id: str
    role_title: str
    seniority: str
    company_profile: dict[str, str]
    signals: list[dict[str, Any]]
    stage_duration: int   # minutes
    stage_difficulty: str
    # Optional flags that select specific assertion classes for a case.
    adversarial_multi_part: bool = False
    adversarial_compliance_knockout: bool = False
    adversarial_no_hallucination: bool = False
    adversarial_or_requirement: bool = False


def _mk_signal(
    value: str,
    *,
    sig_type: str = "competency",
    priority: str = "required",
    weight: int = 3,
    knockout: bool = False,
    stage_tag: str = "interview",
) -> dict[str, Any]:
    return {
        "value": value,
        "type": sig_type,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage_tag,
    }


# ---------------------------------------------------------------------------
# DIVERSE CASES
# ---------------------------------------------------------------------------

CASES: list[BankGenCase] = [
    # ---- CASE 1: Senior Backend Engineer (happy path) ----
    BankGenCase(
        id="backend_senior_happy",
        role_title="Senior Backend Engineer",
        seniority="senior",
        company_profile={
            "about": "Fintech platform processing real-time payments at scale.",
            "industry": "Financial services",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Distributed systems design", knockout=True),
            _mk_signal("AWS production experience", weight=3),
            _mk_signal("Postgres at scale", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 2: Data Engineer (happy path) ----
    BankGenCase(
        id="data_engineer_mid",
        role_title="Data Engineer",
        seniority="mid",
        company_profile={
            "about": "E-commerce analytics platform with 50TB daily ingestion.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("Apache Spark", weight=3, knockout=True),
            _mk_signal("dbt experience", weight=2),
            _mk_signal("Airflow pipeline management", weight=2),
            _mk_signal("SQL proficiency", weight=1),
        ],
        stage_duration=20,
        stage_difficulty="medium",
    ),
    # ---- CASE 3: ML Engineer ----
    BankGenCase(
        id="ml_engineer_senior",
        role_title="Senior ML Engineer",
        seniority="senior",
        company_profile={
            "about": "AI-first B2B SaaS for enterprise document processing.",
            "industry": "Technology",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("LLM fine-tuning in production", weight=3, knockout=True),
            _mk_signal("PyTorch or TensorFlow", weight=3),
            _mk_signal("MLflow or similar experiment tracking", weight=2),
            _mk_signal("RAG pipeline design", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 4: Frontend Engineer ----
    BankGenCase(
        id="frontend_mid",
        role_title="Frontend Engineer",
        seniority="mid",
        company_profile={
            "about": "Consumer-facing marketplace serving 10M monthly users.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("React production experience", weight=3, knockout=True),
            _mk_signal("TypeScript", weight=3),
            _mk_signal("Performance optimization experience", weight=2),
            _mk_signal("Accessibility (WCAG)", weight=1),
        ],
        stage_duration=15,
        stage_difficulty="medium",
    ),
    # ---- CASE 5: Customer Support Engineer ----
    BankGenCase(
        id="support_engineer_mid",
        role_title="Customer Support Engineer",
        seniority="mid",
        company_profile={
            "about": "B2B SaaS serving Fortune 500 retail clients.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("API debugging experience", weight=3, knockout=True),
            _mk_signal("Customer support experience", sig_type="experience", weight=3),
            _mk_signal("Python scripting", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="medium",
    ),
    # ---- CASE 6: DevOps / SRE ----
    BankGenCase(
        id="sre_senior",
        role_title="Senior Site Reliability Engineer",
        seniority="senior",
        company_profile={
            "about": "Cloud-native infrastructure team for a Series D startup.",
            "industry": "Technology",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Kubernetes in production", weight=3, knockout=True),
            _mk_signal("On-call incident response experience", sig_type="experience", weight=3),
            _mk_signal("Terraform or Pulumi", weight=2),
            _mk_signal("Observability (Datadog / Prometheus)", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 7: Product Manager ----
    BankGenCase(
        id="pm_senior",
        role_title="Senior Product Manager",
        seniority="senior",
        company_profile={
            "about": "Growth-stage B2B SaaS selling to enterprise procurement teams.",
            "industry": "Technology",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Product roadmap ownership", sig_type="experience", weight=3, knockout=True),
            _mk_signal("Cross-functional leadership", sig_type="behavioral", weight=3),
            _mk_signal("Data-driven product decisions", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 8: Security Engineer ----
    BankGenCase(
        id="security_engineer_mid",
        role_title="Application Security Engineer",
        seniority="mid",
        company_profile={
            "about": "Healthcare SaaS with HIPAA and SOC 2 requirements.",
            "industry": "Healthcare",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("SAST / DAST tooling experience", weight=3, knockout=True),
            _mk_signal("Threat modeling", weight=3),
            _mk_signal("Secure SDLC program ownership", sig_type="experience", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 9: iOS Engineer ----
    BankGenCase(
        id="ios_engineer_mid",
        role_title="iOS Software Engineer",
        seniority="mid",
        company_profile={
            "about": "Mobile-first consumer app with 5M daily active users.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("Swift production experience", weight=3, knockout=True),
            _mk_signal("UIKit and SwiftUI", weight=3),
            _mk_signal("App Store submission experience", sig_type="experience", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="medium",
    ),
    # ---- CASE 10: Technical Lead (mix of behavioral + experience) ----
    BankGenCase(
        id="tech_lead_senior",
        role_title="Technical Lead",
        seniority="senior",
        company_profile={
            "about": "Global logistics platform handling 100M events daily.",
            "industry": "Logistics",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Technical leadership of engineering teams", sig_type="experience", weight=3, knockout=True),
            _mk_signal("Mentoring junior engineers", sig_type="behavioral", weight=3),
            _mk_signal("Architecture decision ownership", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 11: Low-seniority support analyst ----
    BankGenCase(
        id="support_analyst_junior",
        role_title="IT Support Analyst",
        seniority="junior",
        company_profile={
            "about": "Managed IT services provider for SMB customers.",
            "industry": "IT Services",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("Help desk ticket resolution", sig_type="experience", weight=3, knockout=True),
            _mk_signal("Windows and Active Directory", weight=2),
            _mk_signal("Customer-facing communication", sig_type="behavioral", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="easy",
    ),
    # ---- CASE 12: Executive hire ----
    BankGenCase(
        id="vp_engineering",
        role_title="VP Engineering",
        seniority="executive",
        company_profile={
            "about": "Series C B2B SaaS, 80-person engineering org.",
            "industry": "Technology",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Engineering org leadership at 50+ headcount", sig_type="experience", weight=3, knockout=True),
            _mk_signal("Budget ownership and hiring plan", sig_type="experience", weight=3),
            _mk_signal("Cross-department executive collaboration", sig_type="behavioral", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 13: Recruiter (non-engineering) ----
    BankGenCase(
        id="recruiter_mid",
        role_title="Technical Recruiter",
        seniority="mid",
        company_profile={
            "about": "In-house talent function at a 300-person tech company.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("Full-cycle recruiting experience", sig_type="experience", weight=3, knockout=True),
            _mk_signal("Engineering candidate sourcing", sig_type="experience", weight=3),
            _mk_signal("ATS administration (Greenhouse or Lever)", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="medium",
    ),
    # ---- CASE 14: ADVERSARIAL — multi-part-tempting signal mix ----
    # High-complexity role with many interdependent signals. Purpose: verify
    # that the prompt resists the temptation to bundle them into one question.
    BankGenCase(
        id="adversarial_multi_part",
        role_title="Staff Infrastructure Engineer",
        seniority="staff",
        company_profile={
            "about": "Global payments processor running on multi-region Kubernetes.",
            "industry": "Financial services",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Kubernetes cluster design and operations", weight=3, knockout=True),
            _mk_signal("Service mesh (Istio or Linkerd)", weight=3),
            _mk_signal("CI/CD pipeline architecture (Argo or Tekton)", weight=3),
            _mk_signal("Multi-region failover design", weight=3),
            _mk_signal("Secrets management (Vault or AWS Secrets Manager)", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
        adversarial_multi_part=True,
    ),
    # ---- CASE 15: ADVERSARIAL — UK-shift compliance knockout ----
    BankGenCase(
        id="adversarial_uk_shift_compliance",
        role_title="Customer Success Manager (UK hours)",
        seniority="mid",
        company_profile={
            "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal(
                "Available for UK shift (1pm-9pm UK time)",
                sig_type="experience",
                weight=3,
                knockout=True,
            ),
            _mk_signal("Enterprise customer success experience", sig_type="experience", weight=3),
            _mk_signal("CRM proficiency (Salesforce or HubSpot)", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="medium",
        adversarial_compliance_knockout=True,
    ),
    # ---- CASE 16: ADVERSARIAL — underspecified role / no-hallucination ----
    # Only two vague signals. The generator must NOT invent extra signal_values
    # not present in the snapshot.
    BankGenCase(
        id="adversarial_underspecified",
        role_title="Growth Specialist",
        seniority="mid",
        company_profile={
            "about": "Early-stage startup (seed round) building a consumer app.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal("Growth hacking experience", sig_type="experience", weight=3, knockout=True),
            _mk_signal("Data analysis skills", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="medium",
        adversarial_no_hallucination=True,
    ),
    # ---- CASE 17: Work-authorization compliance knockout (US) ----
    BankGenCase(
        id="work_auth_us_compliance",
        role_title="Software Engineer",
        seniority="mid",
        company_profile={
            "about": "Series B startup that cannot sponsor visas.",
            "industry": "Technology",
            "hiring_bar": "standard",
        },
        signals=[
            _mk_signal(
                "Authorized to work in the US without sponsorship",
                sig_type="experience",
                weight=3,
                knockout=True,
            ),
            _mk_signal("Python backend experience", weight=3),
            _mk_signal("REST API design", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="medium",
        adversarial_compliance_knockout=True,
    ),
    # ---- CASE 18: Behavioral-only signals (no compliance, no technical) ----
    BankGenCase(
        id="behavioral_leadership_signals",
        role_title="Engineering Manager",
        seniority="senior",
        company_profile={
            "about": "Scaled-up SaaS company rebuilding its eng culture post-hypergrowth.",
            "industry": "Technology",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal("Managing underperformance on a team", sig_type="behavioral", weight=3, knockout=True),
            _mk_signal("Stakeholder alignment across product and engineering", sig_type="behavioral", weight=3),
            _mk_signal("Hiring and leveling decisions", sig_type="behavioral", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
    ),
    # ---- CASE 19: Mixed credential + experience knockout ----
    BankGenCase(
        id="credentialed_role",
        role_title="Clinical Data Analyst",
        seniority="mid",
        company_profile={
            "about": "Medical device company pursuing FDA clearance.",
            "industry": "Healthcare",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal(
                "Active CDISC or SAS certification",
                sig_type="credential",
                weight=3,
                knockout=True,
            ),
            _mk_signal("Clinical trial data analysis", sig_type="experience", weight=3),
            _mk_signal("21 CFR Part 11 compliance experience", sig_type="experience", weight=2),
        ],
        stage_duration=15,
        stage_difficulty="medium",
        adversarial_compliance_knockout=True,
    ),
    # ---- CASE 20: ADVERSARIAL — one-of / OR language knockout ----
    # Mirrors the real Workato defect: a "Java, Python, or Ruby" knockout that
    # the generator collapsed to a single language (Ruby), falsely failing a
    # candidate proficient in Java or Python. The generator must NOT name a
    # single language without framing it as the candidate's choice.
    BankGenCase(
        id="adversarial_or_language_knockout",
        role_title="Workato Integration Engineer",
        seniority="mid",
        company_profile={
            "about": "Enterprise iPaaS team building Workato recipes at scale.",
            "industry": "Technology",
            "hiring_bar": "high",
        },
        signals=[
            _mk_signal(
                "Proficiency in at least one programming language: Java, Python, or Ruby",
                weight=3,
                knockout=True,
            ),
            _mk_signal("Workato recipe development", sig_type="experience", weight=3),
            _mk_signal("REST API integration", weight=2),
        ],
        stage_duration=20,
        stage_difficulty="hard",
        adversarial_or_requirement=True,
    ),
]

# Map case id → case for lookup
_CASE_BY_ID: dict[str, BankGenCase] = {c.id: c for c in CASES}

# The four valid question_kind values (schema matches GeneratedQuestion.question_kind)
_VALID_QUESTION_KINDS = {
    "experience_check",
    "behavioral",
    "technical_scenario",
    "compliance_binary",
}


# ---------------------------------------------------------------------------
# User message builder (self-contained, mirrors actors.py::_build_user_message)
# ---------------------------------------------------------------------------

def _build_user_message(case: BankGenCase) -> str:
    """Build a self-contained user message in the shape of actors.py::_build_user_message.

    Inlined here so the eval suite is decoupled from the production actor.
    """
    parts: list[str] = []

    parts.append("# JOB CONTEXT\n\n")
    parts.append(f"Job title: {case.role_title}\n")
    parts.append(f"Seniority: {case.seniority}\n")

    if case.company_profile:
        parts.append("\n# COMPANY PROFILE\n\n")
        for key in ("about", "industry", "hiring_bar"):
            if key in case.company_profile:
                parts.append(f"{key}: {case.company_profile[key]}\n")

    parts.append("\n# SIGNALS TO ASSESS (pinned snapshot)\n\n")
    parts.append(
        "Each signal is listed with its metadata. Use the `value` field exactly "
        "as-is in your question's `signal_values` output.\n\n"
    )
    for signal in case.signals:
        parts.append(
            f"- value: {signal['value']!r}\n"
            f"  type: {signal['type']}\n"
            f"  priority: {signal['priority']}\n"
            f"  weight: {signal['weight']}\n"
            f"  knockout: {signal.get('knockout', False)}\n"
            f"  stage_tag: {signal['stage']}\n"
        )

    parts.append("\n# PIPELINE CONTEXT\n\n")
    parts.append("This pipeline has 1 stage. You are generating questions for STAGE 1.\n\n")
    parts.append(
        f"## Stage 1 — AI Interview (CURRENT — you are generating this)\n"
        f"  Type: ai_screening, Duration: {case.stage_duration} min, "
        f"Difficulty: {case.stage_difficulty}\n"
    )

    parts.append("\n# THIS STAGE'S METADATA\n\n")
    parts.append(
        f"Name: AI Interview\n"
        f"Type: ai_screening\n"
        f"Duration: {case.stage_duration} min\n"
        f"Difficulty: {case.stage_difficulty}\n"
        f"Signal type filter (include_types): ['competency', 'experience', 'credential', 'behavioral']\n"
        f"Advance behavior: manual_review\n"
    )

    # Budget block (soft guidance)
    eligible_knockouts = [s for s in case.signals if s.get("knockout", False)]
    eligible_w3 = [
        s for s in case.signals
        if int(s.get("weight", 1)) == 3 and not s.get("knockout", False)
    ]
    eligible_w2 = [s for s in case.signals if int(s.get("weight", 1)) == 2]
    eligible_w1 = [s for s in case.signals if int(s.get("weight", 1)) == 1]

    parts.append(
        "\n# BUDGET FOR THIS STAGE "
        "(soft guidance — optimize for signal density, not count)\n\n"
        f"Stage duration: {case.stage_duration} min\n\n"
        f"Eligible signals (after include_types filter):\n"
        f"  - knockouts: {len(eligible_knockouts)} (each warrants ONE mandatory question)\n"
        f"  - weight=3 non-knockout: {len(eligible_w3)} (high-priority depth probes)\n"
        f"  - weight=2: {len(eligible_w2)} (depth probes)\n"
        f"  - weight=1: {len(eligible_w1)} (only if every higher-weight signal is covered)\n\n"
        f"Optimize for SIGNAL DENSITY, not question count. "
        f"Under-using the budget is fine; padding shallow questions is not.\n"
    )

    parts.append(
        "\nNow generate the structured question bank output as specified "
        "in the system instructions.\n"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Core generation helper — ONE streamed call against the unified ai_screening prompt
# ---------------------------------------------------------------------------

async def _generate(case: BankGenCase) -> list[GeneratedQuestion]:
    """Generate a bank for one case via the real LLM and return the questions.

    Loads the unified one-call v2 prompt pair, builds the user message, and
    collects all streaming GeneratedQuestion objects.
    """
    loader = PromptLoader(version=ai_config.question_bank_prompt_version)
    system_prompt = loader.load_pair("question_bank_common", "question_bank_ai_screening")

    client = get_openai_client()
    call_kwargs: dict[str, Any] = dict(
        model=ai_config.question_bank_model,
        response_model=GeneratedQuestion,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_message(case)},
        ],
        max_retries=1,
    )
    if ai_config.question_bank_effort:
        call_kwargs["reasoning_effort"] = ai_config.question_bank_effort

    questions: list[GeneratedQuestion] = [
        q async for q in client.chat.completions.create_iterable(**call_kwargs)
    ]
    return questions


# ---------------------------------------------------------------------------
# Test 1 — spoken / single-focus contract (parametrized over ALL cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_questions_are_spoken_single_focus(case: BankGenCase) -> None:
    """Every generated question must satisfy the spoken, single-focus contract.

    Assertions per question:
      - text ≤ 240 chars (schema max)
      - single-ask heuristic: if " and " appears, question mark count must be ≤ 1
        NOTE: the " and "/" ?-count check below is a CRUDE placeholder for an
        eventual LLM-grader. Ship the heuristic now; it catches obvious failures
        in an opt-in suite where prompt iteration is the cost of a miss.
      - primary_signal is non-empty and is present in signal_values
      - follow_ups is a list (may be empty)
      - difficulty is one of {easy, medium, hard}
      - question_kind is one of the four valid values
    """
    questions = await _generate(case)
    assert questions, f"[{case.id}] generator returned zero questions"

    for q in questions:
        assert len(q.text) <= 240, (
            f"[{case.id}] question text exceeds 240 chars: {q.text!r}"
        )

        # Single-ask heuristic: tolerate "and" only when there's at most one "?"
        if " and " in q.text.lower():
            q_count = q.text.count("?")
            assert q_count <= 1, (
                f"[{case.id}] multi-part question suspected — "
                f"contains ' and ' with {q_count} question marks: {q.text!r}"
            )

        assert q.primary_signal, (
            f"[{case.id}] primary_signal is empty for: {q.text!r}"
        )
        assert q.primary_signal in q.signal_values, (
            f"[{case.id}] primary_signal {q.primary_signal!r} not in "
            f"signal_values {q.signal_values!r} for: {q.text!r}"
        )

        assert isinstance(q.follow_ups, list), (
            f"[{case.id}] follow_ups is not a list: {q.follow_ups!r}"
        )

        assert q.difficulty in {"easy", "medium", "hard"}, (
            f"[{case.id}] difficulty {q.difficulty!r} not in {{easy, medium, hard}} "
            f"for: {q.text!r}"
        )

        assert q.question_kind in _VALID_QUESTION_KINDS, (
            f"[{case.id}] question_kind {q.question_kind!r} not in "
            f"{_VALID_QUESTION_KINDS} for: {q.text!r}"
        )


# ---------------------------------------------------------------------------
# Test 2 — within-bank follow-up dimension distinctness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_follow_up_dimensions_are_distinct_within_bank(case: BankGenCase) -> None:
    """Every follow-up `dimension` slug across the whole bank must be unique.

    The live engine fires each dimension at most once per session and tracks
    coverage across the whole screen — a repeated slug re-asks the candidate the
    same thing and breaks cross-candidate report comparability. (Slug-level check;
    semantic near-duplicates need an LLM-grader, out of scope for this fast gate.)
    """
    questions = await _generate(case)
    dims: list[str] = [
        fu.dimension for q in questions for fu in q.follow_ups
    ]
    duplicates = {d for d in dims if dims.count(d) > 1}
    assert not duplicates, (
        f"[{case.id}] repeated follow-up dimension slug(s) across the bank: "
        f"{sorted(duplicates)} (all dims: {dims})"
    )


# ---------------------------------------------------------------------------
# Test 3 — evaluator-only phrasing never leaks into spoken fields
# ---------------------------------------------------------------------------

_RUBRIC_LEAK_PHRASES = [
    "rubric",
    "red flag",
    "positive_evidence",
    "we're looking for",
    "we are looking for",
    "meets_bar",
]

@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_rubric_never_leaks_into_text(case: BankGenCase) -> None:
    """No spoken field should contain evaluator-only phrasing.

    The spoken fields (the lead `text` and each follow-up `seed_probe`) are read
    aloud to the candidate. Rubric framing must NEVER appear there — it belongs
    exclusively in rubric / positive_evidence / red_flags / evaluation_hint.
    """
    questions = await _generate(case)
    for q in questions:
        spoken_parts = [q.text] + [fu.seed_probe for fu in q.follow_ups]
        for part in spoken_parts:
            part_lower = part.lower()
            for banned in _RUBRIC_LEAK_PHRASES:
                assert banned not in part_lower, (
                    f"[{case.id}] rubric phrase {banned!r} leaked into spoken "
                    f"field: {part!r}"
                )


# ---------------------------------------------------------------------------
# Test 4 — compliance knockout cases produce ≥1 compliance_binary question
# ---------------------------------------------------------------------------

_COMPLIANCE_CASES = [c for c in CASES if c.adversarial_compliance_knockout]


@pytest.mark.parametrize("case", _COMPLIANCE_CASES, ids=[c.id for c in _COMPLIANCE_CASES])
async def test_compliance_knockout_emits_binary(case: BankGenCase) -> None:
    """A case with a knockout signal must produce ≥1 compliance_binary question."""
    questions = await _generate(case)
    kinds = [q.question_kind for q in questions]
    assert "compliance_binary" in kinds, (
        f"[{case.id}] has a knockout signal but produced no compliance_binary "
        f"question; kinds={kinds}"
    )
    # At most one compliance_binary per knockout signal (no bundling)
    knockout_count = sum(
        1 for s in case.signals if s.get("knockout", False)
    )
    compliance_count = kinds.count("compliance_binary")
    assert compliance_count <= knockout_count, (
        f"[{case.id}] generated {compliance_count} compliance_binary questions "
        f"but only has {knockout_count} knockout signals"
    )


# ---------------------------------------------------------------------------
# Test 5 — adversarial multi-part: lead questions stay single-focus
# ---------------------------------------------------------------------------

_MULTIPART_CASES = [c for c in CASES if c.adversarial_multi_part]


@pytest.mark.parametrize("case", _MULTIPART_CASES, ids=[c.id for c in _MULTIPART_CASES])
async def test_adversarial_multipart_temptation_stays_single_focus(case: BankGenCase) -> None:
    """A dense multi-signal case must not produce multi-part leads.

    This tests the scenario where many interdependent signals tempt the model to
    bundle them: "design X, handling Y, Z, and W — and how would you test it?"
    Each lead must be a single clean ask.
    """
    questions = await _generate(case)
    violations: list[str] = []
    for q in questions:
        # Multiple question marks in the lead is a strong multi-part signal
        if q.text.count("?") > 1:
            violations.append(f"MULTI-QUESTION-MARK: {q.text!r}")
        # Bulleted/enumerated structure in lead text
        if re.search(r"[\n\r]|\d+\.\s|\-\s{2}", q.text):
            violations.append(f"STRUCTURED-LEAD: {q.text!r}")

    assert not violations, (
        f"[{case.id}] adversarial multi-part case produced multi-part leads:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 6 — adversarial no-hallucination: signal_values ∈ provided set
# ---------------------------------------------------------------------------

_HALLUCINATION_CASES = [c for c in CASES if c.adversarial_no_hallucination]
_HALLUCINATION_CASE_IDS = [c.id for c in _HALLUCINATION_CASES]


@pytest.mark.parametrize(
    "case", _HALLUCINATION_CASES, ids=_HALLUCINATION_CASE_IDS
)
async def test_no_hallucinated_signal_values(case: BankGenCase) -> None:
    """Every emitted signal_value must exactly match a value from the snapshot.

    Underspecified-role cases tempt the model to invent extra signal strings
    not present in the snapshot. The schema validator already enforces signal
    strings match verbatim — this test confirms the prompt+schema combination
    actually catches hallucination in practice.
    """
    provided_signal_values: set[str] = {s["value"] for s in case.signals}
    questions = await _generate(case)

    for q in questions:
        for sv in q.signal_values:
            assert sv in provided_signal_values, (
                f"[{case.id}] hallucinated signal_value {sv!r} not in the "
                f"provided snapshot {provided_signal_values!r}. "
                f"Question text: {q.text!r}"
            )


# ---------------------------------------------------------------------------
# Test 7 — one-of / OR-requirement is never collapsed to a single option
# ---------------------------------------------------------------------------

_OR_REQUIREMENT_CASES = [c for c in CASES if c.adversarial_or_requirement]
_OR_REQUIREMENT_CASE_IDS = [c.id for c in _OR_REQUIREMENT_CASES]

# Words that signal the question is leaving the option up to the candidate
# rather than hard-coding one. If a question names exactly one language but
# also contains one of these, it's framing the choice as the candidate's.
CHOICE_WORDS = (
    "which",
    "whichever",
    "your language",
    "either",
    "one of",
    "you're strongest",
    "you are strongest",
)


@pytest.mark.parametrize(
    "case", _OR_REQUIREMENT_CASES, ids=_OR_REQUIREMENT_CASE_IDS
)
async def test_or_requirement_not_collapsed_to_single_option(case: BankGenCase) -> None:
    """A one-of / OR knockout must not be collapsed to a single option.

    Mirrors the Workato defect: a "Java, Python, or Ruby" knockout that the
    generator collapsed to Ruby, falsely failing a Python/Java expert. A
    candidate satisfies the requirement with ANY ONE option, so a question that
    hard-codes one language grades against the wrong one.

    CRUDE HEURISTIC (pending an LLM-grader): for each generated question, if its
    text names exactly ONE of {java, python, ruby} and does NOT frame it as the
    candidate's choice (via a CHOICE_WORDS marker), that's an OR-collapse → fail.
    Naming zero or all three is fine; naming one is only fine when the candidate
    gets to pick.
    """
    questions = await _generate(case)
    assert questions, f"[{case.id}] generator returned zero questions"

    for q in questions:
        t = q.text.lower()
        named = [lang for lang in ("java", "python", "ruby") if lang in t]
        assert len(named) != 1 or any(w in t for w in CHOICE_WORDS), (
            f"[{case.id}] OR-requirement collapsed to a single language: {q.text!r}"
        )


# ---------------------------------------------------------------------------
# Test 8 — one-call balance: behavioral STAR is not crowded out by depth probes
# ---------------------------------------------------------------------------

# A case whose signals include behavioral-TYPE required signals (collaboration /
# documentation / mentoring) alongside an experience knockout. In one call the
# model must self-balance — depth probes must NOT crowd out behavioral coverage.
# This is the key one-call risk the unified prompt's authoring recipe targets.
_BEHAVIORAL_STAR_CASE = BankGenCase(
    id="one_call_star_coverage",
    role_title="Senior Integration Engineer",
    seniority="senior",
    company_profile={
        "about": "Enterprise iPaaS team building integrations for Fortune 500 clients.",
        "industry": "Technology",
        "hiring_bar": "high",
    },
    signals=[
        _mk_signal(
            "4+ years integration engineering",
            sig_type="experience",
            weight=3,
            knockout=True,
        ),
        _mk_signal(
            "Cross-functional collaboration with product and support",
            sig_type="behavioral",
            priority="required",
            weight=3,
        ),
        _mk_signal(
            "Technical documentation ownership",
            sig_type="behavioral",
            priority="required",
            weight=2,
        ),
        _mk_signal(
            "Mentoring and technical guidance of junior engineers",
            sig_type="behavioral",
            priority="required",
            weight=2,
        ),
    ],
    stage_duration=20,
    stage_difficulty="hard",
)


async def test_one_call_emits_a_star_question_for_behavioral_signals() -> None:
    """When behavioral-TYPE signals are present, the single call must still emit at
    least one true STAR `behavioral` question — depth probes must not crowd out
    behavioral coverage (the key one-call balance risk)."""
    questions = await _generate(_BEHAVIORAL_STAR_CASE)
    assert questions, "generator returned zero questions"

    kinds = [q.question_kind for q in questions]
    assert "behavioral" in kinds, (
        "one-call generation produced no true STAR `behavioral` question despite "
        f"behavioral-type signals being present; kinds={kinds}"
    )
