"""Layer 2 — post-interview per-signal re-check (LLM, Responses API).

Reads the engine's append-only NOTES (verbatim quotes + texture + stance) for one
signal and verifies them against the question rubric — a 'lighter re-check' that may
refine the deterministic demonstration level (e.g. solid→strong, or confirm a thin
answer is a genuine bluff). Graceful refusal keeps the engine's level."""
from __future__ import annotations

import hashlib

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.interview_runtime.evidence import EvidenceNote
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.types import SignalDef

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _render_notes(notes: list[EvidenceNote]) -> str:
    lines = [
        f"[note {n.seq} · {n.stance.value}/{n.texture.value}"
        f"{' · via probe' if n.via_probe else ''}] {n.quote}"
        for n in notes
    ]
    return "\n".join(lines) if lines else "(no supporting notes)"


async def recheck_signal(
    *, signal_def: SignalDef, notes: list[EvidenceNote],
    question_context: str, engine_level: str, correlation_id: str,
    question_kind: str | None = None,
) -> SignalRecheckOut:
    system_prompt = PromptLoader(version=ai_config.report_scorer_prompt_version).get(
        "report_scorer/signal_recheck"
    )
    prefix = (
        f"{system_prompt}\n\n"
        f"<signal>\n{signal_def.value}\n(type: {signal_def.type}, "
        f"priority: {signal_def.priority}, must_have: {signal_def.knockout})\n</signal>\n\n"
        f"<question_kind>\n{question_kind or 'unknown'}\n</question_kind>\n\n"
        f"<question_context>\n{question_context}\n</question_context>\n\n"
        f"<engine_prior>\nlevel={engine_level}\n</engine_prior>"
    )
    notes_block = _render_notes(notes)
    messages = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<notes>\n{notes_block}\n</notes>"},
    ]
    sig_hash = hashlib.sha256(signal_def.value.encode("utf-8")).hexdigest()[:12]
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": SignalRecheckOut,
        "prompt_cache_key": (
            # "rc3" — bumped when the re-check prompt moved to rubric-tier grading (full range,
            # no prior-anchoring) + honest factual-gate downgrade.
            f"{ai_config.report_scorer_prompt_cache_key_prefix}:rc3:"
            f"{ai_config.report_scorer_prompt_version}:{sig_hash}:{ai_config.report_scorer_model}"
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
        return SignalRecheckOut(
            evidence_quotes=[], justification="Model did not return a parse.",
            level=engine_level,  # type: ignore[arg-type]
            overridden=False, override_reason=None)

    grounded, _ = ground_quotes(parsed.evidence_quotes, notes_block)
    return parsed.model_copy(update={"evidence_quotes": grounded})
