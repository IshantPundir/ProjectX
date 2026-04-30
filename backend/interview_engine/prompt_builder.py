"""Assembles the interviewer system prompt from template + session config."""

from __future__ import annotations

import re
from string import Template

from config import InterviewEngineConfig
from models import SessionConfig

# Matches the first capitalized multi-word name at the start of the about text.
# Examples: "Vectra Pay is …" -> "Vectra Pay", "Acme Corp is …" -> "Acme Corp"
_COMPANY_NAME_RE = re.compile(r"^((?:[A-Z][a-zA-Z0-9]*(?:\s+|$)){1,4})")


def _load_prompt_template() -> str:
    """Load the interviewer prompt template.

    NOTE (Phase 3C.2 — Chunk 2): The prompt source moved to
    `backend/nexus/prompts/v1/interview/interviewer.txt` (commit 099bd17).
    Chunk 5 Task 5.9 rewrites this module to read via nexus's
    `prompt_loader.get("interview/interviewer")`. Until then, this engine
    is intentionally non-functional — running the worker against a real
    LiveKit dispatch will raise this NotImplementedError on the first
    session, which is the desired loud failure.
    """
    raise NotImplementedError(
        "interview_engine prompt loading is broken between Phase 3C.2 "
        "Chunks 2 and 5. The prompt file was moved to nexus's prompt_loader "
        "convention; this module will be rewritten in Chunk 5 Task 5.9 to "
        "use prompt_loader.get('interview/interviewer'). Do not run the "
        "engine container until Chunk 5 lands."
    )


def _extract_company_name(about: str) -> str:
    """Best-effort extraction of the company name from the about blurb.

    Falls back to "the company" if the heuristic doesn't match.
    """
    match = _COMPANY_NAME_RE.match(about.strip())
    if match:
        return match.group(1).strip()
    return "the company"


def build_system_prompt(
    session_config: SessionConfig,
    engine_config: InterviewEngineConfig,
) -> str:
    """Assemble the interviewer system prompt.

    Loads the template from nexus's prompt_loader and fills in
    dynamic fields from the session and engine configs.

    Uses :class:`string.Template` (``$variable`` syntax) so that the
    JSON examples in the prompt don't need ``{{`` / ``}}`` escaping.

    NOTE (Phase 3C.2 — Chunk 2): Prompt loading is intentionally broken
    until Chunk 5 Task 5.9 rewrites this module. See `_load_prompt_template`.
    """
    template = Template(_load_prompt_template())

    questions = session_config.stage.questions
    mandatory_count = sum(1 for q in questions if q.is_mandatory)
    optional_count = len(questions) - mandatory_count

    return template.substitute(
        agent_name=engine_config.agent_name,
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
