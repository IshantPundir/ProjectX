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
from app.modules.reporting.schemas import AnswerRating, CommunicationVerdict, JudgeVerdict
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.input_builder import build_messages, render_prefix

log = structlog.get_logger()
_tracer = trace.get_tracer("nexus.ai.openai")

# Level ordering for majority vote / tie-break (conservative = lower is better)
_LEVEL_ORDER: list[str] = ["below_bar", "meets_bar", "excellent"]


def _majority_level(levels: list[str]) -> str:
    """Return the majority level from a list; tie-break by choosing the
    most conservative (lowest) level."""
    counts: dict[str, int] = {}
    for lvl in levels:
        counts[lvl] = counts.get(lvl, 0) + 1
    max_count = max(counts.values())
    candidates = [lvl for lvl, c in counts.items() if c == max_count]
    # Tie-break: pick the level with the smallest index in _LEVEL_ORDER
    for lvl in _LEVEL_ORDER:
        if lvl in candidates:
            return lvl
    # Fallback: return the first candidate (should never reach here with valid levels)
    return candidates[0]


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


async def grade_answer(
    *,
    question: dict,
    transcript_excerpt: str,
    correlation_id: str,
    n_samples: int = 1,
) -> AnswerRating:
    """Grade a single candidate answer with one Responses API call.

    Args:
        question: Dict with keys ``id``, ``text``, ``rubric`` (dict with
                  ``excellent``/``meets_bar``/``below_bar``),
                  ``positive_evidence`` (list[str]), ``red_flags``
                  (list[str]).
        transcript_excerpt: The candidate's answer excerpt.  Dynamic
                            per-candidate; placed last in the user message
                            so the stable prefix is cacheable.
        correlation_id: Correlation ID for tracing and structured logs.
        n_samples: Number of samples to draw (unused here; used by
                   ``grade_answer_consistent``).

    Returns:
        ``AnswerRating`` with grounded evidence quotes and a
        ``grounded`` flag.  If the model refuses, returns a
        conservative ``below_bar`` rating with no evidence and
        ``grounded=False`` rather than crashing the report.
    """
    system_prompt = PromptLoader(
        version=ai_config.report_scorer_prompt_version
    ).get("report_scorer/system")

    prefix = render_prefix(system_prompt=system_prompt, question=question)
    messages = build_messages(prefix=prefix, transcript_excerpt=transcript_excerpt)

    # Build a per-question prompt_cache_key so all candidates answering
    # the same question (on the same model + prompt version) share the
    # cached system-prompt prefix.
    question_id: str = question["id"]
    prompt_cache_key = (
        f"{ai_config.report_scorer_prompt_cache_key_prefix}"
        f":{ai_config.report_scorer_prompt_version}"
        f":{question_id}"
        f":{ai_config.report_scorer_model}"
    )

    # Enrich the active OTel span (no-op when none is recording).
    set_llm_span_attributes(
        prompt_name="report_scorer",
        prompt_version=ai_config.report_scorer_prompt_version,
        correlation_id=correlation_id,
    )

    client = get_raw_openai_client()
    kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "input": messages,
        "text_format": JudgeVerdict,
        "prompt_cache_key": prompt_cache_key,
    }
    # Effort-gating: pass reasoning as a dict (Responses API form), not
    # reasoning_effort= (chat.completions form).
    if ai_config.report_scorer_effort:
        kwargs["reasoning"] = {"effort": ai_config.report_scorer_effort}

    with _tracer.start_as_current_span("openai.responses.parse"):
        set_llm_span_attributes(
            prompt_name="report_scorer",
            prompt_version=ai_config.report_scorer_prompt_version,
            correlation_id=correlation_id,
        )
        response = await client.responses.parse(**kwargs)

    # Log cache usage — never log the transcript or evidence quotes.
    usage = getattr(response, "usage", None)
    if usage is not None:
        details = getattr(usage, "input_tokens_details", None)
        log.info(
            "reporting.judge.usage",
            input_tokens=getattr(usage, "input_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            correlation_id=correlation_id,
            question_id=question_id,
        )

    verdict, is_refusal = _extract_parsed_or_refusal(response)

    if is_refusal or verdict is None:
        log.warning(
            "reporting.judge.refusal",
            question_id=question_id,
            correlation_id=correlation_id,
            is_refusal=is_refusal,
        )
        return AnswerRating(
            question_id=question_id,
            level="below_bar",
            evidence_quotes=[],
            red_flags_hit=[],
            justification="Model refused to grade this answer.",
            grounded=False,
        )

    # Ground evidence quotes against the transcript — drop hallucinations.
    grounded_quotes, ungrounded = ground_quotes(
        verdict.evidence_quotes, transcript_excerpt
    )

    return AnswerRating(
        question_id=question_id,
        level=verdict.level,
        evidence_quotes=grounded_quotes,
        red_flags_hit=verdict.red_flags_hit,
        justification=verdict.justification,
        grounded=(len(ungrounded) == 0),
    )


async def grade_answer_consistent(
    *,
    question: dict,
    transcript_excerpt: str,
    correlation_id: str,
    n_samples: int = 1,
) -> AnswerRating:
    """Selective self-consistency wrapper around ``grade_answer``.

    If ``n_samples <= 1``, delegates directly to ``grade_answer``.
    Otherwise, samples ``grade_answer`` N times and returns the
    ``AnswerRating`` whose ``level`` is the majority level.
    Tie-break is conservative (prefer the lower/stricter level).

    The per-call N decision is made by the service (Task 15), not here.
    """
    if n_samples <= 1:
        return await grade_answer(
            question=question,
            transcript_excerpt=transcript_excerpt,
            correlation_id=correlation_id,
        )

    import asyncio

    ratings = await asyncio.gather(
        *[
            grade_answer(
                question=question,
                transcript_excerpt=transcript_excerpt,
                correlation_id=correlation_id,
            )
            for _ in range(n_samples)
        ]
    )

    majority = _majority_level([r.level for r in ratings])
    # Return the first rating that matches the majority level.
    for rating in ratings:
        if rating.level == majority:
            return rating
    # Should never reach here.
    return ratings[0]


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
