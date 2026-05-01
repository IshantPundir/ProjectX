"""Assembles the interviewer system prompt from template + session config."""

from __future__ import annotations

import re
from string import Template

from app.ai.prompts import prompt_loader
from app.config import settings
from app.modules.interview_runtime.schemas import SessionConfig

# Matches the first capitalized multi-word name at the start of the about text.
# Examples: "Vectra Pay is …" -> "Vectra Pay", "Acme Corp is …" -> "Acme Corp"
_COMPANY_NAME_RE = re.compile(r"^((?:[A-Z][a-zA-Z0-9]*(?:\s+|$)){1,4})")


def _load_prompt_template() -> str:
    """Load the interviewer prompt template via nexus's prompt_loader.

    The template lives at backend/nexus/prompts/v1/interview/interviewer.txt
    (moved there in Phase 3C.2 Chunk 2, commit 099bd17). The engine reads
    it through the path-installed nexus package — same code path that
    nexus's question-bank actors use for their per-stage prompts.
    """
    return prompt_loader.get("interview/interviewer")


def _extract_company_name(about: str) -> str:
    """Best-effort extraction of the company name from the about blurb.

    Falls back to "the company" if the heuristic doesn't match.
    """
    match = _COMPANY_NAME_RE.match(about.strip())
    if match:
        return match.group(1).strip()
    return "the company"


def build_system_prompt(session_config: SessionConfig) -> str:
    """Assemble the interviewer system prompt.

    Loads the template via nexus's prompt_loader and fills in dynamic
    fields from the session config and nexus settings.

    Uses :class:`string.Template` (``$variable`` syntax) so that the
    JSON examples in the prompt don't need ``{{`` / ``}}`` escaping.
    """
    template = Template(_load_prompt_template())

    questions = session_config.stage.questions
    mandatory_count = sum(1 for q in questions if q.is_mandatory)
    optional_count = len(questions) - mandatory_count

    return template.substitute(
        agent_name=settings.engine_agent_name,
        company_name=_extract_company_name(session_config.company.about),
        company_about=session_config.company.about,
        company_industry=session_config.company.industry,
        company_stage=session_config.company.company_stage,
        company_hiring_bar=session_config.company.hiring_bar,
        job_title=session_config.job_title,
        seniority_level=session_config.seniority_level,
        duration_minutes=session_config.stage.duration_minutes,
        total_questions=len(questions),
        mandatory_count=mandatory_count,
        optional_count=optional_count,
    )
