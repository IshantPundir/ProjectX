"""Crisp 1-4 word glance titles for verbose competency statements (LLM).

A small, batched call run during report scoring (async — no latency pressure).
The recruiter report shows the short title big with the full statement as light
subtext, so a report is decidable at a glance. Best-effort: any failure returns
{} and consumers fall back to the verbose `signal` string.
"""
from __future__ import annotations

import json

import structlog
from opentelemetry import trace
from pydantic import BaseModel

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


class _LabelItem(BaseModel):
    id: str
    label: str


class SignalLabelsOut(BaseModel):
    labels: list[_LabelItem]


async def generate_signal_labels(
    values: list[str], *, correlation_id: str
) -> dict[str, str]:
    """Map each verbose competency string → a crisp 1-4 word title.

    Returns {} on empty input, refusal, or API error (caller falls back to the
    full string). Indexes by position so the mapping survives any reordering.
    """
    uniq = list(dict.fromkeys(v for v in values if v and v.strip()))
    if not uniq:
        return {}

    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/signal_labels"
    )
    payload = {"competencies": [{"id": str(i), "value": v} for i, v in enumerate(uniq)]}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_narrative_model,
        "input": messages,
        "text_format": SignalLabelsOut,
        "prompt_cache_key": (
            f"signal_labels.v1:{ai_config.report_scorer_prompt_version}:"
            f"{ai_config.report_narrative_model}"
        ),
    }
    if ai_config.report_narrative_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_narrative_effort}

    try:
        with _tracer.start_as_current_span("openai.responses.parse"):
            set_llm_span_attributes(
                prompt_name="report_signal_labels",
                prompt_version=ai_config.report_scorer_prompt_version,
                correlation_id=correlation_id,
            )
            response = await get_raw_openai_client().responses.parse(**kwargs)
        parsed = getattr(response, "output_parsed", None)
    except Exception:  # noqa: BLE001 — labels are cosmetic; never fail the report
        log.warning("reporting.signal_labels.error", correlation_id=correlation_id,
                    exc_info=True)
        return {}

    if parsed is None:
        log.warning("reporting.signal_labels.refusal", correlation_id=correlation_id)
        return {}

    out: dict[str, str] = {}
    for item in parsed.labels:
        label = (item.label or "").strip()
        if not label or not item.id.isdigit():
            continue
        idx = int(item.id)
        if 0 <= idx < len(uniq):
            out[uniq[idx]] = label
    return out
