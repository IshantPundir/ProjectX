"""Per-answer LLM judge — Responses API with native Structured Outputs.

Grades a single candidate answer against a question's rubric.  The
system prompt + question context form a stable prefix that is shared
across all candidates answering the same question, enabling OpenAI
automatic prefix caching (Responses API caches automatically; no
explicit ``prompt_cache_key`` needed for caching, but we pass it so
the SDK can attach it to the request for Langfuse / OTel correlation).

Uses ``client.responses.parse(text_format=...)`` (NOT function tools)
so ``reasoning={"effort": ...}`` is supported — function tools +
reasoning_effort is explicitly rejected by gpt-5.4 on /v1/chat/completions
with HTTP 400.

Pattern mirrors ``app/modules/interview_engine/brain/service.py``
except the Responses API is used instead of chat.completions:
- ``get_raw_openai_client()`` imported at module level (mockable in tests).
- ``response.output_parsed`` for parsed model extraction.
- Effort-gating: ``reasoning={"effort": ...}`` forwarded only when
  ``ai_config.report_scorer_effort`` is truthy (dict form, not
  ``reasoning_effort=``).
- ``prompt_cache_key`` passed through (accepted by responses.parse).
- ``set_llm_span_attributes`` enriches the active OTel span.
- Structured logs via structlog — no PII (transcripts / quotes never
  logged).
- Refusal path handled gracefully: returns a conservative below_bar
  AnswerRating so one model refusal does not crash the entire report.
"""
from __future__ import annotations

import structlog
from opentelemetry import trace

from app.ai.client import get_raw_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.schemas import CommunicationVerdict
from app.modules.reporting.scoring.input_builder import build_messages

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")


def _extract_parsed_or_refusal(response: object) -> tuple[object | None, bool]:
    """Extract the parsed object from a ParsedResponse, or detect refusal.

    Returns ``(parsed_object, is_refusal)``.

    Primary path: ``response.output_parsed`` (SDK convenience property).
    Fallback: iterate ``response.output[].content[]`` for the first
    ``output_text`` item with a non-None ``parsed`` value, or detect a
    ``refusal`` content type.
    """
    # Fast path — SDK convenience property
    output_parsed = getattr(response, "output_parsed", None)
    if output_parsed is not None:
        return output_parsed, False

    # Detailed walk — also detects refusals
    output_items = getattr(response, "output", None) or []
    for item in output_items:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            for content in getattr(item, "content", None) or []:
                content_type = getattr(content, "type", None)
                if content_type == "refusal":
                    return None, True
                if content_type == "output_text":
                    parsed = getattr(content, "parsed", None)
                    if parsed is not None:
                        return parsed, False

    # output_parsed is None and no refusal found — treat as missing parse
    return None, False


async def grade_communication(
    *,
    transcript_text: str,
    correlation_id: str,
) -> CommunicationVerdict:
    """Grade the candidate's content-level communication across the full transcript.

    Loads ``prompts/v3/report_scorer/communication.txt`` as the stable system
    prefix; the transcript is placed last in the user message (cache-friendly
    pattern).  Returns a :class:`CommunicationVerdict` with ``level`` ∈
    {``weak``, ``adequate``, ``strong``}.

    This score is **NOT** included in the Overall score — it is a separate
    content-level read of the full transcript, independent of JD signals.

    Args:
        transcript_text: Candidate turns joined into a single string.
        correlation_id:  Correlation ID for tracing and structured logs.
    """
    system_prompt = PromptLoader(
        version=ai_config.report_scorer_prompt_version
    ).get("report_scorer/communication")

    messages = build_messages(prefix=system_prompt, transcript_excerpt=transcript_text)

    prompt_cache_key = (
        f"{ai_config.report_scorer_prompt_cache_key_prefix}"
        f":communication"
        f":{ai_config.report_scorer_prompt_version}"
        f":{ai_config.report_scorer_model}"
    )

    client = get_raw_openai_client()
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": CommunicationVerdict,
        "prompt_cache_key": prompt_cache_key,
    }
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(
            prompt_name="report_scorer_communication",
            prompt_version=ai_config.report_scorer_prompt_version,
            correlation_id=correlation_id,
        )
        response = await client.responses.parse(**kwargs)

    # Log cache usage — never log the transcript or evidence quotes.
    usage = getattr(response, "usage", None)
    if usage is not None:
        details = getattr(usage, "input_tokens_details", None)
        log.info(
            "reporting.judge.communication.usage",
            input_tokens=getattr(usage, "input_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            correlation_id=correlation_id,
        )

    verdict, is_refusal = _extract_parsed_or_refusal(response)

    if is_refusal or verdict is None:
        log.warning(
            "reporting.judge.communication.refusal",
            correlation_id=correlation_id,
            is_refusal=is_refusal,
        )
        return CommunicationVerdict(
            evidence_quotes=[],
            justification="Model refused to grade communication.",
            level="weak",
        )

    return verdict
