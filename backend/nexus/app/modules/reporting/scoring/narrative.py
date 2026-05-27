"""Layer 3 — prose-only narrative (LLM). Handed the final numbers as fixed ground truth."""
from __future__ import annotations

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.schemas import NarrativeOut

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


async def write_narrative(*, ground_truth_json: str, correlation_id: str) -> NarrativeOut:
    """ground_truth_json: a compact JSON string with job_title, signals[], scores,
    verdict, questions[] (see service.py:_narrative_ground_truth)."""
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/narrative"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"<report_data>\n{ground_truth_json}\n</report_data>"},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_narrative_model,
        "input": messages,
        "text_format": NarrativeOut,
        "prompt_cache_key": (
            f"narrative:{ai_config.report_scorer_prompt_version}:{ai_config.report_narrative_model}"
        ),
    }
    if ai_config.report_narrative_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_narrative_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(prompt_name="report_narrative",
                                prompt_version=ai_config.report_scorer_prompt_version,
                                correlation_id=correlation_id)
        response = await get_raw_openai_client().responses.parse(**kwargs)

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.narrative.refusal", correlation_id=correlation_id)
        from app.modules.reporting.schemas import DecisionOut, MethodologyOut, WhyColumn
        return NarrativeOut(
            decision=DecisionOut(
                headline="Report narrative unavailable — see scores and signal detail.",
                why_positive=WhyColumn(title="", body=""),
                why_negative=WhyColumn(title="", body="")),
            quick_summary="", strengths=[], concerns=[], questions=[],
            methodology=MethodologyOut(note="Narrative generation failed.", charity_flags=[]))
    return parsed
