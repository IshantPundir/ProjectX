# tests/jd/prompt_evals/test_signal_extraction_evals.py
"""Signal-extraction prompt-quality eval (v2). Opt-in, real API.
Run: docker compose exec nexus pytest tests/jd/prompt_evals -m prompt_quality
"""
from __future__ import annotations
import dataclasses
import pytest

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.schemas import SignalExtractionOutput

pytestmark = [pytest.mark.prompt_quality, pytest.mark.asyncio]


@dataclasses.dataclass
class JDCase:
    id: str
    company: dict
    jd: str


WORKATO_JD = JDCase(
    id="workato_integration_engineer",
    company={"about": "Enterprise IT automation", "industry": "Technology", "hiring_bar": "high"},
    jd="""Job Title: AI Integration Engineer - Workato Specialist
Total Experience Required: 4+ years
Relevant Experience: At least 1 year hands-on with Workato
Must-Have Skills:
- Minimum 1 year hands-on with Workato
- AI engineering with a focus on agent-based systems
- Designing and implementing AI-driven workflows
- Integration project implementation
- APIs (RESTful, SOAP/XML) and data structures (JSON)
- Automation technologies and middleware
- At least one programming language: Java, Python, or Ruby
- RDBMS or NoSQL databases
Good-to-Have: TIBCO/Dell Boomi/MuleSoft; iPaaS/SaaS; Workday/NetSuite/Salesforce; microservices; BPM/RPA
Key Responsibilities: design AI-driven workflows; lead integration projects; collaborate cross-functionally;
develop APIs/connectors; monitor/troubleshoot/optimize; document; provide technical guidance; stay current.
Education: BTech/BE or higher in CS/AI/ML or related field; certifications a plus.""",
)

CASES = [WORKATO_JD]


async def _extract(case: JDCase) -> SignalExtractionOutput:
    loader = PromptLoader(version=ai_config.jd_signal_extraction_prompt_version)
    system = loader.get("jd_signal_extraction")
    user = (f"## Company Profile\n- About: {case.company['about']}\n"
            f"- Industry: {case.company['industry']}\n- Hiring bar: {case.company['hiring_bar']}\n\n"
            f"## Job Description\n\n{case.jd}\n")
    client = get_openai_client()
    kw = dict(model=ai_config.extraction_model, response_model=SignalExtractionOutput,
              messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
              max_retries=1)
    if ai_config.extraction_effort:
        kw["reasoning_effort"] = ai_config.extraction_effort
    return await client.chat.completions.create(**kw)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_extraction_is_lean(case):
    out = await _extract(case)
    n = len(out.signals.signals)
    assert n <= 13, f"[{case.id}] too many signals ({n}); should be lean (~8-10)"


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_eligibility_facts_classified_eligibility(case):
    out = await _extract(case)
    elig_vals = [s.value.lower() for s in out.signals.signals if s.purpose == "eligibility"]
    skill_vals = [s.value.lower() for s in out.signals.signals if s.purpose == "skill"]
    leak = [v for v in skill_vals if "year" in v or "btech" in v or "degree" in v or "certification" in v]
    assert not leak, f"[{case.id}] eligibility facts mis-classified as skill: {leak}"
    assert any("year" in v for v in elig_vals), f"[{case.id}] tenure not captured as eligibility"


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_has_a_skill_and_core_musthaves_weighted(case):
    out = await _extract(case)
    skills = [s for s in out.signals.signals if s.purpose == "skill"]
    assert skills, f"[{case.id}] no skill signals"
    core = [s for s in skills if any(k in s.value.lower() for k in ("workato", "api", "ai-driven", "agent", "integration"))]
    assert any(s.weight >= 2 for s in core), f"[{case.id}] core skills under-weighted"
