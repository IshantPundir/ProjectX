"""Layer 2 — post-interview per-signal re-check (LLM, Responses API + Structured Outputs).

Mirrors scoring/judge.py: get_raw_openai_client(), responses.parse(text_format=...),
effort-gating via dict reasoning=, evidence grounded against the candidate's turns,
graceful refusal fallback (keep the engine's prior state)."""
from __future__ import annotations

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.types import SignalDef, SignalTurn

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _render_turns(turns: list[SignalTurn]) -> str:
    lines = []
    for i, t in enumerate(turns, 1):
        g = t.grade or "null"
        lines.append(f"[turn {i} · engine grade={g}] {t.candidate_quote}")
    return "\n".join(lines) if lines else "(no turns recorded)"


async def recheck_signal(
    *, signal_def: SignalDef, evidence_turns: list[SignalTurn],
    question_context: str, engine_state: str, correlation_id: str,
) -> SignalRecheckOut:
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/signal_recheck"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<signal>\n{signal_def.value}\n(type: {signal_def.type}, "
        f"priority: {signal_def.priority}, must_have: {signal_def.knockout})\n</signal>\n\n"
        f"<question_context>\n{question_context}\n</question_context>\n\n"
        f"<engine_prior>\nstate={engine_state}\n</engine_prior>"
    )
    transcript_block = _render_turns(evidence_turns)
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<turns>\n{transcript_block}\n</turns>"},
    ]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": SignalRecheckOut,
        "prompt_cache_key": (
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:recheck:"
            f"{ai_config.report_scorer_prompt_version}:{signal_def.value}:{ai_config.report_scorer_model}"
        ),
    }
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(prompt_name="report_signal_recheck",
                                prompt_version=ai_config.report_scorer_prompt_version,
                                correlation_id=correlation_id)
        response = await get_raw_openai_client().responses.parse(**kwargs)

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        log.warning("reporting.recheck.refusal", signal=signal_def.value,
                    correlation_id=correlation_id)
        fallback_state = (  # type: ignore[assignment]
            engine_state if engine_state != "none" else "partial"
        )
        return SignalRecheckOut(
            evidence_quotes=[],
            justification="Model did not return a parse.",
            grade="null",
            state=fallback_state,
            overridden=False,
            override_reason=None,
        )

    grounded, _ungrounded = ground_quotes(parsed.evidence_quotes, transcript_block)
    return parsed.model_copy(update={"evidence_quotes": grounded})
