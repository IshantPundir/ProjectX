"""Layer 2.5 — bounded cross-signal holistic adjustment (LLM, Responses API).

Mirrors scoring/recheck.py: get_raw_openai_client(), responses.parse(text_format=...),
effort-gating, grounded evidence, graceful refusal (delta 0). Produces a SMALL,
justified delta to the deterministic session score for gestalt the per-signal sum
misses (e.g. a pervasive surface-level / bluffing pattern). Hard-bounded to ±5 and
re-capped by the caller so it can never break a categorical guarantee."""
from __future__ import annotations

import json

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.scoring.aggregate import ScoredSignal
from app.modules.reporting.scoring.constants import HOLISTIC_ADJ_MAX
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.schemas import HolisticAdjustmentOut

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _signal_digest(scored: list[ScoredSignal]) -> str:
    return json.dumps([
        {"signal": s.value, "state": s.state, "texture": s.texture,
         "must_have": s.knockout, "score": s.score}
        for s in scored
        if s.state != "none"
    ], ensure_ascii=False)


async def score_holistic(
    *, session_score: int | None, scored: list[ScoredSignal], knockout_close: bool,
    coverage: float, transcript_text: str, correlation_id: str,
) -> HolisticAdjustmentOut:
    if session_score is None:
        return HolisticAdjustmentOut(delta=0, justification="No assessable evidence.")

    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/holistic"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<session_score>\n{session_score}\n</session_score>\n\n"
        f"<facts>\nknockout_close={knockout_close}, coverage={coverage:.2f}\n</facts>\n\n"
        f"<per_signal>\n{_signal_digest(scored)}\n</per_signal>"
    )
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<transcript>\n{transcript_text}\n</transcript>"},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": HolisticAdjustmentOut,
        "prompt_cache_key": (
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:holistic:"
            f"{ai_config.report_scorer_prompt_version}:{ai_config.report_scorer_model}"
        ),
    }
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    try:
        with _tracer.start_as_current_span("openai.responses.parse"):
            set_llm_span_attributes(prompt_name="report_holistic",
                                    prompt_version=ai_config.report_scorer_prompt_version,
                                    correlation_id=correlation_id)
            response = await get_raw_openai_client().responses.parse(**kwargs)
    except Exception:  # noqa: BLE001 — non-critical adjustment; degrade to delta 0
        log.warning("reporting.holistic.api_error", correlation_id=correlation_id)
        return HolisticAdjustmentOut(delta=0, justification="API error — skipped.")

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.holistic.refusal", correlation_id=correlation_id)
        return HolisticAdjustmentOut(delta=0, justification="Model did not return a parse.")

    bounded = max(-HOLISTIC_ADJ_MAX, min(HOLISTIC_ADJ_MAX, parsed.delta))
    grounded, _ = ground_quotes(parsed.evidence_quotes, transcript_text)
    return parsed.model_copy(update={"delta": bounded, "evidence_quotes": grounded})
