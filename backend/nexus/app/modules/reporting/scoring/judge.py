"""Per-answer LLM judge — instructor structured call with prefix caching.

Grades a single candidate answer against a question's rubric.  The
system prompt + question context form a stable prefix that is shared
across all candidates answering the same question, enabling OpenAI
automatic prefix caching.

Pattern mirrors ``app/modules/interview_engine_v2/brain/service.py``
exactly:
- ``get_openai_client()`` imported at module level (mockable in tests).
- ``create_with_completion`` for (parsed_model, raw_completion) tuple.
- Effort-gating: ``reasoning_effort`` forwarded only when
  ``ai_config.report_scorer_effort`` is truthy.
- ``prompt_cache_key`` built per call so all candidates grading the
  same question share the cached system-prompt prefix.
- ``set_llm_span_attributes`` enriches the active OTel span (no-op
  when no span is recording).
- Structured logs via structlog — no PII (transcripts / quotes never
  logged).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.ai.client import get_openai_client
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.ai.tracing import set_llm_span_attributes
from app.modules.reporting.schemas import AnswerRating, JudgeVerdict
from app.modules.reporting.scoring.grounding import ground_quotes
from app.modules.reporting.scoring.input_builder import build_messages, render_prefix

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

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


async def grade_answer(
    *,
    question: dict,
    transcript_excerpt: str,
    correlation_id: str,
    n_samples: int = 1,
) -> AnswerRating:
    """Grade a single candidate answer with one LLM call.

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
        ``grounded`` flag.
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

    client = get_openai_client()
    create_kwargs: dict[str, object] = {
        "model": ai_config.report_scorer_model,
        "response_model": JudgeVerdict,
        "messages": messages,
        "max_retries": 1,
        "prompt_cache_key": prompt_cache_key,
    }
    # Effort-gating contract: forward reasoning_effort ONLY when truthy.
    # Reasoning models reject temperature/seed — do not pass them.
    if ai_config.report_scorer_effort:
        create_kwargs["reasoning_effort"] = ai_config.report_scorer_effort

    verdict, completion = await client.chat.completions.create_with_completion(
        **create_kwargs
    )

    # Log cache usage — never log the transcript or evidence quotes.
    usage = getattr(completion, "usage", None)
    if usage is not None:
        details = getattr(usage, "prompt_tokens_details", None)
        log.info(
            "reporting.judge.usage",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            correlation_id=correlation_id,
            question_id=question_id,
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
